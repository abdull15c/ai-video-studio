import json
import logging
import time
import schedule
import os
import subprocess
import sys
from datetime import datetime, timezone
from config import Config
from logging_setup import configure_logging
from modules.trend_parser import get_daily_topic, add_topic_to_queue

logger = logging.getLogger(__name__)

AUTOPILOT_STATE_PATH = os.path.join(Config.STORAGE_PATH, "autopilot_state.json")


def _write_autopilot_state(next_run=None, running=True):
    os.makedirs(Config.STORAGE_PATH, exist_ok=True)
    payload = {
        "running": running,
        "next_run_iso": next_run.isoformat() if next_run else None,
        "schedule_label": os.getenv("AUTOPILOT_SCHEDULE_LABEL", "ежедневно 03:00"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(AUTOPILOT_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("Не удалось записать autopilot_state.json: %s", e)


def night_shift():
    configure_logging(Config.STORAGE_PATH)
    logger.info("Ночная смена автопилота")
    topic = get_daily_topic()
    add_topic_to_queue(topic)

    logger.info("Запуск конвейера: %r", topic)
    cmd = [sys.executable, "main.py", "--topic", topic, "--format", "short"]
    v = (os.getenv("EDGE_TTS_CLI_VOICE") or "").strip()
    if v:
        cmd.extend(["--voice", v])
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(cmd, cwd=root, text=True)
        if result.returncode == 0:
            logger.info("Ночная сборка завершена успешно")
        else:
            logger.error("Ночная сборка завершилась с кодом %s", result.returncode)
    except Exception as e:
        logger.exception("Сбой автопилота: %s", e)


def main():
    configure_logging(Config.STORAGE_PATH)
    logger.info("Автопилот AI Video Studio (24/7)")
    run_time = os.getenv("AUTOPILOT_RUN_TIME", "03:00")
    schedule.every().day.at(run_time).do(night_shift)
    logger.info("Расписание: каждый день в %s", run_time)

    _write_autopilot_state(next_run=schedule.next_run(), running=True)

    try:
        while True:
            schedule.run_pending()
            _write_autopilot_state(next_run=schedule.next_run(), running=True)
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Автопилот остановлен пользователем.")
        _write_autopilot_state(next_run=schedule.next_run(), running=False)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        night_shift()
    else:
        main()
