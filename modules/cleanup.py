import logging
import os
import shutil

logger = logging.getLogger(__name__)


def cleanup_project_temp_files(project_id):
    logger.info("Очистка временных файлов, project_id=%s", project_id)
    project_dir = f"./storage/projects/{project_id}"

    temp_dirs = ["audio", "images", "raw_videos", "render", "subtitles"]

    for d in temp_dirs:
        target_path = os.path.join(project_dir, d)
        if os.path.exists(target_path):
            shutil.rmtree(target_path, ignore_errors=True)
            logger.info("Удалена папка: %s/", d)

    raw_video = os.path.join(project_dir, f"FINAL_VIDEO_{project_id}.mp4")
    if os.path.exists(raw_video):
        try:
            os.remove(raw_video)
        except OSError:
            pass

    logger.info("Очистка завершена")
    return True
