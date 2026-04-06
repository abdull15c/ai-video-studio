import os
from dotenv import load_dotenv

load_dotenv()


def _normalize_llm_provider(raw: str) -> str:
    v = (raw or "deepseek").strip().lower()
    if v in ("deepseek", "anthropic", "openai"):
        return v
    return "deepseek"


def _normalize_image_engine(raw: str) -> str:
    v = (raw or "stock").strip().lower()
    if v in ("google-imagen", "google_imagen", "imagen"):
        return "google-imagen"
    if v == "hybrid":
        return "hybrid"
    return "stock"


class Config:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MAX_RETRIES = int(os.getenv("ANTHROPIC_MAX_RETRIES", "3"))
    ANTHROPIC_RETRY_BASE_DELAY_SEC = float(os.getenv("ANTHROPIC_RETRY_BASE_DELAY_SEC", "1.0"))

    LLM_PROVIDER = _normalize_llm_provider(os.getenv("LLM_PROVIDER", "deepseek"))
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    @staticmethod
    def has_llm_api_key():
        """Ключ для активного LLM_PROVIDER (пустая строка = нет)."""
        p = Config.LLM_PROVIDER
        if p == "anthropic":
            return bool((Config.ANTHROPIC_API_KEY or "").strip())
        if p == "deepseek":
            return bool((Config.DEEPSEEK_API_KEY or "").strip())
        if p == "openai":
            return bool((Config.OPENAI_API_KEY or "").strip())
        return False

    @staticmethod
    def has_anthropic_api_key():
        """Обратная совместимость: то же, что has_llm_api_key()."""
        return Config.has_llm_api_key()

    REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")

    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
    PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
    PEXELS_HTTP_TIMEOUT_SEC = int(os.getenv("PEXELS_HTTP_TIMEOUT_SEC", "10"))

    STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")
    DB_PATH = os.path.join(STORAGE_PATH, "db.sqlite")
    SQLITE_BUSY_TIMEOUT_SEC = float(os.getenv("SQLITE_BUSY_TIMEOUT_SEC", "30"))

    # Видео (кадр)
    VIDEO_WIDTH_SHORT = int(os.getenv("VIDEO_WIDTH_SHORT", "1080"))
    VIDEO_HEIGHT_SHORT = int(os.getenv("VIDEO_HEIGHT_SHORT", "1920"))
    VIDEO_WIDTH_MAIN = int(os.getenv("VIDEO_WIDTH_MAIN", "1920"))
    VIDEO_HEIGHT_MAIN = int(os.getenv("VIDEO_HEIGHT_MAIN", "1080"))

    # Озвучка (Edge-TTS). Список id для LLM и --voice — только из каталога.
    EDGE_TTS_VOICE_CATALOG = (
        ("ru-RU-DmitryNeural", "мужской, нейтральный — история, документалистика, факты"),
        ("ru-RU-SvetlanaNeural", "женский, мягкий — лирика, природа, эмоциональные темы"),
        ("ru-RU-DariyaNeural", "женский, чёткая дикция — наука, образование, динамичный тон"),
    )
    EDGE_TTS_VOICE_IDS = frozenset(v for v, _ in EDGE_TTS_VOICE_CATALOG)
    EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "ru-RU-DmitryNeural")
    EDGE_TTS_CONCURRENCY = max(1, int(os.getenv("EDGE_TTS_CONCURRENCY", "8")))
    EDGE_TTS_DELAY_SEC = float(os.getenv("EDGE_TTS_DELAY_SEC", "1.0"))

    # Движок озвучки: пусто / не google-* → Edge-TTS; google-gemini | google-chirp | google-neural2
    TTS_ENGINE = (os.getenv("TTS_ENGINE", "") or "").strip().lower()
    GOOGLE_TTS_VOICE = os.getenv("GOOGLE_TTS_VOICE", "Kore")
    GOOGLE_TTS_PROMPT = os.getenv(
        "GOOGLE_TTS_PROMPT",
        "Speak as a documentary narrator, calm and authoritative, with dramatic pauses",
    )
    GOOGLE_TTS_SPEAKING_RATE = float(os.getenv("GOOGLE_TTS_SPEAKING_RATE", "1.0"))
    GOOGLE_TTS_PITCH = float(os.getenv("GOOGLE_TTS_PITCH", "0.0"))
    GOOGLE_TTS_MAX_RETRIES = int(os.getenv("GOOGLE_TTS_MAX_RETRIES", "3"))
    GOOGLE_TTS_RETRY_BASE_DELAY_SEC = float(os.getenv("GOOGLE_TTS_RETRY_BASE_DELAY_SEC", "2.0"))

    # Визуалы: сток (Pexels/Pixabay) или Vertex AI Imagen 3
    IMAGE_ENGINE = _normalize_image_engine(os.getenv("IMAGE_ENGINE", "stock"))
    GCP_PROJECT_ID = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1").strip() or "us-central1"
    IMAGEN_MODEL = os.getenv("IMAGEN_MODEL", "imagen-3.0-generate-001").strip() or "imagen-3.0-generate-001"
    IMAGEN_SAFETY_FILTER = os.getenv("IMAGEN_SAFETY_FILTER", "block_few").strip() or "block_few"

    @staticmethod
    def normalize_image_engine(raw):
        """Нормализация IMAGE_ENGINE (stock / google-imagen / hybrid)."""
        return _normalize_image_engine(raw)

    @staticmethod
    def normalize_edge_tts_voice(voice_id):
        """Возвращает допустимый id или дефолт из EDGE_TTS_VOICE."""
        v = (voice_id or "").strip()
        if v in Config.EDGE_TTS_VOICE_IDS:
            return v
        return Config.EDGE_TTS_VOICE if Config.EDGE_TTS_VOICE in Config.EDGE_TTS_VOICE_IDS else next(iter(Config.EDGE_TTS_VOICE_IDS))

    @staticmethod
    def edge_tts_voice_catalog_text():
        return "\n".join(f'    "{vid}": {desc}' for vid, desc in Config.EDGE_TTS_VOICE_CATALOG)

    # Whisper / субтитры (Alignment=2 низ, MarginV — отступ от нижнего края)
    # whisper — таймкоды через Whisper; proportional — только текст сценария, без Whisper (меньше RAM)
    SUBTITLE_ENGINE = (os.getenv("SUBTITLE_ENGINE", "whisper") or "whisper").strip().lower()
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
    SUBTITLE_FONT_SIZE_SHORT = os.getenv("SUBTITLE_FONT_SIZE_SHORT", "34")
    SUBTITLE_FONT_SIZE_MAIN = os.getenv("SUBTITLE_FONT_SIZE_MAIN", "22")
    SUBTITLE_MARGIN_V_SHORT = os.getenv("SUBTITLE_MARGIN_V_SHORT", "72")
    SUBTITLE_MARGIN_V_MAIN = os.getenv("SUBTITLE_MARGIN_V_MAIN", "48")
    SUBTITLE_MAX_WORDS_SHORT = int(os.getenv("SUBTITLE_MAX_WORDS_SHORT", "3"))
    SUBTITLE_MAX_WORDS_MAIN = int(os.getenv("SUBTITLE_MAX_WORDS_MAIN", "6"))

    # Монтаж / SFX (xfade между сценами; чёрный зазор между главами)
    SCENE_TAIL_SILENCE_SEC = float(os.getenv("SCENE_TAIL_SILENCE_SEC", "0.8"))
    SCENE_FADE_OUT_LEAD_SEC = float(os.getenv("SCENE_FADE_OUT_LEAD_SEC", "0.5"))
    SCENE_FADE_IN_SEC = float(os.getenv("SCENE_FADE_IN_SEC", "0.5"))
    SCENE_XFADE_EVEN_SEC = float(os.getenv("SCENE_XFADE_EVEN_SEC", "0.3"))
    SCENE_XFADE_ODD_SEC = float(os.getenv("SCENE_XFADE_ODD_SEC", "0.5"))
    SCENE_XFADE_FAST_SEC = float(os.getenv("SCENE_XFADE_FAST_SEC", "0.15"))
    CHAPTER_GAP_BLACK_SEC = float(os.getenv("CHAPTER_GAP_BLACK_SEC", "0.5"))
    STOCK_DOWNLOAD_TIMEOUT_SEC = int(os.getenv("STOCK_DOWNLOAD_TIMEOUT_SEC", "30"))
    STOCK_MIN_FPS = float(os.getenv("STOCK_MIN_FPS", "15"))
    # ffprobe: отсев слайдшоу/статики (байт/сек по размеру файла)
    STOCK_MIN_BYTES_PER_SEC = float(os.getenv("STOCK_MIN_BYTES_PER_SEC", "200000"))
    SFX_IMPACT_VOLUME = os.getenv("SFX_IMPACT_VOLUME", "0.5")
    SFX_WHOOSH_VOLUME = os.getenv("SFX_WHOOSH_VOLUME", "0.3")
    MOOD_SFX_IMPACT = frozenset(
        s.strip().lower()
        for s in os.getenv("MOOD_SFX_IMPACT", "epic,dramatic,tense,action").split(",")
        if s.strip()
    )

    # Гибридный движок видео
    VIDEO_GENERATOR_PROFILES_COUNT = max(1, int(os.getenv("VIDEO_GENERATOR_PROFILES_COUNT", "3")))

    # Аудио-мастеринг (фон)
    BG_MUSIC_VOLUME = float(os.getenv("BG_MUSIC_VOLUME", "0.4"))
    SIDECHAIN_THRESHOLD = os.getenv("SIDECHAIN_THRESHOLD", "0.08")
    SIDECHAIN_RATIO = os.getenv("SIDECHAIN_RATIO", "4")
    SIDECHAIN_ATTACK = os.getenv("SIDECHAIN_ATTACK", "5")
    SIDECHAIN_RELEASE = os.getenv("SIDECHAIN_RELEASE", "50")

    # Веб-дашборд (FastAPI)
    DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8000"))
    TOPICS_QUEUE_FILE = os.getenv("TOPICS_QUEUE_FILE", "topics.txt")
    REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
    FONTS_DIR = os.path.join(STORAGE_PATH, "fonts")
    SUBTITLE_FONTS_DIR = FONTS_DIR

    os.makedirs(STORAGE_PATH, exist_ok=True)
    os.makedirs(FONTS_DIR, exist_ok=True)
