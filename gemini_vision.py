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


@dataclass
class Action:
    """One next step chosen by the goal-directed recovery reasoner."""
    action: str = "wait"          # click | key | scroll_up | scroll_down | wait | done
    box: list[float] = field(default_factory=list)  # [ymin,xmin,ymax,xmax] 0-1000 (click)
    key: str = ""                 # key name for action == "key" (e.g. "esc", "enter")
    reason: str = ""              # short human-readable rationale, for logging


_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["click", "key", "scroll_up", "scroll_down", "wait", "done"],
        },
        "box": {"type": "array", "items": {"type": "number"}},  # [ymin,xmin,ymax,xmax]
        "key": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action"],
}


def decide_action(png_bytes: bytes, goal: str,
                  mime_type: str = "image/png") -> Action:
    """
    Goal-directed recovery: the fast path is stuck or sees an unexpected screen.
    Given the screenshot and the overall goal, return the SINGLE best next action
    to get unstuck and keep making progress.

    Deliberately cheap (no thinking, tiny schema, tight token cap) so it can be
    the universal fallback without blowing the token budget — it only runs on the
    slow path (idle/stuck/navigation-stuck), never on normal recognized screens.
    Falls back to a harmless Escape on any failure.
    """
    client = _get_client()
    image_part = types.Part.from_bytes(data=png_bytes, mime_type=mime_type)
    prompt = (
        f"You control the Acellus learning app with a mouse and keyboard. "
        f"Your goal: {goal}\n"
        "The normal automation is stuck or the screen is unexpected. Pick the "
        "SINGLE best next action to get unstuck and keep progressing:\n"
        "- Popup/notification/overlay blocking the lesson: click its close (X) or "
        "an OK/Got it/Dismiss/Continue button.\n"
        "- Course-selection or menu screen: click the tile/link that re-enters the "
        "course named in the goal.\n"
        "- A PAUSED video (large play/triangle shown, or progress bar not moving): "
        "click the play button or the center of the video to resume it.\n"
        "- A stray menu is open over the lesson: action='key', key='esc'.\n"
        "- The needed control is off-screen on a scrollable page: scroll_up/scroll_down.\n"
        "- Nothing actionable yet (loading/spinner): action='wait'.\n"
        "- The lesson is already progressing and nothing blocks it: action='done'.\n"
        "For action='click' return box as [ymin, xmin, ymax, xmax] normalized 0-1000 "
        "tightly around the element to click. Keep reason under 8 words. Return ONLY JSON."
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
                response_schema=_ACTION_SCHEMA,
            ),
        )
        data = json.loads(response.text or "{}")
        act = str(data.get("action") or "wait")
        raw_box = data.get("box") or []
        try:
            box = [float(v) for v in raw_box][:4]
            if len(box) != 4:
                box = []
        except (TypeError, ValueError):
            box = []
        return Action(
            action=act,
            box=box,
            key=str(data.get("key") or ""),
            reason=str(data.get("reason") or ""),
        )
    except Exception as exc:
        print(f"[gemini] decide_action failed: {exc}")
        return Action(action="key", key="esc", reason="reasoner failed; escape")


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
