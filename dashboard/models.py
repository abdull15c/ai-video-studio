from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class StatsResponse(BaseModel):
    total_projects: int
    completed: int
    in_progress: int
    failed: int
    disk_free_bytes: int
    disk_total_bytes: int
    autopilot_running: bool
    autopilot_next_run_iso: Optional[str] = None
    autopilot_schedule_label: Optional[str] = None


class PipelineStepState(BaseModel):
    step: str
    state: str  # done | current | pending | error


class ProjectListItem(BaseModel):
    id: int
    title: str
    format: str
    status: str
    ui_status: str
    tts_voice: Optional[str] = None
    created_at: Optional[str] = None
    checkpoints: List[str] = []
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    progress: List[PipelineStepState] = []


class ProjectListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ProjectListItem]


class SceneDetail(BaseModel):
    id: int
    number: int
    narration: Optional[str] = None
    image_prompt: Optional[str] = None
    mood: Optional[str] = None
    camera: Optional[str] = None
    duration_sec: Optional[float] = None
    scene_goal: Optional[str] = None
    visual_role: Optional[str] = None
    shot_type: Optional[str] = None
    motion_type: Optional[str] = None
    transition_in: Optional[str] = None
    transition_out: Optional[str] = None
    intensity: Optional[str] = None
    continuity_anchor: Optional[str] = None
    scene_role: Optional[str] = None
    energy_curve: Optional[str] = None
    edit_density: Optional[str] = None
    semantic_match_score: Optional[float] = None
    motion_score: Optional[float] = None
    technical_score: Optional[float] = None
    continuity_score: Optional[float] = None
    review_reason: Optional[str] = None
    visual_score: Optional[float] = None
    audio_score: Optional[float] = None
    subtitle_score: Optional[float] = None
    scene_status: Optional[str] = None
    manual_override_json: Optional[str] = None


class ChapterDetail(BaseModel):
    id: int
    number: int
    title: Optional[str] = None
    scenes: List[SceneDetail] = []


class ProjectDetailResponse(BaseModel):
    id: int
    title: str
    format: str
    status: str
    ui_status: str
    tts_voice: Optional[str] = None
    created_at: Optional[str] = None
    checkpoints: List[str] = []
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    progress: List[PipelineStepState] = []
    chapters: List[ChapterDetail] = []
    final_video_size_bytes: Optional[int] = None
    final_video_duration_sec: Optional[float] = None
    llm_usage_estimated_usd: Optional[float] = None
    download_url: str
    thumbnail_url: Optional[str] = None
    avatar_type: Optional[str] = "none"
    style_preset: Optional[str] = None
    subtitle_style: Optional[str] = None
    voice_profile: Optional[str] = None
    quality_mode: Optional[str] = None
    manual_review_required: Optional[bool] = False
    keep_intermediates: Optional[bool] = False
    final_quality_score: Optional[float] = None
    quality_report_json: Optional[str] = None
    review_notes: Optional[str] = None
    audio_mastering_preset: Optional[str] = None


class ProjectLogsResponse(BaseModel):
    lines: List[str]


class NewProjectRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    format: Literal["short", "main", "long"] = "short"
    voice: Optional[str] = None
    preset: Optional[str] = "1 hour"  # "30 minutes" | "1 hour"

    @field_validator("voice")
    @classmethod
    def voice_ok(cls, v):
        from config import Config

        if v is None or v == "":
            return None
        if v not in Config.EDGE_TTS_VOICE_IDS:
            raise ValueError("Неизвестный голос Edge-TTS")
        return v


class NewProjectResponse(BaseModel):
    id: int
    message: str


class QueueTopic(BaseModel):
    index: int
    text: str


class QueueResponse(BaseModel):
    topics: List[QueueTopic]


class QueueAddRequest(BaseModel):
    topic: str = Field(..., min_length=1)


class QueueReorderRequest(BaseModel):
    """Новый порядок строк: order[i] = прежний индекс строки на позиции i."""
    order: List[int]


class VoiceOption(BaseModel):
    id: str
    description: str


class VoicesResponse(BaseModel):
    voices: List[VoiceOption]
