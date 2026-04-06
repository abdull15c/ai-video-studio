"""Промежуточные проверки конвейера после voice / videos / subtitles."""

import logging
import os
from typing import Tuple

from mutagen.mp3 import MP3

from database import get_project_script
from modules.ffmpeg_util import ffprobe_video_meta

logger = logging.getLogger(__name__)


def validate_after_voice(project_id: int) -> Tuple[bool, str]:
    """Все mp3 существуют, длительность > 0.5 с."""
    try:
        script_data = get_project_script(project_id)
    except Exception as e:
        return False, f"сценарий: {e}"
    project_dir = f"./storage/projects/{project_id}"
    for ch in script_data:
        for sc in ch["scenes"]:
            if not (sc.get("narration") or "").strip():
                continue
            sid = sc["id"]
            ap = f"{project_dir}/audio/scene_{sid}.mp3"
            if not os.path.isfile(ap):
                return False, f"нет аудио: {ap}"
            try:
                ln = MP3(ap).info.length
            except Exception as e:
                return False, f"битый mp3 scene_{sid}: {e}"
            if ln < 0.5:
                return False, f"аудио scene_{sid} слишком короткое ({ln:.2f}s)"
    return True, ""


def validate_after_videos(project_id: int) -> Tuple[bool, str]:
    """Для каждой сцены: raw mp4 (ffprobe) или картинка Imagen images/scene_N.jpg."""
    try:
        script_data = get_project_script(project_id)
    except Exception as e:
        return False, f"сценарий: {e}"
    project_dir = f"./storage/projects/{project_id}"
    vd = f"{project_dir}/raw_videos"
    imgd = os.path.join(project_dir, "images")
    for ch in script_data:
        for sc in ch["scenes"]:
            if not (sc.get("narration") or "").strip():
                continue
            sid = sc["id"]
            vp = os.path.join(vd, f"scene_{sid}.mp4")
            ip = os.path.join(imgd, f"scene_{sid}.jpg")
            if os.path.isfile(vp):
                meta = ffprobe_video_meta(vp)
                if not meta or meta.get("duration", 0) < 0.3:
                    return False, f"битое или пустое видео: {vp}"
            elif os.path.isfile(ip) and os.path.getsize(ip) > 100:
                continue
            else:
                return False, f"нет видео и нет картинки для сцены scene_{sid}"
    return True, ""


def validate_after_subtitles(project_id: int) -> Tuple[bool, str]:
    """Все .ass непустые."""
    try:
        script_data = get_project_script(project_id)
    except Exception as e:
        return False, f"сценарий: {e}"
    project_dir = f"./storage/projects/{project_id}"
    sd = os.path.join(project_dir, "subtitles")
    for ch in script_data:
        for sc in ch["scenes"]:
            if not (sc.get("narration") or "").strip():
                continue
            sid = sc["id"]
            ap = os.path.join(sd, f"scene_{sid}.ass")
            if not os.path.isfile(ap):
                return False, f"нет субтитров: {ap}"
            try:
                if os.path.getsize(ap) < 80:
                    return False, f"пустой или слишком короткий ass: {ap}"
            except OSError as e:
                return False, str(e)
    return True, ""
