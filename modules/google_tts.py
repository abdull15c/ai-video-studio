"""
Google Cloud Text-to-Speech: единая точка входа для озвучки через Google.
Ленивая инициализация клиента — до первого вызова credentials не требуются.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Callable, List, Optional, TypeVar

import requests
from google.api_core import exceptions as core_exc
from google.auth import exceptions as auth_exc

from config import Config

logger = logging.getLogger(__name__)

_TTS_CLIENT = None
_AUTH_SESSION = None

GEMINI_MODEL_NAME = "gemini-2.5-flash-tts"
GEMINI_SYNTHESIZE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

_GEMINI_USD_PER_TEXT_TOKEN = 1.25e-5
_GEMINI_USD_PER_AUDIO_TOKEN = 6e-6
_CHIRP_USD_PER_CHAR = 1.6e-6
_NEURAL2_USD_PER_CHAR = 1.2e-6

T = TypeVar("T")


def _remove_partial_outfile(out_path: str) -> None:
    try:
        if out_path and os.path.isfile(out_path):
            os.remove(out_path)
    except OSError:
        pass


def _get_tts_client():
    global _TTS_CLIENT
    if _TTS_CLIENT is None:
        from google.cloud import texttospeech

        _TTS_CLIENT = texttospeech.TextToSpeechClient()
    return _TTS_CLIENT


def _get_authorized_session():
    global _AUTH_SESSION
    if _AUTH_SESSION is None:
        from google.auth import default
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _AUTH_SESSION = AuthorizedSession(creds)
    return _AUTH_SESSION


def _split_utf8_bytes(s: str, max_bytes: int) -> List[str]:
    if len(s.encode("utf-8")) <= max_bytes:
        return [s]
    parts: List[str] = []
    buf: List[str] = []
    size = 0
    for ch in s:
        b = ch.encode("utf-8")
        if size + len(b) > max_bytes and buf:
            parts.append("".join(buf))
            buf = [ch]
            size = len(b)
        else:
            buf.append(ch)
            size += len(b)
    if buf:
        parts.append("".join(buf))
    return parts


def estimate_gemini_cost_usd(chars_count: int, duration_sec: float) -> float:
    text_tokens = max(0, chars_count) / 4.0
    audio_tokens = max(0.0, duration_sec) * 25.0
    return text_tokens * _GEMINI_USD_PER_TEXT_TOKEN + audio_tokens * _GEMINI_USD_PER_AUDIO_TOKEN


def estimate_chirp_cost_usd(chars_count: int) -> float:
    return max(0, chars_count) * _CHIRP_USD_PER_CHAR


def estimate_neural2_cost_usd(chars_count: int) -> float:
    return max(0, chars_count) * _NEURAL2_USD_PER_CHAR


def _audio_duration_sec(path: str) -> float:
    try:
        from mutagen.mp3 import MP3

        if os.path.isfile(path):
            return float(MP3(path).info.length)
    except Exception:
        pass
    return 0.0


def _log_usage_row(
    *,
    engine: str,
    model: str,
    voice: str,
    chars_count: int,
    out_path: str,
    est_cost: float,
    project_id: Optional[int],
    scene_id: Optional[int],
) -> None:
    duration = _audio_duration_sec(out_path)
    fname = os.path.basename(out_path)
    logger.info(
        "[google-tts] engine=%s voice=%s chars=%s est_cost=$%.4f file=%s",
        engine,
        voice,
        chars_count,
        est_cost,
        fname,
    )
    try:
        from database import log_tts_usage

        log_tts_usage(
            project_id=project_id,
            scene_id=scene_id,
            engine=engine,
            model=model,
            voice=voice,
            chars_count=chars_count,
            duration_sec=duration,
            estimated_cost_usd=est_cost,
        )
    except Exception as e:
        logger.warning("Не удалось записать tts_usage_log: %s", e)


def _with_retries(fn: Callable[[], T]) -> T:
    n = max(1, Config.GOOGLE_TTS_MAX_RETRIES)
    last_exc: Optional[BaseException] = None
    for attempt in range(n):
        try:
            return fn()
        except (
            core_exc.ServiceUnavailable,
            core_exc.DeadlineExceeded,
            requests.exceptions.ConnectionError,
        ) as e:
            last_exc = e
            if attempt < n - 1:
                delay = Config.GOOGLE_TTS_RETRY_BASE_DELAY_SEC * (2**attempt)
                logger.warning("Google TTS сеть/сервис, попытка %s/%s, пауза %.1f с", attempt + 1, n, delay)
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _synthesize_gemini(text: str, voice: str, prompt: str, out_path: str) -> bool:
    text_chunks = _split_utf8_bytes(text, 4000)
    prompt_chunks = _split_utf8_bytes(prompt or "", 4000)
    if len(prompt_chunks) > 1:
        logger.warning("Gemini TTS: prompt обрезан до 4000 байт UTF-8")
    prompt_use = prompt_chunks[0] if prompt_chunks else ""

    session = _get_authorized_session()
    audio_parts: List[bytes] = []

    for t in text_chunks:

        def do_post(chunk=t):
            body = {
                "input": {"text": chunk, "prompt": prompt_use},
                "voice": {
                    "languageCode": "ru-RU",
                    "name": voice,
                    "modelName": GEMINI_MODEL_NAME,
                },
                "audioConfig": {"audioEncoding": "MP3"},
            }
            r = session.post(GEMINI_SYNTHESIZE_URL, json=body, timeout=120)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
            data = r.json()
            raw = data.get("audioContent")
            if not raw:
                raise RuntimeError("Ответ без audioContent")
            return base64.b64decode(raw)

        try:
            audio_parts.append(_with_retries(do_post))
        except (
            auth_exc.DefaultCredentialsError,
            core_exc.PermissionDenied,
            core_exc.Forbidden,
            core_exc.ResourceExhausted,
        ):
            _remove_partial_outfile(out_path)
            raise
        except Exception as e:
            logger.exception("Gemini TTS: %s", e)
            _remove_partial_outfile(out_path)
            return False

    try:
        d = os.path.dirname(os.path.abspath(out_path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out_path, "wb") as f:
            for part in audio_parts:
                f.write(part)
        return True
    except OSError as e:
        logger.error("Gemini TTS: запись файла: %s", e)
        _remove_partial_outfile(out_path)
        return False


def _synthesize_chirp(text: str, voice: str, speaking_rate: float, out_path: str) -> bool:
    from google.cloud import texttospeech

    sr = max(0.25, min(4.0, float(speaking_rate)))
    client = _get_tts_client()

    def do_syn():
        inp = texttospeech.SynthesisInput(text=text)
        vcfg = texttospeech.VoiceSelectionParams(language_code="ru-RU", name=voice)
        acfg = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=sr,
        )
        return client.synthesize_speech(
            input=inp,
            voice=vcfg,
            audio_config=acfg,
        ).audio_content

    try:
        content = _with_retries(do_syn)
        d = os.path.dirname(os.path.abspath(out_path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(content)
        return True
    except (
        auth_exc.DefaultCredentialsError,
        core_exc.PermissionDenied,
        core_exc.Forbidden,
        core_exc.ResourceExhausted,
    ):
        _remove_partial_outfile(out_path)
        raise
    except Exception as e:
        logger.exception("Chirp TTS: %s", e)
        _remove_partial_outfile(out_path)
        return False


def _synthesize_neural2(text: str, voice: str, speaking_rate: float, out_path: str) -> bool:
    from google.cloud import texttospeech

    sr = max(0.25, min(4.0, float(speaking_rate)))
    pitch = float(getattr(Config, "GOOGLE_TTS_PITCH", 0.0))
    client = _get_tts_client()

    def do_syn():
        inp = texttospeech.SynthesisInput(text=text)
        vcfg = texttospeech.VoiceSelectionParams(language_code="ru-RU", name=voice)
        acfg = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=sr,
            pitch=pitch,
        )
        return client.synthesize_speech(
            input=inp,
            voice=vcfg,
            audio_config=acfg,
        ).audio_content

    try:
        content = _with_retries(do_syn)
        d = os.path.dirname(os.path.abspath(out_path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(content)
        return True
    except (
        auth_exc.DefaultCredentialsError,
        core_exc.PermissionDenied,
        core_exc.Forbidden,
        core_exc.ResourceExhausted,
    ):
        _remove_partial_outfile(out_path)
        raise
    except Exception as e:
        logger.exception("Neural2 TTS: %s", e)
        _remove_partial_outfile(out_path)
        return False


def generate_audio_google(
    text: str,
    voice: Optional[str],
    out_path: str,
    prompt: Optional[str] = None,
    speaking_rate: Optional[float] = None,
    project_id: Optional[int] = None,
    scene_id: Optional[int] = None,
) -> bool:
    engine = (Config.TTS_ENGINE or "").strip().lower()
    v = (voice or Config.GOOGLE_TTS_VOICE or "Kore").strip()
    use_prompt = prompt if prompt is not None else Config.GOOGLE_TTS_PROMPT
    sr = (
        Config.GOOGLE_TTS_SPEAKING_RATE
        if speaking_rate is None
        else float(speaking_rate)
    )
    text = (text or "").strip()
    if not text:
        logger.error("Google TTS: пустой текст")
        return False

    if not engine.startswith("google-"):
        logger.error("Google TTS вызван при TTS_ENGINE=%r", engine)
        return False

    chars_count = len(text)

    try:
        if engine == "google-gemini":
            ok = _synthesize_gemini(text, v, use_prompt, out_path)
            if ok:
                dur = _audio_duration_sec(out_path)
                cost = estimate_gemini_cost_usd(chars_count, dur)
                _log_usage_row(
                    engine=engine,
                    model=GEMINI_MODEL_NAME,
                    voice=v,
                    chars_count=chars_count,
                    out_path=out_path,
                    est_cost=cost,
                    project_id=project_id,
                    scene_id=scene_id,
                )
            return ok

        if engine == "google-chirp":
            ok = _synthesize_chirp(text, v, sr, out_path)
            if ok:
                cost = estimate_chirp_cost_usd(chars_count)
                _log_usage_row(
                    engine=engine,
                    model="chirp",
                    voice=v,
                    chars_count=chars_count,
                    out_path=out_path,
                    est_cost=cost,
                    project_id=project_id,
                    scene_id=scene_id,
                )
            return ok

        if engine == "google-neural2":
            ok = _synthesize_neural2(text, v, sr, out_path)
            if ok:
                cost = estimate_neural2_cost_usd(chars_count)
                _log_usage_row(
                    engine=engine,
                    model="neural2",
                    voice=v,
                    chars_count=chars_count,
                    out_path=out_path,
                    est_cost=cost,
                    project_id=project_id,
                    scene_id=scene_id,
                )
            return ok

        logger.error("Неизвестный Google TTS engine: %s", engine)
        return False

    except auth_exc.DefaultCredentialsError:
        logger.error(
            "Google Cloud credentials not found. Set GOOGLE_APPLICATION_CREDENTIALS in .env"
        )
        _remove_partial_outfile(out_path)
        return False
    except core_exc.PermissionDenied:
        logger.error(
            "Text-to-Speech API not enabled or access denied. Enable it in Google Cloud Console."
        )
        _remove_partial_outfile(out_path)
        return False
    except core_exc.Forbidden:
        logger.error(
            "Text-to-Speech API not enabled or access denied. Enable it in Google Cloud Console."
        )
        _remove_partial_outfile(out_path)
        return False
    except core_exc.ResourceExhausted:
        logger.error(
            "Google TTS quota exceeded. Check billing and quotas in Cloud Console."
        )
        _remove_partial_outfile(out_path)
        return False
    except (core_exc.ServiceUnavailable, core_exc.DeadlineExceeded, requests.exceptions.ConnectionError) as e:
        logger.error("Google TTS: сеть после повторов: %s", e)
        _remove_partial_outfile(out_path)
        return False
    except Exception as e:
        logger.exception("Google TTS: неожиданная ошибка: %s", e)
        _remove_partial_outfile(out_path)
        return False
