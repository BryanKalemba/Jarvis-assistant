"""
gui.py — graphical front end for Jarvis. Run this instead of assistant.py
for normal use: `python gui.py`.

Everything Jarvis actually does — listening, thinking, speaking, running
tools — still lives in assistant.py. This file just displays the
conversation, shows what it's doing right now (via the glowing orb and
status line), and lets you type as an alternative to talking. The voice
loop runs on a background thread since Tkinter's event loop has to own
the main thread.

Interrupting Jarvis mid-reply: say "Hey Jarvis" again while it's talking
and it'll cut off and listen for whatever you say next. This only works
for replies to things you said out loud — see the note in assistant.py's
speak() for why typed commands don't support voice interruption.

The orb: Tkinter has no built-in support for gradients or transparency,
so the glow is faked with dozens of concentric circles, each a slightly
different interpolated color from a dim edge to a bright core — a classic
Tkinter trick. It's genuinely animated (a gentle pulse), and its color and
pulse speed change with Jarvis's status, so it doubles as a real indicator
rather than just decoration.
"""

import threading
import tkinter as tk
from tkinter import scrolledtext

import assistant
import memory as mem

STATUS_LABELS = {
    "idle": "Available...",
    "listening": "Listening...",
    "thinking": "Thinking...",
    "speaking": "Speaking...",
}
STATUS_SUBTEXT = {
    "speaking": "say \"Hey Jarvis\" again to interrupt",
}

SPEAKER_LABELS = {"user": "You", "jarvis": assistant.NAME, "tool": "⚙ tool"}
TAG_COLORS = {"user": "#5b9bff", "jarvis": "#4ade80", "system": "#666666", "tool": "#c084fc"}

BG = "#000000"
ACCENT = "#5fd4ff"


def _lerp_color(c1: str, c2: str, t: float) -> str:
    """Blend two hex colors — t=0 gives c1, t=1 gives c2."""
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


class Orb:
    """The glowing status orb. Draws RINGS concentric circles from a dim
    edge color to a bright core color — faking a radial gradient, since
    Tkinter's canvas fills are flat colors with no native gradient support.
    Rings are created largest-to-smallest, which naturally draws the
    bright core on top of the dim edge without needing explicit z-ordering."""

    RINGS = 36
    PALETTES = {
        "idle":      {"edge": "#071522", "core": "#7fe8ff", "speed": 0.045, "amplitude": 5},
        "listening": {"edge": "#06251c", "core": "#7dffcf", "speed": 0.11,  "amplitude": 9},
        "thinking":  {"edge": "#1e0f2c", "core": "#c99bff", "speed": 0.09,  "amplitude": 7},
        "speaking":  {"edge": "#071522", "core": "#baf3ff", "speed": 0.20,  "amplitude": 13},
    }

    def __init__(self, canvas: tk.Canvas, cx: int, cy: int, base_radius: int):
        self.canvas = canvas
        self.cx = cx
        self.cy = cy
        self.base_radius = base_radius
        self.status = "idle"
        self.phase = 0.0
        self._rings = [canvas.create_oval(0, 0, 0, 0, outline="") for _ in range(self.RINGS)]

    def set_status(self, status: str):
        self.status = status if status in self.PALETTES else "idle"

    def tick(self):
        import math
        palette = self.PALETTES[self.status]
        self.phase += palette["speed"]
        pulse = math.sin(self.phase) * palette["amplitude"]
        radius = self.base_radius + pulse

        for i, ring_id in enumerate(self._rings):
            t = i / (self.RINGS - 1)  # 0 at the edge, 1 at the core
            r = max(radius * (1 - t), 1)
            color = _lerp_color(palette["edge"], palette["core"], t)
            self.canvas.coords(ring_id, self.cx - r, self.cy - r, self.cx + r, self.cy + r)
            self.canvas.itemconfig(ring_id, fill=color)


class JarvisGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{assistant.NAME} AI")
        self.root.geometry("640x760")
        self.root.minsize(440, 460)
        self.root.configure(bg=BG)

        self.conversation = mem.load_conversation()
        if self.conversation and not assistant._history_is_compatible(self.conversation):
            self.conversation = []
            mem.clear_conversation()

        self._animating = True
        self._build_widgets()
        self._append("system", self._startup_banner())
        self._animate_orb()

        # assistant.py calls these from background threads. Tkinter widgets
        # can only be touched from the main thread, so every update hops
        # back onto it via root.after() instead of poking the widgets directly.
        assistant.set_output_hook(lambda kind, text: self.root.after(0, self._append, kind, text))
        assistant.set_status_hook(lambda status: self.root.after(0, self._set_status, status))

        self.shutdown_event = threading.Event()
        threading.Thread(target=self._voice_loop, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _startup_banner(self) -> str:
        model = assistant.GEMINI_MODEL if assistant.AI_PROVIDER == "gemini" else assistant.OLLAMA_MODEL
        resumed = f" Resuming a saved conversation ({len(self.conversation)} messages)." if self.conversation else ""
        return f"{assistant.NAME} is online — running on {model} via {assistant.AI_PROVIDER}.{resumed}"

    def _build_widgets(self):
        # --- header ---
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(
            header, text=f"{assistant.NAME.upper()} AI", font=("Segoe UI", 12, "bold"),
            bg=BG, fg=ACCENT,
        ).pack(side="left")

        # --- orb + status ---
        orb_frame = tk.Frame(self.root, bg=BG, height=260)
        orb_frame.pack(fill="x", pady=(4, 0))
        orb_frame.pack_propagate(False)

        canvas_size = 220
        self.orb_canvas = tk.Canvas(orb_frame, width=canvas_size, height=canvas_size, bg=BG, highlightthickness=0)
        self.orb_canvas.pack(pady=(6, 0))
        self.orb = Orb(self.orb_canvas, canvas_size // 2, canvas_size // 2, base_radius=90)

        self.status_var = tk.StringVar(value=STATUS_LABELS["idle"])
        tk.Label(
            orb_frame, textvariable=self.status_var, font=("Segoe UI", 12),
            bg=BG, fg="#e5e5ea",
        ).pack(pady=(6, 0))

        self.substatus_var = tk.StringVar(value="")
        tk.Label(
            orb_frame, textvariable=self.substatus_var, font=("Segoe UI", 8),
            bg=BG, fg="#555555",
        ).pack()

        tk.Label(orb_frame, text="🎤", font=("Segoe UI", 16), bg=BG, fg="#e5e5ea").pack(pady=(8, 0))

        # --- transcript (all text output, unchanged content/logic) ---
        self.transcript = scrolledtext.ScrolledText(
            self.root, wrap="word", state="disabled", font=("Segoe UI", 10),
            bg="#08080a", fg="#e5e5ea", insertbackground="#e5e5ea", borderwidth=0,
            padx=12, pady=10,
        )
        for tag, color in TAG_COLORS.items():
            self.transcript.tag_config(tag, foreground=color)
        self.transcript.tag_config("speaker", font=("Segoe UI", 10, "bold"))
        self.transcript.pack(fill="both", expand=True, padx=10, pady=(8, 8))

        # --- input row ---
        input_frame = tk.Frame(self.root, bg=BG)
        input_frame.pack(fill="x", padx=10, pady=(0, 12))

        self.entry = tk.Entry(
            input_frame, font=("Segoe UI", 10), bg="#111116", fg="#e5e5ea",
            insertbackground="#e5e5ea", relief="flat",
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=6)
        self.entry.bind("<Return>", self._on_send)
        self.entry.focus_set()

        send_btn = tk.Button(
            input_frame, text="Send", command=self._on_send, width=8,
            bg="#12202b", fg=ACCENT, activebackground="#1a3040", relief="flat",
        )
        send_btn.pack(side="right")

    def _animate_orb(self):
        if not self._animating:
            return
        self.orb.tick()
        self.root.after(45, self._animate_orb)

    def _append(self, kind: str, text: str):
        self.transcript.configure(state="normal")
        if kind == "system":
            self.transcript.insert("end", f"{text}\n", "system")
        else:
            label = SPEAKER_LABELS.get(kind, kind)
            self.transcript.insert("end", f"{label}: ", (kind, "speaker"))
            self.transcript.insert("end", f"{text}\n\n", kind)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _set_status(self, status: str):
        self.status_var.set(STATUS_LABELS.get(status, status))
        self.substatus_var.set(STATUS_SUBTEXT.get(status, ""))
        self.orb.set_status(status)

    def _on_send(self, event=None):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")

        if assistant.awaiting_gui_response.is_set():
            # Jarvis is mid-confirmation waiting on a typed answer — route
            # this there instead of starting a brand new command.
            self._append("user", text)
            assistant.submit_gui_text(text)
            return

        threading.Thread(target=self._handle_text, args=(text,), daemon=True).start()

    def _handle_text(self, text: str):
        if text.lower().strip(" .!") in assistant.EXIT_PHRASES:
            assistant.speak("Goodbye.")
            mem.save_conversation(self.conversation)
            assistant.logger.info("=== Jarvis shutting down (typed goodbye) ===")
            self.root.after(0, self.root.destroy)
            return
        assistant.process_turn(text, self.conversation, mode="gui_text")

    def _voice_loop(self):
        while not self.shutdown_event.is_set():
            try:
                leftover = assistant.wait_for_wake_word()
                if self.shutdown_event.is_set():
                    return

                if leftover:
                    text = leftover
                else:
                    assistant.set_status("listening")
                    assistant.speak("Yes?")
                    text = assistant.listen(timeout=6, phrase_time_limit=12)
                    if not text:
                        assistant.speak("I didn't catch that.")
                        continue

                if text.lower().strip(" .!") in assistant.EXIT_PHRASES:
                    assistant.speak("Goodbye.")
                    mem.save_conversation(self.conversation)
                    assistant.logger.info("=== Jarvis shutting down (said goodbye) ===")
                    self.root.after(0, self.root.destroy)
                    return

                assistant.process_turn(text, self.conversation, mode="voice")

            except Exception as e:
                assistant.logger.exception("Error in GUI voice loop")
                self.root.after(0, self._append, "system", f"Error: {e}")

    def _on_close(self):
        assistant.logger.info("=== Jarvis shutting down (window closed) ===")
        self._animating = False
        self.shutdown_event.set()
        mem.save_conversation(self.conversation)
        self.root.destroy()


def main():
    model_label = assistant.GEMINI_MODEL if assistant.AI_PROVIDER == "gemini" else assistant.OLLAMA_MODEL
    assistant.logger.info(f"=== Jarvis starting (GUI) — provider={assistant.AI_PROVIDER}, model={model_label} ===")
    root = tk.Tk()
    JarvisGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
