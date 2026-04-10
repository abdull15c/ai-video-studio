import argparse
import logging
import sys
from config import Config
from database import (
    ScriptDataError,
    create_project,
    get_project_row,
    get_project_tts_voice,
    has_checkpoint,
    init_db,
    mark_project_completed,
    mark_project_failed,
    mark_project_processing,
    save_checkpoint,
    update_project_image_engine,
    update_project_tts_engine,
)
from logging_setup import configure_logging
from modules.script_generator import generate_script
from modules.voiceover import generate_voiceover
from modules.video_generator import generate_videos
from modules.subtitles import generate_subtitles
from modules.montage import render_project
from modules.audio_engine import add_background_music
from modules.seo_generator import generate_seo
from modules.cleanup import cleanup_project_temp_files
from modules.pipeline_checks import (
    validate_after_subtitles,
    validate_after_videos,
    validate_after_voice,
)

logger = logging.getLogger(__name__)

_STEP_VALIDATORS = {
    "voice": validate_after_voice,
    "videos": validate_after_videos,
    "subtitles": validate_after_subtitles,
}


def run_step(step_name, func, project_id, *args):
    if has_checkpoint(project_id, step_name):
        logger.info("Шаг «%s» уже отмечен выполненным (checkpoint), project_id=%s", step_name, project_id)
        return True
    ok = func(project_id, *args)
    if ok:
        vfn = _STEP_VALIDATORS.get(step_name)
        if vfn:
            vok, verr = vfn(project_id)
            if not vok:
                logger.error(
                    "Проверка после шага «%s» не прошла (project_id=%s): %s — checkpoint не записан",
                    step_name,
                    project_id,
                    verr,
                )
                return False
        save_checkpoint(project_id, step_name)
        logger.info("Шаг «%s» выполнен, checkpoint сохранён, project_id=%s", step_name, project_id)
        return True
    logger.error(
        "Шаг «%s» вернул False (сбой без исключения), project_id=%s — checkpoint не записан",
        step_name,
        project_id,
    )
    return False


def check_manual_review_status(project_id) -> bool:
    """Возвращает True, если есть сцены, ожидающие ручного ревью или ретрая."""
    from database import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM scenes c JOIN chapters ch ON c.chapter_id = ch.id WHERE ch.project_id = ? AND c.scene_status IN ('needs_review', 'retry_pending')", (project_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0

def run_pipeline_steps(
    project_id,
    topic,
    format_type,
    pure_stock=False,
    cli_voice=None,
    tts_engine=None,
    image_engine=None,
):
    """Выполняет все шаги конвейера для существующего project_id."""
    if tts_engine is not None:
        update_project_tts_engine(project_id, tts_engine)
    if image_engine is not None:
        update_project_image_engine(project_id, image_engine)
    mark_project_processing(project_id)
    logger.info("СТАРТ ПРОЕКТА id=%s: %s (формат: %s, голос CLI: %s)", project_id, topic, format_type, cli_voice or "—")

    from database import get_project_row
    row = get_project_row(project_id)
    keep_intermediates = row.get("keep_intermediates", False) if row else False

    try:
        if not run_step(
            "script",
            generate_script,
            project_id,
            topic,
            format_type,
            False,
            cli_voice,
        ):
            raise RuntimeError("Ошибка скрипта")
            
        # Voice generation
        if not run_step("voice", generate_voiceover, project_id):
            raise RuntimeError("Ошибка голоса")

        # Video generation & QC validation
        if not run_step("videos", generate_videos, project_id, pure_stock):
            raise RuntimeError("Ошибка видео (Visual QC не пройдена)")

        # STOP for manual review if required and any scene is marked as needs_review
        if check_manual_review_status(project_id):
            msg = "Пайплайн приостановлен: есть сцены с низким скором. Ожидается ручное ревью (manual_review_required=1)."
            logger.warning(msg)
            from database import mark_project_paused_for_review
            mark_project_paused_for_review(project_id, msg)
            return False

        if not run_step("subtitles", generate_subtitles, project_id):
            raise RuntimeError("Ошибка субтитров")
        if not run_step("montage", render_project, project_id):
            raise RuntimeError("Ошибка монтажа")
        if not run_step("audio", add_background_music, project_id):
            raise RuntimeError("Ошибка звука")
        if not run_step("seo", generate_seo, project_id, topic):
            raise RuntimeError("Ошибка SEO")

        if not keep_intermediates:
            cleanup_project_temp_files(project_id)
        else:
            logger.info("Пропуск очистки временных файлов: keep_intermediates=1")
            
        mark_project_completed(project_id)
        logger.info("ПРОЕКТ id=%s '%s' ПОЛНОСТЬЮ ЗАВЕРШЁН", project_id, topic)
        return True
    except ScriptDataError as e:
        logger.error("Сценарий project_id=%s: %s", project_id, e)
        mark_project_failed(project_id, str(e))
        print(f"\n[X] ОШИБКА СЦЕНАРИЯ: {e}")
        return False
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        logger.exception("❌ ОШИБКА ПАЙПЛАЙНА для проекта %s:\n%s", project_id, tb_str)
        # Записываем в БД полную ошибку с Traceback, обрезаем до 4000 символов
        mark_project_failed(project_id, f"{str(e)}\n\n{tb_str}"[:4000])
        print(f"\n[X] ОШИБКА: {e}")
        return False


def run_pipeline(
    topic,
    format_type,
    pure_stock=False,
    cli_voice=None,
    tts_engine=None,
    image_engine=None,
    style_preset=None,
    subtitle_style=None,
    voice_profile=None,
    quality_mode=None,
    keep_intermediates=False,
    review_required=False,
):
    """CLI: создаёт проект (с дедупликацией по названию) и запускает конвейер."""
    project_id = create_project(
        topic,
        format_type,
        "default",
        tts_voice=cli_voice,
        tts_engine=tts_engine,
        image_engine=image_engine,
    )

    # Save new settings directly into the database
    from database import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    updates = []
    params = []
    if style_preset:
        updates.append("style_preset = ?")
        params.append(style_preset)
    if subtitle_style:
        updates.append("subtitle_style = ?")
        params.append(subtitle_style)
    if voice_profile:
        updates.append("voice_profile = ?")
        params.append(voice_profile)
    if quality_mode:
        updates.append("quality_mode = ?")
        params.append(quality_mode)
    if keep_intermediates:
        updates.append("keep_intermediates = 1")
    if review_required:
        updates.append("manual_review_required = 1")

    if updates:
        params.append(project_id)
        cursor.execute(f"UPDATE projects SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    conn.close()

    return run_pipeline_steps(
        project_id,
        topic,
        format_type,
        pure_stock,
        cli_voice,
        tts_engine,
        image_engine,
    )


def run_pipeline_by_project_id(
    project_id,
    pure_stock=False,
    tts_engine=None,
    image_engine=None,
):
    """Запуск по id (дашборд): строка проекта уже есть в БД."""
    row = get_project_row(project_id)
    if not row:
        logger.error("Проект id=%s не найден", project_id)
        return False
    if row["status"] == "completed":
        logger.info("Проект id=%s уже завершён — пропуск", project_id)
        return True
    topic = row["title"]
    format_type = row["format"]
    voice_row = row.get("tts_voice")
    cli_voice = voice_row if voice_row else None
    return run_pipeline_steps(
        project_id,
        topic,
        format_type,
        pure_stock,
        cli_voice,
        tts_engine,
        image_engine,
    )


def main():
    configure_logging(Config.STORAGE_PATH)

    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", type=str)
    parser.add_argument("--format", type=str, choices=["short", "main", "long"], default="short")
    parser.add_argument("--pure-stock", action="store_true", help="Использовать ТОЛЬКО сток видео (без ИИ)")
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        choices=sorted(Config.EDGE_TTS_VOICE_IDS),
        metavar="VOICE_ID",
        help="Голос Edge-TTS (перекрывает выбор LLM). См. EDGE_TTS_VOICE_CATALOG в config.py",
    )
    parser.add_argument("--project-id", type=int, default=None, help="Запуск конвейера для существующего проекта (дашборд)")
    parser.add_argument(
        "--tts-engine",
        type=str,
        default=None,
        choices=["edge", "google-gemini", "google-chirp", "google-neural2"],
        help="Движок озвучки в БД для проекта (перекрывает TTS_ENGINE из .env)",
    )
    parser.add_argument(
        "--image-engine",
        type=str,
        default=None,
        choices=["stock", "google-imagen", "hybrid"],
        help="Источник визуала в БД для проекта (перекрывает IMAGE_ENGINE из .env)",
    )
    parser.add_argument("--style-preset", type=str, choices=Config.STYLE_PRESETS, help="Стиль генерации сценария")
    parser.add_argument("--subtitle-style", type=str, choices=Config.SUBTITLE_STYLES, help="Стиль генерации субтитров")
    parser.add_argument("--voice-profile", type=str, choices=Config.VOICE_PROFILES, help="Профиль обработки голоса")
    parser.add_argument("--quality-mode", type=str, choices=["draft", "standard", "premium"], help="Режим качества (оценка сцен)")
    parser.add_argument("--keep-intermediates", action="store_true", help="Оставлять временные файлы (для отладки)")
    parser.add_argument("--review-required", action="store_true", help="Останавливать перед финальным рендером для проверки")
    parser.add_argument("--regenerate-scene", type=int, help="Перегенерировать конкретную сцену и выйти")
    parser.add_argument("--init-db", action="store_true")
    args = parser.parse_args()

    if args.init_db:
        init_db()
        logger.info("База данных инициализирована")
        return

    import os
    if args.regenerate_scene and args.project_id:
        target = os.environ.get("REGENERATE_TARGET", "voice")
        logger.info("Перегенерация сцены %s (таргет: %s) для проекта %s", args.regenerate_scene, target, args.project_id)
        from database import update_scene_fields
        if target == "voice":
            from modules.voiceover import generate_voiceover
            generate_voiceover(args.project_id, specific_scene_id=args.regenerate_scene)
            update_scene_fields(args.regenerate_scene, {"scene_status": "approved"})
        elif target == "visual":
            from modules.video_generator import generate_videos
            generate_videos(args.project_id, args.pure_stock, specific_scene_id=args.regenerate_scene)
            update_scene_fields(args.regenerate_scene, {"scene_status": "approved"})
        return

    if args.project_id is not None:
        ok = run_pipeline_by_project_id(
            args.project_id,
            args.pure_stock,
            tts_engine=args.tts_engine,
            image_engine=args.image_engine,
        )
        sys.exit(0 if ok else 1)

    if args.topic:
        ok = run_pipeline(
            args.topic,
            args.format,
            args.pure_stock,
            cli_voice=args.voice,
            tts_engine=args.tts_engine,
            image_engine=args.image_engine,
            style_preset=args.style_preset,
            subtitle_style=args.subtitle_style,
            voice_profile=args.voice_profile,
            quality_mode=args.quality_mode,
            keep_intermediates=args.keep_intermediates,
            review_required=args.review_required,
        )
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
