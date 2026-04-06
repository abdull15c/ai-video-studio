import logging

from config import Config
from .anthropic_helpers import CLAUDE_MODEL_SONNET, llm_generate

logger = logging.getLogger(__name__)


def generate_seo(project_id, topic):
    logger.info("SEO generator, project_id=%s", project_id)
    project_dir = f"./storage/projects/{project_id}"
    seo_file = f"{project_dir}/seo.txt"

    if not Config.has_llm_api_key():
        raise ValueError(
            "КРИТИЧЕСКАЯ ОШИБКА: нет API ключа для LLM (LLM_PROVIDER и ключ в .env). SEO не сгенерировано."
        )

    prompt = (
        f"Напиши SEO для YouTube видео на тему '{topic}'. Выдай 3 кликбейтных заголовка, "
        f"2 абзаца интригующего описания и 10 тегов через запятую. Ответь только текстом, без лишних слов."
    )

    try:
        seo_text = llm_generate(
            prompt,
            max_tokens=1000,
            temperature=0.8,
            model=CLAUDE_MODEL_SONNET,
            project_id=project_id,
            step="seo",
        )

        with open(seo_file, "w", encoding="utf-8") as f:
            f.write(seo_text)

        logger.info("SEO сохранён: %s", seo_file)
        return True
    except Exception as e:
        logger.error("Ошибка генерации SEO: %s", e)
        return False
