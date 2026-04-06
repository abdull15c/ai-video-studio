"""
Vertex AI Imagen 3: картинки по image_prompt сценария (Ken Burns в montage.py).
Требуются GOOGLE_APPLICATION_CREDENTIALS, GCP_PROJECT_ID (или GOOGLE_CLOUD_PROJECT), Vertex AI API.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from config import Config
from database import get_project_format, get_project_script

logger = logging.getLogger(__name__)

_PROMPT_PREFIX = "High quality cinematic documentary shot, 8k resolution, photorealistic."
_FALLBACK_PROMPT = "Dramatic cinematic landscape, atmospheric, photorealistic documentary style."

_model_cache: Optional[object] = None


def _gcp_project() -> str:
    p = (Config.GCP_PROJECT_ID or "").strip() or (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    return p


def _get_model():
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    project = _gcp_project()
    if not project:
        raise ValueError(
            "GCP_PROJECT_ID (или GOOGLE_CLOUD_PROJECT) не задан — нужен ID проекта Google Cloud для Vertex AI."
        )
    import vertexai
    from vertexai.preview.vision_models import ImageGenerationModel

    vertexai.init(project=project, location=Config.GCP_LOCATION)
    _model_cache = ImageGenerationModel.from_pretrained(Config.IMAGEN_MODEL)
    logger.info(
        "Vertex Imagen: project=%s location=%s model=%s",
        project,
        Config.GCP_LOCATION,
        Config.IMAGEN_MODEL,
    )
    return _model_cache


def _images_from_response(resp) -> List:
    if resp is None:
        return []
    if hasattr(resp, "images"):
        return list(resp.images or [])
    try:
        return list(resp)
    except TypeError:
        return [resp]


def _save_first_image(resp, image_path: str) -> bool:
    imgs = _images_from_response(resp)
    if not imgs:
        return False
    img0 = imgs[0]
    save_fn = getattr(img0, "save", None)
    if not callable(save_fn):
        logger.error("Imagen: объект ответа без метода save")
        return False
    d = os.path.dirname(os.path.abspath(image_path))
    if d:
        os.makedirs(d, exist_ok=True)
    try:
        save_fn(location=image_path)
    except TypeError:
        save_fn(image_path)
    return os.path.isfile(image_path) and os.path.getsize(image_path) > 100


def _generate_once(model, prompt: str, aspect_ratio: str, safety: Optional[str]) -> object:
    if safety:
        try:
            return model.generate_images(
                prompt=prompt,
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                safety_filter_level=safety,
            )
        except TypeError:
            logger.warning("Imagen: safety_filter_level не поддержан SDK — запрос без него")
    return model.generate_images(
        prompt=prompt,
        number_of_images=1,
        aspect_ratio=aspect_ratio,
    )


def generate_scene_image_imagen(
    image_path: str,
    image_prompt: str,
    *,
    aspect_ratio: str,
) -> bool:
    """
    Одна картинка по промпту. Используется в hybrid и внутри generate_images_google.
    """
    from google.auth import exceptions as auth_exc
    from google.api_core import exceptions as core_exc

    raw = (image_prompt or "").strip() or "cinematic documentary scene"
    full_prompt = f"{_PROMPT_PREFIX} {raw}"
    safety = (Config.IMAGEN_SAFETY_FILTER or "").strip() or None

    try:
        model = _get_model()
    except ValueError as e:
        logger.error("%s", e)
        return False
    except Exception as e:
        logger.error(
            "Imagen: не удалось инициализировать Vertex AI (credentials / aiplatform). %s",
            e,
        )
        return False

    try:
        resp = _generate_once(model, full_prompt, aspect_ratio, safety)
        if _save_first_image(resp, image_path):
            return True

        logger.warning("Imagen: пустой ответ или фильтр без исключения, промпт: %s...", full_prompt[:120])
        resp2 = _generate_once(model, _FALLBACK_PROMPT, aspect_ratio, safety)
        return _save_first_image(resp2, image_path)

    except auth_exc.DefaultCredentialsError:
        logger.error(
            "Imagen: нет credentials. Укажите GOOGLE_APPLICATION_CREDENTIALS на JSON ключ сервисного аккаунта."
        )
        return False
    except core_exc.PermissionDenied:
        logger.error(
            "Imagen: доступ запрещён. Включите Vertex AI API и выдайте роль Vertex AI User сервисному аккаунту."
        )
        return False
    except core_exc.Forbidden:
        logger.error("Imagen: API запрещён — проверьте Vertex AI API и IAM.")
        return False
    except core_exc.ResourceExhausted:
        logger.error("Imagen: квота или биллинг — проверьте лимиты и привязку биллинга в Google Cloud.")
        return False
    except Exception as e:
        err = str(e).lower()
        if "safety" in err or "blocked" in err or "rai" in err:
            logger.warning("Imagen: сработал safety / блокировка, промпт: %s — повтор с упрощённым промптом", raw[:200])
            try:
                resp = _generate_once(model, _FALLBACK_PROMPT, aspect_ratio, safety)
                return _save_first_image(resp, image_path)
            except Exception as e2:
                logger.error("Imagen: fallback не удался: %s", e2)
                return False
        logger.exception("Imagen: ошибка генерации: %s", e)
        return False


def generate_images_google(project_id: int) -> bool:
    """
    Все сцены проекта: images/scene_{id}.jpg. Уже существующие файлы пропускаются.
    """
    script_data = get_project_script(project_id)
    video_format = get_project_format(project_id)
    is_vertical = video_format == "short"
    aspect_ratio = "9:16" if is_vertical else "16:9"

    project_dir = f"./storage/projects/{project_id}"
    images_dir = f"{project_dir}/images"
    os.makedirs(images_dir, exist_ok=True)

    for chapter in script_data:
        for scene in chapter["scenes"]:
            scene_id = scene["id"]
            image_path = os.path.join(images_dir, f"scene_{scene_id}.jpg")
            if os.path.isfile(image_path) and os.path.getsize(image_path) > 100:
                continue
            ip = scene.get("image_prompt", "Cinematic documentary scene")
            logger.info("Imagen: сцена %s (id=%s)", scene.get("number"), scene_id)
            if not generate_scene_image_imagen(image_path, ip, aspect_ratio=aspect_ratio):
                logger.error("Imagen: не удалось сгенерировать сцену id=%s", scene_id)
                return False

    logger.info("Imagen: все картинки для проекта %s готовы", project_id)
    return True
