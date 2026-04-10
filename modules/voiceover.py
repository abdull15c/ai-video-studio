import asyncio
import logging
import os

import edge_tts
from aiohttp.client_exceptions import ConnectionTimeoutError
from edge_tts.exceptions import NoAudioReceived

from config import Config
from database import (
    get_project_script,
    get_project_tts_engine,
    get_project_tts_prompt,
    get_project_tts_voice,
    update_scene_audio,
)
from modules.ffmpeg_util import run_ffmpeg

logger = logging.getLogger(__name__)


async def _generate_audio(text, voice, out_path, rate_str="+0%", pitch_str="+0Hz"):
    communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
    await communicate.save(out_path)


def _remove_if_exists(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _silence_mp3(out_path: str, duration_sec: float) -> bool:
    """Тишина после исчерпания retry Edge-TTS (~оценка длительности по длине текста)."""
    d = max(2.0, min(180.0, float(duration_sec)))
    ddir = os.path.dirname(os.path.abspath(out_path))
    if ddir:
        os.makedirs(ddir, exist_ok=True)
    try:
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                str(d),
                "-c:a",
                "libmp3lame",
                out_path,
            ],
            context="edge-tts silence fallback",
        )
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 100
    except Exception as e:
        logger.error("Не удалось сгенерировать тишину %s: %s", out_path, e)
        return False


async def _edge_tts_with_retry(text: str, voice: str, out_path: str, scene_id: int, rate_str="+0%", pitch_str="+0Hz") -> bool:
    """3 попытки; паузы 2 / 5 / 10 с; затем тишина через FFmpeg."""
    pause_after_fail = (2.0, 5.0, 10.0)
    est_dur = max(2.0, min(180.0, len(text) / 12.0))

    for attempt in range(3):
        _remove_if_exists(out_path)
        try:
            await asyncio.sleep(max(0.0, Config.EDGE_TTS_DELAY_SEC))
            await _generate_audio(text, voice, out_path, rate_str, pitch_str)
            if os.path.isfile(out_path) and os.path.getsize(out_path) > 100:
                return True
            raise NoAudioReceived("empty or missing mp3 after save")
        except (NoAudioReceived, ConnectionTimeoutError) as e:
            logger.warning(
                "Edge-TTS сцена id=%s попытка %s/3: %s",
                scene_id,
                attempt + 1,
                e,
            )
            if attempt == 2:
                await asyncio.sleep(pause_after_fail[2])
                ok = await asyncio.to_thread(_silence_mp3, out_path, est_dur)
                if ok:
                    logger.warning(
                        "Edge-TTS: подставлена тишина %.1f с для сцены id=%s",
                        est_dur,
                        scene_id,
                    )
                return ok
            await asyncio.sleep(pause_after_fail[attempt])
    return False

def apply_voice_profile(voice_profile: str):
    """Returns (speaking_rate_multiplier, pitch_shift, extra_prompt) based on profile."""
    p = voice_profile or "default"
    if p == "documentary_authoritative":
        return 0.95, -2.0, "Speak very calmly, slowly, with an authoritative deep documentary tone."
    elif p == "soft_mystery":
        return 0.9, -1.0, "Speak softly, with mystery, suspense, and a slight whisper-like tone."
    elif p == "dramatic_epic":
        return 1.05, 0.0, "Speak with high dramatic energy, epic scale, strong emphasis."
    elif p == "educational_clear":
        return 1.1, 1.0, "Speak clearly, like a teacher, articulate well, slightly upbeat."
    elif p == "viral_fast":
        return 1.25, 2.0, "Speak very fast, highly energetic, excited, YouTube Shorts style."
    return 1.0, 0.0, ""

def preprocess_text_for_ssml(text: str, intensity: str, mood: str) -> str:
    """Adds SSML tags for pauses and emphasis if using engines that support it. 
       For edge-tts it works partially, for google it works if SSML is sent."""
    if not text:
        return text
    if str(intensity).isdigit() and int(intensity) > 7:
        if not text.endswith(("!", "?", ".")):
            text += "!"
    elif str(intensity).isdigit() and int(intensity) < 4:
        text = text.replace(" - ", "... ")
    return text

def generate_voiceover(project_id, specific_scene_id=None):
    tts_engine = get_project_tts_engine(project_id)
    use_google = tts_engine.startswith("google-")
    logger.info(
        "Генерация озвучки, project_id=%s, engine=%s, specific_scene=%s",
        project_id,
        tts_engine or "edge",
        specific_scene_id,
    )
    script_data = get_project_script(project_id)
    project_dir = f"./storage/projects/{project_id}"
    audio_dir = f"{project_dir}/audio"
    os.makedirs(audio_dir, exist_ok=True)

    from database import get_project_row
    row = get_project_row(project_id)
    voice_profile = row.get("voice_profile", "default") if row else "default"
    rate_mult, pitch_shift, extra_prompt = apply_voice_profile(voice_profile)

    voice = get_project_tts_voice(project_id)
    base_tts_prompt = get_project_tts_prompt(project_id) if use_google else None
    tts_prompt = f"{base_tts_prompt}. {extra_prompt}".strip() if base_tts_prompt else extra_prompt
    
    base_speaking_rate = Config.GOOGLE_TTS_SPEAKING_RATE
    speaking_rate = base_speaking_rate * rate_mult
    
    original_pitch = getattr(Config, "GOOGLE_TTS_PITCH", 0.0)
    Config.GOOGLE_TTS_PITCH = pitch_shift

    logger.info("Голос: %s, Profile: %s, Rate: %.2f, Pitch: %.2f", voice, voice_profile, speaking_rate, pitch_shift)
    concurrency = Config.EDGE_TTS_CONCURRENCY
    sem = asyncio.Semaphore(concurrency)

    async def one_scene(chapter_num, chapter_title, scene):
        raw_text = (scene.get("narration") or "").strip()
        if not raw_text:
            return
            
        intensity = scene.get("intensity", "5")
        mood = scene.get("mood", "neutral")
        text = preprocess_text_for_ssml(raw_text, intensity, mood)

        out_path = f"{audio_dir}/scene_{scene['id']}.mp3"
        logger.info(
            "Озвучка глава %s «%s», сцена %s (id=%s)",
            chapter_num,
            chapter_title,
            scene["number"],
            scene["id"],
        )
        async with sem:
            if use_google:
                from modules.google_tts import generate_audio_google

                success = await asyncio.to_thread(
                    generate_audio_google,
                    text,
                    voice,
                    out_path,
                    prompt=tts_prompt,
                    speaking_rate=speaking_rate,
                    project_id=project_id,
                    scene_id=scene["id"],
                )
                if not success:
                    logger.error("Google TTS failed for scene %s", scene["id"])
                    return
            else:
                rate_str = f"+{int((rate_mult-1)*100)}%" if rate_mult >= 1 else f"{int((rate_mult-1)*100)}%"
                pitch_str = f"+{int(pitch_shift)}Hz" if pitch_shift >= 0 else f"{int(pitch_shift)}Hz"
                ok = await _edge_tts_with_retry(text, voice, out_path, scene["id"], rate_str=rate_str, pitch_str=pitch_str)
                if not ok:
                    logger.error("Edge-TTS и fallback тишины не удались для сцены %s", scene["id"])
                    return
        await asyncio.to_thread(update_scene_audio, scene["id"], out_path)

    async def run_all():
        tasks = []
        for chapter in script_data:
            cn, ct = chapter["number"], chapter["title"]
            logger.info("Озвучка главы %s: %s", cn, ct)
            for scene in chapter["scenes"]:
                if specific_scene_id and scene["id"] != specific_scene_id:
                    continue
                tasks.append(asyncio.create_task(one_scene(cn, ct, scene)))
        if tasks:
            await asyncio.gather(*tasks)

    asyncio.run(run_all())
    
    Config.GOOGLE_TTS_PITCH = original_pitch
    
    logger.info("Озвучка завершена: %s", audio_dir)
    return True
