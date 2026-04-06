import logging
import os
import time

import pyperclip
from config import Config
from database import get_project_format, get_project_image_engine, get_project_script
from modules.browser_manager import launch_profile
from modules.brolls_engine import get_smart_stock
from modules.visual_query_planner import plan_visual_queries

logger = logging.getLogger(__name__)


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


def generate_videos(project_id, pure_stock=False, profiles_count=None):
    if profiles_count is None:
        profiles_count = Config.VIDEO_GENERATOR_PROFILES_COUNT

    logger.info("Гибридный движок видео + ИИ-режиссёр, project_id=%s", project_id)

    image_engine = get_project_image_engine(project_id)

    if image_engine == "google-imagen":
        from modules.google_imagen import generate_images_google

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

    if image_engine == "hybrid":
        os.makedirs(images_dir, exist_ok=True)
        aspect_ratio = "9:16" if is_vertical else "16:9"
        from modules.google_imagen import generate_scene_image_imagen

        used_urls = set()
        scene_index = 0
        for chapter in script_data:
            for scene in chapter["scenes"]:
                scene_id = scene["id"]
                target_file = f"scene_{scene_id}.mp4"
                target_path = os.path.join(videos_dir, target_file)
                image_path = os.path.join(images_dir, f"scene_{scene_id}.jpg")

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

                plan = plan_visual_queries(narration, image_prompt, mood, camera, project_id=project_id)
                confidence = (plan.get("stock_confidence") or "medium").lower()

                logger.info(
                    "План hybrid: stock_confidence=%s, primary_query=%s",
                    confidence,
                    plan.get("primary_query", ""),
                )

                if confidence == "high":
                    dur = scene.get("duration_sec")
                    min_dur = float(dur) if dur is not None else None
                    success, src_url = get_smart_stock(
                        plan,
                        is_vertical,
                        target_path,
                        min_duration_sec=min_dur,
                        used_urls=used_urls,
                    )
                    if success:
                        if src_url:
                            used_urls.add(src_url)
                        logger.info("B-Roll (hybrid, high): %s", target_file)
                        scene_index += 1
                        continue
                    logger.warning("Hybrid: сток для high не сработал — Imagen")

                if not generate_scene_image_imagen(image_path, image_prompt, aspect_ratio=aspect_ratio):
                    logger.error("Hybrid: Imagen не удалась для сцены id=%s", scene_id)
                    return False
                scene_index += 1

        logger.info("Режим hybrid: сцены собраны (сток и/или Imagen), project_id=%s", project_id)
        return True

    ai_url = "https://grok.com"
    scene_index = 0
    used_urls = set()

    for chapter in script_data:
        for scene in chapter["scenes"]:
            scene_id = scene["id"]
            target_file = f"scene_{scene_id}.mp4"
            target_path = os.path.join(videos_dir, target_file)

            if os.path.exists(target_path):
                scene_index += 1
                continue

            narration = scene.get("narration", "")
            image_prompt = scene.get("image_prompt", "Cinematic view")
            mood = scene.get("mood", "calm")
            camera = scene.get("camera", "wide")

            logger.info("Сцена %s | текст: %s...", scene["number"], narration[:50])

            plan = plan_visual_queries(narration, image_prompt, mood, camera, project_id=project_id)

            logger.info(
                "План: режим=%s, stock_confidence=%s, primary_query=%s",
                plan.get("visual_mode", "unknown"),
                plan.get("stock_confidence", "unknown"),
                plan.get("primary_query", ""),
            )

            confidence = plan.get("stock_confidence", "medium")

            if should_try_stock_first(pure_stock, confidence):
                dur = scene.get("duration_sec")
                min_dur = float(dur) if dur is not None else None
                success, src_url = get_smart_stock(
                    plan,
                    is_vertical,
                    target_path,
                    min_duration_sec=min_dur,
                    used_urls=used_urls,
                )
                if success:
                    if src_url:
                        used_urls.add(src_url)
                    logger.info("B-Roll скачан: %s", target_file)
                    scene_index += 1
                    continue
                logger.warning("Сток не сработал (сеть/лимиты/пустая выдача)")

            if not pure_stock:
                logger.info("ИИ-генерация: сложная сцена, открываем браузер")
                current_profile = (scene_index % profiles_count) + 1
                ai_prompt = f"{image_prompt}, {mood} mood. {'--ar 9:16' if is_vertical else '--ar 16:9'}"
                pyperclip.copy(ai_prompt)

                launch_profile(current_profile, ai_url)

                logger.info("Вставьте промпт в браузере (профиль %s); сохраните как %s", current_profile, target_path)
                wait_for_download(target_path)
                logger.info("ИИ-видео загружено: %s", target_file)

            scene_index += 1

    logger.info("Все видео для проекта %s собраны", project_id)
    return True
