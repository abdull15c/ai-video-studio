import logging
import os
import subprocess

from config import Config
from modules.ffmpeg_util import run_ffmpeg

logger = logging.getLogger(__name__)


def add_background_music(project_id):
    logger.info("Аудио-мастеринг (фон + ducking), project_id=%s", project_id)
    project_dir = f"./storage/projects/{project_id}"
    input_video = f"{project_dir}/FINAL_VIDEO_{project_id}.mp4"
    output_video = f"{project_dir}/FINAL_CINEMATIC_{project_id}.mp4"

    music_dir = "./storage/music"
    music_file = f"{music_dir}/background.mp3"
    os.makedirs(music_dir, exist_ok=True)

    try:
        if not os.path.exists(music_file):
            run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=80:duration=15",
                    "-af",
                    "lowpass=f=300,volume=0.3",
                    "-c:a",
                    "libmp3lame",
                    music_file,
                ],
                context="synth background music",
            )

        vol = Config.BG_MUSIC_VOLUME
        th = Config.SIDECHAIN_THRESHOLD
        ratio = Config.SIDECHAIN_RATIO
        att = Config.SIDECHAIN_ATTACK
        rel = Config.SIDECHAIN_RELEASE

        complex_filter = (
            f"[1:a]volume={vol}[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_video,
            "-stream_loop",
            "-1",
            "-i",
            music_file,
            "-filter_complex",
            complex_filter,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            output_video,
        ]
        run_ffmpeg(cmd, context="audio ducking / mix")
        logger.info("Готово: %s", output_video)
        return True
    except subprocess.CalledProcessError:
        logger.exception("FFmpeg: ошибка мастеринга project_id=%s", project_id)
        return False
