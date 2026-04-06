import json
import logging
import time
from config import Config
from database import get_project_tts_engine, save_script_to_db, update_project_tts_voice
from .anthropic_helpers import CLAUDE_MODEL_SONNET, llm_generate
from .json_extract import clean_json_from_llm

logger = logging.getLogger(__name__)


def _apply_narrator_voice(project_id, payload, cli_voice):
    """
    Приоритет: CLI > поле narrator_voice в JSON от LLM > дефолт (уже в Config при озвучке).
    payload — корень JSON (short/main) или объект с ключами chapters + опционально narrator_voice (long outline).
    """
    if get_project_tts_engine(project_id).startswith("google-"):
        logger.info("Проект с Google TTS — голос не перезаписывается из LLM/CLI (tts_voice в БД или Config)")
        return
    if cli_voice:
        v = update_project_tts_voice(project_id, cli_voice)
        logger.info("Голос озвучки задан из CLI: %s", v)
        return
    raw = payload.get("narrator_voice") if isinstance(payload, dict) else None
    if not raw:
        logger.info("Голос не задан LLM — будет использован EDGE_TTS_VOICE из конфигурации")
        return
    normalized = Config.normalize_edge_tts_voice(raw)
    if normalized != str(raw).strip():
        logger.warning("LLM вернул голос %r вне каталога — используем %s", raw, normalized)
    update_project_tts_voice(project_id, normalized)
    logger.info("Голос озвучки от LLM: %s", normalized)


def generate_script(project_id, topic, video_format, semi_auto=False, cli_voice=None):
    logger.info("AI-сценарист: тема=%r, формат=%s", topic, video_format)
    if not Config.has_llm_api_key():
        logger.error("Нет API ключа для LLM (проверьте LLM_PROVIDER и ключ в .env)")
        return False
    voice_lines = Config.edge_tts_voice_catalog_text()

    if video_format in ["short", "main"]:
        prompt = f'''Create a highly engaging video script for a YouTube channel. Topic: "{topic}". Format: {video_format}.

    Also choose the best Russian Edge-TTS narrator voice for this topic from the list below (genre fit: e.g. female for lyrical/nature, male for historical/documentary tone). Use ONLY exact voice ids from the list.

    Allowed narrator_voice values:
{voice_lines}

    Return ONLY a raw JSON object:
    {{
      "narrator_voice": "one-of-the-ids-above",
      "chapters": [{{"chapter_number": 1, "chapter_title": "Title", "scenes": [{{"scene_number": 1, "narration": "Text", "image_prompt": "Prompt", "mood": "epic", "camera": "wide", "duration_sec": 8}}]}}]
    }}'''

        for attempt in range(3):
            try:
                raw = llm_generate(
                    prompt,
                    max_tokens=8000,
                    temperature=0.7,
                    model=CLAUDE_MODEL_SONNET,
                    project_id=project_id,
                    step="script",
                )
                script_data = json.loads(clean_json_from_llm(raw))
                _apply_narrator_voice(project_id, script_data, cli_voice)
                save_script_to_db(project_id, script_data)
                logger.info("Сценарий сохранён в БД")
                return True
            except Exception as e:
                logger.warning("Ошибка генерации скрипта (попытка %s/3): %s", attempt + 1, e)
        return False

    logger.info("Режим LONG: многошаговая генерация")
    outline_prompt = f'''Write an outline for a 2-hour documentary video on "{topic}".

    Choose the best Russian Edge-TTS narrator voice for this documentary from the list (match tone: history/science → more neutral male; poetic/nature → softer female, etc.). Use ONLY exact voice ids.

    Allowed narrator_voice values:
{voice_lines}

    Return ONLY a JSON object:
    {{
      "narrator_voice": "one-of-the-ids-above",
      "chapters": ["Intro", "Chapter 2 title", "Conclusion"]
    }}'''

    try:
        raw_outline = llm_generate(
            outline_prompt,
            max_tokens=1000,
            temperature=0.7,
            model=CLAUDE_MODEL_SONNET,
            project_id=project_id,
            step="script_outline",
        )
        outline_data = json.loads(clean_json_from_llm(raw_outline))
        if "chapters" not in outline_data:
            raise ValueError("В ответе LLM нет ключа chapters")
        outline = outline_data["chapters"]
        _apply_narrator_voice(project_id, outline_data, cli_voice)
        logger.info("Структура: %s глав", len(outline))

        full_script = {"chapters": []}

        for i, chapter_title in enumerate(outline, 1):
            logger.info("Глава %s/%s: %s", i, len(outline), chapter_title)
            chap_prompt = f'''Write the script ONLY for Chapter {i}: "{chapter_title}" for a documentary on "{topic}".
            Return ONLY a JSON object: {{"chapter_number": {i}, "chapter_title": "{chapter_title}", "scenes": [{{"scene_number": 1, "narration": "Detailed long text", "image_prompt": "visual description", "mood": "epic", "camera": "wide", "duration_sec": 15}}]}}'''

            for attempt in range(3):
                try:
                    raw_ch = llm_generate(
                        chap_prompt,
                        max_tokens=3000,
                        temperature=0.7,
                        model=CLAUDE_MODEL_SONNET,
                        project_id=project_id,
                        step="script_chapter",
                    )
                    chap_data = json.loads(clean_json_from_llm(raw_ch))

                    start_scene_num = sum(len(c["scenes"]) for c in full_script["chapters"]) + 1
                    for idx, s in enumerate(chap_data["scenes"]):
                        s["scene_number"] = start_scene_num + idx

                    full_script["chapters"].append(chap_data)
                    break
                except Exception as e:
                    logger.warning("Ошибка в главе %s (попытка %s/3): %s", i, attempt + 1, e)
                    time.sleep(2)
            time.sleep(3)

        save_script_to_db(project_id, full_script)
        n_scenes = sum(len(c["scenes"]) for c in full_script["chapters"])
        logger.info("Сохранён сценарий на %s сцен", n_scenes)
        return True
    except Exception as e:
        logger.exception("Критическая ошибка LONG-генерации: %s", e)
        return False
