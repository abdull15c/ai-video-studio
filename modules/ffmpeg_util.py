"""Запуск FFmpeg с проверкой кода возврата."""

import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

from config import Config

logger = logging.getLogger(__name__)

Cmd = Union[List[str], tuple]


def run_ffmpeg(cmd: Cmd, *, context: str = "ffmpeg") -> subprocess.CompletedProcess:
    """Выполняет команду, где cmd[0] обычно 'ffmpeg'. При ошибке пишет stderr в лог и бросает CalledProcessError."""
    result = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        tail = (result.stderr or "")[-4000:]
        logger.error("%s завершился с кодом %s. stderr (хвост): %s", context, result.returncode, tail)
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result


def subtitles_filter_path(ass_path: str) -> str:
    """Путь для встраивания в фильтр subtitles='...' (кавычки снаружи)."""
    ap = os.path.abspath(ass_path).replace("\\", "/")
    if sys.platform == "win32" and len(ap) > 1 and ap[1] == ":":
        ap = ap[0] + "\\:" + ap[2:]
    return ap.replace("'", r"\'")


def _filter_path_for_subtitles_option(path: str) -> str:
    """Экранирование пути для опций subtitles=...:fontsdir=..."""
    ap = os.path.abspath(path).replace("\\", "/")
    if sys.platform == "win32" and len(ap) > 1 and ap[1] == ":":
        ap = ap[0] + "\\:" + ap[2:]
    return ap.replace("'", r"\'")


def subtitles_filter_graph(ass_path: str, fonts_dir: Optional[str] = None) -> str:
    """
    Готовый фрагмент фильтра subtitles для -vf / filter_complex.
    fonts_dir по умолчанию — Config.FONTS_DIR; при наличии TTF подключается fontsdir.
    """
    sp = subtitles_filter_path(ass_path)
    if fonts_dir is None:
        fonts_dir = Config.FONTS_DIR
    if fonts_dir and os.path.isdir(fonts_dir):
        try:
            if any(
                n.lower().endswith((".ttf", ".otf", ".ttc"))
                for n in os.listdir(fonts_dir)
            ):
                fd = _filter_path_for_subtitles_option(fonts_dir)
                return f"subtitles='{sp}':fontsdir='{fd}'"
        except OSError:
            pass
    return f"subtitles='{sp}'"


def ffprobe_json(path: str) -> Optional[Dict[str, Any]]:
    """JSON ffprobe по файлу; None при ошибке."""
    if not path or not os.path.isfile(path):
        return None
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,avg_frame_rate,r_frame_rate,nb_read_frames,nb_frames,duration",
                "-show_entries",
                "format=duration,size,bit_rate",
                "-of",
                "json",
                os.path.abspath(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("ffprobe %s: %s", path, e)
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return None


def _parse_frame_rate(s: Optional[str]) -> float:
    if not s or s in ("0/0", "N/A"):
        return 0.0
    if "/" in s:
        a, _, b = s.partition("/")
        try:
            x, y = float(a), float(b)
            return x / y if y else 0.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def ffprobe_video_meta(path: str) -> Optional[Dict[str, Any]]:
    """
    duration (сек), fps, has_video, nb_frames (оценка), size.
    """
    data = ffprobe_json(path)
    if not data:
        return None
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    vstream = None
    for st in streams:
        if st.get("codec_type") == "video":
            vstream = st
            break
    if not vstream:
        return None
    dur_s = vstream.get("duration") or fmt.get("duration")
    try:
        duration = float(dur_s) if dur_s not in (None, "N/A") else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 and fmt.get("duration"):
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            pass
    fps = _parse_frame_rate(vstream.get("avg_frame_rate")) or _parse_frame_rate(
        vstream.get("r_frame_rate")
    )
    nb = vstream.get("nb_frames") or vstream.get("nb_read_frames")
    try:
        nb_frames = int(float(nb)) if nb not in (None, "N/A") else 0
    except (TypeError, ValueError):
        nb_frames = 0
    if nb_frames <= 0 and duration > 0 and fps > 0:
        nb_frames = int(duration * fps)
    try:
        size_b = int(fmt.get("size") or os.path.getsize(path))
    except (TypeError, ValueError, OSError):
        size_b = os.path.getsize(path) if os.path.isfile(path) else 0
    return {
        "duration": duration,
        "fps": fps,
        "has_video": True,
        "nb_frames": nb_frames,
        "size": size_b,
    }


def ffprobe_duration(path: str) -> float:
    m = ffprobe_video_meta(path)
    return float(m["duration"]) if m else 0.0


def validate_video_file(
    path: str,
    *,
    min_duration_sec: float = 1.0,
    min_fps: float = 15.0,
    min_bytes_per_sec: float = 25_000.0,
) -> Tuple[bool, str]:
    """Проверка целостности и пригодности видео (после скачивания)."""
    if not path or not os.path.isfile(path):
        return False, "файл отсутствует"
    meta = ffprobe_video_meta(path)
    if not meta:
        return False, "ffprobe не смог прочитать файл (битый или не видео)"
    d = meta["duration"]
    if d < min_duration_sec:
        return False, f"длительность {d:.2f}s < {min_duration_sec:.2f}s"
    fps = meta["fps"]
    if fps > 0 and fps < min_fps:
        return False, f"fps={fps:.1f} < {min_fps} (подозрение на статику/таймлапс)"
    nb = meta["nb_frames"]
    if d > 1.5 and nb > 0 and nb < d * (min_fps * 0.85):
        return False, f"мало кадров ({nb} за {d:.1f}s)"
    bps = meta["size"] / d if d > 0 else 0
    if bps < min_bytes_per_sec:
        return False, f"низкий битрейт {bps:.0f} B/s"
    return True, ""


def validate_stock_clip(
    path: str,
    *,
    min_duration_sec: float,
    min_fps: float = 15.0,
    min_bytes_per_sec: float = 200_000.0,
) -> Tuple[bool, str]:
    """Сток: не короче сцены, fps/кадры; байт/сек — отсев слайдшоу/статики (дефолт 200k)."""
    ok, err = validate_video_file(
        path,
        min_duration_sec=max(1.0, min_duration_sec - 0.15),
        min_fps=min_fps,
        min_bytes_per_sec=min_bytes_per_sec,
    )
    if not ok:
        return ok, err
    meta = ffprobe_video_meta(path)
    if meta and meta["duration"] + 0.05 < min_duration_sec:
        return False, f"клип {meta['duration']:.1f}s короче требуемых {min_duration_sec:.1f}s"
    return True, ""
