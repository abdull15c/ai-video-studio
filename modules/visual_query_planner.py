import json
import logging
from config import Config
from .anthropic_helpers import CLAUDE_MODEL_HAIKU, llm_generate
from .json_extract import clean_json_from_llm

logger = logging.getLogger(__name__)


def plan_visual_queries(narration, image_prompt, mood, camera, project_id=None, scene_index=0):
    logger.info("ИИ-режиссёр: подбор визуальных запросов для стока")

    fallback_plan = {
        "primary_query": "ocean waves crashing",
        "alt_queries": ["people walking city", "clouds moving sky", "river flowing forest"],
        "fallback_queries": ["traffic street night", "wind trees", "waterfall nature"],
        "visual_mode": "metaphor",
        "stock_confidence": "medium",
    }

    if not Config.has_llm_api_key():
        return fallback_plan

    model = CLAUDE_MODEL_HAIKU if Config.LLM_PROVIDER == "anthropic" else None

    if not hasattr(plan_visual_queries, '_recent_queries'):
        plan_visual_queries._recent_queries = []
        
    used_queries_text = ""
    if scene_index > 0 and plan_visual_queries._recent_queries:
        recent = plan_visual_queries._recent_queries[-5:]
        used_queries_text = f"\n    ALREADY USED queries (DO NOT REPEAT): {', '.join(recent)}"

    prompt = f'''You are a Senior Visual Director & Cinematographer for a TOP-10 YouTube documentary channel.
    Your job is to translate the scene into SHORT, HIGH-QUALITY English search queries for premium stock video sites.

    Scene Data:
    Narration: "{narration}"
    Initial Prompt: "{image_prompt}"
    Mood: {mood}
    Camera: {camera}

    CINEMATOGRAPHY RULES (CRITICAL):
    1. Every query MUST include camera movement or subject motion. Use terms like: "pan", "zoom", "tilt", "tracking", "timelapse", "aerial", "slow motion", "walking", "running", "flying".
    2. Prefer themes that stock libraries have in abundance in 4K: nature, water, fire, futuristic cities, neon streets, cinematic close-ups of hands, epic landscapes, abstract data flows, storms.
    3. AVOID hyper-specific objects ("Soviet rocket model X"). Use visual metaphors instead (e.g., "rocket taking off fire").
    
    EXAMPLES OF GOOD QUERIES:
    - "camera panning over old map"
    - "aerial flying over mountains"
    - "slow motion rain window"
    - "timelapse city night traffic"
    - "neon cyberpunk street walking"
    
    RULES:
    1. Maximum 3-5 words per query (English).
    2. ALTS are used for JUMP-CUTS! They must be visually distinct from the primary query but fit the same scene (e.g., Primary: "aerial mountains", Alt 1: "close up hiking boots", Alt 2: "timelapse clouds peak").

    {used_queries_text}
    IMPORTANT: Make this query VISUALLY DISTINCT from previous scenes. Use different camera angles,
    different subjects, different lighting. Think like a film editor — variety keeps viewers watching.

    Return ONLY a JSON object:
    {{
        "primary_query": "cinematic query with motion",
        "alt_queries": ["distinct cinematic alt 1", "distinct cinematic alt 2", "distinct cinematic alt 3"],
        "fallback_queries": ["safe generic motion 1", "safe generic motion 2"],
        "visual_mode": "literal or metaphor or atmospheric",
        "stock_confidence": "high or medium or low"
    }}'''

    try:
        raw = llm_generate(
            prompt,
            max_tokens=300,
            temperature=0.7,
            model=model,
            project_id=project_id,
            step="visual_queries",
        )
        plan = json.loads(clean_json_from_llm(raw))
        if "primary_query" not in plan:
            raise ValueError("Invalid JSON structure")
            
        plan_visual_queries._recent_queries.append(plan["primary_query"])
        # Keep only the last 20 queries to prevent memory leak
        if len(plan_visual_queries._recent_queries) > 20:
            plan_visual_queries._recent_queries.pop(0)
            
        return plan
    except Exception as e:
        logger.warning("ИИ-режиссёр: %s — fallback-план", e)
        return fallback_plan
