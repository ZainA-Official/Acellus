"""
setup_wizard.py — First-run setup and settings for ApexAutomation.

Three steps: API key, question-card capture region (Assist mode), graph region
(Assist mode graph-change detection). All the old click-coordinate steps have
been removed — Auto mode now locates every click target by vision (Gemini
bounding boxes) so no screen calibration is needed for clicking.

Legacy coordinates are preserved in user_config_legacy_coords.json if needed.

Run standalone:  python setup_wizard.py
From the GUI:    Settings button
"""

from __future__ import annotations
import json
import os
import sys
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pyautogui

def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(_app_dir(), "user_config.json")

# ── Palette ────────────────────────────────────────────────────────────────────
BG      = "#1a1a2e"
SIDEBAR = "#0f1e3c"
CARD    = "#16213e"
ACCENT  = "#0f3460"
GREEN   = "#4ecca3"
YELLOW  = "#ffd460"
RED     = "#e94560"
TEXT    = "#eaeaea"
DIM     = "#7f8c8d"
BLUE    = "#2196f3"

# ── Step definitions ───────────────────────────────────────────────────────────
# capture_type:
#   "text"   → text entry field    → saved as string
#   "region" → two captures TL→BR → saved as [l,t,w,h]
#
# Only three steps remain. Auto mode finds every click target by vision
# (Gemini bounding boxes), so no click-coordinate calibration is needed.

STEPS: list[tuple[str, str, str, str, str]] = [
    # (save_key, sidebar_name, full_label, capture_type, description)
    (
        "GEMINI_API_KEY",
        "API Key",
        "Gemini API Key",
        "text",
        "ApexAutomation uses Google Gemini to read and solve questions.\n\n"
        "Get a free key at: https://aistudio.google.com/apikey\n\n"
        "Paste your key below:",
    ),
    (
        "CAPTURE_REGION",
        "Question Card",
        "Question Card Area",
        "region",
        "Used by Assist mode: the white box that shows the math problem.\n"
        "A tight crop here gives Gemini a cleaner view of the question.\n\n"
        "Capture 1: hover over the TOP-LEFT corner of the card, then click Capture.\n"
        "Capture 2: hover over the BOTTOM-RIGHT corner, then click Capture.\n\n"
        "You can skip this — the full monitor will be used as a fallback.",
    ),
    (
        "GRAPH_REGION",
        "Graph Area",
        "Graph / Chart Area",
        "region",
        "Used by Assist mode: some questions show a graph alongside the text.\n"
        "This region is watched separately so a new graph triggers a re-check\n"
        "even when the question text hasn't changed.\n\n"
        "Capture 1: hover over the TOP-LEFT corner of the graph area.\n"
        "Capture 2: hover over the BOTTOM-RIGHT corner.\n\n"
        "You can skip this if you don't use Assist mode or have no graph questions.",
    ),
]


class SetupWizard:
    def __init__(self, parent=None):
        if parent:
            self.win = tk.Toplevel(parent)
            self.win.grab_set()
        else:
            self.win = tk.Tk()

        self.win.title("Setup — ApexAutomation")
        self.win.configure(bg=BG)
        self.win.resizable(False, False)
        self.win.geometry("660x450")
        self.win.attributes("-topmost", True)

        self._saved: dict = {}
        self._step = 0
        self._clicks: list[tuple[int, int]] = []
        self._sidebar_rows: list[tuple[tk.Frame, tk.Label, tk.Label]] = []

        self._load_existing()
        self._build()
        self._load_step()
        self._track()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_existing(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    self._saved = json.load(f)
            except Exception:
                pass

    def _save(self):
        existing: dict = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(self._saved)
        with open(CONFIG_PATH, "w") as f:
            json.dump(existing, f, indent=2)

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build(self):
        w = self.win

        # ── Top bar ───────────────────────────────────────────────────────
        top = tk.Frame(w, bg=ACCENT, padx=14, pady=8)
        top.pack(fill="x")
        tk.Label(top, text="Setup Wizard", bg=ACCENT, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        self._prog_lbl = tk.Label(top, text="", bg=ACCENT, fg=DIM,
                                  font=("Segoe UI", 9))
        self._prog_lbl.pack(side="right")

        # ── Two-panel body ────────────────────────────────────────────────
        body = tk.Frame(w, bg=BG)
        body.pack(fill="both", expand=True)

        # Left sidebar
        sb = tk.Frame(body, bg=SIDEBAR, width=150)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        tk.Label(sb, text="STEPS", bg=SIDEBAR, fg=DIM,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=10, pady=(8, 4))

        for i, step in enumerate(STEPS):
            save_key, short, _, _, _ = step
            row = tk.Frame(sb, bg=SIDEBAR, cursor="hand2")
            row.pack(fill="x")

            dot = tk.Label(row, text=" ", bg=SIDEBAR, fg=GREEN,
                           font=("Segoe UI", 9), width=2)
            dot.pack(side="left", padx=(6, 0))

            name = tk.Label(row, text=short, bg=SIDEBAR, fg=DIM,
                            font=("Segoe UI", 8), anchor="w", cursor="hand2")
            name.pack(side="left", fill="x", pady=3)

            # Bind clicks on all parts of the row
            for widget in (row, dot, name):
                widget.bind("<Button-1>", lambda e, idx=i: self._jump(idx))
                widget.bind("<Enter>",    lambda e, r=row: r.configure(bg="#1a2d5a"))
                widget.bind("<Leave>",    lambda e, r=row, idx=i: r.configure(
                    bg=ACCENT if idx == self._step else SIDEBAR))

            self._sidebar_rows.append((row, dot, name))

        # Right content panel
        self._content = tk.Frame(body, bg=BG, padx=16, pady=12)
        self._content.pack(side="left", fill="both", expand=True)

        # Step title
        self._step_lbl = tk.Label(self._content, text="", bg=BG, fg=BLUE,
                                  font=("Segoe UI", 11, "bold"))
        self._step_lbl.pack(anchor="w")

        # Description
        self._desc_lbl = tk.Label(self._content, text="", bg=BG, fg=TEXT,
                                  font=("Segoe UI", 9), wraplength=460,
                                  justify="left")
        self._desc_lbl.pack(anchor="w", pady=(6, 0))

        # Text entry (API key step)
        self._entry_frame = tk.Frame(self._content, bg=BG)
        self._entry_var = tk.StringVar()
        self._entry = tk.Entry(
            self._entry_frame, textvariable=self._entry_var,
            bg=CARD, fg=TEXT, insertbackground=TEXT,
            font=("Consolas", 10), relief="flat", width=46, show="*",
        )
        self._entry.pack(fill="x", ipady=7)
        show_row = tk.Frame(self._entry_frame, bg=BG)
        show_row.pack(anchor="w", pady=(3, 0))
        self._show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(show_row, text="Show key", variable=self._show_var,
                       command=self._toggle_show,
                       bg=BG, fg=DIM, activebackground=BG, selectcolor=CARD,
                       font=("Segoe UI", 8)).pack(side="left")

        # Mouse readout (coordinate steps)
        self._mouse_frame = tk.Frame(self._content, bg=CARD, padx=10, pady=7)
        tk.Label(self._mouse_frame, text="Mouse:", bg=CARD, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left")
        self._pos_var = tk.StringVar(value="—")
        tk.Label(self._mouse_frame, textvariable=self._pos_var, bg=CARD, fg=YELLOW,
                 font=("Consolas", 10, "bold")).pack(side="left", padx=(6, 0))
        self._collect_lbl = tk.Label(self._mouse_frame, text="", bg=CARD, fg=GREEN,
                                     font=("Segoe UI", 9))
        self._collect_lbl.pack(side="right")

        # Action buttons (inside content)
        self._action_row = tk.Frame(self._content, bg=BG)
        self._action_row.pack(anchor="w", pady=(10, 0))
        self._cap_btn = tk.Button(
            self._action_row, text="Capture", command=self._capture,
            bg=GREEN, fg=BG, relief="flat",
            font=("Segoe UI", 9, "bold"), padx=12, pady=5, cursor="hand2",
        )
        self._cap_btn.pack(side="left", padx=(0, 6))
        self._skip_btn = tk.Button(
            self._action_row, text="Skip this step", command=self._skip,
            bg="#333", fg=TEXT, relief="flat",
            font=("Segoe UI", 8), padx=10, pady=5, cursor="hand2",
        )
        self._skip_btn.pack(side="left")

        # Status line
        self._status_var = tk.StringVar(value="")
        tk.Label(self._content, textvariable=self._status_var,
                 bg=BG, fg=DIM, font=("Segoe UI", 8, "italic")
                 ).pack(anchor="w", pady=(4, 0))

        # ── Bottom navigation bar ─────────────────────────────────────────
        nav = tk.Frame(w, bg=ACCENT, padx=14, pady=8)
        nav.pack(fill="x", side="bottom")

        self._back_btn = tk.Button(
            nav, text="← Back", command=self._back,
            bg=CARD, fg=TEXT, relief="flat",
            font=("Segoe UI", 9), padx=10, pady=5, cursor="hand2",
        )
        self._back_btn.pack(side="left")

        tk.Button(
            nav, text="Save & Close", command=self._finish,
            bg=GREEN, fg=BG, relief="flat",
            font=("Segoe UI", 9, "bold"), padx=12, pady=5, cursor="hand2",
        ).pack(side="right")

        self._next_btn = tk.Button(
            nav, text="Next →", command=self._next,
            bg=BLUE, fg=TEXT, relief="flat",
            font=("Segoe UI", 9, "bold"), padx=12, pady=5, cursor="hand2",
        )
        self._next_btn.pack(side="right", padx=(0, 8))

    def _toggle_show(self):
        self._entry.configure(show="" if self._show_var.get() else "*")

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _jump(self, idx: int):
        self._step = idx
        self._load_step()

    def _back(self):
        if self._step > 0:
            self._step -= 1
            self._load_step()

    def _next(self):
        if self._step < len(STEPS) - 1:
            self._step += 1
            self._load_step()
        else:
            self._finish()

    def _skip(self):
        if self._step < len(STEPS) - 1:
            self._step += 1
            self._load_step()
        else:
            self._finish()

    def _load_step(self):
        save_key, short, label, ctype, desc = STEPS[self._step]
        self._clicks = []

        is_text = ctype == "text"

        # Show/hide panels
        if is_text:
            self._entry_frame.pack(anchor="w", pady=(8, 0))
            self._mouse_frame.pack_forget()
            self._cap_btn.configure(text="Save Key")
            existing = self._saved.get(save_key, "")
            self._entry_var.set(existing if isinstance(existing, str) else "")
            self._win_after(lambda: self._entry.focus_set())
        else:  # region
            self._entry_frame.pack_forget()
            self._mouse_frame.pack(fill="x", pady=(8, 0))
            self._clicks_needed = 2
            self._cap_btn.configure(text="Capture 1 — Top-Left")

        # Re-order: desc → input panel → action row → status
        self._desc_lbl.pack_forget()
        self._action_row.pack_forget()

        self._desc_lbl.pack(anchor="w", pady=(6, 0))
        if is_text:
            self._entry_frame.pack(anchor="w", pady=(8, 0))
        else:
            self._mouse_frame.pack(fill="x", pady=(8, 0))
        self._action_row.pack(anchor="w", pady=(10, 0))

        self._step_lbl.configure(text=label)
        self._desc_lbl.configure(text=desc)
        self._prog_lbl.configure(text=f"Step {self._step + 1} / {len(STEPS)}")
        self._collect_lbl.configure(text="")
        self._status_var.set("")
        self._skip_btn.configure(
            text="Skip for now" if is_text else "Skip this step"
        )
        self._back_btn.configure(state="normal" if self._step > 0 else "disabled")
        self._next_btn.configure(
            text="Next →" if self._step < len(STEPS) - 1 else "Finish"
        )

        self._update_sidebar()

    def _win_after(self, fn):
        self.win.after(50, fn)

    def _update_sidebar(self):
        for i, (row, dot, name) in enumerate(self._sidebar_rows):
            save_key = STEPS[i][0]
            is_current = i == self._step
            is_done = save_key in self._saved

            row.configure(bg=ACCENT if is_current else SIDEBAR)
            dot.configure(bg=ACCENT if is_current else SIDEBAR)
            name.configure(bg=ACCENT if is_current else SIDEBAR)

            if is_current:
                dot.configure(text="→", fg=YELLOW)
                name.configure(fg=TEXT)
            elif is_done:
                dot.configure(text="✓", fg=GREEN)
                name.configure(fg=GREEN)
            else:
                dot.configure(text=" ", fg=DIM)
                name.configure(fg=DIM)

    # ── Capture logic ──────────────────────────────────────────────────────────

    def _capture(self):
        save_key, _, _, ctype, _ = STEPS[self._step]

        if ctype == "text":
            val = self._entry_var.get().strip()
            if not val:
                self._status_var.set("Please paste your API key first.")
                return
            self._saved[save_key] = val
            self._collect_lbl.configure(text="✓ saved")
            self._status_var.set("API key saved.")
            self._update_sidebar()
            return

        # For region captures the user must move their mouse to the target AFTER
        # clicking the button. A 3-second countdown gives them time to do that
        # without the captured position being the button itself.
        n_pending = len(self._clicks) + 1
        corner = "Top-Left" if n_pending == 1 else "Bottom-Right"
        self._cap_btn.configure(state="disabled")
        self._skip_btn.configure(state="disabled")
        self._status_var.set(
            f"Move your mouse to the {corner} corner — capturing in 3 …"
        )
        self.win.update()
        self._countdown_capture(save_key, remaining=3)

    def _countdown_capture(self, save_key: str, remaining: int):
        n_pending = len(self._clicks) + 1
        corner = "Top-Left" if n_pending == 1 else "Bottom-Right"

        if remaining > 0:
            self._status_var.set(
                f"Move your mouse to the {corner} corner — capturing in {remaining} …"
            )
            self.win.after(1000, lambda: self._countdown_capture(save_key, remaining - 1))
            return

        # Time's up — read position now.
        x, y = pyautogui.position()
        self._clicks.append((x, y))
        n = len(self._clicks)

        self._cap_btn.configure(state="normal")
        self._skip_btn.configure(state="normal")

        if n == 1:
            self._cap_btn.configure(text="Capture 2 — Bottom-Right")
            self._collect_lbl.configure(text=f"TL ({x},{y})")
            self._status_var.set(
                f"Top-left captured at ({x},{y}).  Now position for Bottom-Right."
            )
        else:
            x1, y1 = self._clicks[0]; x2, y2 = self._clicks[1]
            left = min(x1, x2); top = min(y1, y2)
            w = abs(x2 - x1); h = abs(y2 - y1)
            self._saved[save_key] = [left, top, w, h]
            self._collect_lbl.configure(text=f"✓ {w}×{h}px")
            self._status_var.set(f"Saved ({left},{top},{w},{h})")
            self._cap_btn.configure(text="Re-capture 1 — Top-Left")
            self._clicks = []
            self._update_sidebar()

    # ── Finish ─────────────────────────────────────────────────────────────────

    def _finish(self):
        self._save()
        messagebox.showinfo(
            "Saved",
            "Settings saved to user_config.json.\n\n"
            "Restart the app for changes to take effect.",
            parent=self.win,
        )
        self.win.destroy()

    # ── Mouse tracker ──────────────────────────────────────────────────────────

    def _track(self):
        x, y = pyautogui.position()
        self._pos_var.set(f"({x}, {y})")
        self.win.after(50, self._track)


def run(parent=None):
    wiz = SetupWizard(parent=parent)
    if parent is None:
        wiz.win.mainloop()


if __name__ == "__main__":
    run()
