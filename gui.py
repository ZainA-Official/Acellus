"""
gui.py — ApexAutomation desktop GUI.

Launch via ApexAutomation.pyw (no terminal) or:  python gui.py

On first launch (no user_config.json) the Setup Wizard opens automatically.
The Settings button reopens it at any time — jump to any step via the sidebar.
"""

from __future__ import annotations
import os
import queue
import sys
import threading

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
TEXT   = "#eaeaea"
DIM    = "#7f8c8d"
WHITE  = "#ffffff"


class ApexApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ApexAutomation")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._q: queue.Queue = queue.Queue()
        self._stop  = threading.Event()
        self._force = threading.Event()
        self._worker: threading.Thread | None = None

        self._build()
        self._poll()

        # Open setup wizard automatically on first launch
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

        # Answer card
        card = tk.Frame(root, bg=CARD, padx=18, pady=14)
        card.pack(fill="x", padx=14, pady=(12, 6))

        tk.Label(card, text="ANSWER", bg=CARD, fg=GREEN,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        self._answer_var = tk.StringVar(value="—")
        tk.Label(card, textvariable=self._answer_var, bg=CARD, fg=WHITE,
                 font=("Segoe UI", 28, "bold"),
                 wraplength=360, justify="left").pack(anchor="w")

        self._hint_var = tk.StringVar(value="")
        tk.Label(card, textvariable=self._hint_var, bg=CARD, fg=YELLOW,
                 font=("Segoe UI", 10),
                 wraplength=360, justify="left").pack(anchor="w", pady=(3, 0))

        self._q_var = tk.StringVar(value="")
        tk.Label(card, textvariable=self._q_var, bg=CARD, fg=DIM,
                 font=("Segoe UI", 8, "italic"),
                 wraplength=360, justify="left").pack(anchor="w", pady=(8, 0))

        # Control buttons
        row1 = tk.Frame(root, bg=BG)
        row1.pack(fill="x", padx=14, pady=(0, 4))

        self._start_btn   = self._btn(row1, "▶  Start",    GREEN,  self._start)
        self._stop_btn    = self._btn(row1, "■  Stop",     RED,    self._stop,    "disabled")
        self._recheck_btn = self._btn(row1, "↺  Re-check", YELLOW, self._recheck, "disabled")
        for b in (self._start_btn, self._stop_btn, self._recheck_btn):
            b.pack(side="left", padx=(0, 6))

        row2 = tk.Frame(root, bg=BG)
        row2.pack(fill="x", padx=14, pady=(0, 8))
        self._btn(row2, "⚙  Settings", PURPLE, self._open_setup).pack(side="left")

        # Log
        tk.Label(root, text="Log", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=14)
        self._log = scrolledtext.ScrolledText(
            root, height=7, width=52, state="disabled",
            bg=CARD, fg=DIM, font=("Consolas", 8),
            relief="flat", borderwidth=0, insertbackground=TEXT,
        )
        self._log.pack(padx=14, pady=(0, 14), fill="x")

    def _btn(self, parent, text, color, cmd, state="normal"):
        return tk.Button(
            parent, text=text, command=cmd,
            bg=color, fg=BG, activebackground=color, activeforeground=BG,
            relief="flat", font=("Segoe UI", 9, "bold"),
            padx=10, pady=6, state=state, cursor="hand2",
        )

    # ── Queue polling ─────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "log":
                    self._append_log(data)
                elif kind == "answer":
                    self._display_answer(data)
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

    # ── Button handlers ───────────────────────────────────────────────────────

    def _start(self):
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._force.clear()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()
        self._status_lbl.configure(text="● RUNNING", fg=GREEN)
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._recheck_btn.configure(state="normal")
        self._append_log("[assist] Started.")

    def _stop(self):
        self._stop.set()
        self._status_lbl.configure(text="● STOPPED", fg=RED)
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._recheck_btn.configure(state="disabled")
        self._answer_var.set("—")
        self._hint_var.set("")
        self._q_var.set("")
        self._append_log("[assist] Stopped.")

    def _recheck(self):
        self._force.set()
        self._append_log("[assist] Manual re-check requested.")

    def _open_setup(self):
        import setup_wizard
        setup_wizard.run(parent=self.root)

    # ── Background thread ─────────────────────────────────────────────────────

    def _loop(self):
        orchestrator.run_assist(
            stop_event  = self._stop,
            force_event = self._force,
            on_answer   = lambda r: self._q.put(("answer", r)),
            on_status   = lambda m: self._q.put(("log",    m)),
        )

    def run(self):
        self.root.mainloop()


def main():
    ApexApp().run()


if __name__ == "__main__":
    main()
