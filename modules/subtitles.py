import logging
import os
import re
import warnings

import whisper
from mutagen.mp3 import MP3
from config import Config
from database import get_project_script, get_project_format

warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")

logger = logging.getLogger(__name__)


def _pick_subtitle_font():
    fonts_dir = Config.FONTS_DIR
    pairs = [
        ("Montserrat Bold", "Montserrat-Bold.ttf"),
        ("Montserrat Bold", "MontserratBold.ttf"),
        ("Bebas Neue", "BebasNeue-Regular.ttf"),
        ("Bebas Neue", "BebasNeue-Bold.ttf"),
        ("Arial Black", "ArialBlack.ttf"),
    ]
    for font_name, fname in pairs:
        path = os.path.join(fonts_dir, fname)
        if os.path.isfile(path):
            logger.info("Субтитры: шрифт %s (%s)", font_name, fname)
            return font_name, fonts_dir
    logger.info("Субтитры: кастомный TTF не найден — Impact (системный)")
    return "Impact", None


def _word_highlight_ass_bgr(mood: str) -> str:
    m = (mood or "calm").lower()
    if any(k in m for k in ("tense", "action")):
        return "&H0000FF&"
    if any(k in m for k in ("epic", "dramatic")):
        return "&H0066FF&"
    if any(k in m for k in ("calm", "mysterious", "mystery")):
        return "&HFFAA00&"
    return "&HFFAA00&"


def _format_ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _build_ass_header(font_name: str, font_size: int, margin_v: int) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,2,0,2,24,24,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _normalize_words(text: str):
    return [w for w in re.findall(r"\S+", (text or "").strip()) if w]


def _flatten_whisper_words(result) -> list:
    words = []
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            if "start" in w and "end" in w:
                words.append(w)
    return words


def _timing_script_words(
    script_words: list,
    total_duration: float,
    whisper_words: list,
) -> list:
    """
    Список (слово, start, end). Текст из сценария; таймкоды — от Whisper
    (сопоставление по индексу) или пропорционально длине слова.
    """
    n = len(script_words)
    if n == 0:
        return []
    td = max(0.1, float(total_duration))
    m = len(whisper_words)
    if m > 0:
        ratio = m / max(n, 1)
        if 0.55 <= ratio <= 1.45 or m >= max(3, int(0.5 * n)):
            out = []
            for j in range(n):
                i0 = min(m - 1, int(j * m / max(n, 1)))
                i1 = min(m - 1, max(i0, int((j + 1) * m / max(n, 1)) - 1))
                st = float(whisper_words[i0]["start"])
                en = float(whisper_words[i1].get("end", whisper_words[i1]["start"]))
                if en <= st:
                    en = min(td, st + 0.08)
                out.append((script_words[j], st, min(en, td)))
            if out[-1][2] < td - 0.05:
                w, st, _ = out[-1]
                out[-1] = (w, st, td)
            return out

    weights = [max(1, len(w)) for w in script_words]
    s = sum(weights)
    t = 0.0
    out = []
    for w, wt in zip(script_words, weights):
        dt = td * wt / s
        out.append((w, t, min(t + dt, td)))
        t += dt
    return out


def generate_subtitles(project_id):
    logger.info("Субтитры (сценарий + Whisper для таймкодов), project_id=%s", project_id)
    script_data = get_project_script(project_id)
    video_format = get_project_format(project_id)
    is_vertical = video_format == "short"

    project_dir = f"./storage/projects/{project_id}"
    subs_dir = f"{project_dir}/subtitles"
    os.makedirs(subs_dir, exist_ok=True)

    use_proportional = Config.SUBTITLE_ENGINE == "proportional"
    model = None
    if not use_proportional:
        model_name = Config.WHISPER_MODEL
        logger.info("Загрузка Whisper, модель %s", model_name)
        model = whisper.load_model(model_name)
    else:
        logger.info("Субтитры: режим proportional (без Whisper, тайминг по длине слов)")

    font_name, _ = _pick_subtitle_font()
    font_size = int(Config.SUBTITLE_FONT_SIZE_SHORT if is_vertical else Config.SUBTITLE_FONT_SIZE_MAIN)
    margin_v = int(Config.SUBTITLE_MARGIN_V_SHORT if is_vertical else Config.SUBTITLE_MARGIN_V_MAIN)
    max_words = Config.SUBTITLE_MAX_WORDS_SHORT if is_vertical else Config.SUBTITLE_MAX_WORDS_MAIN

    ass_header = _build_ass_header(font_name, font_size, margin_v)

    for chapter in script_data:
        for scene in chapter["scenes"]:
            audio_path = f"{project_dir}/audio/scene_{scene['id']}.mp3"
            ass_path = f"{subs_dir}/scene_{scene['id']}.ass"
            mood = scene.get("mood", "calm")
            hi = _word_highlight_ass_bgr(mood)
            narration = (scene.get("narration") or "").strip()

            if not os.path.exists(audio_path) or not narration:
                continue

            try:
                audio_dur = float(MP3(audio_path).info.length)
            except Exception as e:
                logger.warning("Сцена %s: не удалось прочитать длительность mp3: %s", scene["number"], e)
                continue

            script_words = _normalize_words(narration)
            if not script_words:
                continue

            if use_proportional:
                logger.info("Сцена %s: proportional + текст сценария (mood=%s)", scene["number"], mood)
                timed = _timing_script_words(script_words, audio_dur, [])
            else:
                logger.info("Сцена %s: Whisper + текст сценария (mood=%s)", scene["number"], mood)
                prompt = narration[: min(244, len(narration))]
                result = model.transcribe(
                    audio_path,
                    word_timestamps=True,
                    language="ru",
                    initial_prompt=prompt,
                )
                wh_words = _flatten_whisper_words(result)
                wdur = audio_dur
                if wh_words:
                    try:
                        wdur = min(audio_dur, float(wh_words[-1].get("end", audio_dur)))
                    except (TypeError, ValueError):
                        pass

                timed = _timing_script_words(script_words, wdur, wh_words)

            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(ass_header)

                chunks = [timed[i : i + max_words] for i in range(0, len(timed), max_words)]

                for chunk in chunks:
                    if not chunk:
                        continue
                    for i, (tw, t0, t1) in enumerate(chunk):
                        word_start = _format_ass_time(t0)
                        word_end = _format_ass_time(t1)
                        line_text = ""
                        for j, (w2, _, _) in enumerate(chunk):
                            clean_word = w2.strip()
                            if not clean_word:
                                continue
                            if j == i:
                                line_text += f"{{\\c{hi}}}{clean_word}{{\\c&HFFFFFF&}} "
                            else:
                                line_text += f"{clean_word} "
                        if line_text.strip():
                            f.write(
                                f"Dialogue: 0,{word_start},{word_end},Default,,0,0,0,,{line_text.strip()}\n"
                            )

    logger.info("Субтитры готовы")
    return True
