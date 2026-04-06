import json
import logging
from config import Config
from .anthropic_helpers import CLAUDE_MODEL_HAIKU, llm_generate
from .json_extract import clean_json_from_llm

logger = logging.getLogger(__name__)


def plan_visual_queries(narration, image_prompt, mood, camera, project_id=None):
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

    prompt = f'''You are a Senior Visual Director for a documentary YouTube channel.
    Your job is to translate the scene into SHORT English search queries for stock VIDEO sites (Pexels/Pixabay).

    Scene Data:
    Narration: "{narration}"
    Initial Prompt: "{image_prompt}"
    Mood: {mood}
    Camera: {camera}

    STOCK-FRIENDLY CONTENT (high chance of good dynamic footage):
    Prefer themes that stock libraries actually have in quantity: people in motion, nature, water, fire, weather,
    cities, traffic, animals, sports, hands doing generic actions, sky/clouds, forests, beaches, roads, timelapse-friendly motion.

    AVOID queries that rarely return good VIDEO on stock sites:
    Specific weapons, named historical artifacts, classified/secret documents, ultra-niche props,
    exact rare objects ("Soviet rocket model X"), misspelled or overly specific combinations ("torn tent snow").

    ACTION NOT STATIC OBJECT — every query must describe MOVEMENT or a dynamic situation, not a still prop.
    This applies to both Pexels and Pixabay: motion-focused queries return better footage on both sites.
    - NOT "old map" → USE "hands opening old book" or "camera panning over paper"
    - NOT "mountain peak" → USE "clouds moving over mountain" or "aerial flying mountains"
    - NOT "classified documents" → USE "person walking dark corridor" or "papers shuffling desk"
    - NOT "torn tent" → USE "wind blowing fabric" or "camping storm trees"

    RULES:
    1. Maximum 2-4 words per query (English). No long phrases.
    2. Each of primary + alts + fallbacks must imply visible motion or change in the frame.
    3. If the scene is abstract, use a visual metaphor with motion (e.g. tension → "lightning storm clouds").

    Return ONLY a JSON object:
    {{
        "primary_query": "best short query with implied motion",
        "alt_queries": ["alt 1", "alt 2", "alt 3", "alt 4"],
        "fallback_queries": ["generic motion 1", "generic motion 2"],
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
        return plan
    except Exception as e:
        logger.warning("ИИ-режиссёр: %s — fallback-план", e)
        return fallback_plan
