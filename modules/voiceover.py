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


async def _generate_audio(text, voice, out_path):
    communicate = edge_tts.Communicate(text, voice)
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


async def _edge_tts_with_retry(text: str, voice: str, out_path: str, scene_id: int) -> bool:
    """3 попытки; паузы 2 / 5 / 10 с; затем тишина через FFmpeg."""
    pause_after_fail = (2.0, 5.0, 10.0)
    est_dur = max(2.0, min(180.0, len(text) / 12.0))

    for attempt in range(3):
        _remove_if_exists(out_path)
        try:
            await asyncio.sleep(max(0.0, Config.EDGE_TTS_DELAY_SEC))
            await _generate_audio(text, voice, out_path)
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


def generate_voiceover(project_id):
    tts_engine = get_project_tts_engine(project_id)
    use_google = tts_engine.startswith("google-")
    logger.info(
        "Генерация озвучки, project_id=%s, engine=%s",
        project_id,
        tts_engine or "edge",
    )
    script_data = get_project_script(project_id)
    project_dir = f"./storage/projects/{project_id}"
    audio_dir = f"{project_dir}/audio"
    os.makedirs(audio_dir, exist_ok=True)

    voice = get_project_tts_voice(project_id)
    tts_prompt = get_project_tts_prompt(project_id) if use_google else None
    speaking_rate = Config.GOOGLE_TTS_SPEAKING_RATE
    logger.info("Голос: %s", voice)
    concurrency = Config.EDGE_TTS_CONCURRENCY
    sem = asyncio.Semaphore(concurrency)

    async def one_scene(chapter_num, chapter_title, scene):
        text = (scene.get("narration") or "").strip()
        if not text:
            return
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
                ok = await _edge_tts_with_retry(text, voice, out_path, scene["id"])
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
                tasks.append(asyncio.create_task(one_scene(cn, ct, scene)))
        if tasks:
            await asyncio.gather(*tasks)

    asyncio.run(run_all())
    logger.info("Озвучка завершена: %s", audio_dir)
    return True
