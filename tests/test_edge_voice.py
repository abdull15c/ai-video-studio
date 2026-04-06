from config import Config


def test_normalize_known_voice():
    assert Config.normalize_edge_tts_voice("ru-RU-SvetlanaNeural") == "ru-RU-SvetlanaNeural"


def test_normalize_unknown_falls_back():
    bad = Config.normalize_edge_tts_voice("ru-RU-NonexistentNeural")
    assert bad in Config.EDGE_TTS_VOICE_IDS
