# ApexAutomation

Vision-driven automation for the **Acellus** desktop app, powered by
**Gemini 2.5 Flash** vision. It looks at your screen, solves the on-screen
question, and answers it by controlling your real mouse and keyboard.

No browser, no DOM selectors — it works off screenshots, so it adapts to
whatever Acellus puts on screen.

## Architecture

```
orchestrator.py        ← main loop: capture → think → act → advance
  ├── screen_capture.py ← grabs the Acellus window/monitor as PNG (mss)
  ├── gemini_vision.py  ← sends the screenshot to Gemini, gets an action plan
  ├── input_controller.py ← human-emulated mouse clicks & keyboard typing
  └── config.py         ← API key, capture region, timing, pacing
```

### How it answers

For each frame Gemini returns an ordered list of actions in normalized
(0–1000) coordinates:

- **click** → for multiple-choice answers and the submit/next button
- **type** → focuses an input box and types the answer for fill-in questions

`screen_capture.Frame.to_screen()` converts those normalized coordinates into
absolute screen pixels for the mouse.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Gemini API key (or hardcode it in config.py)
$env:GEMINI_API_KEY="your-key-here"     # PowerShell
set GEMINI_API_KEY=your-key-here         # Windows CMD

# 3. Assist mode (DEFAULT): the bot solves, YOU click
python orchestrator.py

# 4. Full auto: the bot clicks and types for you
python orchestrator.py --auto

# Test what Gemini sees WITHOUT moving the mouse (auto mode)
python orchestrator.py --auto --once --dry-run
```

### Two modes

**Assist mode (default)** — the main way to use this. The bot never touches
your mouse or keyboard. It watches the screen and, whenever the question
changes, solves it and prints the answer in a big banner telling you exactly
what to click or type. You do the clicking. To save tokens it only calls Gemini
when the screen actually changes (so it auto-detects when you move to the next
question). For multiple-choice it tells you the tile position (e.g.
`option 2 (TOP-RIGHT)`); for fill-in it tells you the value to type.

**Auto mode** (`--auto`) — fully hands-off. The bot loops
capture → ask Gemini → click/type → advance, handling fill-in, multiple-choice,
score screens, and videos (setting 1.5x speed) on its own. You have a few
seconds (`STARTUP_GRACE`) at launch to bring Acellus to the foreground.

## Configuration (`config.py`)

| Constant | Purpose |
|---|---|
| `GEMINI_API_KEY` | Your Gemini key (env var `GEMINI_API_KEY` overrides it) |
| `GEMINI_MODEL` | Vision model (default `gemini-2.5-flash`) |
| `CAPTURE_MONITOR` | Which monitor to grab (1 = primary) |
| `CAPTURE_REGION` | `(left, top, w, h)` to capture just the Acellus window; `None` = whole monitor |
| `STARTUP_GRACE` | Seconds before the loop starts (time to focus Acellus) |
| `AFTER_ADVANCE_DELAY` | Wait between questions while the next one loads |
| `MAX_IDLE_FRAMES` | Stop after this many frames with no question |
| `MAX_QUESTIONS` | Safety cap on questions per run (`None` = unlimited) |
| `TYPING_DELAY_MIN/MAX` | Per-keystroke delay range (seconds) |
| `MOUSE_MOVE_DURATION_MIN/MAX` | Mouse glide time range (seconds) |

## Safety

- **Dry run first:** `python orchestrator.py --once --dry-run` prints the
  planned clicks/typing and the pixel targets without touching your mouse.
- **Fail-safe:** slam the mouse pointer into any screen corner to instantly
  abort (pyautogui fail-safe), or press **Ctrl+C** in the terminal.
- **Tighten the capture region** (`CAPTURE_REGION`) around the Acellus window
  for faster, more accurate targeting.
