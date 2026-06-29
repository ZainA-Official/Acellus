"""
orchestrator.py – Main loop for the vision-based Acellus automation.

Two modes share the same capture → ask Gemini plumbing:

  Assist (run_assist): solves and shows the answer; never touches mouse/keyboard.
  Auto   (run_auto):   clicks and types for you.

Auto mode is VISION-GROUNDED: for every screen Gemini returns the answer AND a
bounding box (target_box) around the exact element to act on — the input field,
the correct answer tile, or the Continue button. We click the CENTER of that box,
so it adapts to wherever the element actually renders instead of relying on fixed
pixel coordinates (the old approach, which kept missing). Each action is then
verified by re-capturing the screen (typing actually appeared; the question
advanced; the video didn't get paused) and self-corrected.

Usage:
    python orchestrator.py                 # assist mode (default)
    python orchestrator.py --auto          # full auto: clicks and types
    python orchestrator.py --auto --once   # one screen then exit
    python orchestrator.py --auto --dry-run  # print plan without clicking
"""

from __future__ import annotations
import argparse
import io
import sys
import threading
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


def _call_gemini_interruptible(fn, *args, stopped_fn, poll: float = 0.1):
    """
    Run fn(*args) in a daemon thread and return its result. Polls stopped_fn()
    every `poll` seconds; if it returns True, returns None immediately (the
    background thread is abandoned — it's daemon so it won't block shutdown).

    This lets the Stop button interrupt a long Gemini API call rather than
    waiting up to 30 seconds for it to complete before the loop can check
    stop_event.
    """
    result_holder: list = [None]
    exc_holder: list = [None]
    done = threading.Event()

    def _run():
        try:
            result_holder[0] = fn(*args)
        except Exception as exc:
            exc_holder[0] = exc
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    while not done.wait(timeout=poll):
        if stopped_fn():
            return None
    if exc_holder[0] is not None:
        raise exc_holder[0]
    return result_holder[0]


def run_auto(
    stop_event=None,
    force_event=None,
    pause_event=None,
    on_answer=None,
    on_status=None,
    dry_run: bool = False,
    once: bool = False,
) -> None:
    """
    Full auto: capture → ask Gemini → click/type → verify → advance.

    Every click target is located by vision (result.target_box) and we click its
    CENTER — no fixed pixel coordinates. Mirrors run_assist's signature so the GUI
    can drive it in a worker thread.

    CLI mode (events all None): prints to stdout, sleeps STARTUP_GRACE, stops on
    Ctrl+C. GUI mode: uses threading Events and callbacks.
    """
    cli = stop_event is None

    def _log(msg: str) -> None:
        if on_status:
            on_status(msg)
        else:
            print(msg)

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def _sleep(secs: float) -> None:
        """Interruptible sleep — wakes immediately on stop."""
        end = time.time() + secs
        while time.time() < end and not _stopped():
            time.sleep(0.05)

    def _wait_if_paused() -> bool:
        """Block while paused. Returns True if we should abort (stopped)."""
        if pause_event is None or not pause_event.is_set():
            return False
        _log("[auto] Paused.")
        while pause_event.is_set() and not _stopped():
            time.sleep(0.1)
        if _stopped():
            return True
        _log("[auto] Resumed.")
        return False

    # Capture the full monitor by default so every click target is in-frame.
    auto_region = config.AUTO_CAPTURE_REGION
    if auto_region is None:
        auto_region = screen_capture.monitor_region()

    def _capture(region=auto_region):
        return screen_capture.capture(region=region)

    def _field_region(frame, box, pad: int = 12):
        """Screen-pixel (l,t,w,h) around a normalized box, for verify snapshots."""
        ymin, xmin, ymax, xmax = box
        left = frame.offset_x + int(xmin / 1000.0 * frame.width) - pad
        top = frame.offset_y + int(ymin / 1000.0 * frame.height) - pad
        w = int((xmax - xmin) / 1000.0 * frame.width) + 2 * pad
        h = int((ymax - ymin) / 1000.0 * frame.height) + 2 * pad
        return (left, top, max(w, 1), max(h, 1))

    # ── per-screen action handlers ──────────────────────────────────────────

    def _answer_multiple_choice(frame, result) -> None:
        pt = frame.box_center_to_screen(result.target_box)
        if pt is None and result.click_x:
            # 2-option fallback: Gemini's x picks the tile; use box if we can.
            pt = frame.to_screen(result.click_x, result.click_y or 500)
        if pt is None:
            _log(f"[auto] Could not locate answer tile for '{result.answer}'; "
                 "skipping (will retry).")
            return
        if dry_run:
            _log(f"[dry-run] would click '{result.answer}' at {pt}")
        else:
            input_controller.click(pt[0], pt[1], result.answer)

    def _answer_fill_in(frame, result) -> None:
        pt = frame.box_center_to_screen(result.target_box)
        if pt is None:
            _log("[auto] Could not locate the input field; skipping (will retry).")
            return
        x, y = pt
        if dry_run:
            _log(f"[dry-run] would type {result.answer!r} into the field at {pt}, "
                 "then Enter")
            return

        verify = config.AUTO_VERIFY_TYPING and bool(result.target_box)
        before = None
        if verify:
            fr = _field_region(frame, result.target_box)
            before = _thumb(_capture(fr).png_bytes)

        for attempt in range(config.AUTO_ACTION_RETRIES + 1):
            input_controller.answer_fill_in(x, y, result.answer)
            _sleep(0.3)
            if not verify:
                break
            after = _thumb(_capture(_field_region(frame, result.target_box)).png_bytes)
            if _frame_diff(before, after) >= config.AUTO_VERIFY_THRESHOLD:
                break  # text actually appeared in the field
            _log(f"[auto] Typing didn't register (attempt {attempt + 1}); retrying.")

        input_controller.press_key("enter", "submit")

    def _handle_video() -> None:
        _log("[auto] Video detected.")
        if dry_run:
            _log("[dry-run] would reveal controls, try gear→speed→1.5x (vision), "
                 "verify not paused, else wait it out at 1x.")
            return

        left, top, w, h = auto_region
        cx = left + w // 2
        y_bottom = top + int(h * 0.92)
        y_up = top + int(h * 0.72)

        def _reveal() -> None:
            input_controller.sweep(cx, y_bottom, cx, y_up,
                                   duration=config.AUTO_VIDEO_REVEAL_DURATION,
                                   label="reveal video controls")
            input_controller.wiggle(cx, y_up, amplitude=20, reps=3,
                                    label="keep controls alive")

        def _locate_and_click(what: str) -> bool:
            if _stopped():
                return False
            f = _capture()
            box = _call_gemini_interruptible(
                gemini_vision.locate, f.png_bytes, what, f.mime_type,
                stopped_fn=_stopped)
            if box is None or _stopped():
                return False
            pt = f.box_center_to_screen(box)
            if pt is None:
                return False
            input_controller.click(pt[0], pt[1], what)
            _sleep(config.AUTO_VIDEO_STEP_DELAY)
            return True

        # Try vision-guided 1.5x.
        _reveal()
        speed_set = (
            _locate_and_click("the settings/gear/options icon in the video "
                              "player control bar")
            and _locate_and_click("the playback speed menu option in the open "
                                  "settings menu")
            and _locate_and_click("the 1.5x (or '1.5') playback speed option")
        )

        if not speed_set:
            _log("[auto] Couldn't set 1.5x via controls — falling back to 1x wait.")
            input_controller.press_key("esc", "close any open menu")
            _sleep(0.3)

        # If our clicks paused the video, resume it.
        if _stopped():
            return
        _reveal()
        f = _capture()
        pause_box = _call_gemini_interruptible(
            gemini_vision.locate, f.png_bytes,
            "a large play (triangle) button overlay in the "
            "center of the video, shown only when paused",
            f.mime_type,
            stopped_fn=_stopped,
        )
        if pause_box is not None and not _stopped():
            _log("[auto] Video is paused — clicking to resume.")
            input_controller.click(cx, top + h // 2, "resume video")
            _sleep(0.5)

        # Read remaining time and wait it out.
        if _stopped():
            return
        _reveal()
        _vf = _capture()
        secs = _call_gemini_interruptible(
            gemini_vision.read_video_remaining, _vf.png_bytes, _vf.mime_type,
            stopped_fn=_stopped) or 30.0
        if speed_set:
            wait = secs / config.VIDEO_SPEED_MULTIPLIER + config.AUTO_VIDEO_BUFFER
            _log(f"[auto] ~{secs:.0f}s left at 1.5x → waiting {wait:.0f}s.")
        else:
            wait = secs + config.AUTO_VIDEO_BUFFER
            _log(f"[auto] ~{secs:.0f}s left at 1x → waiting {wait:.0f}s.")
        _sleep(wait)

    # ── startup ───────────────────────────────────────────────────────────────

    if cli:
        print("\n" + "-" * 60)
        print("  Acellus Vision Automation — AUTO mode")
        print("  The bot will CONTROL your mouse and keyboard.")
        print("  Bring Acellus to the FOREGROUND now.")
        if not dry_run:
            print(f"  Starting in {config.STARTUP_GRACE:.0f}s ...")
        print("  (Ctrl+C or slam mouse to a corner to abort.)")
        print("-" * 60 + "\n")
        if not dry_run:
            time.sleep(config.STARTUP_GRACE)

    answered = 0
    idle_frames = 0
    last_question = ""
    stuck_count = 0
    last_auto_thumb = None   # for screen-change dedup (avoid API on unchanged frames)
    acted = True             # True on first iteration and after every action

    try:
        while not _stopped():
            if _wait_if_paused():
                break

            frame = _capture()
            thumb = _thumb(frame.png_bytes)
            if (not acted
                    and last_auto_thumb is not None
                    and _frame_diff(last_auto_thumb, thumb) < config.ASSIST_CHANGE_THRESHOLD):
                _sleep(config.AUTO_POLL_INTERVAL)
                continue
            last_auto_thumb = thumb

            _log(f"[auto] Captured {frame.width}x{frame.height}. Asking Gemini ...")
            result = _call_gemini_interruptible(
                gemini_vision.analyze, frame.png_bytes, frame.mime_type,
                stopped_fn=_stopped)
            if result is None or _stopped():
                break

            stype = result.screen_type

            # --- Video ---
            if stype == "video":
                _handle_video()
                idle_frames = 0
                acted = True
                if once:
                    break
                continue

            # --- Score / results ---
            if stype == "score":
                pt = frame.box_center_to_screen(result.target_box)
                if dry_run:
                    _log(f"[dry-run] would click Continue at {pt}")
                elif pt is not None:
                    input_controller.click(pt[0], pt[1], "Continue")
                else:
                    _log("[auto] Continue button not located; waiting.")
                _sleep(config.POST_CONTINUE_DELAY)
                idle_frames = 0
                acted = True
                if once:
                    break
                continue

            # --- No answerable question ---
            if not result.question_present or not result.answer.strip():
                idle_frames += 1
                acted = False
                _log(f"[auto] {stype} — waiting "
                     f"(idle {idle_frames}/{config.MAX_IDLE_FRAMES}) ...")
                if once or idle_frames >= config.MAX_IDLE_FRAMES:
                    break
                _sleep(config.AFTER_ADVANCE_DELAY)
                continue

            idle_frames = 0
            q_key = result.question_text.strip()

            # --- Stuck detection (also catches a missed click) ---
            if q_key and q_key == last_question:
                stuck_count += 1
                _log(f"[auto] Same question again (stuck x{stuck_count}).")
                if stuck_count >= 2:
                    _log("[auto] Stuck — stopping.")
                    break
                _sleep(config.AFTER_ADVANCE_DELAY * 2)
                continue
            else:
                stuck_count = 0

            last_question = q_key
            _log(f"[auto] Q: {result.question_text[:120]}")
            _log(f"[auto] A: {result.answer}  [{result.answer_type}]")
            if on_answer:
                on_answer(result)

            if result.answer_type == "multiple_choice":
                _answer_multiple_choice(frame, result)
            else:
                _answer_fill_in(frame, result)

            acted = True
            answered += 1
            _log(f"[auto] Question #{answered} submitted.")

            if once:
                break
            if config.MAX_QUESTIONS and answered >= config.MAX_QUESTIONS:
                _log("[auto] Reached MAX_QUESTIONS — stopping.")
                break

            _sleep(config.AFTER_ADVANCE_DELAY)

    except KeyboardInterrupt:
        if cli:
            print("\n\n[auto] Aborted (Ctrl+C).")
    finally:
        _log(f"[auto] Done. {answered} question(s) handled.")


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
    pause_event=None,
    on_answer=None,
    on_status=None,
) -> None:
    """
    Watch the screen and solve questions — never clicks or types.

    CLI mode (all params None): prints banners, reads R key via msvcrt, stops on Ctrl+C.
    GUI mode: uses threading Events and callbacks instead.

    Args:
        stop_event:  threading.Event — set to stop the loop immediately.
        force_event: threading.Event — set to force an immediate re-check (auto-cleared).
        pause_event: threading.Event — set to pause, clear to resume.
        on_answer:   callable(VisionResult) — called when an answer is ready.
        on_status:   callable(str) — called with status/log messages.
    """
    cli = stop_event is None

    def _log(msg: str) -> None:
        if on_status:
            on_status(msg)
        else:
            print(msg)

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def _forced() -> bool:
        if force_event is not None:
            if force_event.is_set():
                force_event.clear()
                return True
            return False
        return _drain_kb()

    def _sleep(secs: float) -> None:
        """Interruptible sleep — wakes immediately on stop or unpause."""
        end = time.time() + secs
        while time.time() < end and not _stopped():
            time.sleep(0.05)

    def _wait_if_paused() -> bool:
        """Block while paused. Returns True if we should abort (stopped)."""
        if pause_event is None or not pause_event.is_set():
            return False
        _log("[assist] Paused.")
        while pause_event.is_set() and not _stopped():
            time.sleep(0.1)
        if _stopped():
            return True
        _log("[assist] Resumed.")
        return False

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
            if _wait_if_paused():
                break

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
                _sleep(config.ASSIST_POLL_INTERVAL)
                continue

            if forced:
                _log("[assist] Manual re-check triggered ...")
            else:
                _sleep(config.ASSIST_SETTLE_DELAY)
                if _stopped():
                    break
                frame = screen_capture.capture()
                thumb = _thumb(frame.png_bytes)
                graph_frame = screen_capture.capture(region=config.GRAPH_REGION)
                graph_thumb = _thumb(graph_frame.png_bytes)

            last_thumb = thumb
            last_graph_thumb = graph_thumb
            result = _call_gemini_interruptible(
                gemini_vision.analyze, frame.png_bytes, frame.mime_type,
                stopped_fn=_stopped)

            if result is None or _stopped():
                break

            if (result.screen_type == "question"
                    and result.question_present
                    and result.answer.strip()):
                q = result.question_text.strip()
                if q and q == last_question and not forced:
                    _sleep(config.ASSIST_POLL_INTERVAL)
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

            _sleep(config.ASSIST_POLL_INTERVAL)

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
        run_auto(once=args.once, dry_run=args.dry_run)
    else:
        run_assist()


if __name__ == "__main__":
    main()
