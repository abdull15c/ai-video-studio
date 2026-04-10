import logging
import sqlite3
from config import Config

logger = logging.getLogger(__name__)


class ScriptDataError(ValueError):
    """Сценарий проекта пустой или без сцен — дальнейшие шаги бессмысленны."""


PIPELINE_STEPS = ("script", "voice", "videos", "subtitles", "montage", "audio", "seo")


def get_connection():
    conn = sqlite3.connect(Config.DB_PATH, timeout=Config.SQLITE_BUSY_TIMEOUT_SEC)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executescript('''
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        status TEXT DEFAULT 'created',
        format TEXT NOT NULL,
        preset TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        number INTEGER,
        title TEXT,
        status TEXT DEFAULT 'pending',
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    CREATE TABLE IF NOT EXISTS scenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chapter_id INTEGER,
        number INTEGER,
        narration TEXT,
        image_prompt TEXT,
        mood TEXT,
        camera TEXT,
        duration_sec REAL,
        audio_path TEXT,
        FOREIGN KEY(chapter_id) REFERENCES chapters(id)
    );
    CREATE TABLE IF NOT EXISTS checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        step TEXT,
        status TEXT,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    ''')
    try:
        cursor.execute("ALTER TABLE scenes ADD COLUMN audio_path TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN tts_voice TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN error_message TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN tts_engine TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN tts_prompt TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN image_engine TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN avatar_type TEXT DEFAULT 'none'")
    except sqlite3.OperationalError:
        pass

    # New fields for projects
    for col, def_val in [
        ("style_preset", "TEXT"),
        ("subtitle_style", "TEXT"),
        ("voice_profile", "TEXT"),
        ("quality_mode", "TEXT"),
        ("manual_review_required", "BOOLEAN DEFAULT 0"),
        ("keep_intermediates", "BOOLEAN DEFAULT 0"),
        ("final_quality_score", "REAL"),
        ("quality_report_json", "TEXT"),
        ("review_notes", "TEXT"),
        ("audio_mastering_preset", "TEXT")
    ]:
        try:
            cursor.execute(f"ALTER TABLE projects ADD COLUMN {col} {def_val}")
        except sqlite3.OperationalError:
            pass

    # New fields for scenes
    for col, def_val in [
        ("scene_goal", "TEXT"),
        ("visual_role", "TEXT"),
        ("shot_type", "TEXT"),
        ("motion_type", "TEXT"),
        ("transition_in", "TEXT"),
        ("transition_out", "TEXT"),
        ("intensity", "TEXT"),
        ("continuity_anchor", "TEXT"),
        ("visual_score", "REAL"),
        ("audio_score", "REAL"),
        ("subtitle_score", "REAL"),
        ("scene_status", "TEXT DEFAULT 'pending'"),
        ("manual_override_json", "TEXT"),
        ("scene_role", "TEXT"),
        ("energy_curve", "TEXT"),
        ("edit_density", "TEXT"),
        ("semantic_match_score", "REAL"),
        ("motion_score", "REAL"),
        ("technical_score", "REAL"),
        ("continuity_score", "REAL"),
        ("review_reason", "TEXT")
    ]:
        try:
            cursor.execute(f"ALTER TABLE scenes ADD COLUMN {col} {def_val}")
        except sqlite3.OperationalError:
            pass
            
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS project_quality_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        overall_score REAL,
        weak_scene_count INTEGER,
        avg_visual_score REAL,
        avg_audio_score REAL,
        avg_subtitle_score REAL,
        warnings_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tts_usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        scene_id INTEGER,
        engine TEXT,
        model TEXT,
        voice TEXT,
        chars_count INTEGER,
        duration_sec REAL,
        estimated_cost_usd REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scene_generation_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scene_id INTEGER,
        attempt_no INTEGER,
        query_json TEXT,
        selected_asset_path TEXT,
        visual_score REAL,
        failure_reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(scene_id) REFERENCES scenes(id)
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS llm_usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        step TEXT,
        provider TEXT,
        model TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        estimated_cost_usd REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    """)
    conn.commit()
    conn.close()


def create_project(
    title,
    format_type,
    preset,
    tts_voice=None,
    tts_engine=None,
    image_engine=None,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM projects WHERE title = ? AND status != 'completed' ORDER BY id DESC LIMIT 1",
        (title,),
    )
    row = cursor.fetchone()
    te = str(tts_engine).strip().lower() if tts_engine is not None else None
    ie = Config.normalize_image_engine(image_engine) if image_engine is not None else None

    if row:
        project_id = row[0]
        sets = []
        params = []
        if tts_voice is not None:
            sets.append("tts_voice = ?")
            params.append(tts_voice)
        if te is not None:
            sets.append("tts_engine = ?")
            params.append(te)
        if ie is not None:
            sets.append("image_engine = ?")
            params.append(ie)
        if sets:
            params.append(project_id)
            cursor.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
        conn.close()
        return project_id

    cursor.execute(
        "INSERT INTO projects (title, format, preset, tts_voice, tts_engine, image_engine) VALUES (?, ?, ?, ?, ?, ?)",
        (title, format_type, preset, tts_voice, te, ie),
    )
    project_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return project_id

def get_project_format(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT format FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "main"


def get_project_tts_engine(project_id):
    """Движок TTS проекта или глобальный TTS_ENGINE из Config."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tts_engine FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] and str(row[0]).strip():
        return str(row[0]).strip().lower()
    return (Config.TTS_ENGINE or "").strip().lower()


def update_project_tts_engine(project_id, engine: str):
    e = str(engine or "").strip().lower()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE projects SET tts_engine = ? WHERE id = ?", (e, project_id))
    conn.commit()
    conn.close()
    return e


def get_project_image_engine(project_id):
    """Режим картинок/стока проекта или IMAGE_ENGINE из Config."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT image_engine FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] and str(row[0]).strip():
        return Config.normalize_image_engine(row[0])
    return Config.IMAGE_ENGINE


def update_project_image_engine(project_id, image_engine: str):
    eng = Config.normalize_image_engine(image_engine)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE projects SET image_engine = ? WHERE id = ?", (eng, project_id))
    conn.commit()
    conn.close()
    return eng


def get_project_tts_prompt(project_id):
    """Стилевой промпт для Gemini TTS или дефолт из Config."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tts_prompt FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] and str(row[0]).strip():
        return str(row[0]).strip()
    return Config.GOOGLE_TTS_PROMPT


def get_project_tts_voice(project_id):
    """Сохранённый голос: для Google — как в БД/Config; для Edge — нормализация каталога."""
    engine = get_project_tts_engine(project_id)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tts_voice FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    raw = row[0] if row and row[0] else None
    if engine.startswith("google-"):
        v = (raw or Config.GOOGLE_TTS_VOICE or "Kore").strip()
        return v
    if raw:
        return Config.normalize_edge_tts_voice(raw)
    return Config.normalize_edge_tts_voice(Config.EDGE_TTS_VOICE)


def update_project_tts_voice(project_id, voice_id):
    v = Config.normalize_edge_tts_voice(voice_id)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE projects SET tts_voice = ? WHERE id = ?", (v, project_id))
    conn.commit()
    conn.close()
    return v


def log_scene_generation_attempt(
    scene_id: int,
    query_json: str,
    selected_asset_path: str,
    visual_score: float = None,
    failure_reason: str = None
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM scene_generation_attempts WHERE scene_id = ?", (scene_id,))
    attempt_no = cursor.fetchone()[0]
    
    cursor.execute(
        """
        INSERT INTO scene_generation_attempts (
            scene_id, attempt_no, query_json, selected_asset_path, visual_score, failure_reason
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (scene_id, attempt_no, query_json, selected_asset_path, visual_score, failure_reason)
    )
    conn.commit()
    conn.close()

def log_tts_usage(
    project_id,
    scene_id,
    engine,
    model,
    voice,
    chars_count,
    duration_sec,
    estimated_cost_usd,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tts_usage_log (
            project_id, scene_id, engine, model, voice,
            chars_count, duration_sec, estimated_cost_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            scene_id,
            engine,
            model,
            voice,
            chars_count,
            duration_sec,
            float(estimated_cost_usd),
        ),
    )
    conn.commit()
    conn.close()


def log_llm_usage(
    project_id,
    step,
    provider,
    model,
    input_tokens,
    output_tokens,
    estimated_cost_usd,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO llm_usage_log (
            project_id, step, provider, model,
            input_tokens, output_tokens, estimated_cost_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            step,
            provider,
            model,
            int(input_tokens or 0),
            int(output_tokens or 0),
            float(estimated_cost_usd or 0),
        ),
    )
    conn.commit()
    conn.close()


def sum_llm_usage_estimated_usd_for_project(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM llm_usage_log WHERE project_id = ?",
        (project_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return float(row[0] or 0) if row else 0.0


def has_checkpoint(project_id, step):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM checkpoints WHERE project_id = ? AND step = ? AND status = 'done'", (project_id, step))
    row = cursor.fetchone()
    conn.close()
    return bool(row)

def save_checkpoint(project_id, step):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO checkpoints (project_id, step, status) VALUES (?, ?, 'done')", (project_id, step))
    conn.commit()
    conn.close()

def mark_project_completed(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE projects SET status = 'completed', error_message = NULL WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()


def mark_project_processing(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE projects SET status = 'processing', error_message = NULL WHERE id = ?",
        (project_id,),
    )
    conn.commit()
    conn.close()

def mark_project_paused_for_review(project_id, message: str):
    msg = (message or "")[:4000]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE projects SET status = 'paused_for_review', error_message = ? WHERE id = ?",
        (msg, project_id),
    )
    conn.commit()
    conn.close()

def mark_project_failed(project_id, message: str):
    msg = (message or "")[:4000]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE projects SET status = 'failed', error_message = ? WHERE id = ?",
        (msg, project_id),
    )
    conn.commit()
    conn.close()


def insert_dashboard_project(title, format_type, tts_voice=None, preset="default"):
    """Новая строка проекта для дашборда (без дедупликации по названию)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO projects (title, format, preset, status, tts_voice) VALUES (?, ?, ?, 'pending', ?)",
        (title, format_type, preset, tts_voice),
    )
    project_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return project_id


def get_project_row(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, title, status, format, preset, tts_voice, created_at, error_message, avatar_type,
                  style_preset, subtitle_style, voice_profile, quality_mode, manual_review_required,
                  keep_intermediates, final_quality_score, quality_report_json, review_notes, audio_mastering_preset
           FROM projects WHERE id = ?""",
        (project_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "status": row[2],
        "format": row[3],
        "preset": row[4],
        "tts_voice": row[5],
        "created_at": row[6],
        "error_message": row[7],
        "avatar_type": row[8] if len(row) > 8 else "none",
        "style_preset": row[9] if len(row) > 9 else "default",
        "subtitle_style": row[10] if len(row) > 10 else "default",
        "voice_profile": row[11] if len(row) > 11 else "default",
        "quality_mode": row[12] if len(row) > 12 else "standard",
        "manual_review_required": bool(row[13]) if len(row) > 13 else False,
        "keep_intermediates": bool(row[14]) if len(row) > 14 else False,
        "final_quality_score": row[15] if len(row) > 15 else None,
        "quality_report_json": row[16] if len(row) > 16 else None,
        "review_notes": row[17] if len(row) > 17 else None,
        "audio_mastering_preset": row[18] if len(row) > 18 else None,
    }


def update_project_avatar(project_id, avatar_type):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE projects SET avatar_type = ? WHERE id = ?", (avatar_type, project_id))
    conn.commit()
    conn.close()


def get_checkpoint_steps(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT step FROM checkpoints WHERE project_id = ? AND status = 'done' ORDER BY id",
        (project_id,),
    )
    steps = [r[0] for r in cursor.fetchall()]
    conn.close()
    return steps


def derive_project_ui_status(row, checkpoint_steps):
    """Унифицированный статус для UI: completed | failed | processing | pending | queued | paused_for_review."""
    st = row["status"] or "created"
    cps = set(checkpoint_steps)
    if st == "completed":
        return "completed"
    if st == "failed":
        return "failed"
    if st == "paused_for_review":
        return "paused_for_review"
    if st == "processing":
        return "processing"
    if st == "pending":
        return "queued"
    if st == "created" and cps:
        return "processing"
    if st == "created":
        return "pending"
    return "pending"


def get_current_pipeline_step(checkpoint_steps):
    """Следующий шаг после последнего чекпоинта (или первый, если пусто)."""
    done = set(checkpoint_steps)
    for s in PIPELINE_STEPS:
        if s not in done:
            return s
    return None


def list_projects_paginated(
    page=1,
    page_size=20,
    status_filter=None,
    format_filter=None,
    search=None,
    sort_desc=True,
):
    """
    status_filter: all | completed | failed | processing | queued | pending | paused_for_review
    queued = только status pending (дашборд, ещё не стартовал)
    pending = created без чекпоинтов (старый CLI)
    processing = processing или created с чекпоинтами
    """
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    offset = (page - 1) * page_size

    conn = get_connection()
    cursor = conn.cursor()

    where = ["1=1"]
    params = []

    if format_filter and format_filter in ("short", "main", "long"):
        where.append("p.format = ?")
        params.append(format_filter)

    if search and search.strip():
        where.append("p.title LIKE ?")
        params.append(f"%{search.strip()}%")

    if status_filter and status_filter != "all":
        if status_filter == "completed":
            where.append("p.status = 'completed'")
        elif status_filter == "failed":
            where.append("p.status = 'failed'")
        elif status_filter == "paused_for_review":
            where.append("p.status = 'paused_for_review'")
        elif status_filter == "queued":
            where.append("p.status = 'pending'")
        elif status_filter == "pending":
            where.append(
                """p.status = 'created' AND NOT EXISTS (
                SELECT 1 FROM checkpoints c WHERE c.project_id = p.id)"""
            )
        elif status_filter == "processing":
            where.append(
                """(
                p.status = 'processing'
                OR (p.status = 'created' AND EXISTS (SELECT 1 FROM checkpoints c WHERE c.project_id = p.id))
            )"""
            )

    where_sql = " AND ".join(where)
    order = "DESC" if sort_desc else "ASC"

    cursor.execute(f"SELECT COUNT(*) FROM projects p WHERE {where_sql}", params)
    total = cursor.fetchone()[0]

    cursor.execute(
        f"""
        SELECT p.id, p.title, p.status, p.format, p.tts_voice, p.created_at, p.error_message, p.avatar_type,
               p.style_preset, p.subtitle_style, p.voice_profile, p.quality_mode, p.manual_review_required,
               p.keep_intermediates, p.final_quality_score, p.quality_report_json, p.review_notes, p.audio_mastering_preset
        FROM projects p
        WHERE {where_sql}
        ORDER BY p.created_at {order}
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    )
    rows = cursor.fetchall()
    conn.close()

    items = []
    for r in rows:
        pid = r[0]
        row_dict = {
            "id": r[0],
            "title": r[1],
            "status": r[2],
            "format": r[3],
            "tts_voice": r[4],
            "created_at": r[5],
            "error_message": r[6],
            "avatar_type": r[7] if len(r) > 7 else "none",
            "style_preset": r[8] if len(r) > 8 else "default",
            "subtitle_style": r[9] if len(r) > 9 else "default",
            "voice_profile": r[10] if len(r) > 10 else "default",
            "quality_mode": r[11] if len(r) > 11 else "standard",
            "manual_review_required": bool(r[12]) if len(r) > 12 else False,
            "keep_intermediates": bool(r[13]) if len(r) > 13 else False,
            "final_quality_score": r[14] if len(r) > 14 else None,
            "quality_report_json": r[15] if len(r) > 15 else None,
            "review_notes": r[16] if len(r) > 16 else None,
            "audio_mastering_preset": r[17] if len(r) > 17 else None,
        }
        cps = get_checkpoint_steps(pid)
        items.append(
            {
                **row_dict,
                "ui_status": derive_project_ui_status(row_dict, cps),
                "checkpoints": cps,
                "current_step": get_current_pipeline_step(cps)
                if row_dict["status"] not in ("completed", "failed")
                else None,
            }
        )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def count_projects_stats():
    """Сводные числа для дашборда."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM projects")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM projects WHERE status = 'completed'")
    completed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM projects WHERE status = 'failed'")
    failed = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT COUNT(*) FROM projects p
        WHERE p.status NOT IN ('completed', 'failed')
        AND (
            p.status = 'processing'
            OR p.status = 'paused_for_review'
            OR p.status = 'pending'
            OR (p.status = 'created' AND EXISTS (SELECT 1 FROM checkpoints c WHERE c.project_id = p.id))
        )
        """
    )
    in_progress = cursor.fetchone()[0]
    conn.close()
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "in_progress": in_progress,
    }


def save_script_to_db(project_id, script_data):
    conn = get_connection()
    cursor = conn.cursor()
    for chapter in script_data.get('chapters', []):
        cursor.execute("INSERT INTO chapters (project_id, number, title) VALUES (?, ?, ?)",
                       (project_id, chapter['chapter_number'], chapter['chapter_title']))
        chapter_id = cursor.lastrowid
        for scene in chapter.get('scenes', []):
            cursor.execute('''INSERT INTO scenes (
                                  chapter_id, number, narration, image_prompt, mood, camera, duration_sec,
                                  scene_goal, visual_role, shot_type, motion_type, transition_in, transition_out, intensity, continuity_anchor,
                                  scene_role, energy_curve, edit_density
                              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                           (
                               chapter_id, 
                               scene.get('scene_number'), 
                               scene.get('narration'), 
                               scene.get('image_prompt'), 
                               scene.get('mood'), 
                               scene.get('camera'), 
                               scene.get('duration_sec'),
                               scene.get('scene_goal'),
                               scene.get('visual_role'),
                               scene.get('shot_type'),
                               scene.get('motion_type'),
                               scene.get('transition_in'),
                               scene.get('transition_out'),
                               str(scene.get('intensity', '')),
                               scene.get('continuity_anchor'),
                               scene.get('scene_role'),
                               scene.get('energy_curve'),
                               scene.get('edit_density')
                           ))
    conn.commit()
    conn.close()


def validate_script_data(script_data):
    """Возвращает (ok, сообщение_об_ошибке)."""
    if not script_data:
        return False, "нет глав в сценарии"
    total = sum(len(ch.get("scenes") or []) for ch in script_data)
    if total == 0:
        return False, "нет ни одной сцены"
    return True, ""


def get_project_preset(project_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT preset FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "default"

def get_project_script(project_id, require_scenes=True):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, number, title FROM chapters WHERE project_id = ? ORDER BY number", (project_id,))
    chapters = cursor.fetchall()
    result = []
    for ch_id, ch_num, ch_title in chapters:
        cursor.execute(
            """SELECT id, number, narration, image_prompt, mood, camera, duration_sec,
                      scene_goal, visual_role, shot_type, motion_type, transition_in, transition_out, intensity, continuity_anchor,
                      scene_role, energy_curve, edit_density, scene_status, review_reason
               FROM scenes WHERE chapter_id = ? ORDER BY number""",
            (ch_id,),
        )
        scenes_raw = cursor.fetchall()
        scenes = [
            {
                "id": r[0],
                "number": r[1],
                "narration": r[2],
                "image_prompt": r[3],
                "mood": r[4],
                "camera": r[5],
                "duration_sec": r[6],
                "scene_goal": r[7],
                "visual_role": r[8],
                "shot_type": r[9],
                "motion_type": r[10],
                "transition_in": r[11],
                "transition_out": r[12],
                "intensity": r[13],
                "continuity_anchor": r[14],
                "scene_role": r[15],
                "energy_curve": r[16],
                "edit_density": r[17],
                "scene_status": r[18],
                "review_reason": r[19],
            }
            for r in scenes_raw
        ]
        result.append({"id": ch_id, "number": ch_num, "title": ch_title, "scenes": scenes})
    conn.close()
    if require_scenes:
        ok, err = validate_script_data(result)
        if not ok:
            logger.error("Проект %s: сценарий невалиден: %s", project_id, err)
            raise ScriptDataError(f"Проект {project_id}: сценарий невалиден ({err})")
    return result

def update_scene_audio(scene_id, path):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE scenes SET audio_path = ? WHERE id = ?", (path, scene_id))
    conn.commit()
    conn.close()

def update_scene_fields(scene_id: int, fields: dict):
    if not fields:
        return
    conn = get_connection()
    cursor = conn.cursor()
    
    set_clauses = []
    params = []
    
    allowed_fields = {
        "narration", "image_prompt", "mood", "camera", "duration_sec",
        "scene_goal", "visual_role", "shot_type", "motion_type",
        "transition_in", "transition_out", "intensity", "continuity_anchor",
        "scene_status", "manual_override_json", "scene_role", "energy_curve", 
        "edit_density", "semantic_match_score", "motion_score", "technical_score",
        "continuity_score", "review_reason", "visual_score", "audio_score", "subtitle_score"
    }
    
    for k, v in fields.items():
        if k in allowed_fields:
            set_clauses.append(f"{k} = ?")
            params.append(v)
            
    if set_clauses:
        params.append(scene_id)
        sql = f"UPDATE scenes SET {', '.join(set_clauses)} WHERE id = ?"
        cursor.execute(sql, params)
        conn.commit()
    conn.close()

def get_scene_by_id(scene_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, chapter_id, number, narration, image_prompt, mood, camera, duration_sec,
                  scene_goal, visual_role, shot_type, motion_type, transition_in, transition_out, intensity, continuity_anchor,
                  visual_score, audio_score, subtitle_score, scene_status, manual_override_json
           FROM scenes WHERE id = ?""",
        (scene_id,)
    )
    r = cursor.fetchone()
    conn.close()
    if not r:
        return None
    return {
        "id": r[0],
        "chapter_id": r[1],
        "number": r[2],
        "narration": r[3],
        "image_prompt": r[4],
        "mood": r[5],
        "camera": r[6],
        "duration_sec": r[7],
        "scene_goal": r[8],
        "visual_role": r[9],
        "shot_type": r[10],
        "motion_type": r[11],
        "transition_in": r[12],
        "transition_out": r[13],
        "intensity": r[14],
        "continuity_anchor": r[15],
        "visual_score": r[16],
        "audio_score": r[17],
        "subtitle_score": r[18],
        "scene_status": r[19],
        "manual_override_json": r[20],
    }

def get_project_id_by_scene(scene_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT c.project_id FROM chapters c JOIN scenes s ON s.chapter_id = c.id WHERE s.id = ?", (scene_id,))
    r = cursor.fetchone()
    conn.close()
    return r[0] if r else None

