import logging
import os
import subprocess
import shutil
import sys
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def get_chrome_path():
    if sys.platform == "win32":
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
        ]
        for p in paths:
            if os.path.isfile(p):
                return p
        return None

    if sys.platform == "darwin":
        mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return mac if os.path.isfile(mac) else None

    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def launch_profile(profile_number, url="https://grok.com"):
    try:
        n = int(profile_number)
    except (TypeError, ValueError):
        logger.warning("Некорректный номер профиля")
        return False
    if n < 0:
        logger.warning("Некорректный номер профиля")
        return False

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        logger.warning("Разрешены только URL с протоколом http(s)")
        return False

    chrome_path = get_chrome_path()
    if not chrome_path:
        logger.error("Chrome/Chromium не найден — установите браузер или добавьте в PATH")
        return False

    profiles_dir = os.path.abspath(f"./storage/browser_profiles/Profile_{n}")
    os.makedirs(profiles_dir, exist_ok=True)

    logger.info("Запуск изолированного профиля браузера №%s", n)

    cmd = [
        chrome_path,
        f"--user-data-dir={profiles_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        url,
    ]

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
