import logging
import random

from config import Config

logger = logging.getLogger(__name__)


def get_daily_topic():
    logger.info("Trend parser: выбор темы")

    evergreen_niches = [
        "Неразгаданные тайны древних цивилизаций",
        "Самые загадочные находки археологов",
        "Секреты, которые скрывают океаны",
        "Что на самом деле произошло с Майя?",
        "Загадочные исчезновения кораблей",
        "Технологии прошлого, которые мы не можем повторить",
        "Что скрывают льды Антарктиды?",
        "Секретные проекты времен Холодной войны",
        "Самые странные существа глубоководья",
        "Правда о строительстве пирамид",
        "Города, исчезнувшие за одну ночь",
        "Самые жуткие места на планете",
        "Тайны тамплиеров и их сокровища",
        "Как жили гладиаторы на самом деле",
        "Загадки космоса, ставящие ученых в тупик",
    ]

    if Config.has_llm_api_key():
        from .anthropic_helpers import CLAUDE_MODEL_SONNET, llm_generate

        base_niche = random.choice(evergreen_niches)
        prompt = (
            f"Придумай одну невероятно интригующую, кликбейтную и конкретную тему для YouTube Shorts на основе этой ниши: '{base_niche}'. "
            f"Тема должна быть исторической или научной, с реальными фактами, но звучать как загадка. "
            f"В ответе напиши ТОЛЬКО саму тему (1 предложение, до 10 слов), без кавычек и лишнего текста."
        )

        try:
            logger.info("ИИ генерирует тему")
            topic = llm_generate(
                prompt,
                max_tokens=50,
                temperature=0.9,
                model=CLAUDE_MODEL_SONNET,
            ).strip().replace('"', "")
            logger.info("Тема от ИИ: %s", topic)
            return topic
        except Exception as e:
            logger.warning("Ошибка ИИ при теме: %s — берём нишу из списка", e)

    topic = random.choice(evergreen_niches)
    logger.info("Тема из списка: %s", topic)
    return topic


def add_topic_to_queue(topic):
    batch_file = "topics.txt"
    with open(batch_file, "a", encoding="utf-8") as f:
        f.write(f"\n{topic}")
    logger.info("Тема добавлена в %s", batch_file)
