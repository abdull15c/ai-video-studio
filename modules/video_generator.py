import logging
import os
import time

import pyperclip
import json
from config import Config
from database import get_project_format, get_project_image_engine, get_project_script, log_scene_generation_attempt
from modules.browser_manager import launch_profile
from modules.brolls_engine import get_smart_stock, get_smart_stock_multiple
from modules.visual_query_planner import plan_visual_queries
from modules.ffmpeg_util import run_ffmpeg

logger = logging.getLogger(__name__)


def concat_broll_parts(parts: list, target_path: str):
    """
    Склеивает части B-rolls (Jump-cuts) в один длинный файл.
    Мы используем re-encoding (-c:v libx264), так как сток-видео могут иметь 
    разные кодеки, разрешение, битрейт и framerate.
    """
    if not parts:
        return False
    if len(parts) == 1:
        os.rename(parts[0], target_path)
        return True
        
    # Создаем фильтр `concat`, который перекодирует все кусочки в единый видеоряд:
    # [0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]
    
    cmd = ["ffmpeg", "-y"]
    for p in parts:
        cmd.extend(["-i", p])
        
    filter_complex = ""
    for i in range(len(parts)):
        filter_complex += f"[{i}:v][{i}:a]"
        
    filter_complex += f"concat=n={len(parts)}:v=1:a=1[outv][outa]"
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        target_path
    ])
            
    run_ffmpeg(cmd, context="concat broll parts (jump-cuts hard-cut)")
    
    # Cleanup
    for p in parts:
        try:
            os.remove(p)
        except Exception:
            pass
            
    return os.path.exists(target_path)


def should_try_stock_first(pure_stock, stock_confidence):
    """Маршрутизация: сначала сток, если forced pure_stock или уверенность high/medium."""
    conf = (stock_confidence or "medium").lower()
    return bool(pure_stock or conf in ("high", "medium"))


def wait_for_download(filepath):
    while not os.path.exists(filepath):
        if os.path.exists(filepath + ".crdownload"):
            time.sleep(1)
            continue
        time.sleep(1)
    old_size = -1
    while True:
        size = os.path.getsize(filepath)
        if size > 0 and size == old_size:
            if not os.path.exists(filepath + ".crdownload"):
                break
        old_size = size
        time.sleep(1.5)
    return True


def generate_videos(project_id, pure_stock=False, profiles_count=None, specific_scene_id=None):
    if profiles_count is None:
        profiles_count = Config.VIDEO_GENERATOR_PROFILES_COUNT

    logger.info("Гибридный движок видео + ИИ-режиссёр, project_id=%s, specific_scene=%s", project_id, specific_scene_id)

    image_engine = get_project_image_engine(project_id)

    if image_engine == "google-imagen":
        from modules.google_imagen import generate_images_google
        # В идеале google_imagen должен тоже понимать specific_scene_id
        # Но для простоты: генерируем все, если их нет. Или если specific_scene_id - надо удалить старую
        if specific_scene_id:
            old_ip = f"./storage/projects/{project_id}/images/scene_{specific_scene_id}.jpg"
            if os.path.exists(old_ip):
                os.remove(old_ip)
        return generate_images_google(project_id)

    script_data = get_project_script(project_id)
    video_format = get_project_format(project_id)
    is_vertical = video_format == "short"

    if video_format == "long" and image_engine == "stock":
        pure_stock = True
        logger.info("Формат LONG + сток: принудительно только сток")

    project_dir = f"./storage/projects/{project_id}"
    videos_dir = f"{project_dir}/raw_videos"
    images_dir = f"{project_dir}/images"
    os.makedirs(videos_dir, exist_ok=True)

    if image_engine in ("hybrid", "replicate"):
        os.makedirs(images_dir, exist_ok=True)
        aspect_ratio = "9:16" if is_vertical else "16:9"
        if image_engine == "replicate":
            try:
                from modules.image_generator import generate_scene_image
                image_func = generate_scene_image
            except ImportError:
                from modules.google_imagen import generate_scene_image_imagen
                image_func = generate_scene_image_imagen
        else:
            from modules.google_imagen import generate_scene_image_imagen
            image_func = generate_scene_image_imagen

        used_urls = set()
        scene_index = 0
        for chapter in script_data:
            for scene in chapter["scenes"]:
                scene_id = scene["id"]
                if specific_scene_id and scene_id != specific_scene_id:
                    scene_index += 1
                    continue

                target_file = f"scene_{scene_id}.mp4"
                target_path = os.path.join(videos_dir, target_file)
                image_path = os.path.join(images_dir, f"scene_{scene_id}.jpg")

                if specific_scene_id:
                    if os.path.exists(target_path): os.remove(target_path)
                    if os.path.exists(image_path): os.remove(image_path)
                else:
                    if os.path.exists(target_path):
                        scene_index += 1
                        continue
                    if os.path.isfile(image_path) and os.path.getsize(image_path) > 100:
                        scene_index += 1
                        continue

                narration = scene.get("narration", "")
                image_prompt = scene.get("image_prompt", "Cinematic view")
                mood = scene.get("mood", "calm")
                camera = scene.get("camera", "wide")

                logger.info("Сцена %s (hybrid) | текст: %s...", scene["number"], narration[:50])

                plan = plan_visual_queries(narration, image_prompt, mood, camera, project_id=project_id, scene_index=scene_index)
                confidence = (plan.get("stock_confidence") or "medium").lower()

                logger.info(
                    "План hybrid: stock_confidence=%s, primary_query=%s",
                    confidence,
                    plan.get("primary_query", ""),
                )

                if confidence == "high":
                    dur = scene.get("duration_sec")
                    min_dur = float(dur) if dur is not None else 8.0
                    
                    # Jump-cuts логика: если сцена > 6 секунд, качаем несколько видео
                    if min_dur > 8.0:
                        count = 3 if min_dur > 12.0 else 2
                        logger.info("Jump-cuts для %s: качаем %s видео", target_file, count)
                        success, paths = get_smart_stock_multiple(
                            plan, is_vertical, target_path.replace(".mp4", ""), count=count, min_duration_sec=min_dur, used_urls=used_urls
                        )
                        if success and paths:
                            concat_broll_parts(paths, target_path)
                            success = os.path.exists(target_path)
                    else:
                        success, src_url = get_smart_stock(
                            plan, is_vertical, target_path, min_duration_sec=min_dur, used_urls=used_urls
                        )
                        if success and src_url:
                            used_urls.add(src_url)
                            
                    if success:
                        logger.info("B-Roll (hybrid, high): %s", target_file)
                        log_scene_generation_attempt(scene_id, json.dumps(plan, ensure_ascii=False), target_path)
                        scene_index += 1
                        continue
                    logger.warning("Hybrid: сток для high не сработал — Imagen")

                if not image_func(image_path, image_prompt, aspect_ratio=aspect_ratio):
                    logger.error("Hybrid/Replicate: генерация не удалась для сцены id=%s", scene_id)
                    log_scene_generation_attempt(scene_id, json.dumps(plan, ensure_ascii=False), "", failure_reason="Image func failed")
                    return False
                
                log_scene_generation_attempt(scene_id, json.dumps(plan, ensure_ascii=False), image_path)
                scene_index += 1

        logger.info("Режим %s: сцены собраны (сток и/или генерация), project_id=%s", image_engine, project_id)
        return True

    # Для режима "stock" (только сток) 
    # Если мы дошли сюда, значит image_engine = "stock"
    scene_index = 0
    used_urls = set()

    for chapter in script_data:
        for scene in chapter["scenes"]:
            scene_id = scene["id"]
            if specific_scene_id and scene_id != specific_scene_id:
                scene_index += 1
                continue

            target_file = f"scene_{scene_id}.mp4"
            target_path = os.path.join(videos_dir, target_file)

            if specific_scene_id:
                if os.path.exists(target_path): os.remove(target_path)
            else:
                if os.path.exists(target_path):
                    scene_index += 1
                    continue

            narration = scene.get("narration", "")
            image_prompt = scene.get("image_prompt", "Cinematic view")
            mood = scene.get("mood", "calm")
            camera = scene.get("camera", "wide")

            logger.info("Сцена %s (stock only) | текст: %s...", scene["number"], narration[:50])

            plan = plan_visual_queries(narration, image_prompt, mood, camera, project_id=project_id, scene_index=scene_index)

            dur = scene.get("duration_sec")
            min_dur = float(dur) if dur is not None else 8.0
            
            # Jump-cuts логика:
            if min_dur > 8.0:
                count = 3 if min_dur > 12.0 else 2
                logger.info("Jump-cuts для %s: качаем %s видео", target_file, count)
                success, paths = get_smart_stock_multiple(
                    plan, is_vertical, target_path.replace(".mp4", ""), count=count, min_duration_sec=min_dur, used_urls=used_urls
                )
                if success and paths:
                    concat_broll_parts(paths, target_path)
                    success = os.path.exists(target_path)
            else:
                success, src_url = get_smart_stock(
                    plan, is_vertical, target_path, min_duration_sec=min_dur, used_urls=used_urls
                )
                if success and src_url:
                    used_urls.add(src_url)
                    
            if success:
                logger.info("B-Roll скачан: %s", target_file)
                log_scene_generation_attempt(scene_id, json.dumps(plan, ensure_ascii=False), target_path)
                scene_index += 1
                continue
            
            logger.warning("Сток не сработал. Так как режим 'stock', генерация невозможна. Сцена %s останется пустой.", scene_id)
            log_scene_generation_attempt(scene_id, json.dumps(plan, ensure_ascii=False), "", failure_reason="No stock found")
            scene_index += 1

    logger.info("Все видео для проекта %s собраны", project_id)
    return True
