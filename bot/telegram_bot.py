import asyncio
import logging
import os
import sqlite3
import subprocess
import sys
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from config import Config
from logging_setup import configure_logging

configure_logging(Config.STORAGE_PATH)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

_gen_pl = asyncio.Lock()
_generation_in_progress = False


def get_latest_project_id():
    try:
        db_path = os.path.join(Config.STORAGE_PATH, "db.sqlite")
        conn = sqlite3.connect(db_path, timeout=Config.SQLITE_BUSY_TIMEOUT_SEC)
        cur = conn.cursor()
        cur.execute("SELECT id FROM projects ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except (sqlite3.Error, OSError):
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 *AI Video Studio Bot* готова!\nЖду команду:\n`/new [Тема]`",
        parse_mode="Markdown",
    )


async def new_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _generation_in_progress

    if not context.args:
        await update.message.reply_text("⚠️ Укажите тему. Пример:\n/new Загадка Сфинкса")
        return

    async with _gen_pl:
        if _generation_in_progress:
            await update.message.reply_text(
                "⏳ Уже выполняется генерация видео. Дождитесь окончания или отмените процесс."
            )
            return
        _generation_in_progress = True

    topic = " ".join(context.args)
    msg = await update.message.reply_text(
        f"⏳ *Генерация запущена*\nТема: `{topic}`\n\n"
        f"1️⃣ Пишем сценарий...\n2️⃣ Генерируем голос...\n3️⃣ Монтируем видео...\n4️⃣ Рисуем превью...",
        parse_mode="Markdown",
    )

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def run_generator():
        cmd = [sys.executable, "main.py", "--topic", topic, "--format", "short"]
        v = (os.getenv("EDGE_TTS_CLI_VOICE") or "").strip()
        if v:
            cmd.extend(["--voice", v])
        subprocess.run(cmd, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        await asyncio.to_thread(run_generator)

        proj_id = get_latest_project_id()
        if proj_id:
            project_dir = os.path.join(Config.STORAGE_PATH, "projects", str(proj_id))
            video_path = os.path.join(project_dir, f"FINAL_CINEMATIC_{proj_id}.mp4")
            thumb_path = os.path.join(project_dir, f"THUMBNAIL_{proj_id}.jpg")
            seo_path = os.path.join(project_dir, "seo.txt")

            if os.path.exists(video_path):
                await msg.edit_text("✅ *Проект успешно завершен!* Отправляю файлы...", parse_mode="Markdown")

                if os.path.exists(thumb_path):
                    with open(thumb_path, "rb") as f:
                        await update.message.reply_photo(photo=f, caption="🖼 Ваше превью для YouTube (Thumbnail)")

                with open(video_path, "rb") as f:
                    await update.message.reply_video(video=f, caption=f"🎬 Готовое видео: {topic}")

                if os.path.exists(seo_path):
                    with open(seo_path, "r", encoding="utf-8") as f:
                        seo_text = f.read()
                    await update.message.reply_text(
                        f"📝 *SEO Метаданные (Скопируйте на YouTube):*\n\n{seo_text}",
                        parse_mode="Markdown",
                    )
            else:
                await msg.edit_text("❌ Ошибка сборки видео.")
        else:
            await msg.edit_text("❌ Ошибка базы данных.")
    finally:
        async with _gen_pl:
            _generation_in_progress = False


def main():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не найден в .env")
        return
    logger.info("Запуск Telegram-бота")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_video))
    app.run_polling()


if __name__ == "__main__":
    main()
