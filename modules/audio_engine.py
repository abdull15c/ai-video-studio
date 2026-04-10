import glob
import logging
import os
import random
import subprocess

from config import Config
from modules.ffmpeg_util import run_ffmpeg

logger = logging.getLogger(__name__)


def _get_project_primary_mood(project_id):
    from database import get_project_script
    try:
        script = get_project_script(project_id, require_scenes=False)
        moods = []
        for ch in script:
            for sc in ch.get("scenes", []):
                m = sc.get("mood", "calm").lower()
                moods.append(m)
        if moods:
            from collections import Counter
            return Counter(moods).most_common(1)[0][0]
    except Exception as e:
        logger.warning("Could not determine primary mood: %s", e)
    return "calm"

def add_background_music(project_id):
    logger.info("Аудио-мастеринг (мастеринг голоса + фон + ducking), project_id=%s", project_id)
    project_dir = f"./storage/projects/{project_id}"
    input_video = f"{project_dir}/FINAL_VIDEO_{project_id}.mp4"
    output_video = f"{project_dir}/FINAL_CINEMATIC_{project_id}.mp4"

    from database import get_project_row
    row = get_project_row(project_id)
    style_preset = row.get("style_preset", "default") if row else "default"
    is_short = row and row.get("format") == "short"

    music_dir = "./storage/music"
    os.makedirs(music_dir, exist_ok=True)

    # Music Mood Routing
    primary_mood = _get_project_primary_mood(project_id)
    logger.info("Primary mood for routing: %s", primary_mood)
    
    # Сначала ищем треки в папке mood (напр. ./storage/music/epic/*.mp3)
    mood_tracks = glob.glob(os.path.join(music_dir, primary_mood, "*.mp3"))
    if not mood_tracks:
        # Если папок нет, ищем файлы с нужным словом в названии
        all_mp3 = glob.glob(os.path.join(music_dir, "*.mp3"))
        mood_tracks = [t for t in all_mp3 if primary_mood in os.path.basename(t).lower()]
        if not mood_tracks:
            # Fallback на любые треки
            mood_tracks = [t for t in all_mp3 if not os.path.basename(t) == "background.mp3"]

    if mood_tracks:
        music_file = random.choice(mood_tracks)
        logger.info("Фоновая музыка выбрана по mood '%s': %s", primary_mood, os.path.basename(music_file))
    else:
        # Fallback: генерируем более приятный эмбиент (не просто синусоиду)
        music_file = f"{music_dir}/background.mp3"
        if not os.path.exists(music_file):
            run_ffmpeg(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i",
                    "sine=frequency=65:duration=30",
                    "-f", "lavfi", "-i",
                    "sine=frequency=98:duration=30",
                    "-f", "lavfi", "-i",
                    "sine=frequency=131:duration=30",
                    "-filter_complex",
                    "[0:a]volume=0.3[a];[1:a]volume=0.2[b];[2:a]volume=0.15[c];"
                    "[a][b][c]amix=inputs=3[mix];"
                    "[mix]lowpass=f=400,areverse,afade=t=in:d=2,areverse,afade=t=out:st=27:d=3[out]",
                    "-map", "[out]",
                    "-c:a", "libmp3lame",
                    music_file,
                ],
                context="synth ambient background",
            )

    vol = Config.BG_MUSIC_VOLUME
    th = Config.SIDECHAIN_THRESHOLD
    ratio = Config.SIDECHAIN_RATIO
    att = Config.SIDECHAIN_ATTACK
    rel = Config.SIDECHAIN_RELEASE

    # Voice Mastering Chain (high-pass, EQ, compressor, loudnorm)
    # Target loudness: -14 LUFS for shorts (platform-friendly), -18 LUFS for main (comfortable long-form)
    target_lufs = "-14" if is_short else "-18"
    
    # Full Voice Mastering Chain (Studio Level)
    # Target loudness: -14 LUFS for shorts (platform-friendly), -18 LUFS for main (comfortable long-form)
    target_lufs = "-14" if is_short else "-18"
    
    # 1. High-pass filter at 80Hz (remove rumble/low-end noise)
    # 2. Light EQ (boost clarity around 3kHz)
    # 3. De-esser (simple treble compression via compand on highs, simulated via a treble shelf limit)
    # 4. Compressor (smooth out voice dynamics)
    # 5. Limiter / Loudnorm (EBU R128)
    voice_chain = (
        "highpass=f=80,"
        "equalizer=f=3000:width_type=o:width=2:g=3,"
        "compand=attacks=0:points=-80/-80|-30/-30|-20/-20|0/-10:gain=3,"
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    )

    try:
        # 1:a - Music, 0:a - Voice (from video)
        # Apply mastering to voice, ducking to music, then mix
        complex_filter = (
            f"[0:a]{voice_chain}[mastered_voice];"
            f"[mastered_voice]asplit=2[voice_out][voice_ref];"
            f"[1:a]volume={vol}[bg];"
            f"[bg][voice_ref]sidechaincompress="
            f"threshold={th}:ratio={ratio}:attack={att}:release={rel}[ducked_bg];"
            f"[voice_out][ducked_bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_video,
            "-stream_loop", "-1",
            "-i", music_file,
            "-filter_complex", complex_filter,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            output_video,
        ]
        run_ffmpeg(cmd, context="audio sidechain ducking")
        logger.info("Готово: %s", output_video)
        return True
    except subprocess.CalledProcessError:
        logger.exception("FFmpeg sidechain не сработал, пробуем простой микс")
        # Fallback на простой микс
        try:
            simple_filter = (
                f"[1:a]volume={float(vol) * 0.5}[bg];"
                f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", input_video,
                "-stream_loop", "-1",
                "-i", music_file,
                "-filter_complex", simple_filter,
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                output_video,
            ]
            run_ffmpeg(cmd, context="audio simple mix fallback")
            return True
        except subprocess.CalledProcessError:
            logger.exception("FFmpeg: ошибка мастеринга project_id=%s", project_id)
            return False
