"""
config.py – Configuration for the vision-based Acellus automation.
"""

# ─────────────────────────────────────────────
# SCREEN CAPTURE
# ─────────────────────────────────────────────

CAPTURE_MONITOR = 1

# Crop tightly around the Acellus question card ONLY (no lesson panel, no
# empty dark margins). On a 2560x1080 screen the card sits in the center-left.
# Gemini reads math much more accurately when the equation fills the image.
# Adjust left/top/width/height if your screen layout differs.
CAPTURE_REGION = (850, 310, 850, 600)  # (left, top, width, height) — reaches y=910

# ─────────────────────────────────────────────
# FIXED INPUT BAR POSITION
# ─────────────────────────────────────────────

# The Acellus input bar is always at the same screen position for fill-in
# questions. Rather than asking Gemini to guess coordinates (error-prone),
# we click here directly. Adjust these if clicks land in the wrong place.
#
# To calibrate: hover your mouse over the center of the dark input bar
# in Acellus and note the (x, y) coordinates shown in your cursor tool.
INPUT_BAR_X = 1230   # screen pixel X center of the input bar
INPUT_BAR_Y = 946   # screen pixel Y center of the input bar

# Fixed screen Y for 2-option multiple-choice tiles (Yes/No).
# Gemini's x picks the correct tile; y is overridden because Gemini's y is ~70px low.
CHOICE_Y = 855

# Fixed positions for 3-option multiple-choice questions (single row):
#   [1]  [2]  [3]
MC3_POSITIONS = {
    1: (1411, 709),
    2: (1476, 709),
    3: (1556, 709),
}

# Fixed positions for 4-option multiple-choice questions (2×2 grid layout):
#   [1]  [2]
#   [3]  [4]
MC4_POSITIONS = {
    1: (1043, 750),
    2: (1399, 767),
    3: (1098, 838),
    4: (1454, 837),
}

# Position of the "Continue" button on score/results screens.
CONTINUE_BTN_X = 1528
CONTINUE_BTN_Y = 840

# Sweep start: begin at the BOTTOM EDGE of the video so the upward movement
# triggers the control bar. Move fast upward-and-right toward the settings icon.
VIDEO_HOVER_X = 1100
VIDEO_HOVER_Y = 980   # bottom edge of video — sweep goes UP from here

# Small capture region centered on the video time display at (1659, 895).
# Captures ~200px wide around that point so Gemini sees "elapsed / total" text.
VIDEO_TIME_REGION = (1560, 878, 220, 34)   # (left, top, width, height)

# Video speed controls (revealed after hovering).
VIDEO_SETTINGS_X = 1694   # settings/gear icon
VIDEO_SETTINGS_Y = 901
VIDEO_SPEED_MENU_X = 1694  # "Speed" option inside settings menu
VIDEO_SPEED_MENU_Y = 812
VIDEO_SPEED_1_5X_X = 1681  # 1.5x speed choice
VIDEO_SPEED_1_5X_Y = 851
VIDEO_SPEED_MULTIPLIER = 1.5

# ─────────────────────────────────────────────
# AUTOMATION LOOP
# ─────────────────────────────────────────────

STARTUP_GRACE = 4.0
AFTER_ADVANCE_DELAY = 3.0
POST_CONTINUE_DELAY = 8.0    # wait after clicking Continue before first check
VIDEO_POLL_INTERVAL = 30.0
ACTION_PACING = 0.5
MAX_IDLE_FRAMES = 3
MAX_QUESTIONS = None

# ─────────────────────────────────────────────
# ASSIST MODE (you click, the bot solves)
# ─────────────────────────────────────────────
# In assist mode the bot never touches the mouse/keyboard. It watches the
# screen and, whenever the question changes, solves it and shows you the
# answer so you can click it yourself. To save tokens it only calls Gemini
# when the captured image actually changes.

ASSIST_POLL_INTERVAL = 0.7     # seconds between cheap screen checks
ASSIST_SETTLE_DELAY = 0.6      # wait after a change before solving (let it render)
ASSIST_CHANGE_THRESHOLD = 6.0  # mean per-pixel diff (0-255) that counts as "changed"

# Graph region uses a much lower threshold. Graphs occupy only a fraction of
# this area so a new graph produces a small mean diff — we need to be sensitive.
ASSIST_GRAPH_CHANGE_THRESHOLD = 1.5

# Extra region watched for graph changes. Questions can share the same text but
# show a different graph — so we diff this area independently from the question
# card. (1200,370) → (1700,770) in screen coords = 500×400px.
GRAPH_REGION = (1200, 370, 500, 400)   # (left, top, width, height)

# ─────────────────────────────────────────────
# HUMAN-EMULATION
# ─────────────────────────────────────────────

MOUSE_MOVE_DURATION_MIN = 0.25
MOUSE_MOVE_DURATION_MAX = 0.5
PRE_CLICK_DELAY_MIN = 0.15
PRE_CLICK_DELAY_MAX = 0.35
TYPING_DELAY_MIN = 0.05
TYPING_DELAY_MAX = 0.12

# ─────────────────────────────────────────────
# GEMINI VISION API
# ─────────────────────────────────────────────

# Set via environment variable (recommended) or paste your key here as a fallback.
# Get a free key at https://aistudio.google.com/apikey
GEMINI_API_KEY = ""
GEMINI_MODEL = "gemini-2.5-flash"
IMAGE_MAX_WIDTH = None

MAX_RETRIES = 4
RETRY_BASE_DELAY = 2.0
RETRY_JITTER = 1.0

# Gemini's only job now is to READ the question and return the correct answer.
# Coordinates are handled by fixed positions above -- Gemini does NOT need to
# locate UI elements, which was causing all the vision mistakes.
GEMINI_SYSTEM_INSTRUCTION = (
    "You are looking at a screenshot of the Acellus learning app. "
    "Identify the screen type, then respond accordingly.\n\n"

    "SCREEN TYPES:\n"
    "  'question' -- an answerable question (fill-in box OR multiple-choice tiles).\n"
    "  'video'    -- a lesson video is playing.\n"
    "  'score'    -- score/results card (%, Accuracy, Reward, Continue button).\n"
    "  'other'    -- loading, transition, or anything else.\n\n"

    "IF screen_type == 'question':\n"
    "  Read carefully. Coefficients matter: '7x' = seven times x, not just x. "
    "  En-dash (--) between terms = subtraction.\n\n"

    "  answer_type must be one of:\n"
    "  'fill_in'        -- there is a text input bar to type into.\n"
    "  'multiple_choice'-- there are clickable answer tiles (Yes/No, A/B/C/D, etc.).\n\n"

    "  For 'fill_in': set answer to the value to type. "
    "  Leave click_x and click_y as 0.\n\n"

    "  For 'multiple_choice':\n"
    "    Count the answer tiles. Set num_choices to 2 or 4.\n"
    "    Set answer to the text of the correct choice.\n\n"

    "    If num_choices == 4 (2x2 grid layout):\n"
    "      Number them: 1=top-left, 2=top-right, 3=bottom-left, 4=bottom-right.\n"
    "      Set choice_index to the correct tile number (1, 2, 3, or 4).\n"
    "      Leave click_x and click_y as 0.\n\n"

    "    If num_choices == 3 (vertical column of 3 tiles):\n"
    "      Number them top to bottom: 1=top, 2=middle, 3=bottom.\n"
    "      Set choice_index to the correct tile number (1, 2, or 3).\n"
    "      Leave click_x and click_y as 0.\n\n"

    "    If num_choices == 2 (side-by-side layout, e.g. Yes/No):\n"
    "      Set choice_index to 1 (left tile) or 2 (right tile).\n"
    "      Set click_x to the NORMALIZED x center (0-1000) of the correct tile.\n"
    "      Leave click_y as 0.\n\n"

    "IF screen_type != 'question': set question_present=false, answer=''.\n\n"

    "Return ONLY the JSON."
)

# ─────────────────────────────────────────────
# USER CONFIG OVERRIDES  (written by setup wizard)
# ─────────────────────────────────────────────
# user_config.json maps wizard-captured keys to config vars.
# Point keys (INPUT_BAR, CONTINUE_BTN, …) → expand to _X / _Y vars.
# Region keys (CAPTURE_REGION, …) → stored as [l,t,w,h] → tuple.
# MC dicts (MC3_POSITIONS, MC4_POSITIONS) → list of [x,y] → {1:(x,y), …}.
# CHOICE_Y → int.

import json as _json, os as _os

_POINT_KEYS = {
    "INPUT_BAR":       ("INPUT_BAR_X",       "INPUT_BAR_Y"),
    "CONTINUE_BTN":    ("CONTINUE_BTN_X",    "CONTINUE_BTN_Y"),
    "VIDEO_HOVER":     ("VIDEO_HOVER_X",     "VIDEO_HOVER_Y"),
    "VIDEO_SETTINGS":  ("VIDEO_SETTINGS_X",  "VIDEO_SETTINGS_Y"),
    "VIDEO_SPEED_MENU":("VIDEO_SPEED_MENU_X","VIDEO_SPEED_MENU_Y"),
    "VIDEO_SPEED_1_5X":("VIDEO_SPEED_1_5X_X","VIDEO_SPEED_1_5X_Y"),
}
_REGION_KEYS = {"CAPTURE_REGION", "VIDEO_TIME_REGION", "GRAPH_REGION"}

_ucfg_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "user_config.json")
if _os.path.exists(_ucfg_path):
    with open(_ucfg_path) as _f:
        _ucfg = _json.load(_f)
    _g = globals()
    for _k, _v in _ucfg.items():
        if _k == "GEMINI_API_KEY":
            _g[_k] = str(_v)
        elif _k in _POINT_KEYS:
            _xk, _yk = _POINT_KEYS[_k]
            _g[_xk], _g[_yk] = int(_v[0]), int(_v[1])
        elif _k in _REGION_KEYS:
            _g[_k] = tuple(int(x) for x in _v)
        elif _k == "CHOICE_Y":
            _g[_k] = int(_v)
        elif _k == "MC3_POSITIONS":
            _g[_k] = {i + 1: (int(p[0]), int(p[1])) for i, p in enumerate(_v)}
        elif _k == "MC4_POSITIONS":
            _g[_k] = {i + 1: (int(p[0]), int(p[1])) for i, p in enumerate(_v)}
