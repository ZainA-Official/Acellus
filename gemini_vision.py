"""
gemini_vision.py – Sends a screenshot to Gemini and gets back the answer.

Gemini's ONLY job here is to read the equation and return the correct answer.
Coordinates/clicks are handled by fixed hardcoded positions in the orchestrator.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import json
import os
import time
import random

from google import genai
from google.genai import types

import config


@dataclass
class VisionResult:
    question_present: bool
    question_text: str = ""
    answer: str = ""
    screen_type: str = "other"
    answer_type: str = "fill_in"   # "fill_in" or "multiple_choice"
    click_x: float = 0.0           # normalized 0-1000, only for 2-option MC
    click_y: float = 0.0
    num_choices: int = 0            # 2 or 4 for MC questions
    choice_index: int = 0           # 1-4 for 4-option MC; 1-2 for 2-option MC
    # Auto mode: normalized [ymin, xmin, ymax, xmax] (0-1000) bounding box of the
    # element to act on this frame — the input field (fill_in), the correct answer
    # tile (multiple_choice), or the Continue button (score). Empty if not provided.
    target_box: list[float] = field(default_factory=list)

    def box_center(self) -> tuple[float, float] | None:
        """Normalized (x, y) center of target_box, or None if no box."""
        if not self.target_box or len(self.target_box) != 4:
            return None
        ymin, xmin, ymax, xmax = self.target_box
        return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)


_SCHEMA = {
    "type": "object",
    "properties": {
        "screen_type": {
            "type": "string",
            "enum": ["question", "video", "score", "other"],
        },
        "question_present": {"type": "boolean"},
        "question_text": {"type": "string"},
        "answer": {"type": "string"},
        "answer_type": {
            "type": "string",
            "enum": ["fill_in", "multiple_choice"],
        },
        "num_choices": {"type": "integer"},   # 2 or 4 for MC
        "choice_index": {"type": "integer"},  # 1-4, which option is correct
        "click_x": {"type": "number"},        # normalized 0-1000, 2-option MC only
        "click_y": {"type": "number"},
        "target_box": {                        # [ymin, xmin, ymax, xmax] 0-1000
            "type": "array",
            "items": {"type": "number"},
        },
    },
    "required": ["screen_type", "question_present", "answer"],
}

_PROMPT = (
    "Look at this Acellus screenshot. Read the math question carefully, "
    "solve it, and return the answer. Remember: read every coefficient "
    "(e.g. '7x' means seven-x, not just x)."
)


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY") or config.GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set.")
    return genai.Client(api_key=api_key)


def analyze(png_bytes: bytes, mime_type: str = "image/png") -> VisionResult:
    """Send screenshot to Gemini, return question text and answer."""
    client = _get_client()
    image_part = types.Part.from_bytes(data=png_bytes, mime_type=mime_type)

    last_error = None
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[_PROMPT, image_part],
                config=types.GenerateContentConfig(
                    system_instruction=config.GEMINI_SYSTEM_INSTRUCTION,
                    temperature=1.0,
                    max_output_tokens=512,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=config.GEMINI_THINKING_BUDGET),
                    response_mime_type="application/json",
                    response_schema=_SCHEMA,
                ),
            )
            return _parse(response)

        except Exception as exc:
            last_error = exc
            err_str = str(exc)
            is_transient = any(code in err_str for code in [
                "503", "429", "500", "UNAVAILABLE", "overloaded",
                "Resource exhausted", "rate limit", "quota",
            ])
            if is_transient and attempt < config.MAX_RETRIES:
                delay = (
                    config.RETRY_BASE_DELAY * (2 ** attempt)
                    + random.uniform(0, config.RETRY_JITTER)
                )
                print(f"[gemini] Transient error (attempt {attempt + 1}): "
                      f"{err_str[:80]}")
                print(f"[gemini]   Retrying in {delay:.1f}s ...")
                time.sleep(delay)
            else:
                raise

    raise last_error


_VIDEO_TIME_SCHEMA = {
    "type": "object",
    "properties": {
        "seconds_remaining": {"type": "number"},
    },
    "required": ["seconds_remaining"],
}

_VIDEO_TIME_PROMPT = (
    "Look at the video player controls visible in this screenshot. "
    "Find the current elapsed time and total duration (e.g. '1:23 / 5:00'). "
    "Return seconds_remaining = total_seconds - elapsed_seconds. "
    "If you cannot read the time clearly, return 30."
)


def read_video_remaining(png_bytes: bytes, mime_type: str = "image/png") -> float:
    """
    Move mouse has already revealed the video controls. Send the screenshot
    and ask Gemini to read remaining seconds. Cheap call — tiny schema.
    Returns seconds remaining (float), or 30.0 as a fallback.
    """
    client = _get_client()
    image_part = types.Part.from_bytes(data=png_bytes, mime_type=mime_type)
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[_VIDEO_TIME_PROMPT, image_part],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=64,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                response_mime_type="application/json",
                response_schema=_VIDEO_TIME_SCHEMA,
            ),
        )
        import json as _json
        data = _json.loads(response.text or "{}")
        secs = float(data.get("seconds_remaining", 30))
        return max(secs, 3.0)  # never sleep less than 3s
    except Exception as exc:
        print(f"[gemini] Could not read video time: {exc}")
        return 30.0


_LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "box": {"type": "array", "items": {"type": "number"}},  # [ymin,xmin,ymax,xmax]
    },
    "required": ["found"],
}


def locate(png_bytes: bytes, what: str, mime_type: str = "image/png") -> list[float] | None:
    """
    Find a single UI element described by `what` and return its bounding box as
    normalized [ymin, xmin, ymax, xmax] (0-1000), or None if not visible.

    Cheap, no-thinking call used for the multi-step video flow (gear icon, speed
    menu, 1.5x option, play overlay). Returns None on any failure so callers can
    fall back gracefully rather than misclick.
    """
    client = _get_client()
    image_part = types.Part.from_bytes(data=png_bytes, mime_type=mime_type)
    prompt = (
        f"Look at this screenshot. Find: {what}. "
        "If it is clearly visible, set found=true and return its bounding box as "
        "[ymin, xmin, ymax, xmax] normalized to 0-1000. "
        "If it is not visible or you are unsure, set found=false."
    )
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=128,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                response_mime_type="application/json",
                response_schema=_LOCATE_SCHEMA,
            ),
        )
        data = json.loads(response.text or "{}")
        if not data.get("found"):
            return None
        box = [float(v) for v in (data.get("box") or [])][:4]
        return box if len(box) == 4 else None
    except Exception as exc:
        print(f"[gemini] locate({what!r}) failed: {exc}")
        return None


def _parse(response) -> VisionResult:
    raw = response.text or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[gemini] Bad JSON ({len(raw)} chars): {raw[:300]}")
        return VisionResult(question_present=False)

    raw_box = data.get("target_box") or []
    try:
        target_box = [float(v) for v in raw_box][:4]
        if len(target_box) != 4:
            target_box = []
    except (TypeError, ValueError):
        target_box = []

    return VisionResult(
        question_present=bool(data.get("question_present", False)),
        question_text=data.get("question_text", "") or "",
        answer=data.get("answer", "") or "",
        screen_type=data.get("screen_type", "other") or "other",
        answer_type=data.get("answer_type", "fill_in") or "fill_in",
        click_x=float(data.get("click_x") or 0),
        click_y=float(data.get("click_y") or 0),
        num_choices=int(data.get("num_choices") or 0),
        choice_index=int(data.get("choice_index") or 0),
        target_box=target_box,
    )
