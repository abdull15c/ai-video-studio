import sys
from google.auth import exceptions as auth_exc

import config


def test_lazy_client_not_created_on_import(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Убираем кэш модуля, чтобы повторно проверить состояние после импорта
    for key in list(sys.modules.keys()):
        if key == "modules.google_tts" or key.endswith(".google_tts"):
            del sys.modules[key]
    import modules.google_tts as gt

    assert gt._TTS_CLIENT is None
    assert gt._AUTH_SESSION is None


def test_routing_calls_gemini(monkeypatch, tmp_path):
    import modules.google_tts as gt

    called = []

    def fake_gemini(text, voice, prompt, out_path):
        called.append("gemini")
        open(out_path, "wb").write(b"ID3")
        return True

    monkeypatch.setattr(config.Config, "TTS_ENGINE", "google-gemini")
    monkeypatch.setattr(gt, "_synthesize_gemini", fake_gemini)
    monkeypatch.setattr(gt, "_synthesize_chirp", lambda *a, **k: (_ for _ in ()).throw(AssertionError("chirp")))
    monkeypatch.setattr(gt, "_synthesize_neural2", lambda *a, **k: (_ for _ in ()).throw(AssertionError("neural2")))
    p = tmp_path / "a.mp3"
    assert gt.generate_audio_google("hello", "Kore", str(p)) is True
    assert called == ["gemini"]


def test_routing_calls_chirp(monkeypatch, tmp_path):
    import modules.google_tts as gt

    called = []

    def fake_chirp(text, voice, rate, out_path):
        called.append("chirp")
        open(out_path, "wb").write(b"ID3")
        return True

    monkeypatch.setattr(config.Config, "TTS_ENGINE", "google-chirp")
    monkeypatch.setattr(gt, "_synthesize_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gemini")))
    monkeypatch.setattr(gt, "_synthesize_chirp", fake_chirp)
    monkeypatch.setattr(gt, "_synthesize_neural2", lambda *a, **k: (_ for _ in ()).throw(AssertionError("neural2")))
    p = tmp_path / "b.mp3"
    assert gt.generate_audio_google("hello", "ru-RU-Chirp3-HD-F", str(p)) is True
    assert called == ["chirp"]


def test_routing_calls_neural2(monkeypatch, tmp_path):
    import modules.google_tts as gt

    called = []

    def fake_n2(text, voice, rate, out_path):
        called.append("neural2")
        open(out_path, "wb").write(b"ID3")
        return True

    monkeypatch.setattr(config.Config, "TTS_ENGINE", "google-neural2")
    monkeypatch.setattr(gt, "_synthesize_gemini", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gemini")))
    monkeypatch.setattr(gt, "_synthesize_chirp", lambda *a, **k: (_ for _ in ()).throw(AssertionError("chirp")))
    monkeypatch.setattr(gt, "_synthesize_neural2", fake_n2)
    p = tmp_path / "c.mp3"
    assert gt.generate_audio_google("hello", "ru-RU-Neural2-D", str(p)) is True
    assert called == ["neural2"]


def test_gemini_cost_estimate_range():
    import modules.google_tts as gt

    # 1000 символов, ~20 с аудио
    cost = gt.estimate_gemini_cost_usd(1000, 20.0)
    assert 0.001 <= cost <= 0.01


def test_default_credentials_error_no_file(monkeypatch, tmp_path):
    import modules.google_tts as gt

    def boom(*a, **k):
        raise auth_exc.DefaultCredentialsError("no creds")

    monkeypatch.setattr(config.Config, "TTS_ENGINE", "google-chirp")
    monkeypatch.setattr(gt, "_synthesize_chirp", boom)
    p = tmp_path / "fail.mp3"
    assert gt.generate_audio_google("text", "ru-RU-Chirp3-HD-F", str(p)) is False
    assert not p.is_file()


def test_non_google_engine_returns_false(monkeypatch, tmp_path, caplog):
    import logging
    import modules.google_tts as gt

    monkeypatch.setattr(config.Config, "TTS_ENGINE", "edge")
    caplog.set_level(logging.ERROR)
    p = tmp_path / "n.mp3"
    assert gt.generate_audio_google("x", "v", str(p)) is False
    assert "Google TTS вызван" in caplog.text or "TTS_ENGINE" in caplog.text
