"""
input_controller.py – Drives the real mouse and keyboard via pyautogui,
with light human-emulation (glided moves, randomized delays, per-key timing).

All coordinates passed in here are ABSOLUTE screen pixels.
"""

from __future__ import annotations
import random
import time

import pyautogui

import config

# Safety: slamming the mouse into a screen corner aborts the program.
pyautogui.FAILSAFE = True
# We handle our own pacing, so disable pyautogui's global pause.
pyautogui.PAUSE = 0.0


def _rand(lo: float, hi: float) -> float:
    return random.uniform(lo, hi)


def move_to(x: int, y: int, label: str = "") -> None:
    """Move the mouse to (x, y) without clicking — reveals hover UI."""
    if label:
        print(f"[input] hover → ({x}, {y})  «{label}»")
    pyautogui.moveTo(
        x, y,
        duration=_rand(config.MOUSE_MOVE_DURATION_MIN,
                       config.MOUSE_MOVE_DURATION_MAX),
    )


def sweep(x1: int, y1: int, x2: int, y2: int,
          duration: float = 2.0, label: str = "") -> None:
    """
    Drag the mouse from (x1,y1) to (x2,y2) without clicking.
    Upward movement (y1 > y2) is what triggers video control bars to appear.
    """
    if label:
        print(f"[input] sweep ({x1},{y1}) → ({x2},{y2})  «{label}»")
    pyautogui.moveTo(x1, y1, duration=0.2)
    pyautogui.moveTo(x2, y2, duration=duration)


def wiggle(x: int, y: int, amplitude: int = 20, reps: int = 4,
           label: str = "") -> None:
    """
    Oscillate the mouse up and down around (x, y) to keep video controls
    visible. The control bar only stays up during active mouse movement;
    this keeps it alive long enough to screenshot and click.
    """
    if label:
        print(f"[input] wiggle ({x},{y}) ±{amplitude}px x{reps}  «{label}»")
    for _ in range(reps):
        pyautogui.moveTo(x, y - amplitude, duration=0.12)
        pyautogui.moveTo(x, y + amplitude, duration=0.12)
    pyautogui.moveTo(x, y, duration=0.08)


def click(x: int, y: int, label: str = "") -> None:
    """Glide the mouse to (x, y) and click, with human-ish timing."""
    if label:
        print(f"[input] click → ({x}, {y})  «{label}»")
    pyautogui.moveTo(
        x, y,
        duration=_rand(config.MOUSE_MOVE_DURATION_MIN,
                       config.MOUSE_MOVE_DURATION_MAX),
    )
    time.sleep(_rand(config.PRE_CLICK_DELAY_MIN, config.PRE_CLICK_DELAY_MAX))
    pyautogui.click()


def type_text(text: str, label: str = "") -> None:
    """Type a string with human-paced keystrokes."""
    if not text:
        return
    if label:
        print(f"[input] type  «{label}»: {text!r}")
    # pyautogui.write types each character with a delay between keystrokes.
    # It handles digits and basic ASCII reliably.
    pyautogui.write(text, interval=_rand(config.TYPING_DELAY_MIN, config.TYPING_DELAY_MAX))


def clear_field() -> None:
    """Select-all + delete, to clear an input box before typing."""
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.08)
    pyautogui.press("delete")
    time.sleep(0.08)


def press_key(key: str, label: str = "") -> None:
    """Press a single key or hotkey combination (e.g. 'enter', 'tab')."""
    if label:
        print(f"[input] key   «{label}»: {key}")
    pyautogui.press(key)


def answer_fill_in(x: int, y: int, text: str, label: str = "answer") -> None:
    """
    Robustly enter a fill-in answer: click the field to focus it, clear any
    existing content, then type. Clearing first forces a key event into the
    focused element, which surfaces the "clicked but never focused" case (the old
    bug where typing went nowhere and only Enter registered). Does NOT press Enter
    — the caller submits after verifying text actually appeared.
    """
    click(x, y, label)
    time.sleep(_rand(config.PRE_CLICK_DELAY_MIN, config.PRE_CLICK_DELAY_MAX))
    clear_field()
    type_text(text, label)
