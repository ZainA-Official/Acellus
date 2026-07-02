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
import pyautogui

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

    def _box_center(b: list[float]) -> tuple[float, float]:
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    def _box_dist(a: list[float], b: list[float]) -> float:
        ay, ax = _box_center(a)
        by, bx = _box_center(b)
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    def _answer_fill_in(frame, result) -> None:
        nonlocal cached_fill_in_box, pending_fill_in_box, pending_fill_in_count

        raw_box = result.target_box
        box = raw_box

        # Gemini's bounding box for the (fixed) input bar can drift slightly
        # frame-to-frame, especially right after a misread question — a single
        # bad reading must NOT be allowed to drag the click target around.
        # Lock to the first good detection. A new box is only accepted as the
        # real position once it shows up TWICE in a row (hysteresis); a
        # one-off divergent reading is ignored and we keep using the cache.
        SNAP_THRESHOLD = 60  # ~6% of image — "same spot" tolerance
        if cached_fill_in_box and raw_box and len(raw_box) == 4:
            dist = _box_dist(cached_fill_in_box, raw_box)
            if dist < SNAP_THRESHOLD:
                box = cached_fill_in_box  # bar hasn't moved — don't jitter
                pending_fill_in_box = None
                pending_fill_in_count = 0
            else:
                if pending_fill_in_box and _box_dist(pending_fill_in_box, raw_box) < SNAP_THRESHOLD:
                    pending_fill_in_count += 1
                else:
                    pending_fill_in_box = raw_box
                    pending_fill_in_count = 1

                if pending_fill_in_count >= 2:
                    _log(f"[auto] Input bar position confirmed changed (drift={dist:.1f}); updating cache.")
                    cached_fill_in_box = raw_box
                    box = raw_box
                    pending_fill_in_box = None
                    pending_fill_in_count = 0
                else:
                    _log(f"[auto] Ignoring one-off box drift (drift={dist:.1f}); keeping cached position.")
                    box = cached_fill_in_box
        elif raw_box and len(raw_box) == 4:
            cached_fill_in_box = raw_box  # first detection — save it

        pt = frame.box_center_to_screen(box)
        if pt is None:
            _log("[auto] Could not locate the input field; skipping (will retry).")
            return
        x, y = pt

        if dry_run:
            _log(f"[dry-run] would type {result.answer!r} into field at {pt}, then Enter")
            return

        input_controller.answer_fill_in(x, y, result.answer)
        _sleep(0.3)
        input_controller.press_key("enter", "submit")

    def _handle_video() -> None:
        _log("[auto] Video detected.")
        if dry_run:
            _log("[dry-run] would reveal controls, try gear→speed→1.5x "
                 "(vision + calibrated fallback), verify not paused, else wait "
                 "it out at 1x.")
            return

        left, top, w, h = auto_region

        # Anchor the reveal sweep on the calibrated video position when it falls
        # inside the captured monitor; otherwise fall back to region geometry.
        if left <= config.VIDEO_HOVER_X <= left + w and top < config.VIDEO_HOVER_Y <= top + h:
            cx = config.VIDEO_HOVER_X
            y_bottom = config.VIDEO_HOVER_Y
        else:
            cx = left + w // 2
            y_bottom = top + int(h * 0.92)
        y_up = max(top + 1, y_bottom - config.AUTO_VIDEO_SWEEP_UP)
        y_ctrl = max(top + 1, y_bottom - 40)  # where the control bar sits

        def _in_region(pt) -> bool:
            return (pt is not None
                    and left <= pt[0] <= left + w
                    and top <= pt[1] <= top + h)

        def _reveal(reps: int = 3) -> None:
            """Summon the auto-hiding control bar: sweep up from the bottom edge,
            then wiggle over the bar to keep it on screen."""
            input_controller.sweep(cx, y_bottom, cx, y_up,
                                   duration=config.AUTO_VIDEO_REVEAL_DURATION,
                                   label="reveal video controls")
            input_controller.wiggle(cx, y_ctrl, amplitude=20, reps=reps,
                                    label="keep controls alive")

        def _click_step(what: str, fallback_xy, resummon: bool) -> bool:
            """Locate `what` by vision and click it; retry, then fall back to the
            calibrated fixed coordinate.

            `resummon`=True re-summons the control bar before the capture and
            again right before the click — needed for the gear icon, whose bar
            auto-hides during the (multi-second) Gemini call. For steps that act
            on an already-open menu (speed option, 1.5x), resummon=False leaves
            the menu untouched instead of sweeping the mouse away and closing it.
            Returns True once a click has been issued.
            """
            for attempt in range(config.AUTO_VIDEO_LOCATE_TRIES):
                if _stopped():
                    return False
                if resummon:
                    _reveal()
                f = _capture()
                box = _call_gemini_interruptible(
                    gemini_vision.locate, f.png_bytes, what, f.mime_type,
                    stopped_fn=_stopped)
                if _stopped():
                    return False
                pt = f.box_center_to_screen(box) if box else None
                if _in_region(pt):
                    # The overlay auto-hides while Gemini was thinking; re-summon
                    # so the bar is actually on screen at the instant we click.
                    if resummon:
                        input_controller.wiggle(cx, y_ctrl, amplitude=15, reps=2,
                                                label="hold before click")
                    input_controller.click(pt[0], pt[1], what)
                    _sleep(config.AUTO_VIDEO_STEP_DELAY)
                    return True
                _log(f"[auto] «{what}» not located "
                     f"(try {attempt + 1}/{config.AUTO_VIDEO_LOCATE_TRIES}).")

            if config.AUTO_VIDEO_USE_FIXED_FALLBACK and _in_region(fallback_xy):
                _log(f"[auto] Using calibrated fallback for «{what}» at {fallback_xy}.")
                if resummon:
                    _reveal(reps=2)
                input_controller.click(fallback_xy[0], fallback_xy[1],
                                       f"{what} (calibrated fallback)")
                _sleep(config.AUTO_VIDEO_STEP_DELAY)
                return True
            return False

        # Try vision-guided 1.5x, falling back to calibrated coordinates per step.
        _reveal()
        speed_set = (
            _click_step("the settings/gear/options icon in the video player "
                        "control bar",
                        (config.VIDEO_SETTINGS_X, config.VIDEO_SETTINGS_Y),
                        resummon=True)
            and _click_step("the playback speed menu option in the open settings "
                            "menu",
                            (config.VIDEO_SPEED_MENU_X, config.VIDEO_SPEED_MENU_Y),
                            resummon=False)
            and _click_step("the 1.5x (or '1.5') playback speed option",
                            (config.VIDEO_SPEED_1_5X_X, config.VIDEO_SPEED_1_5X_Y),
                            resummon=False)
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

    def _attempt_recovery(frame) -> None:
        """
        Try to unstick an unrecognized or blocked screen: locate a close (X)
        button, 'OK'/'Dismiss'/'Continue' button, or anything else that would
        close a popup/notification/overlay, and click it. Falls back to
        pressing Escape. Never raises — this exists purely so an overnight,
        unattended run keeps going instead of idling forever or giving up.
        """
        if _stopped():
            return
        box = None
        try:
            box = _call_gemini_interruptible(
                gemini_vision.locate, frame.png_bytes,
                "a close (X) button, an 'OK'/'Got it'/'Dismiss'/'Continue' "
                "button, or any other element that would close a popup, "
                "notification, or overlay currently blocking the lesson content",
                frame.mime_type, stopped_fn=_stopped)
        except Exception as exc:
            _log(f"[auto] Recovery lookup failed ({exc!r}); falling back to Escape.")
        if box is not None and not _stopped():
            pt = frame.box_center_to_screen(box)
            if pt is not None:
                _log("[auto] Recovery: closing detected popup/overlay.")
                if not dry_run:
                    input_controller.click(pt[0], pt[1], "dismiss popup (recovery)")
                return
        _log("[auto] Recovery: nothing found to click — pressing Escape.")
        if not dry_run:
            input_controller.press_key("esc", "recovery")

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
    last_answer = ""
    stuck_count = 0
    nav_stuck = 0            # consecutive navigation clicks with no visible effect
    last_auto_thumb = None   # for screen-change dedup (avoid API on unchanged frames)
    acted = True             # True on first iteration and after every action
    cached_fill_in_box: list[float] | None = None  # stable input-bar box across questions
    pending_fill_in_box: list[float] | None = None  # candidate replacement, awaiting confirmation
    pending_fill_in_count = 0

    try:
        while not _stopped():
            if _wait_if_paused():
                break

            # Everything below is wrapped so an unexpected error (a Gemini call
            # that exhausted its own retries, a transient screenshot failure,
            # etc.) logs and cools down instead of crashing an overnight run.
            # The mouse-to-corner failsafe is the one error that must still
            # propagate — it's the user's deliberate emergency abort.
            try:
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

                # --- Navigation (goal overlay, course-select, popups) ---
                if stype == "navigation":
                    pt = frame.box_center_to_screen(result.target_box)
                    if dry_run:
                        _log(f"[dry-run] would click navigation target at {pt}")
                    elif pt is not None:
                        _log(f"[auto] Navigation screen — clicking to continue at {pt}.")
                        input_controller.click(pt[0], pt[1], "navigation")
                        _sleep(config.AUTO_SETTLE_DELAY * 2)
                        # Verify the click actually took us somewhere. A missed
                        # click on a popup would otherwise re-fire on the same
                        # dead spot forever; escalate to active recovery instead.
                        after = _thumb(_capture().png_bytes)
                        if _frame_diff(thumb, after) < config.ASSIST_CHANGE_THRESHOLD:
                            nav_stuck += 1
                            _log(f"[auto] Navigation click had no visible effect "
                                 f"(stuck x{nav_stuck}/{config.NAV_STUCK_LIMIT}).")
                            if nav_stuck >= config.NAV_STUCK_LIMIT:
                                _log("[auto] Navigation stuck — recovery + Escape.")
                                _attempt_recovery(frame)
                                input_controller.press_key("esc", "nav unstick")
                                nav_stuck = 0
                                _sleep(config.AUTO_SETTLE_DELAY)
                        else:
                            nav_stuck = 0
                    else:
                        _log("[auto] Navigation screen but no target found — "
                             "attempting recovery.")
                        _attempt_recovery(frame)
                        _sleep(config.AFTER_ADVANCE_DELAY)
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

                # --- No answerable question (unknown screen, popup, loading) ---
                if not result.question_present or not result.answer.strip():
                    idle_frames += 1
                    acted = False
                    _log(f"[auto] {stype} — waiting (idle {idle_frames}) ...")
                    if once:
                        break
                    # Never give up permanently here — this is meant to run
                    # unattended for hours. Periodically try to actively clear
                    # whatever's blocking progress (e.g. a notification popup
                    # that isn't a recognized screen_type) instead of idling
                    # forever, and back off the poll interval between tries.
                    if idle_frames % config.IDLE_RECOVERY_ATTEMPTS == 0:
                        _attempt_recovery(frame)
                    else:
                        backoff = min(config.AUTO_POLL_INTERVAL * idle_frames,
                                      config.IDLE_MAX_POLL_INTERVAL)
                        _sleep(backoff)
                    continue

                idle_frames = 0
                q_key = result.question_text.strip()
                a_key = result.answer.strip()

                # --- Stuck detection (also catches a missed click) ---
                # For multi-part fill-in questions, consecutive sub-parts share the same
                # question text but have different answers (different blank is green each
                # time). Only call it stuck when BOTH the question text and Gemini's answer
                # are unchanged — that means our action genuinely had no effect.
                stuck = bool(q_key) and q_key == last_question and a_key == last_answer
                if stuck:
                    stuck_count += 1
                    _log(f"[auto] Same question+answer "
                         f"(stuck x{stuck_count}/{config.STUCK_RETRY_LIMIT}).")
                    if stuck_count >= config.STUCK_RETRY_LIMIT:
                        _log("[auto] Giving up on this question after repeated "
                             "attempts — clearing it and moving on (run continues).")
                        if not dry_run:
                            input_controller.press_key("esc", "force-clear stuck question")
                        stuck_count = 0
                        last_question = ""
                        last_answer = ""
                        idle_frames = 0
                        acted = True
                        _sleep(config.AFTER_ADVANCE_DELAY * 2)
                        continue
                    if stuck_count >= 2:
                        # Our last click/type may have been swallowed by a stray
                        # overlay (notification, popup) — try to clear it, then
                        # fall through below to retry the SAME answer this frame.
                        _log("[auto] Action may be blocked — attempting recovery before retry.")
                        _attempt_recovery(frame)
                else:
                    stuck_count = 0

                last_question = q_key
                last_answer = a_key
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

            except pyautogui.FailSafeException:
                raise
            except Exception as exc:
                _log(f"[auto] Unexpected error this cycle: {exc!r} — "
                     f"recovering in {config.ERROR_COOLDOWN:.0f}s.")
                acted = False
                _sleep(config.ERROR_COOLDOWN)
                continue

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

            # Wrapped so a Gemini/network hiccup logs and cools down instead
            # of silently killing an overnight watch session.
            try:
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

            except Exception as exc:
                _log(f"[assist] Unexpected error this cycle: {exc!r} — "
                     f"recovering in {config.ERROR_COOLDOWN:.0f}s.")
                _sleep(config.ERROR_COOLDOWN)
                continue

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
