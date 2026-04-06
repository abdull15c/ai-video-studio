import logging
import os
import pickle
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_JSON = "token.json"
LEGACY_TOKEN_PICKLE = "token.pickle"


def _migrate_pickle_to_json():
    try:
        with open(LEGACY_TOKEN_PICKLE, "rb") as f:
            old = pickle.load(f)
    except (OSError, pickle.UnpicklingError, AttributeError, EOFError) as e:
        logger.error("Не удалось прочитать token.pickle: %s", e)
        return
    if not hasattr(old, "to_json"):
        logger.error("token.pickle: неподдерживаемый объект — удалите и пройдите OAuth заново")
        return
    with open(TOKEN_JSON, "w", encoding="utf-8") as f:
        f.write(old.to_json())
    try:
        os.remove(LEGACY_TOKEN_PICKLE)
    except OSError:
        pass
    logger.info("Учётные данные перенесены в token.json")


def _load_credentials():
    if os.path.exists(TOKEN_JSON):
        return Credentials.from_authorized_user_file(TOKEN_JSON, SCOPES)
    if os.path.exists(LEGACY_TOKEN_PICKLE):
        logger.warning("Обнаружен token.pickle — однократный перенос в token.json")
        _migrate_pickle_to_json()
    if os.path.exists(TOKEN_JSON):
        return Credentials.from_authorized_user_file(TOKEN_JSON, SCOPES)
    return None


def _save_credentials(creds):
    with open(TOKEN_JSON, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def get_authenticated_service():
    creds = _load_credentials()

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("client_secrets.json"):
                logger.error("Файл client_secrets.json не найден")
                return None

            flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
            creds = flow.run_local_server(port=0)

        _save_credentials(creds)

    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(project_id, topic):
    logger.info("YouTube upload, project_id=%s", project_id)
    project_dir = f"./storage/projects/{project_id}"
    video_file = f"{project_dir}/FINAL_CINEMATIC_{project_id}.mp4"
    thumb_file = f"{project_dir}/THUMBNAIL_{project_id}.jpg"
    seo_file = f"{project_dir}/seo.txt"

    if not os.path.exists(video_file):
        logger.error("Видео не найдено: %s", video_file)
        return False

    youtube = get_authenticated_service()
    if not youtube:
        return False

    title = topic
    description = ""
    tags = []

    if os.path.exists(seo_file):
        with open(seo_file, "r", encoding="utf-8") as f:
            seo_content = f.read()
            lines = seo_content.split("\n")
            for line in lines:
                if line.startswith("1. "):
                    title = line.replace("1. ", "").strip()
                    break
            description = seo_content
            if "ТЕГИ:" in seo_content:
                tags_line = [l for l in lines if l.startswith("ТЕГИ:")][0]
                tags = [t.replace("#", "").strip() for t in tags_line.replace("ТЕГИ:", "").split() if t]

    logger.info("Загрузка на YouTube: %r", title)

    request_body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": "27",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    media_file = MediaFileUpload(video_file, chunksize=-1, resumable=True)

    try:
        response_upload = (
            youtube.videos()
            .insert(part="snippet,status", body=request_body, media_body=media_file)
            .execute()
        )

        video_id = response_upload.get("id")
        logger.info("Видео загружено, id=%s https://youtu.be/%s", video_id, video_id)

        if os.path.exists(thumb_file):
            logger.info("Загрузка thumbnail")
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb_file)).execute()
            logger.info("Превью установлено")

        return True
    except Exception as e:
        logger.exception("Ошибка загрузки на YouTube: %s", e)
        return False
