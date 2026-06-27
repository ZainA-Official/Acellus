"""
gui.py — ApexAutomation desktop GUI.

Launch via ApexAutomation.pyw (no terminal) or:  python gui.py

On first launch (no user_config.json) the Setup Wizard opens automatically.
The Settings button reopens it at any time.
"""

from __future__ import annotations
import os
import queue
import sys
import threading
import winsound

import tkinter as tk
from tkinter import messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import orchestrator
from setup_wizard import CONFIG_PATH

# ── Palette ───────────────────────────────────────────────────────────────────
BG     = "#1a1a2e"
CARD   = "#16213e"
ACCENT = "#0f3460"
GREEN  = "#4ecca3"
YELLOW = "#ffd460"
RED    = "#e94560"
PURPLE = "#9b59b6"
BLUE   = "#2196f3"
TEXT   = "#eaeaea"
DIM    = "#7f8c8d"
WHITE  = "#ffffff"
FLASH  = "#1a4a2e"   # card background during answer flash


class TimedPauseDialog:
    """
    Small popup for entering a timed pause duration.
    Optionally divides by 1.5 for video playback timing.
    Calls on_confirm(seconds) if the user confirms.
    """

    def __init__(self, parent, on_confirm):
        self._on_confirm = on_confirm

        win = tk.Toplevel(parent)
        win.title("Timed Pause")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.geometry("320x230")
        win.attributes("-topmost", True)
        win.grab_set()
        self.win = win

        tk.Label(win, text="Pause for how long?", bg=BG, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 6))

        # Time entry row
        row = tk.Frame(win, bg=BG)
        row.pack(anchor="w", padx=16)

        self._min_var = tk.StringVar(value="5")
        self._sec_var = tk.StringVar(value="00")

        tk.Entry(row, textvariable=self._min_var, width=4,
                 bg=CARD, fg=TEXT, insertbackground=TEXT,
                 font=("Segoe UI", 13), relief="flat", justify="center"
                 ).pack(side="left", ipady=4)
        tk.Label(row, text="min", bg=BG, fg=DIM,
                 font=("Segoe UI", 10)).pack(side="left", padx=(4, 10))
        tk.Entry(row, textvariable=self._sec_var, width=4,
                 bg=CARD, fg=TEXT, insertbackground=TEXT,
                 font=("Segoe UI", 13), relief="flat", justify="center"
                 ).pack(side="left", ipady=4)
        tk.Label(row, text="sec", bg=BG, fg=DIM,
                 font=("Segoe UI", 10)).pack(side="left", padx=(4, 0))

        # 1.5× checkbox
        self._video_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            win, text="Video at 1.5× speed  (enter the full video length;\n"
                      "actual pause = duration ÷ 1.5)",
            variable=self._video_var, command=self._update_preview,
            bg=BG, fg=DIM, activebackground=BG, selectcolor=CARD,
            font=("Segoe UI", 8), justify="left",
        ).pack(anchor="w", padx=16, pady=(10, 0))

        # Preview line
        self._preview_var = tk.StringVar(value="")
        tk.Label(win, textvariable=self._preview_var, bg=BG, fg=GREEN,
                 font=("Segoe UI", 9, "italic")).pack(anchor="w", padx=16, pady=(4, 0))

        # Buttons
        bf = tk.Frame(win, bg=BG)
        bf.pack(fill="x", padx=16, pady=(10, 14))
        tk.Button(bf, text="Cancel", command=win.destroy,
                  bg=CARD, fg=TEXT, relief="flat",
                  font=("Segoe UI", 9), padx=10, pady=5,
                  cursor="hand2").pack(side="left")
        tk.Button(bf, text="Pause Now", command=self._confirm,
                  bg=GREEN, fg=BG, relief="flat",
                  font=("Segoe UI", 9, "bold"), padx=12, pady=5,
                  cursor="hand2").pack(side="right")

        # Update preview whenever values change
        for var in (self._min_var, self._sec_var):
            var.trace_add("write", lambda *_: self._update_preview())
        self._update_preview()

    def _parse_seconds(self) -> int | None:
        try:
            mins = int(self._min_var.get() or 0)
            secs = int(self._sec_var.get() or 0)
            return mins * 60 + secs
        except ValueError:
            return None

    def _update_preview(self):
        total = self._parse_seconds()
        if total is None or total <= 0:
            self._preview_var.set("")
            return
        if self._video_var.get():
            actual = total / config.VIDEO_SPEED_MULTIPLIER
            m, s = divmod(int(actual), 60)
            self._preview_var.set(
                f"Actual pause: {m}m {s:02d}s  "
                f"({total//60}m {total%60:02d}s ÷ {config.VIDEO_SPEED_MULTIPLIER}×)"
            )
        else:
            m, s = divmod(total, 60)
            self._preview_var.set(f"Actual pause: {m}m {s:02d}s")

    def _confirm(self):
        total = self._parse_seconds()
        if not total or total <= 0:
            return
        actual = total / config.VIDEO_SPEED_MULTIPLIER if self._video_var.get() else total
        self.win.destroy()
        self._on_confirm(int(actual))


class ApexApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ApexAutomation")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._q: queue.Queue = queue.Queue()
        self._stop_ev  = threading.Event()
        self._force_ev = threading.Event()
        self._pause_ev = threading.Event()
        self._worker: threading.Thread | None = None
        self._paused = False
        self._countdown_id = None   # after() handle for countdown ticks

        self._build()
        self._poll()

        if not os.path.exists(CONFIG_PATH):
            self.root.after(300, self._first_launch)

    # ── First-launch prompt ───────────────────────────────────────────────────

    def _first_launch(self):
        if messagebox.askyesno(
            "Welcome to ApexAutomation",
            "No configuration found.\n\n"
            "Run the Setup Wizard now to enter your Gemini API key\n"
            "and calibrate screen positions?\n\n"
            "(You can always reopen it from the Settings button.)",
            parent=self.root,
        ):
            self._open_setup()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        root = self.root

        # Header
        hdr = tk.Frame(root, bg=ACCENT, padx=14, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ApexAutomation", bg=ACCENT, fg=WHITE,
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        self._status_lbl = tk.Label(hdr, text="● STOPPED", bg=ACCENT, fg=RED,
                                    font=("Segoe UI", 9, "bold"))
        self._status_lbl.pack(side="right")

        # Answer card — keep frame ref for flashing
        self._card_frame = tk.Frame(root, bg=CARD, padx=18, pady=14)
        self._card_frame.pack(fill="x", padx=14, pady=(12, 6))

        self._answer_tag = tk.Label(self._card_frame, text="ANSWER",
                                    bg=CARD, fg=GREEN,
                                    font=("Segoe UI", 8, "bold"))
        self._answer_tag.pack(anchor="w")

        self._answer_var = tk.StringVar(value="—")
        self._answer_lbl = tk.Label(self._card_frame, textvariable=self._answer_var,
                                    bg=CARD, fg=WHITE,
                                    font=("Segoe UI", 28, "bold"),
                                    wraplength=360, justify="left")
        self._answer_lbl.pack(anchor="w")

        self._hint_var = tk.StringVar(value="")
        self._hint_lbl = tk.Label(self._card_frame, textvariable=self._hint_var,
                                  bg=CARD, fg=YELLOW, font=("Segoe UI", 10),
                                  wraplength=360, justify="left")
        self._hint_lbl.pack(anchor="w", pady=(3, 0))

        self._q_var = tk.StringVar(value="")
        self._q_lbl = tk.Label(self._card_frame, textvariable=self._q_var,
                               bg=CARD, fg=DIM, font=("Segoe UI", 8, "italic"),
                               wraplength=360, justify="left")
        self._q_lbl.pack(anchor="w", pady=(8, 0))

        # All card child labels — needed to recolor them during flash
        self._card_labels = [
            self._answer_tag, self._answer_lbl, self._hint_lbl, self._q_lbl,
        ]

        # Control buttons — row 1
        row1 = tk.Frame(root, bg=BG)
        row1.pack(fill="x", padx=14, pady=(0, 4))

        self._start_btn    = self._btn(row1, "▶  Start",       GREEN,  self._on_start)
        self._stop_btn     = self._btn(row1, "■  Stop",        RED,    self._on_stop,         "disabled")
        self._pause_btn    = self._btn(row1, "⏸  Pause",      BLUE,   self._on_pause,        "disabled")
        self._tpause_btn   = self._btn(row1, "⏱  Timed",      PURPLE, self._on_timed_pause,  "disabled")
        self._recheck_btn  = self._btn(row1, "↺  Re-check",   YELLOW, self._on_recheck,      "disabled")
        for b in (self._start_btn, self._stop_btn, self._pause_btn,
                  self._tpause_btn, self._recheck_btn):
            b.pack(side="left", padx=(0, 5))

        # Control buttons — row 2
        row2 = tk.Frame(root, bg=BG)
        row2.pack(fill="x", padx=14, pady=(0, 8))
        self._btn(row2, "⚙  Settings", PURPLE, self._open_setup).pack(side="left")

        # Log
        tk.Label(root, text="Log", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=14)
        self._log = scrolledtext.ScrolledText(
            root, height=7, width=54, state="disabled",
            bg=CARD, fg=DIM, font=("Consolas", 8),
            relief="flat", borderwidth=0, insertbackground=TEXT,
        )
        self._log.pack(padx=14, pady=(0, 14), fill="x")

    def _btn(self, parent, text, color, cmd, state="normal"):
        return tk.Button(
            parent, text=text, command=cmd,
            bg=color, fg=BG, activebackground=color, activeforeground=BG,
            relief="flat", font=("Segoe UI", 9, "bold"),
            padx=9, pady=6, state=state, cursor="hand2",
        )

    # ── Feedback ──────────────────────────────────────────────────────────────

    def _play_alert(self):
        """Two short beeps in a daemon thread so the UI never blocks."""
        def _beep():
            winsound.Beep(880, 120)
            winsound.Beep(1100, 180)
        threading.Thread(target=_beep, daemon=True).start()

    def _flash_card(self, flashes: int = 4):
        """Rapidly flash the answer card green to signal a new question."""
        card_children = [self._card_frame] + self._card_labels

        def _set(on: bool):
            bg = FLASH if on else CARD
            self._card_frame.configure(bg=bg)
            for lbl in self._card_labels:
                lbl.configure(bg=bg)

        def _tick(remaining: int, on: bool):
            if remaining <= 0:
                _set(False)
                return
            _set(on)
            self.root.after(120, lambda: _tick(remaining - 1, not on))

        _tick(flashes * 2, True)

    # ── Queue polling ─────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "log":
                    self._append_log(data)
                elif kind == "answer":
                    self._display_answer(data)
                elif kind == "done":
                    self._on_worker_done()
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _append_log(self, msg: str):
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _display_answer(self, result):
        if result.answer_type == "multiple_choice":
            where = orchestrator._choice_label(result)
            self._answer_var.set(result.answer)
            self._hint_var.set(f"Click: {where}" if where else "Multiple choice")
        else:
            self._answer_var.set(result.answer)
            self._hint_var.set("Type into the answer box, then press Enter")
        q = result.question_text.strip()
        self._q_var.set(f"Q: {q[:100]}" if q else "")

        # Notify the user that the question changed
        self._play_alert()
        self._flash_card()

    # ── Worker lifecycle ──────────────────────────────────────────────────────

    def _on_worker_done(self):
        self._cancel_countdown()
        self._paused = False
        self._status_lbl.configure(text="● STOPPED", fg=RED)
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._pause_btn.configure(text="⏸  Pause", bg=BLUE, state="disabled")
        self._tpause_btn.configure(state="disabled")
        self._recheck_btn.configure(state="disabled")
        self._answer_var.set("—")
        self._hint_var.set("")
        self._q_var.set("")

    # ── Button handlers ───────────────────────────────────────────────────────

    def _on_start(self):
        if self._worker and self._worker.is_alive():
            return
        self._stop_ev.clear()
        self._force_ev.clear()
        self._pause_ev.clear()
        self._paused = False
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()
        self._status_lbl.configure(text="● RUNNING", fg=GREEN)
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._pause_btn.configure(text="⏸  Pause", bg=BLUE, state="normal")
        self._tpause_btn.configure(state="normal")
        self._recheck_btn.configure(state="normal")
        self._append_log("[assist] Started.")

    def _on_stop(self):
        self._cancel_countdown()
        self._stop_ev.set()
        self._pause_ev.clear()
        self._stop_btn.configure(state="disabled")
        self._pause_btn.configure(state="disabled")
        self._tpause_btn.configure(state="disabled")
        self._recheck_btn.configure(state="disabled")
        self._status_lbl.configure(text="● STOPPING...", fg=YELLOW)
        self._append_log("[assist] Stopping ...")

    def _on_pause(self, timed_label: str | None = None):
        """Toggle pause/resume. Optionally show a timed label in status."""
        if not self._paused:
            self._pause_ev.set()
            self._paused = True
            self._pause_btn.configure(text="▶  Resume", bg=GREEN)
            status = f"● PAUSED  {timed_label}" if timed_label else "● PAUSED"
            self._status_lbl.configure(text=status, fg=YELLOW)
            if not timed_label:
                self._append_log("[assist] Paused.")
        else:
            self._cancel_countdown()
            self._pause_ev.clear()
            self._paused = False
            self._pause_btn.configure(text="⏸  Pause", bg=BLUE)
            self._status_lbl.configure(text="● RUNNING", fg=GREEN)
            self._append_log("[assist] Resumed.")

    def _on_timed_pause(self):
        if not self._paused:
            TimedPauseDialog(self.root, on_confirm=self._start_timed_pause)
        else:
            # Already paused — timed button acts as Resume
            self._on_pause()

    def _start_timed_pause(self, seconds: int):
        """Begin a timed pause with a visible countdown in the status bar."""
        self._cancel_countdown()
        m, s = divmod(seconds, 60)
        self._on_pause(timed_label=f"({m}:{s:02d} remaining)")
        self._append_log(f"[assist] Timed pause: {m}m {s:02d}s")
        self._countdown_tick(seconds)

    def _countdown_tick(self, remaining: int):
        if not self._paused or self._stop_ev.is_set():
            return
        if remaining <= 0:
            self._append_log("[assist] Timed pause complete — resuming.")
            self._on_pause()   # auto-resume
            return
        m, s = divmod(remaining, 60)
        self._status_lbl.configure(
            text=f"● PAUSED  ({m}:{s:02d} remaining)", fg=YELLOW
        )
        self._countdown_id = self.root.after(
            1000, lambda: self._countdown_tick(remaining - 1)
        )

    def _cancel_countdown(self):
        if self._countdown_id is not None:
            self.root.after_cancel(self._countdown_id)
            self._countdown_id = None

    def _on_recheck(self):
        self._force_ev.set()
        self._append_log("[assist] Manual re-check requested.")

    def _open_setup(self):
        import setup_wizard
        setup_wizard.run(parent=self.root)

    # ── Background thread ─────────────────────────────────────────────────────

    def _loop(self):
        orchestrator.run_assist(
            stop_event  = self._stop_ev,
            force_event = self._force_ev,
            pause_event = self._pause_ev,
            on_answer   = lambda r: self._q.put(("answer", r)),
            on_status   = lambda m: self._q.put(("log",    m)),
        )
        self._q.put(("done", None))

    def run(self):
        self.root.mainloop()


def main():
    ApexApp().run()


if __name__ == "__main__":
    main()
