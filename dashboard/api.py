"""
Веб-дашборд AI Video Studio. Запуск:
  uvicorn dashboard.api:app --host 127.0.0.1 --port 8000
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from config import Config
from database import (
    update_project_avatar,
    PIPELINE_STEPS,
    count_projects_stats,
    derive_project_ui_status,
    get_checkpoint_steps,
    get_current_pipeline_step,
    get_project_row,
    get_project_script,
    init_db,
    insert_dashboard_project,
    list_projects_paginated,
    sum_llm_usage_estimated_usd_for_project,
    update_scene_fields,
    get_scene_by_id,
    get_project_id_by_scene,
)
from dashboard.models import (
    ChapterDetail,
    QueueAddRequest,
    NewProjectRequest,
    NewProjectResponse,
    PipelineStepState,
    ProjectDetailResponse,
    ProjectListItem,
    ProjectListResponse,
    ProjectLogsResponse,
    QueueReorderRequest,
    QueueResponse,
    QueueTopic,
    SceneDetail,
    StatsResponse,
    VoiceOption,
    VoicesResponse,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(Config.REPO_ROOT).resolve()
TOPICS_PATH = REPO_ROOT / Config.TOPICS_QUEUE_FILE
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
STORAGE = Path(Config.STORAGE_PATH).resolve()
LOG_PATH = STORAGE / "logs" / "pipeline.log"
AUTOPILOT_STATE_PATH = STORAGE / "autopilot_state.json"

def _autopilot_running() -> bool:
    try:
        import psutil

        for p in psutil.process_iter(["cmdline"]):
            try:
                cl = p.info["cmdline"] or []
                line = " ".join(cl)
                if "autopilot.py" in line and "python" in line.lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    if AUTOPILOT_STATE_PATH.is_file():
        try:
            with open(AUTOPILOT_STATE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("running") and data.get("updated_at"):
                return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


def _autopilot_meta():
    next_iso = None
    label = None
    if AUTOPILOT_STATE_PATH.is_file():
        try:
            with open(AUTOPILOT_STATE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            next_iso = data.get("next_run_iso")
            label = data.get("schedule_label")
        except (json.JSONDecodeError, OSError):
            pass
    return next_iso, label


def _video_meta(project_id: int):
    video_path = STORAGE / "projects" / str(project_id) / f"FINAL_CINEMATIC_{project_id}.mp4"
    size = None
    duration = None
    if video_path.is_file():
        size = video_path.stat().st_size
        try:
            r = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode == 0 and r.stdout.strip():
                duration = float(r.stdout.strip())
        except (subprocess.SubprocessError, ValueError, FileNotFoundError):
            pass
    return size, duration


def _build_progress(checkpoints_list, current_step, ui_status: str):
    done = set(checkpoints_list)
    if ui_status == "completed":
        return [PipelineStepState(step=s, state="done") for s in PIPELINE_STEPS]

    first_incomplete = get_current_pipeline_step(checkpoints_list)
    cur = current_step or first_incomplete
    prog = []
    for s in PIPELINE_STEPS:
        if s in done:
            prog.append(PipelineStepState(step=s, state="done"))
        elif ui_status == "failed" and s == first_incomplete:
            prog.append(PipelineStepState(step=s, state="error"))
        elif ui_status in ("processing", "queued", "pending") and cur and s == cur:
            prog.append(PipelineStepState(step=s, state="current"))
        else:
            prog.append(PipelineStepState(step=s, state="pending"))
    return prog


def _row_to_list_item(row_dict, cps) -> ProjectListItem:
    ui = derive_project_ui_status(row_dict, cps)
    cur = (
        get_current_pipeline_step(cps)
        if row_dict.get("status") not in ("completed", "failed")
        else None
    )
    prog = _build_progress(cps, cur, ui)
    return ProjectListItem(
        id=row_dict["id"],
        title=row_dict["title"],
        format=row_dict["format"],
        status=row_dict["status"],
        ui_status=ui,
        tts_voice=row_dict.get("tts_voice"),
        created_at=str(row_dict["created_at"]) if row_dict.get("created_at") is not None else None,
        checkpoints=cps,
        current_step=cur,
        error_message=row_dict.get("error_message"),
        progress=prog,
    )


app = FastAPI(title="AI Video Studio Dashboard", version="1.0")


@app.on_event("startup")
async def _startup_migrate():
    init_db()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index_page():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        return HTMLResponse("<h1>dashboard/frontend/index.html not found</h1>", status_code=404)
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/api/stats", response_model=StatsResponse)
async def api_stats():
    counts = count_projects_stats()
    du = shutil.disk_usage(str(STORAGE))
    next_iso, label = _autopilot_meta()
    return StatsResponse(
        total_projects=counts["total"],
        completed=counts["completed"],
        in_progress=counts["in_progress"],
        failed=counts["failed"],
        disk_free_bytes=du.free,
        disk_total_bytes=du.total,
        autopilot_running=_autopilot_running(),
        autopilot_next_run_iso=next_iso,
        autopilot_schedule_label=label,
    )


@app.get("/api/voices", response_model=VoicesResponse)
async def api_voices():
    return VoicesResponse(
        voices=[VoiceOption(id=v, description=d) for v, d in Config.EDGE_TTS_VOICE_CATALOG]
    )


@app.get("/api/projects", response_model=ProjectListResponse)
async def api_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all"),
    format_filter: Optional[str] = Query(None),
    q: str | None = None,
    sort_desc: bool = True,
):
    fmt = format_filter if format_filter in ("short", "main", "long") else None
    st = status if status in ("all", "completed", "failed", "processing", "queued", "pending") else "all"
    data = list_projects_paginated(
        page=page,
        page_size=page_size,
        status_filter=st,
        format_filter=fmt,
        search=q,
        sort_desc=sort_desc,
    )
    items = []
    for it in data["items"]:
        rd = {
            "id": it["id"],
            "title": it["title"],
            "status": it["status"],
            "format": it["format"],
            "tts_voice": it["tts_voice"],
            "created_at": it["created_at"],
            "error_message": it["error_message"],
        }
        items.append(_row_to_list_item(rd, it["checkpoints"]))
    return ProjectListResponse(
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        items=items,
    )


@app.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
async def api_project_detail(project_id: int):
    row = get_project_row(project_id)
    if not row:
        raise HTTPException(404, "Проект не найден")
    cps = get_checkpoint_steps(project_id)
    ui = derive_project_ui_status(row, cps)
    cur = get_current_pipeline_step(cps) if row["status"] not in ("completed", "failed") else None
    prog = _build_progress(cps, cur, ui)

    chapters_raw = get_project_script(project_id, require_scenes=False)

    chapters = [
        ChapterDetail(
            id=ch["id"],
            number=ch["number"],
            title=ch.get("title"),
            scenes=[
                SceneDetail(
                    id=sc["id"],
                    number=sc["number"],
                    narration=sc.get("narration"),
                    image_prompt=sc.get("image_prompt"),
                    mood=sc.get("mood"),
                    camera=sc.get("camera"),
                    duration_sec=sc.get("duration_sec"),
                    scene_goal=sc.get("scene_goal"),
                    visual_role=sc.get("visual_role"),
                    shot_type=sc.get("shot_type"),
                    motion_type=sc.get("motion_type"),
                    transition_in=sc.get("transition_in"),
                    transition_out=sc.get("transition_out"),
                    intensity=str(sc.get("intensity", "")),
                    continuity_anchor=sc.get("continuity_anchor"),
                    scene_status=sc.get("scene_status"),
                    manual_override_json=sc.get("manual_override_json"),
                )
                for sc in ch.get("scenes", [])
            ],
        )
        for ch in chapters_raw
    ]

    size, duration = _video_meta(project_id)
    llm_usd = sum_llm_usage_estimated_usd_for_project(project_id)
    thumb = STORAGE / "projects" / str(project_id) / f"THUMBNAIL_{project_id}.jpg"
    thumb_url = f"/api/projects/{project_id}/thumbnail" if thumb.is_file() else None

    return ProjectDetailResponse(
        id=row["id"],
        title=row["title"],
        format=row["format"],
        status=row["status"],
        ui_status=ui,
        tts_voice=row.get("tts_voice"),
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        checkpoints=cps,
        current_step=cur,
        error_message=row.get("error_message"),
        progress=prog,
        chapters=chapters,
        final_video_size_bytes=size,
        final_video_duration_sec=duration,
        llm_usage_estimated_usd=llm_usd,
        download_url=f"/api/projects/{project_id}/download",
        thumbnail_url=thumb_url,
    )


@app.get("/api/projects/{project_id}/logs", response_model=ProjectLogsResponse)
async def api_project_logs(project_id: int, tail: int = Query(400, ge=10, le=5000)):
    if not get_project_row(project_id):
        raise HTTPException(404, "Проект не найден")
    if not LOG_PATH.is_file():
        return ProjectLogsResponse(lines=[])
    needle = f"project_id={project_id}"
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ProjectLogsResponse(lines=[])
    lines = [ln for ln in text.splitlines() if needle in ln]
    return ProjectLogsResponse(lines=lines[-tail:])


@app.get("/api/projects/{project_id}/download")
async def api_project_download(project_id: int):
    path = STORAGE / "projects" / str(project_id) / f"FINAL_CINEMATIC_{project_id}.mp4"
    if not path.is_file():
        raise HTTPException(404, "Файл видео не найден")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/projects/{project_id}/thumbnail")
async def api_project_thumbnail(project_id: int):
    path = STORAGE / "projects" / str(project_id) / f"THUMBNAIL_{project_id}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Превью не найдено")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/api/projects/{project_id}/avatar")
async def api_project_avatar_upload(project_id: int, file: UploadFile = File(...)):
    """Загрузка файла аватара для конкретного проекта"""
    row = get_project_row(project_id)
    if not row:
        raise HTTPException(404, "Проект не найден")
        
    ext = Path(file.filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".mp4"]:
        raise HTTPException(400, "Поддерживаются только форматы PNG, JPG и MP4")
        
    is_video = ext == ".mp4"
    avatar_type = "mp4" if is_video else "png"
    target_name = f"avatar.{avatar_type}"
    
    project_dir = STORAGE / "projects" / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    
    target_path = project_dir / target_name
    
    # Удаляем старые аватары
    old_png = project_dir / "avatar.png"
    old_mp4 = project_dir / "avatar.mp4"
    if old_png.exists(): old_png.unlink()
    if old_mp4.exists(): old_mp4.unlink()
    
    with open(target_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
        
    update_project_avatar(project_id, avatar_type)
    
    return {"ok": True, "message": "Аватар успешно загружен", "avatar_type": avatar_type}


@app.post("/api/projects", response_model=NewProjectResponse)
async def api_new_project(body: NewProjectRequest):
    topic = body.topic.strip()
    pid = insert_dashboard_project(topic, body.format, body.voice, preset=body.preset)
    cmd = [sys.executable, str(REPO_ROOT / "main.py"), "--project-id", str(pid)]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        logger.exception("Не удалось запустить конвейер: %s", e)
        raise HTTPException(500, f"Не удалось запустить процесс: {e}") from e
    return NewProjectResponse(id=pid, message="Конвейер запущен в фоне")


def _read_queue() -> list[str]:
    if not TOPICS_PATH.is_file():
        return []
    lines = TOPICS_PATH.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip()]


def _write_queue(topics: list[str]):
    TOPICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOPICS_PATH.write_text("\n".join(topics) + ("\n" if topics else ""), encoding="utf-8")


@app.get("/api/queue", response_model=QueueResponse)
async def api_queue_get():
    topics = _read_queue()
    return QueueResponse(topics=[QueueTopic(index=i, text=t) for i, t in enumerate(topics)])


@app.post("/api/queue")
async def api_queue_add(body: QueueAddRequest):
    t = body.topic.strip()
    topics = _read_queue()
    topics.append(t)
    _write_queue(topics)
    return {"ok": True, "index": len(topics) - 1}


@app.delete("/api/queue/{index}")
async def api_queue_delete(index: int):
    topics = _read_queue()
    if index < 0 or index >= len(topics):
        raise HTTPException(404, "Нет такой позиции")
    topics.pop(index)
    _write_queue(topics)
    return {"ok": True}


@app.patch("/api/queue")
async def api_queue_reorder(body: QueueReorderRequest):
    topics = _read_queue()
    n = len(topics)
    if sorted(body.order) != list(range(n)):
        raise HTTPException(400, "order должен быть перестановкой индексов 0..n-1")
    new_lines = [topics[i] for i in body.order]
    _write_queue(new_lines)
    return {"ok": True}

# -----------------
# Scene Management (Human-in-the-loop)
# -----------------

@app.patch("/api/scenes/{scene_id}")
async def api_update_scene(scene_id: int, updates: dict):
    """Обновление полей сцены (narration, image_prompt и т.д.)"""
    scene = get_scene_by_id(scene_id)
    if not scene:
        raise HTTPException(404, "Сцена не найдена")
        
    update_scene_fields(scene_id, updates)
    return {"ok": True, "message": "Сцена обновлена"}

@app.post("/api/scenes/{scene_id}/approve")
async def api_approve_scene(scene_id: int):
    scene = get_scene_by_id(scene_id)
    if not scene:
        raise HTTPException(404, "Сцена не найдена")
    update_scene_fields(scene_id, {"scene_status": "approved"})
    return {"ok": True, "message": "Сцена утверждена"}

@app.post("/api/scenes/{scene_id}/reject")
async def api_reject_scene(scene_id: int):
    scene = get_scene_by_id(scene_id)
    if not scene:
        raise HTTPException(404, "Сцена не найдена")
    update_scene_fields(scene_id, {"scene_status": "rejected"})
    return {"ok": True, "message": "Сцена отклонена (нужна перегенерация)"}

@app.post("/api/scenes/{scene_id}/regenerate-voice")
async def api_regenerate_scene_voice(scene_id: int):
    project_id = get_project_id_by_scene(scene_id)
    if not project_id:
        raise HTTPException(404, "Сцена или проект не найдены")
        
    cmd = [sys.executable, str(REPO_ROOT / "main.py"), "--project-id", str(project_id), "--regenerate-scene", str(scene_id)]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=dict(os.environ, REGENERATE_TARGET="voice")
        )
    except OSError as e:
        raise HTTPException(500, f"Не удалось запустить процесс: {e}") from e
    
    update_scene_fields(scene_id, {"scene_status": "retry_pending"})
    return {"ok": True, "message": "Запущена перегенерация аудио для сцены"}

@app.post("/api/scenes/{scene_id}/regenerate-visual")
async def api_regenerate_scene_visual(scene_id: int):
    project_id = get_project_id_by_scene(scene_id)
    if not project_id:
        raise HTTPException(404, "Сцена или проект не найдены")
        
    cmd = [sys.executable, str(REPO_ROOT / "main.py"), "--project-id", str(project_id), "--regenerate-scene", str(scene_id)]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=dict(os.environ, REGENERATE_TARGET="visual")
        )
    except OSError as e:
        raise HTTPException(500, f"Не удалось запустить процесс: {e}") from e
        
    update_scene_fields(scene_id, {"scene_status": "retry_pending"})
    return {"ok": True, "message": "Запущена перегенерация визуала для сцены"}

@app.get("/api/projects/{project_id}/quality-report")
async def api_project_quality_report(project_id: int):
    """Возвращает детальный отчет о качестве проекта (записывает в БД и отдает клиенту)."""
    row = get_project_row(project_id)
    if not row:
        raise HTTPException(404, "Проект не найден")
        
    script_raw = get_project_script(project_id, require_scenes=False)
    
    total_score = 0
    scored_scenes = 0
    needs_review_count = 0
    report_scenes = []
    
    from config import Config
    
    for ch in script_raw:
        for sc in ch.get("scenes", []):
            vs = sc.get("visual_score")
            st = sc.get("scene_status", "pending")
            reason = sc.get("review_reason", "")
            
            if vs is not None:
                total_score += vs
                scored_scenes += 1
                
            if st == "needs_review":
                needs_review_count += 1
                
            warnings = []
            if vs is not None and vs < Config.MIN_VISUAL_SCORE:
                warnings.append("Низкий visual_score")
            if sc.get("technical_score") is not None and sc.get("technical_score") < 0.8:
                warnings.append("Низкое техническое качество видео (FPS или битрейт)")
            if sc.get("semantic_match_score") is not None and sc.get("semantic_match_score") < 0.6:
                warnings.append("Слабое соответствие кадра и текста")
            if sc.get("continuity_score") is not None and sc.get("continuity_score") < 0.7:
                warnings.append("Нарушен Continuity Anchor")
                
            report_scenes.append({
                "scene_id": sc["id"],
                "scene_number": sc["number"],
                "visual_score": vs,
                "semantic_match_score": sc.get("semantic_match_score"),
                "continuity_score": sc.get("continuity_score"),
                "status": st,
                "review_reason": reason,
                "warnings": warnings
            })
            
    avg_score = (total_score / scored_scenes) if scored_scenes > 0 else None
    
    report_data = {
        "project_id": project_id,
        "overall_score": avg_score,
        "weak_scene_count": needs_review_count,
        "is_ready_for_render": needs_review_count == 0,
        "scenes_report": report_scenes
    }
    
    # Save final score and report to database
    from database import get_connection
    import json
    conn = get_connection()
    cursor = conn.cursor()
    
    if avg_score is not None:
        cursor.execute("UPDATE projects SET final_quality_score = ?, quality_report_json = ? WHERE id = ?", 
                       (avg_score, json.dumps(report_data, ensure_ascii=False), project_id))
    
    # Insert into project_quality_reports
    cursor.execute("""
        INSERT INTO project_quality_reports 
        (project_id, overall_score, weak_scene_count, avg_visual_score, warnings_json) 
        VALUES (?, ?, ?, ?, ?)
    """, (project_id, avg_score, needs_review_count, avg_score, json.dumps([s for s in report_scenes if s["warnings"]], ensure_ascii=False)))
    
    conn.commit()
    conn.close()
        
    return report_data
