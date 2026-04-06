"""Константы моделей, Anthropic Messages API, мультипровайдерный llm_generate (DeepSeek / Anthropic / OpenAI)."""

import logging
import time
from typing import Any, Callable, Optional, Tuple, TypeVar

from config import Config

CLAUDE_MODEL_HAIKU = "claude-haiku-4-5"
CLAUDE_MODEL_SONNET = "claude-sonnet-4-6"

logger = logging.getLogger(__name__)

T = TypeVar("T")

_anthropic_client = None
_openai_deepseek_client = None
_openai_default_client = None


def message_text(msg) -> str:
    """Текст из ответа client.messages.create (SDK возвращает content как список блоков)."""
    parts = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    return _anthropic_client


def _get_openai_deepseek_client():
    global _openai_deepseek_client
    if _openai_deepseek_client is None:
        from openai import OpenAI

        _openai_deepseek_client = OpenAI(
            api_key=Config.DEEPSEEK_API_KEY,
            base_url=Config.DEEPSEEK_BASE_URL,
        )
    return _openai_deepseek_client


def _get_openai_default_client():
    global _openai_default_client
    if _openai_default_client is None:
        from openai import OpenAI

        _openai_default_client = OpenAI(api_key=Config.OPENAI_API_KEY)
    return _openai_default_client


def _resolve_llm_model(provider: str, model: Optional[str]) -> str:
    if provider == "anthropic":
        m = str(model).strip() if model else ""
        return m or CLAUDE_MODEL_SONNET
    if provider == "deepseek":
        return Config.DEEPSEEK_MODEL
    return Config.OPENAI_MODEL


def _estimate_llm_cost_usd(provider: str, model: str, inp: int, out: int) -> float:
    """Грубая оценка для дашборда (актуальные тарифы уточнять в биллинге)."""
    inp, out = max(0, inp), max(0, out)
    if provider == "deepseek":
        return inp * 0.14e-6 + out * 0.28e-6
    if provider == "openai":
        return inp * 0.15e-6 + out * 0.60e-6
    if provider == "anthropic":
        return inp * 3.0e-6 + out * 15.0e-6
    return 0.0


def _with_llm_retries(fn: Callable[[], T]) -> T:
    max_retries = Config.ANTHROPIC_MAX_RETRIES
    base_delay = Config.ANTHROPIC_RETRY_BASE_DELAY_SEC
    last_exc: Optional[BaseException] = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt >= max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "LLM запрос попытка %s/%s не удалась: %s; пауза %.1f с",
                attempt + 1,
                max_retries,
                e,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _call_anthropic(prompt: str, max_tokens: int, temperature: float, model: str) -> Tuple[str, int, int]:
    client = _get_anthropic_client()

    def do_call():
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )

    msg = _with_llm_retries(do_call)
    u = getattr(msg, "usage", None)
    inp = int(getattr(u, "input_tokens", 0) or 0) if u else 0
    out = int(getattr(u, "output_tokens", 0) or 0) if u else 0
    return message_text(msg), inp, out


def _call_openai_chat(
    client, model: str, prompt: str, max_tokens: int, temperature: float
) -> Tuple[str, int, int]:
    def do_call():
        return client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )

    resp = _with_llm_retries(do_call)
    ch = (resp.choices[0].message.content or "").strip()
    u = getattr(resp, "usage", None)
    inp = int(getattr(u, "prompt_tokens", 0) or 0) if u else 0
    out = int(getattr(u, "completion_tokens", 0) or 0) if u else 0
    return ch, inp, out


def llm_generate(
    prompt: str,
    max_tokens: int,
    temperature: float,
    model: Optional[str] = None,
    *,
    project_id: Optional[int] = None,
    step: Optional[str] = None,
) -> str:
    """
    Единая точка вызова LLM по Config.LLM_PROVIDER.
    project_id + step — опционально для записи в llm_usage_log.
    """
    provider = Config.LLM_PROVIDER
    m = _resolve_llm_model(provider, model)

    if provider == "anthropic":
        text, inp, out = _call_anthropic(prompt, max_tokens, temperature, m)
    elif provider == "deepseek":
        client = _get_openai_deepseek_client()
        text, inp, out = _call_openai_chat(client, m, prompt, max_tokens, temperature)
    elif provider == "openai":
        client = _get_openai_default_client()
        text, inp, out = _call_openai_chat(client, m, prompt, max_tokens, temperature)
    else:
        raise ValueError(f"Неизвестный LLM_PROVIDER: {provider!r}")

    cost = _estimate_llm_cost_usd(provider, m, inp, out)
    logger.info(
        "[llm] provider=%s model=%s step=%s in_tok=%s out_tok=%s est_cost=$%.5f",
        provider,
        m,
        step or "-",
        inp,
        out,
        cost,
    )
    if project_id is not None and step:
        try:
            from database import log_llm_usage

            log_llm_usage(project_id, step, provider, m, inp, out, cost)
        except Exception as e:
            logger.warning("Не удалось записать llm_usage_log: %s", e)

    return text


def anthropic_messages_create(client, *, max_retries: int, base_delay_sec: float, **kwargs: Any):
    """
    Вызов client.messages.create с экспоненциальным backoff при сбоях сети/API.
    Последняя попытка пробрасывает исключение.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except Exception as e:
            last_exc = e
            if attempt >= max_retries - 1:
                raise
            delay = base_delay_sec * (2**attempt)
            logger.warning(
                "Anthropic messages.create попытка %s/%s не удалась: %s; пауза %.1f с",
                attempt + 1,
                max_retries,
                e,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
