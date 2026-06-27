"""
orchestrator.py – Main loop for the vision-based Acellus automation.

Flow:
    1. Capture a tight crop of the Acellus question card.
    2. Send it to Gemini vision → get the correct answer (text only).
    3. Click the input bar at fixed hardcoded coordinates, type the answer, Enter.
    4. Wait for the next question, repeat.

Gemini's only job is reading math and returning the answer. Clicking is done
with fixed screen coordinates from config.py (INPUT_BAR_X / INPUT_BAR_Y) so
coordinate estimation errors can never cause a missed click.

Usage:
    python orchestrator.py
    python orchestrator.py --once       # one question then exit
    python orchestrator.py --dry-run    # print plan without clicking
"""

from __future__ import annotations
import argparse
import io
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from PIL import Image

import config
import screen_capture
import gemini_vision
import input_controller


def run(once: bool = False, dry_run: bool = False) -> None:
    print("\n" + "-" * 60)
    print("  Acellus Vision Automation")
    print(f"  Input bar target: ({config.INPUT_BAR_X}, {config.INPUT_BAR_Y})")
    print("  Bring Acellus to the FOREGROUND now.")
    print(f"  Starting in {config.STARTUP_GRACE:.0f}s ...")
    print("  (Ctrl+C or slam mouse to a corner to abort.)")
    print("-" * 60 + "\n")

    if not dry_run:
        time.sleep(config.STARTUP_GRACE)

    answered = 0
    idle_frames = 0
    last_question = ""
    stuck_count = 0

    try:
        while True:
            frame = screen_capture.capture()
            print(f"\n[orchestrator] Captured {frame.width}x{frame.height} "
                  f"region. Asking Gemini ...")

            result = gemini_vision.analyze(frame.png_bytes)

            # --- No question on screen ---
            if not result.question_present or not result.answer.strip():
                stype = result.screen_type
                print(f"[orchestrator] Screen: {stype}")

                if stype == "video":
                    if dry_run:
                        print("[dry-run] would hover, read time, set 1.5x speed, sleep")
                        continue

                    # 1. Fast upward sweep from the bottom edge of the video to
                    #    near the settings icon — upward movement triggers the bar.
                    input_controller.sweep(
                        config.VIDEO_HOVER_X, config.VIDEO_HOVER_Y,
                        config.VIDEO_SETTINGS_X, config.VIDEO_SETTINGS_Y,
                        duration=0.8,
                        label="fast upward sweep to settings",
                    )

                    # 2. Wiggle up-and-down to keep the control bar alive, then
                    #    screenshot immediately while controls are still visible.
                    input_controller.wiggle(
                        config.VIDEO_SETTINGS_X, config.VIDEO_SETTINGS_Y,
                        amplitude=20, reps=4,
                        label="keep controls alive",
                    )
                    hover_frame = screen_capture.capture(
                        region=config.VIDEO_TIME_REGION
                    )
                    secs_remaining = gemini_vision.read_video_remaining(
                        hover_frame.png_bytes)
                    print(f"[orchestrator] Video remaining: {secs_remaining:.0f}s")

                    # 3. Wiggle once more to re-reveal controls, then click Settings.
                    input_controller.wiggle(
                        config.VIDEO_SETTINGS_X, config.VIDEO_SETTINGS_Y,
                        amplitude=20, reps=3,
                        label="re-reveal before settings click",
                    )
                    import pyautogui as _pag
                    _pag.moveTo(config.VIDEO_SETTINGS_X, config.VIDEO_SETTINGS_Y,
                                duration=0.15)
                    _pag.click()
                    print(f"[input] click → ({config.VIDEO_SETTINGS_X}, "
                          f"{config.VIDEO_SETTINGS_Y})  «settings»")
                    time.sleep(0.5)
                    input_controller.click(config.VIDEO_SPEED_MENU_X,
                                           config.VIDEO_SPEED_MENU_Y, "speed menu")
                    time.sleep(0.4)
                    input_controller.click(config.VIDEO_SPEED_1_5X_X,
                                           config.VIDEO_SPEED_1_5X_Y, "1.5x")
                    time.sleep(0.3)

                    # 4. Sleep for remaining / 1.5x + small buffer.
                    adjusted = secs_remaining / config.VIDEO_SPEED_MULTIPLIER + 4.0
                    print(f"[orchestrator] Sleeping {adjusted:.0f}s "
                          f"({secs_remaining:.0f}s / {config.VIDEO_SPEED_MULTIPLIER}x"
                          f" + 4s buffer)")
                    time.sleep(adjusted)
                    continue  # don't increment idle

                elif stype == "score":
                    # Score/results card — click Continue then wait longer for
                    # the video/next screen to fully load (avoids 2 wasted checks).
                    print(f"[orchestrator] Score screen -- clicking Continue ...")
                    if not dry_run:
                        input_controller.click(config.CONTINUE_BTN_X,
                                               config.CONTINUE_BTN_Y, "Continue")
                    else:
                        print(f"[dry-run] would click Continue at "
                              f"({config.CONTINUE_BTN_X}, {config.CONTINUE_BTN_Y})")
                    time.sleep(config.POST_CONTINUE_DELAY)
                    continue  # don't increment idle

                else:
                    # Loading / transition / unknown — short wait.
                    idle_frames += 1
                    print(f"[orchestrator] Waiting "
                          f"(idle {idle_frames}/{config.MAX_IDLE_FRAMES}) ...")
                    if once or idle_frames >= config.MAX_IDLE_FRAMES:
                        break
                    time.sleep(config.AFTER_ADVANCE_DELAY)
                    continue

            idle_frames = 0
            q_key = result.question_text.strip()

            # --- Stuck detection ---
            if q_key and q_key == last_question:
                stuck_count += 1
                print(f"[orchestrator] Same question again (stuck x{stuck_count}).")
                if stuck_count >= 2:
                    print("[orchestrator] Stuck -- stopping.")
                    break
                time.sleep(config.AFTER_ADVANCE_DELAY * 2)
                continue
            else:
                stuck_count = 0

            last_question = q_key
            print(f"[orchestrator] Q: {result.question_text[:120]}")
            print(f"[orchestrator] A: {result.answer}  [{result.answer_type}]")

            if result.answer_type == "multiple_choice":
                if result.num_choices == 4 and result.choice_index in config.MC4_POSITIONS:
                    px, py = config.MC4_POSITIONS[result.choice_index]
                    if dry_run:
                        print(f"[dry-run] would click '{result.answer}' "
                              f"at ({px}, {py}) [4-option index {result.choice_index}]")
                    else:
                        input_controller.click(px, py, result.answer)
                elif result.num_choices == 3 and result.choice_index in config.MC3_POSITIONS:
                    px, py = config.MC3_POSITIONS[result.choice_index]
                    if dry_run:
                        print(f"[dry-run] would click '{result.answer}' "
                              f"at ({px}, {py}) [3-option index {result.choice_index}]")
                    else:
                        input_controller.click(px, py, result.answer)
                else:
                    # 2-option (Yes/No): Gemini's x picks the tile; y is fixed
                    # because Gemini's y estimate is consistently ~70px low.
                    left, top, w, h = config.CAPTURE_REGION
                    px = int(left + (result.click_x / 1000.0) * w)
                    py = config.CHOICE_Y
                    if dry_run:
                        print(f"[dry-run] would click '{result.answer}' "
                              f"at ({px}, {py}) [2-option: Gemini x + fixed y]")
                    else:
                        input_controller.click(px, py, result.answer)
            else:
                # Fill-in: click fixed input bar, type, Enter.
                if dry_run:
                    print(f"[dry-run] would click ({config.INPUT_BAR_X}, "
                          f"{config.INPUT_BAR_Y}) then type {result.answer!r}")
                else:
                    input_controller.click(config.INPUT_BAR_X, config.INPUT_BAR_Y,
                                           "input bar")
                    time.sleep(0.3)
                    input_controller.type_text(result.answer, "answer")
                    time.sleep(0.2)
                    input_controller.press_key("enter", "submit")

            answered += 1
            print(f"[orchestrator] Question #{answered} submitted.")

            if once:
                break
            if config.MAX_QUESTIONS and answered >= config.MAX_QUESTIONS:
                print(f"[orchestrator] Reached MAX_QUESTIONS -- stopping.")
                break

            time.sleep(config.AFTER_ADVANCE_DELAY)

    except KeyboardInterrupt:
        print("\n\n[orchestrator] Aborted (Ctrl+C).")
    finally:
        print(f"\n[orchestrator] Done. {answered} question(s) handled.\n")


# ───────────────────────────────────────────────────────────────────────────
# ASSIST MODE — the bot solves, you click
# ───────────────────────────────────────────────────────────────────────────

def _thumb(png_bytes: bytes, size: tuple[int, int] = (64, 64)) -> list[int]:
    """Downscaled grayscale signature of a frame, for cheap change detection."""
    img = Image.open(io.BytesIO(png_bytes)).convert("L").resize(size)
    return list(img.getdata())


def _frame_diff(a: list[int] | None, b: list[int] | None) -> float:
    """Mean absolute per-pixel difference (0-255) between two thumbnails."""
    if not a or not b:
        return 1e9
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _choice_label(result) -> str:
    """Human description of which tile to click for a multiple-choice answer."""
    if result.num_choices == 4:
        names = {1: "TOP-LEFT", 2: "TOP-RIGHT", 3: "BOTTOM-LEFT", 4: "BOTTOM-RIGHT"}
        where = names.get(result.choice_index, f"#{result.choice_index}")
        return f"option {result.choice_index} ({where})"
    if result.num_choices == 3:
        names = {1: "UP", 2: "MIDDLE", 3: "DOWN"}
        where = names.get(result.choice_index, f"#{result.choice_index}")
        return f"option {result.choice_index} ({where})"
    if result.num_choices == 2:
        side = "LEFT" if result.click_x and result.click_x < 500 else "RIGHT"
        return f"the {side} tile"
    return ""


def _show_answer_banner(result) -> None:
    """Print the answer in a big, impossible-to-miss block."""
    if result.answer_type == "multiple_choice":
        where = _choice_label(result)
        action = f"CLICK: {result.answer}"
        detail = f"  → {where}" if where else ""
    else:
        action = f"TYPE:  {result.answer}"
        detail = "  → into the answer box, then Enter"

    line = action + detail
    width = max(len(line), len(result.question_text[:60]), 40) + 4
    bar = "═" * width
    print("\n╔" + bar + "╗")
    print("║ " + "ANSWER".ljust(width - 1) + "║")
    print("╠" + bar + "╣")
    print("║ " + line.ljust(width - 1) + "║")
    print("╚" + bar + "╝")
    if result.question_text.strip():
        print(f"   Q: {result.question_text.strip()[:120]}")
    print()


def _drain_kb() -> bool:
    """
    Check if 'R' (or 'r') was pressed without blocking. Returns True if so.
    Drains the entire key buffer so stale keypresses don't accumulate.
    Windows only — uses msvcrt which is always available.
    """
    import msvcrt
    forced = False
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch.lower() == "r":
            forced = True
    return forced


def run_assist(
    stop_event=None,
    force_event=None,
    on_answer=None,
    on_status=None,
) -> None:
    """
    Watch the screen and solve questions — never clicks or types.

    CLI mode (all params None): prints banners, reads R key via msvcrt, stops on Ctrl+C.
    GUI mode: uses threading Events and callbacks instead.

    Args:
        stop_event:  threading.Event — set it to stop the loop.
        force_event: threading.Event — set it to force an immediate re-check.
        on_answer:   callable(VisionResult) — called when an answer is ready.
        on_status:   callable(str) — called with status/log messages.
    """
    cli = stop_event is None  # True when launched from the terminal

    def _log(msg: str) -> None:
        if on_status:
            on_status(msg)
        else:
            print(msg)

    def _forced() -> bool:
        if force_event is not None:
            if force_event.is_set():
                force_event.clear()
                return True
            return False
        return _drain_kb()

    def _stopped() -> bool:
        return False if stop_event is None else stop_event.is_set()

    if cli:
        print("\n" + "─" * 60)
        print("  Acellus Assist Mode  (you click, the bot solves)")
        print("  The bot will NOT touch your mouse or keyboard.")
        print("  Whenever a new question appears, the answer prints below.")
        print("  Press R to force a re-check of the current screen.")
        print("  Ctrl+C to stop.")
        print("─" * 60)

    last_thumb: list[int] | None = None
    last_graph_thumb: list[int] | None = None
    last_question = ""
    solved = 0

    try:
        while not _stopped():
            frame = screen_capture.capture()
            thumb = _thumb(frame.png_bytes)
            graph_frame = screen_capture.capture(region=config.GRAPH_REGION)
            graph_thumb = _thumb(graph_frame.png_bytes)

            forced = _forced()
            screen_changed = (
                _frame_diff(last_thumb, thumb) >= config.ASSIST_CHANGE_THRESHOLD
                or _frame_diff(last_graph_thumb, graph_thumb) >= config.ASSIST_GRAPH_CHANGE_THRESHOLD
            )

            if not screen_changed and not forced:
                time.sleep(config.ASSIST_POLL_INTERVAL)
                continue

            if forced:
                _log("[assist] Manual re-check triggered ...")
            else:
                time.sleep(config.ASSIST_SETTLE_DELAY)
                frame = screen_capture.capture()
                thumb = _thumb(frame.png_bytes)
                graph_frame = screen_capture.capture(region=config.GRAPH_REGION)
                graph_thumb = _thumb(graph_frame.png_bytes)

            last_thumb = thumb
            last_graph_thumb = graph_thumb
            result = gemini_vision.analyze(frame.png_bytes)

            if (result.screen_type == "question"
                    and result.question_present
                    and result.answer.strip()):
                q = result.question_text.strip()
                if q and q == last_question and not forced:
                    time.sleep(config.ASSIST_POLL_INTERVAL)
                    continue
                last_question = q
                solved += 1
                if on_answer:
                    on_answer(result)
                else:
                    _show_answer_banner(result)
            else:
                last_question = ""
                _log(f"[assist] {result.screen_type} — waiting for a question ...")

            time.sleep(config.ASSIST_POLL_INTERVAL)

    except KeyboardInterrupt:
        if cli:
            print("\n\n[assist] Stopped (Ctrl+C).")
    finally:
        if cli:
            print(f"\n[assist] Done. {solved} answer(s) shown.\n")


def main():
    parser = argparse.ArgumentParser(
        description="Acellus vision automation."
    )
    parser.add_argument("--auto", action="store_true",
                        help="Full auto mode: the bot clicks and types for you. "
                             "(Default is assist mode: it only solves, you click.)")
    parser.add_argument("--once", action="store_true",
                        help="[auto] Handle one question then exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="[auto] Print the plan without clicking or typing.")
    args = parser.parse_args()

    if args.auto:
        run(once=args.once, dry_run=args.dry_run)
    else:
        run_assist()


if __name__ == "__main__":
    main()
