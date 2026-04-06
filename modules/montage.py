import logging
import os
import random
import shutil
import subprocess

from mutagen.mp3 import MP3
from config import Config
from database import get_project_script, get_project_format
from modules.ffmpeg_util import ffprobe_duration, run_ffmpeg, subtitles_filter_graph

logger = logging.getLogger(__name__)


def _ken_burns_vf(w: int, h: int, scene_duration: float) -> str:
    d = max(1, int(scene_duration * 30))
    uw = max(w, (w * 12 // 10) // 2 * 2)
    uh = max(h, (h * 12 // 10) // 2 * 2)
    kind = random.choice(("zoom_in", "zoom_out", "pan_r", "pan_l"))
    if kind == "zoom_in":
        z = "if(eq(on\\,0)\\,1\\,min(zoom+0.0012\\,1.12))"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "zoom_out":
        z = "if(eq(on\\,0)\\,1.12\\,max(zoom-0.0012\\,1))"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "pan_r":
        z = "1.07"
        x = f"(iw-iw/zoom)*on/{d}"
        y = "ih/2-(ih/zoom/2)"
    else:
        z = "1.07"
        x = f"(iw-iw/zoom)*(1-on/{d})"
        y = "ih/2-(ih/zoom/2)"
    zp = f"zoompan=z='{z}':x='{x}':y='{y}':d={d}:s={w}x{h}:fps=30"
    return f"scale={uw}:{uh}:force_original_aspect_ratio=increase,crop={uw}:{uh}:(iw-{uw})/2:(ih-{uh})/2,{zp}"


def _xfade_duration_entering_scene(scene_number_1based: int, mood: str) -> float:
    """Длительность перехода при входе в сцену с номером scene_number_1based и mood."""
    m = (mood or "calm").lower()
    if any(x in m for x in ("epic", "action", "tense")):
        return Config.SCENE_XFADE_FAST_SEC
    if scene_number_1based % 2 == 0:
        return Config.SCENE_XFADE_EVEN_SEC
    return Config.SCENE_XFADE_ODD_SEC


def _merge_scenes_xfade(scene_paths, transitions, out_path: str) -> None:
    """Склейка сцен xfade (видео) + acrossfade (аудио). transitions — длины len-1."""
    n = len(scene_paths)
    if n == 0:
        raise ValueError("нет сцен для склейки")
    if n == 1:
        shutil.copyfile(scene_paths[0], out_path)
        return

    durations = [ffprobe_duration(p) for p in scene_paths]
    if any(d <= 0 for d in durations):
        raise ValueError("не удалось определить длительность одной из сцен")

    trans = []
    for i in range(n - 1):
        d_req = transitions[i] if i < len(transitions) else 0.4
        d = max(0.08, min(d_req, durations[i] * 0.45, durations[i + 1] * 0.45))
        trans.append(d)

    fc_parts = []
    acc_v = "0:v"
    acc_a = "0:a"
    acc_len = durations[0]
    v_last = acc_v
    a_last = acc_a

    for i in range(1, n):
        d = trans[i - 1]
        off = acc_len - d
        v_new = f"vxf{i}"
        a_new = f"axf{i}"
        fc_parts.append(
            f"[{acc_v}][{i}:v]xfade=transition=fade:duration={d:.4f}:offset={off:.4f}[{v_new}]"
        )
        fc_parts.append(
            f"[{acc_a}][{i}:a]acrossfade=d={d:.4f}[{a_new}]"
        )
        acc_v = v_new
        acc_a = a_new
        acc_len += durations[i] - d
        v_last = v_new
        a_last = a_new

    graph = ";".join(fc_parts)
    cmd = ["ffmpeg", "-y"]
    for p in scene_paths:
        cmd.extend(["-i", p])
    cmd.extend(
        [
            "-filter_complex",
            graph,
            "-map",
            f"[{v_last}]",
            "-map",
            f"[{a_last}]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            out_path,
        ]
    )
    run_ffmpeg(cmd, context="chapter xfade")


def _ensure_black_gap_clip(path: str, w: int, h: int, gap_sec: float) -> None:
    if os.path.isfile(path) and os.path.getsize(path) > 1000:
        return
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={w}x{h}:r=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            str(gap_sec),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            path,
        ],
        context="black gap between chapters",
    )


def generate_sfx():
    sfx_dir = "./storage/sfx"
    os.makedirs(sfx_dir, exist_ok=True)
    whoosh = f"{sfx_dir}/whoosh.mp3"
    impact = f"{sfx_dir}/impact.mp3"

    if not os.path.exists(whoosh):
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=800:duration=1.5",
                "-af",
                "afade=t=in:d=0.4,afade=t=out:st=0.8:d=0.7,lowpass=f=1500",
                "-c:a",
                "libmp3lame",
                whoosh,
            ],
            context="SFX whoosh",
        )

    if not os.path.exists(impact):
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=150:duration=2",
                "-af",
                "afade=t=out:st=0.5:d=1.5",
                "-c:a",
                "libmp3lame",
                impact,
            ],
            context="SFX impact",
        )

    return whoosh, impact


def render_project(project_id):
    logger.info("Монтаж проекта %s", project_id)
    try:
        whoosh_path, impact_path = generate_sfx()

        script_data = get_project_script(project_id)
        video_format = get_project_format(project_id)
        is_vertical = video_format == "short"

        project_dir = f"./storage/projects/{project_id}"
        videos_dir = f"{project_dir}/raw_videos"
        images_dir = f"{project_dir}/images"
        render_dir = f"{project_dir}/render"
        os.makedirs(render_dir, exist_ok=True)

        w = Config.VIDEO_WIDTH_SHORT if is_vertical else Config.VIDEO_WIDTH_MAIN
        h = Config.VIDEO_HEIGHT_SHORT if is_vertical else Config.VIDEO_HEIGHT_MAIN
        tail = Config.SCENE_TAIL_SILENCE_SEC
        gap_sec = Config.CHAPTER_GAP_BLACK_SEC
        gap_clip = os.path.join(render_dir, "_chapter_gap_black.mp4")

        chapter_files = []
        global_scene_index = 0

        for chapter in script_data:
            chap_num = chapter["number"]
            chap_file = f"{project_dir}/CHAPTER_{chap_num}.mp4"

            if os.path.exists(chap_file):
                chapter_files.append(chap_file)
                global_scene_index += len(chapter["scenes"])
                continue

            logger.info("Монтаж главы %s", chap_num)
            scene_paths_meta = []

            for scene in chapter["scenes"]:
                global_scene_index += 1
                scene_id = scene["id"]
                audio_path = f"{project_dir}/audio/scene_{scene_id}.mp3"
                raw_video_path = f"{videos_dir}/scene_{scene_id}.mp4"
                image_path = f"{images_dir}/scene_{scene_id}.jpg"
                out_video = f"{render_dir}/scene_{scene_id}.mp4"
                ass_path = os.path.join(project_dir, "subtitles", f"scene_{scene_id}.ass")

                use_image = False
                if os.path.exists(raw_video_path):
                    visual_path = raw_video_path
                elif os.path.isfile(image_path) and os.path.getsize(image_path) > 100:
                    visual_path = image_path
                    use_image = True
                else:
                    continue

                if not os.path.exists(audio_path):
                    continue

                audio_info = MP3(audio_path)
                voice_duration = audio_info.info.length
                scene_duration = voice_duration + tail

                mood = scene.get("mood", "calm").lower()
                sfx_path = impact_path if mood in Config.MOOD_SFX_IMPACT else whoosh_path
                sfx_vol = Config.SFX_IMPACT_VOLUME if sfx_path == impact_path else Config.SFX_WHOOSH_VOLUME

                logger.info(
                    "Рендер сцены %s (глобально #%s), длительность %.2f с",
                    scene["number"],
                    global_scene_index,
                    scene_duration,
                )

                kb = _ken_burns_vf(w, h, scene_duration)
                subs_seg = subtitles_filter_graph(ass_path)
                vf_chain = f"{kb},{subs_seg}"
                af_chain = (
                    f"[1:a]apad[padded_voice];[2:a]volume={sfx_vol}[sfx];"
                    f"[padded_voice][sfx]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
                )

                if use_image:
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-loop",
                        "1",
                        "-framerate",
                        "30",
                        "-i",
                        visual_path,
                        "-i",
                        audio_path,
                        "-i",
                        sfx_path,
                        "-filter_complex",
                        f"[0:v]{vf_chain}[vout];{af_chain}",
                        "-map",
                        "[vout]",
                        "-map",
                        "[aout]",
                        "-t",
                        str(scene_duration),
                        "-c:v",
                        "libx264",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-pix_fmt",
                        "yuv420p",
                        out_video,
                    ]
                else:
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-stream_loop",
                        "-1",
                        "-i",
                        visual_path,
                        "-i",
                        audio_path,
                        "-i",
                        sfx_path,
                        "-filter_complex",
                        f"[0:v]{vf_chain}[vout];{af_chain}",
                        "-map",
                        "[vout]",
                        "-map",
                        "[aout]",
                        "-t",
                        str(scene_duration),
                        "-c:v",
                        "libx264",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-pix_fmt",
                        "yuv420p",
                        out_video,
                    ]
                run_ffmpeg(cmd, context=f"scene {scene_id} render")
                scene_paths_meta.append(
                    (out_video, global_scene_index, scene.get("mood", "calm"))
                )

            if not scene_paths_meta:
                logger.error("Глава %s: нет ни одной отрендеренной сцены", chap_num)
                return False

            paths = [x[0] for x in scene_paths_meta]
            transitions = []
            for b in range(len(paths) - 1):
                gnum, mood = scene_paths_meta[b + 1][1], scene_paths_meta[b + 1][2]
                transitions.append(_xfade_duration_entering_scene(gnum, mood))

            logger.info("Сборка главы %s: xfade между %s клипами", chap_num, len(paths))
            _merge_scenes_xfade(paths, transitions, chap_file)

            for pth, _, _ in scene_paths_meta:
                try:
                    os.remove(pth)
                except OSError:
                    pass

            chapter_files.append(chap_file)

        logger.info("Финальная склейка с зазором между главами")
        _ensure_black_gap_clip(gap_clip, w, h, gap_sec)
        final_list = f"{project_dir}/concat_final.txt"
        with open(final_list, "w", encoding="utf-8") as f:
            for i, cf in enumerate(chapter_files):
                f.write(f"file '{os.path.abspath(cf).replace(chr(92), '/')}'\n")
                if i < len(chapter_files) - 1:
                    f.write(f"file '{os.path.abspath(gap_clip).replace(chr(92), '/')}'\n")

        final_mp4 = f"{project_dir}/FINAL_VIDEO_{project_id}.mp4"
        run_ffmpeg(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", final_list, "-c", "copy", final_mp4],
            context="final concat",
        )

        return True
    except subprocess.CalledProcessError:
        logger.exception("Ошибка FFmpeg при монтаже project_id=%s", project_id)
        return False
    except (ValueError, OSError) as e:
        logger.exception("Монтаж project_id=%s: %s", project_id, e)
        return False
