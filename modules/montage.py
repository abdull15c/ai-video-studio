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
    # Увеличиваем margin для более плавного и заметного движения
    uw = max(w, (w * 15 // 10) // 2 * 2)
    uh = max(h, (h * 15 // 10) // 2 * 2)
    
    # Добавляем больше вариантов (диагональные панорамы)
    kind = random.choice(("zoom_in", "zoom_out", "pan_r", "pan_l", "pan_up", "pan_down", "pan_ur", "pan_dl"))
    
    if kind == "zoom_in":
        # Плавный zoom in
        z = "min(zoom+0.0015,1.15)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "zoom_out":
        # Плавный zoom out
        z = "max(1.15-(on*0.0015),1)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "pan_r":
        z = "1.1"
        x = f"(iw-iw/zoom)*on/{d}"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "pan_l":
        z = "1.1"
        x = f"(iw-iw/zoom)*(1-on/{d})"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "pan_up":
        z = "1.1"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*(1-on/{d})"
    elif kind == "pan_down":
        z = "1.1"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*on/{d}"
    elif kind == "pan_ur":
        z = "1.1"
        x = f"(iw-iw/zoom)*on/{d}"
        y = f"(ih-ih/zoom)*(1-on/{d})"
    else: # pan_dl
        z = "1.1"
        x = f"(iw-iw/zoom)*(1-on/{d})"
        y = f"(ih-ih/zoom)*on/{d}"
        
    zp = f"zoompan=z='{z}':x='{x}':y='{y}':d={d}:s={w}x{h}:fps=30"
    return f"scale={uw}:{uh}:force_original_aspect_ratio=increase,crop={uw}:{uh}:(iw-{uw})/2:(ih-{uh})/2,{zp}"


_XFADE_BATCH_SIZE = 10  # Максимум сцен за один проход FFmpeg


def _merge_scenes_xfade_batched(scene_paths, transitions, out_path: str) -> None:
    """Склейка с разбивкой на батчи: каждые 10 сцен → промежуточный файл, потом финальная склейка."""
    n = len(scene_paths)
    if n <= _XFADE_BATCH_SIZE:
        _merge_scenes_xfade(scene_paths, transitions, out_path)
        return

    batch_files = []
    for start in range(0, n, _XFADE_BATCH_SIZE):
        end = min(start + _XFADE_BATCH_SIZE, n)
        batch_paths = scene_paths[start:end]
        
        trans_start = start
        trans_end = end - 1
        batch_trans = transitions[trans_start:trans_end] if trans_start < len(transitions) else []

        if len(batch_paths) == 1:
            batch_files.append(batch_paths[0])
            continue

        batch_out = out_path.replace(".mp4", f"_batch{len(batch_files)}.mp4")
        _merge_scenes_xfade(batch_paths, batch_trans, batch_out)
        batch_files.append(batch_out)

    if len(batch_files) == 1:
        os.rename(batch_files[0], out_path)
        return

    # Финальная склейка батчей через concat (без xfade между батчами — быстрее и стабильнее)
    concat_list = out_path.replace(".mp4", "_batchlist.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for bf in batch_files:
            f.write(f"file '{os.path.abspath(bf).replace(chr(92), '/')}'\n")
    run_ffmpeg(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", out_path],
        context="batch concat",
    )
    for bf in batch_files:
        if bf != out_path:
            try:
                os.remove(bf)
            except OSError:
                pass
    try:
        os.remove(concat_list)
    except OSError:
        pass


def _xfade_duration_entering_scene(scene_number_1based: int, mood: str, intensity: str = "5", transition_in: str = "") -> float:
    """Длительность перехода при входе в сцену с учетом режиссерских полей."""
    # Smart transition policy: hard_cut overrides all
    t_in = (transition_in or "").lower()
    if t_in == "hard_cut" or t_in == "hard cut":
        return 0.0

    # Beat-aware cutting: high intensity = faster cut
    if str(intensity).isdigit() and int(intensity) >= 8:
        return Config.SCENE_XFADE_FAST_SEC * 0.5 # Even faster
    elif str(intensity).isdigit() and int(intensity) >= 6:
        return Config.SCENE_XFADE_FAST_SEC

    m = (mood or "calm").lower()
    if any(x in m for x in ("epic", "action", "tense")):
        return Config.SCENE_XFADE_FAST_SEC
    if scene_number_1based % 2 == 0:
        return Config.SCENE_XFADE_EVEN_SEC
    return Config.SCENE_XFADE_ODD_SEC


def _apply_unified_visual_finishing(filter_complex: list, out_stream_name: str, style_preset: str):
    """Unified visual finishing: добавляет финальную цветокоррекцию (псевдо-LUT)"""
    if style_preset == "documentary_clean":
        filter_complex.append(f"[{out_stream_name}]eq=contrast=1.05:brightness=0.01:saturation=0.9[finished]")
        return "finished"
    elif style_preset == "cinematic_premium":
        filter_complex.append(f"[{out_stream_name}]eq=contrast=1.10:brightness=-0.02:saturation=1.05:gamma=0.95[finished]")
        return "finished"
    elif style_preset == "mystery_dark":
        filter_complex.append(f"[{out_stream_name}]eq=contrast=1.15:brightness=-0.05:saturation=0.8[finished]")
        return "finished"
    elif style_preset == "viral_shorts":
        filter_complex.append(f"[{out_stream_name}]eq=contrast=1.05:brightness=0.03:saturation=1.15,unsharp=5:5:1.0:5:5:0.0[finished]")
        return "finished"
    return out_stream_name


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


def _render_chapter_title_card(
    chapter_num: int,
    chapter_title: str,
    out_path: str,
    w: int,
    h: int,
    duration: float = 3.0,
) -> None:
    """Чёрный экран с анимированным текстом главы (fade in/out)."""
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 1000:
        return
        
    font_path = ""
    # Try typical font paths for different OSes
    for path in [
        "C:\\Windows\\Fonts\\arial.ttf",  # Windows
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        "/System/Library/Fonts/Helvetica.ttc",  # MacOS
    ]:
        if os.path.exists(path):
            font_path = path
            break
            
    font_arg = f"fontfile='{font_path}':" if font_path else ""

    # Белый текст с fade-in на чёрном фоне
    title_text = f"Глава {chapter_num}"
    
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(chapter_title)
        sub_file = f.name
        
    # Путь для FFmpeg фильтра (экранирование слэшей для Windows)
    sub_file_ff = sub_file.replace("\\", "/")
    
    drawtext_title = (
        f"drawtext={font_arg}text='{title_text}':fontsize={h//18}:fontcolor=white:"
        f"x=(w-text_w)/2:y=(h/2)-{h//12}:alpha='if(lt(t,0.8),t/0.8,if(gt(t,{duration-0.8}),(({duration}-t)/0.8),1))'"
    )
    drawtext_sub = (
        f"drawtext={font_arg}textfile='{sub_file_ff}':fontsize={h//25}:fontcolor=0xCCCCCC:"
        f"x=(w-text_w)/2:y=(h/2)+{h//20}:alpha='if(lt(t,1.2),t/1.2,if(gt(t,{duration-0.8}),(({duration}-t)/0.8),1))'"
    )

    run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x0a0a12:s={w}x{h}:r=30:d={duration}",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
            "-t", str(duration),
            "-vf", f"{drawtext_title},{drawtext_sub}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            out_path,
        ],
        context=f"chapter title card {chapter_num}",
    )
    
    try:
        os.remove(sub_file)
    except OSError:
        pass


def generate_sfx():
    sfx_dir = "./storage/sfx"
    os.makedirs(sfx_dir, exist_ok=True)

    sfx_files = {
        "whoosh": (800, 1.5, "afade=t=in:d=0.4,afade=t=out:st=0.8:d=0.7,lowpass=f=1500"),
        "impact": (150, 2.0, "afade=t=out:st=0.5:d=1.5"),
        "rise": (200, 2.0, "asetrate=44100*1.5,atempo=0.75,afade=t=in:d=0.3,afade=t=out:st=1.2:d=0.8"),
        "deep_hit": (60, 1.5, "afade=t=out:st=0.3:d=1.2,lowpass=f=200"),
        "sweep": (1200, 2.0, "asetrate=44100*0.7,afade=t=in:d=0.5,afade=t=out:st=1.0:d=1.0,lowpass=f=2000"),
    }

    paths = {}
    for name, (freq, dur, af) in sfx_files.items():
        path = f"{sfx_dir}/{name}.mp3"
        if not os.path.exists(path):
            run_ffmpeg(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={dur}",
                    "-af", af,
                    "-c:a", "libmp3lame",
                    path,
                ],
                context=f"SFX {name}",
            )
        paths[name] = path

    return paths


def render_project(project_id):
    logger.info("Монтаж проекта %s", project_id)
    try:
        sfx_map = generate_sfx()

        script_data = get_project_script(project_id)
        video_format = get_project_format(project_id)
        is_vertical = video_format == "short"

        from database import get_project_row
        row = get_project_row(project_id)
        style_preset = row.get("style_preset", "default") if row else "default"

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
                if mood in Config.MOOD_SFX_IMPACT:
                    sfx_path = random.choice([sfx_map["impact"], sfx_map["deep_hit"]])
                    sfx_vol = Config.SFX_IMPACT_VOLUME
                else:
                    sfx_path = random.choice([sfx_map["whoosh"], sfx_map["sweep"], sfx_map["rise"]])
                    sfx_vol = Config.SFX_WHOOSH_VOLUME

                logger.info(
                    "Рендер сцены %s (глобально #%s), длительность %.2f с",
                    scene["number"],
                    global_scene_index,
                    scene_duration,
                )

                kb = _ken_burns_vf(w, h, scene_duration)
                subs_seg = subtitles_filter_graph(ass_path)
                
                # -----------------------------------------------------
                # LEVEL 1: AVATAR & INTERACTION (TALKING HEAD)
                # Архитектура: Интеграция "Живого маскота". Мы поддерживаем как статичный PNG (аватар пульсирует),
                # так и зацикленное видео MP4 (например, с морганием и мимикой, сгенерированное в Hedra или Wav2Lip).
                # -----------------------------------------------------
                avatar_img_path = os.path.join(project_dir, "avatar.png")
                avatar_vid_path = os.path.join(project_dir, "avatar.mp4")
                
                has_avatar = False
                avatar_path = None
                is_video_avatar = False
                
                if os.path.isfile(avatar_vid_path) and os.path.getsize(avatar_vid_path) > 100:
                    has_avatar = True
                    avatar_path = avatar_vid_path
                    is_video_avatar = True
                    logger.info("Обнаружен ВИДЕО-аватар (мимика/моргание): %s", avatar_path)
                elif os.path.isfile(avatar_img_path) and os.path.getsize(avatar_img_path) > 100:
                    has_avatar = True
                    avatar_path = avatar_img_path
                    is_video_avatar = False
                    logger.info("Обнаружен PNG-аватар (статичный): %s", avatar_path)
                
                # Check for ambience
                ambience_name = scene.get("ambient_sound")
                ambience_path = None
                if ambience_name:
                    ap = f"./storage/sfx/ambience/{ambience_name}.mp3"
                    if os.path.isfile(ap):
                        ambience_path = ap
                
                # Audio filter chain
                if ambience_path:
                    af_chain = (
                        f"[1:a]apad[padded_voice];[2:a]volume={sfx_vol}[sfx];[3:a]volume=0.15[amb];"
                        f"[padded_voice][sfx][amb]amix=inputs=3:duration=longest:dropout_transition=0[aout]"
                    )
                    a_inputs = ["-stream_loop", "-1", "-i", ambience_path]
                else:
                    af_chain = (
                        f"[1:a]apad[padded_voice];[2:a]volume={sfx_vol}[sfx];"
                        f"[padded_voice][sfx]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
                    )
                    a_inputs = []

                # Video filter chain with Avatar (Круглый "Talking Head" с обводкой и пульсацией)
                if has_avatar:
                    # Размер аватара пропорционален экрану (более профессионально)
                    av_size = int(w * 0.35) if is_vertical else int(h * 0.4)
                    av_border = 8 # Стильная толстая обводка (Stroke)
                    
                    # Фильтр: 
                    # 1. Извлекаем квадратную область (центрируем).
                    # 2. Применяем альфа-маску (идеальный круг) через geq.
                    # 3. Генерируем сплошной круг для белой рамки (Border).
                    # 4. Накладываем аватар на белую рамку.
                    # 5. Дыхание (микро-пульсация в такт времени).
                    
                    avatar_filter = (
                        f"[img_avatar]scale={av_size}:{av_size}:force_original_aspect_ratio=increase,crop={av_size}:{av_size}[av_sq];"
                        f"color=c=white@0.0:s={av_size}x{av_size},format=rgba,geq=a='if(lt((X-W/2)*(X-W/2)+(Y-H/2)*(Y-H/2)\\,(W/2)*(W/2))\\,255\\,0)'[av_mask];"
                        f"[av_sq]format=rgba[av_rgba];[av_rgba][av_mask]alphamerge[av_circle];"
                        f"color=c=white:s={av_size+av_border*2}x{av_size+av_border*2},format=rgba,geq=a='if(lt((X-W/2)*(X-W/2)+(Y-H/2)*(Y-H/2)\\,(W/2)*(W/2))\\,255\\,0)'[border_circle];"
                        f"[border_circle][av_circle]overlay={av_border}:{av_border}[av_with_border];"
                        f"[av_with_border]zoompan=z='1.02+0.02*sin(time*4)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(scene_duration*30)}:s={av_size+av_border*2}x{av_size+av_border*2}:fps=30:opt_zoom_filter=1[av_animated]"
                    )
                    
                    # Располагаем над субтитрами по центру
                    x_pos = "(main_w-overlay_w)/2"
                    y_pos = "main_h-overlay_h-450" if is_vertical else "main_h-overlay_h-280"
                    
                    vf_chain = f"[0:v]{kb}[bg];{avatar_filter};[bg][av_animated]overlay={x_pos}:{y_pos}[v_av];[v_av]{subs_seg}[vout]"
                    
                    # Если аватар - это короткое видео моргания/мимики, зацикливаем его (-stream_loop -1)
                    if is_video_avatar:
                        v_inputs = ["-stream_loop", "-1", "-i", avatar_path]
                    else:
                        v_inputs = ["-loop", "1", "-framerate", "30", "-i", avatar_path]
                        
                    avatar_in_idx = 3 if not ambience_path else 4
                    vf_chain = vf_chain.replace("[img_avatar]", f"[{avatar_in_idx}:v]")
                else:
                    vf_chain = f"[0:v]{kb},{subs_seg}[vout]"
                    v_inputs = []

                cmd = [
                    "ffmpeg",
                    "-y",
                ]
                
                if use_image:
                    cmd.extend(["-loop", "1", "-framerate", "30"])
                else:
                    cmd.extend(["-stream_loop", "-1"])
                    
                cmd.extend(["-i", visual_path, "-i", audio_path, "-i", sfx_path])
                cmd.extend(a_inputs)
                cmd.extend(v_inputs)
                
                # Add Unified Visual Finishing to individual clips before they are xfaded
                vf_finish = _apply_unified_visual_finishing([vf_chain], "vout", style_preset)
                if vf_finish == "finished":
                    vf_chain_final = f"{vf_chain};{vf_chain.replace('[vout]', '')};[vout]eq=contrast...[vout]" # Simplification, handled in _merge if we did it on concat, but doing on scene render is safer for xfade.
                    # Since it returns a string name, we rewrite vf_chain if a finish happened:
                    if style_preset == "documentary_clean":
                        vf_chain = vf_chain.replace("[vout]", "[pre_finish];[pre_finish]eq=contrast=1.05:brightness=0.01:saturation=0.9[vout]")
                    elif style_preset == "cinematic_premium":
                        vf_chain = vf_chain.replace("[vout]", "[pre_finish];[pre_finish]eq=contrast=1.10:brightness=-0.02:saturation=1.05:gamma=0.95[vout]")
                    elif style_preset == "mystery_dark":
                        vf_chain = vf_chain.replace("[vout]", "[pre_finish];[pre_finish]eq=contrast=1.15:brightness=-0.05:saturation=0.8[vout]")
                    elif style_preset == "viral_shorts":
                        vf_chain = vf_chain.replace("[vout]", "[pre_finish];[pre_finish]eq=contrast=1.05:brightness=0.03:saturation=1.15,unsharp=5:5:1.0:5:5:0.0[vout]")

                cmd.extend([
                    "-filter_complex",
                    f"{vf_chain};{af_chain}",
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
                ])
                
                run_ffmpeg(cmd, context=f"scene {scene_id} render")
                
                # Store full scene data for transition logic
                scene_paths_meta.append(
                    (out_video, global_scene_index, scene)
                )

            if not scene_paths_meta:
                logger.error("Глава %s: нет ни одной отрендеренной сцены", chap_num)
                return False

            paths = [x[0] for x in scene_paths_meta]
            transitions = []
            for b in range(len(paths) - 1):
                gnum = scene_paths_meta[b + 1][1]
                scn = scene_paths_meta[b + 1][2]
                mood = scn.get("mood", "calm")
                intensity = scn.get("intensity", "5")
                t_in = scn.get("transition_in", "")
                transitions.append(_xfade_duration_entering_scene(gnum, mood, intensity, t_in))

            logger.info("Сборка главы %s: xfade между %s клипами", chap_num, len(paths))
            _merge_scenes_xfade_batched(paths, transitions, chap_file)

            for pth, _, _ in scene_paths_meta:
                try:
                    os.remove(pth)
                except OSError:
                    pass

            chapter_files.append(chap_file)

        logger.info("Финальная склейка с карточками между главами")
        final_list = f"{project_dir}/concat_final.txt"
        with open(final_list, "w", encoding="utf-8") as f:
            for i, cf in enumerate(chapter_files):
                f.write(f"file '{os.path.abspath(cf).replace(chr(92), '/')}'\n")
                if i < len(chapter_files) - 1:
                    next_chap = script_data[i + 1]
                    card_path = os.path.join(render_dir, f"_title_card_{next_chap['number']}.mp4")
                    _render_chapter_title_card(
                        next_chap["number"], next_chap.get("title", f"Chapter {next_chap['number']}"), card_path, w, h
                    )
                    f.write(f"file '{os.path.abspath(card_path).replace(chr(92), '/')}'\n")

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
