# ApexAutomation

Vision-driven automation for the **Acellus** desktop app, powered by
**Gemini 2.5 Flash** vision. It looks at your screen, solves the on-screen
question, and (in Auto mode) answers it by controlling your real mouse and keyboard.

No browser, no DOM selectors — it works off screenshots, so it adapts to
whatever Acellus puts on screen.

## Architecture

```
orchestrator.py        ← main loop: capture → think → act → verify → advance
  ├── screen_capture.py ← grabs the Acellus window/monitor as PNG (mss)
  ├── gemini_vision.py  ← sends the screenshot to Gemini, gets answer + bounding box
  ├── input_controller.py ← human-emulated mouse clicks & keyboard typing
  └── config.py         ← API key, capture region, timing, pacing
```

### How it answers

Gemini returns both the correct answer **and** a bounding box (`target_box`) for the
exact on-screen element to act on — the input field, the correct answer tile, or the
Continue button. Auto mode clicks the **center** of that box.

This is the key difference from the old design: nothing is hard-coded. Gemini locates
the target per-screenshot, so clicks land correctly regardless of where Acellus places
things on your screen.

For each frame Gemini returns:
- **answer** — the correct text
- **answer_type** — `fill_in` or `multiple_choice`
- **target_box** — `[ymin, xmin, ymax, xmax]` normalized 0–1000; Auto clicks its center
- **choice_index** / **num_choices** — used by Assist mode to describe the tile position

### Fill-in verification

After clicking the input field and typing, Auto mode re-screenshots the field region
and compares it to the pre-type snapshot. If nothing changed (focus was missed), it
retries once before pressing Enter — so it never submits a blank answer.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Gemini API key (or hardcode it in config.py)
$env:GEMINI_API_KEY="your-key-here"     # PowerShell
set GEMINI_API_KEY=your-key-here         # Windows CMD

# 3. Launch the GUI (recommended)
python gui.py

# 4. Or run from the command line
python orchestrator.py            # assist mode: the bot solves, YOU click
python orchestrator.py --auto     # full auto: the bot clicks and types for you
```

### Two modes

**Assist mode (default)** — the bot never touches your mouse or keyboard. Whenever the
question changes, it solves it and shows the answer in the GUI (big banner in CLI mode).
For multiple-choice it tells you the tile position (e.g. `TOP-RIGHT`); for fill-in it
tells you what to type. Only calls Gemini when the screen actually changes, saving tokens.

**Auto mode** (`--auto` flag, or "Auto" toggle in the GUI) — fully hands-off. The bot
loops capture → Gemini → click/type → verify → advance, handling:

- **Fill-in questions**: locates the input field by vision, focuses it, clears it, types
  the answer, verifies text appeared, then submits.
- **Multiple-choice**: locates the correct answer tile by vision and clicks its center.
  Works for 2-, 3-, and 4-option layouts without any fixed grid positions.
- **Score screens**: locates the Continue button by vision and clicks it.
- **Videos**: reveals controls with an upward sweep, then attempts gear → Speed → 1.5× by
  vision. Confirms the video is still playing (resumes if paused). If any step fails,
  falls back to waiting at 1× speed. Either way, waits out the remaining time and moves on.

### Stopping

Click **Stop** (or slam the mouse into a corner). The bot stops at the next opportunity —
if Gemini is mid-call, it waits for that call to return (usually a few seconds) then exits
immediately. The status bar shows "STOPPING…" during this brief window.

## GUI

Launch `ApexAutomation.pyw` (no terminal) or `python gui.py`.

- **Assist / Auto** radio buttons below the control row select the mode.
- Switching to Auto shows a confirmation dialog before starting.
- **Pause** / **Timed Pause** both work in either mode.
- The answer card shows what the bot solved and (in Assist mode) where to click.
- On first launch (no `user_config.json`) the Setup Wizard opens automatically.

## Configuration (`config.py`)

| Constant | Purpose |
|---|---|
| `GEMINI_API_KEY` | Your Gemini key (env var `GEMINI_API_KEY` overrides it) |
| `GEMINI_MODEL` | Vision model (default `gemini-2.5-flash`) |
| `CAPTURE_MONITOR` | Which monitor to grab (1 = primary) |
| `CAPTURE_REGION` | Tight crop for Assist mode (question card only); `None` = whole monitor |
| `AUTO_CAPTURE_REGION` | Capture region for Auto mode; `None` = full monitor (default, needed to see every click target) |
| `STARTUP_GRACE` | Seconds before auto loop starts (time to focus Acellus) |
| `AFTER_ADVANCE_DELAY` | Wait between questions while the next one loads |
| `AUTO_ACTION_RETRIES` | How many times to retry typing if the field didn't register |
| `AUTO_VERIFY_TYPING` | Re-screenshot the field to confirm text appeared before submitting |
| `VIDEO_SPEED_MULTIPLIER` | Expected speed after setting 1.5× (used to compute wait time) |
| `TYPING_DELAY_MIN/MAX` | Per-keystroke delay range (seconds) |
| `MOUSE_MOVE_DURATION_MIN/MAX` | Mouse glide time range (seconds) |

## Safety

- **Dry run:** `python orchestrator.py --auto --once --dry-run` prints the
  planned click targets (box centers in screen pixels) without moving the mouse.
- **Fail-safe:** slam the mouse pointer into any screen corner to instantly
  abort (pyautogui fail-safe), or press **Ctrl+C** in the terminal.
- **Stop button:** in the GUI, Stop signals the worker to exit; it finishes
  the current Gemini call (a few seconds at most) then halts.
