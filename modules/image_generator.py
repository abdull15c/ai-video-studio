import logging
import os
import replicate
import requests
from config import Config
from database import get_project_script, get_project_format

logger = logging.getLogger(__name__)


def generate_images(project_id):
    logger.info("Генерация изображений (Replicate Flux), project_id=%s", project_id)

    token = (Config.REPLICATE_API_TOKEN or "").strip()
    if not token or token in ("your_token_here", "r8_xxx", "REPLACE_ME"):
        raise ValueError("Нет корректного REPLICATE_API_TOKEN в .env")

    script_data = get_project_script(project_id)
    video_format = get_project_format(project_id)
    is_vertical = video_format == "short"

    project_dir = f"./storage/projects/{project_id}"
    images_dir = f"{project_dir}/images"
    os.makedirs(images_dir, exist_ok=True)

    for chapter in script_data:
        for scene in chapter["scenes"]:
            image_path = f"{images_dir}/scene_{scene['id']}.jpg"
            if os.path.exists(image_path):
                continue

            prompt = scene.get("image_prompt", "Cinematic view, highly detailed")
            logger.info("Flux: сцена %s", scene["number"])

            try:
                output = replicate.run(
                    "black-forest-labs/flux-schnell",
                    input={
                        "prompt": prompt,
                        "aspect_ratio": "9:16" if is_vertical else "16:9",
                        "output_format": "jpg",
                    },
                )
                with open(image_path, "wb") as handler:
                    handler.write(requests.get(output[0], timeout=120).content)
            except Exception as e:
                logger.error("Ошибка генерации картинки: %s", e)
                return False

    logger.info("Все изображения сохранены")
    return True
