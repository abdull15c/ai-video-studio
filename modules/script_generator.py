import json
import logging
import time
from config import Config
from database import get_project_tts_engine, save_script_to_db, update_project_tts_voice, get_project_preset
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

    from database import get_project_row
    row = get_project_row(project_id)
    style_preset = row.get("style_preset", "default") if row else "default"

    style_instructions = ""
    if style_preset == "viral_shorts":
        style_instructions = "Style: VIRAL SHORTS. High energy, aggressive hooks, fast pacing, lots of emojis, emotional tone. 1st scene MUST be a strong hook."
    elif style_preset == "documentary_clean":
        style_instructions = "Style: DOCUMENTARY CLEAN. Serious, informative, calm, authoritative tone. Minimum emojis. Slower pacing, cinematic scenes."
    elif style_preset == "cinematic_premium":
        style_instructions = "Style: CINEMATIC PREMIUM. Highly visual, metaphorical language, dramatic pauses. Focus on cinematic visual roles and transitions."
    elif style_preset == "mystery_dark":
        style_instructions = "Style: MYSTERY DARK. Tense, suspenseful, dark imagery, whisper-like tone. Focus on creating intrigue."
    elif style_preset == "educational_explainer":
        style_instructions = "Style: EDUCATIONAL EXPLAINER. Clear, structured, logical. Focus on visual metaphors that explain complex concepts."

    if video_format in ["short", "main"]:
        prompt = f'''Create a highly engaging video script for a YouTube channel. Topic: "{topic}". Format: {video_format}.
    {style_instructions}

    CRITICAL RULES FOR NARRATION:
    1. Hook Design (for short/main): Scene 1 MUST be a visual/emotional strike. Scene 2 MUST be intrigue/contrast. Scene 3 MUST be a promise of revelation. NO slow intros!
    2. Add relevant emojis at the end of sentences or after key words in the 'narration' field (unless style forbids it).
    3. You must provide Director's fields for each scene: scene_goal, visual_role, shot_type, motion_type, intensity (1-10), continuity_anchor, transition_in, transition_out.
    4. Continuity Rules: Avoid repeating visual archetypes and shot patterns more than 2 times in a row. Maintain one visual style. Maintain character/environment continuity using the 'continuity_anchor' field (if a scene features a specific subject, ensure the next scene visually flows from it or deliberately contrasts).

    Allowed narrator_voice values:
{voice_lines}

    Return ONLY a raw JSON object:
    {{
      "narrator_voice": "one-of-the-ids-above",
      "chapters": [
        {{
          "chapter_number": 1,
          "chapter_title": "Title",
          "scenes": [
            {{
              "scene_number": 1,
              "narration": "Text 🗿",
              "image_prompt": "Prompt",
              "mood": "epic",
              "camera": "wide",
              "duration_sec": 8,
              "scene_goal": "Hook the viewer with a shocking fact",
              "visual_role": "establish context",
              "shot_type": "aerial",
              "motion_type": "fast pan",
              "transition_in": "hard_cut",
              "transition_out": "xfade",
              "intensity": "9",
              "continuity_anchor": "main subject"
            }}
          ]
        }}
      ]
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

    preset = get_project_preset(project_id)
    DURATION_PRESETS = {"30 minutes": 30, "1 hour": 60, "2 hours": 120}
    duration_str = preset if preset in DURATION_PRESETS else "1 hour"
    target_minutes = DURATION_PRESETS.get(duration_str, 60)
    # Ограничиваем до 60 мин максимум (для стабильности)
    target_minutes = min(target_minutes, 60)

    min_chapters = target_minutes // 3  # ~10 глав для 30 мин, ~20 для 60 мин
    max_chapters = min_chapters + 5

    logger.info("Режим LONG: многошаговая генерация (длительность: %s мин, глав: %s-%s)", target_minutes, min_chapters, max_chapters)
    outline_prompt = f'''Write a detailed outline for a {target_minutes}-minute documentary video on "{topic}".

    CRITICAL: You MUST write EXACTLY {min_chapters} to {max_chapters} chapters.
    Each chapter = 2-4 minutes of narrated content.
    Structure: strong hook intro → rising tension → climax → resolution → outro.

    Documentary storytelling rules:
    1. Chapter 1 = HOOK. Start with the most shocking/mysterious fact. "В 1908 году что-то уничтожило 2000 км² тайги..."
    2. Build tension across chapters — each reveals new layer of the mystery/story.
    3. Include "plot twists" — chapters where common knowledge gets debunked.
    4. Предпоследняя глава = кульминация (самый шокирующий факт).
    5. Последняя глава = разрешение + открытый вопрос (зритель хочет комментировать).

    Choose the best Russian Edge-TTS narrator voice for this documentary:
{voice_lines}

    Return ONLY a JSON object:
    {{
      "narrator_voice": "one-of-the-ids-above",
      "total_target_minutes": {target_minutes},
      "chapters": ["Глава 1: Шокирующее начало", "Глава 2: ...", ...]
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
            
            prev_title = outline[i - 2] if i > 1 else "нет (это первая глава)"
            next_title = outline[i] if i < len(outline) else "нет (это последняя глава)"
            
            scenes_per_chapter = 10 if target_minutes >= 45 else 8
            
            chap_prompt = f'''You are writing Chapter {i} of {len(outline)} for a {target_minutes}-min documentary on "{topic}".
    {style_instructions}

    Chapter title: "{chapter_title}"
    Previous chapter: "{prev_title}"
    Next chapter: "{next_title}"

    CRITICAL RULES:
    1. Write EXACTLY {scenes_per_chapter} to {scenes_per_chapter + 4} scenes for this chapter.
    2. Each scene narration = 2-4 предложения (40-80 слов). NOT одно предложение!
    3. duration_sec = 12 to 20 seconds per scene.
    4. Narration должен ПЛАВНО продолжать предыдущую главу и подводить к следующей.
    5. Continuity Rules: Avoid repeating visual archetypes and shot patterns more than 2 times in a row. Maintain one visual style per chapter. Maintain character/environment continuity using the 'continuity_anchor' field. If a sequence focuses on one person/object, keep the continuity_anchor exactly the same across those scenes.
    6. You must provide Director's fields for each scene: scene_goal, visual_role, shot_type, motion_type, intensity (1-10), continuity_anchor, transition_in, transition_out.

    Return ONLY a JSON object:
    {{
      "chapter_number": {i},
      "chapter_title": "{chapter_title}",
      "scenes": [
        {{
          "scene_number": 1,
          "narration": "Длинный текст 40-80 слов 🗿 с деталями и интригой 🔍",
          "image_prompt": "specific visual description in English for stock video",
          "mood": "mysterious",
          "camera": "aerial",
          "duration_sec": 15,
          "scene_goal": "Establish the mystery of the chapter",
          "visual_role": "context",
          "shot_type": "wide",
          "motion_type": "slow zoom",
          "transition_in": "black_fade",
          "transition_out": "hard_cut",
          "intensity": "4",
          "continuity_anchor": "forest location"
        }}
      ]
    }}'''

            for attempt in range(3):
                try:
                    raw_ch = llm_generate(
                        chap_prompt,
                        max_tokens=6000,
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
