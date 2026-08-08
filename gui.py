"""
gui.py — graphical front end for Jarvis. Run this instead of assistant.py
for normal use: `python gui.py`.

Everything Jarvis actually does — listening, thinking, speaking, running
tools — still lives in assistant.py. This file just displays the
conversation, shows what it's doing right now, and lets you type as an
alternative to talking. The voice loop runs on a background thread since
Tkinter's event loop has to own the main thread.

Interrupting Jarvis mid-reply: say "Hey Jarvis" again while it's talking
and it'll cut off and listen for whatever you say next. This only works
for replies to things you said out loud — see the note in assistant.py's
speak() for why typed commands don't support voice interruption.
"""

import threading
import tkinter as tk
from tkinter import scrolledtext

import assistant
import memory as mem

STATUS_LABELS = {
    "idle": "💤 Idle — say \"Hey Jarvis\" or type below",
    "listening": "🎤 Listening...",
    "thinking": "🤔 Thinking...",
    "speaking": "🔊 Speaking — say \"Hey Jarvis\" again to interrupt",
}

SPEAKER_LABELS = {"user": "You", "jarvis": assistant.NAME, "tool": "⚙ tool"}
TAG_COLORS = {"user": "#5b9bff", "jarvis": "#4ade80", "system": "#888888", "tool": "#c084fc"}


class JarvisGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(assistant.NAME)
        self.root.geometry("640x700")
        self.root.minsize(440, 420)
        self.root.configure(bg="#1a1a1f")

        self.conversation = mem.load_conversation()
        if self.conversation and not assistant._history_is_compatible(self.conversation):
            self.conversation = []
            mem.clear_conversation()

        self._build_widgets()
        self._append("system", self._startup_banner())

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
        self.status_var = tk.StringVar(value=STATUS_LABELS["idle"])
        status_label = tk.Label(
            self.root, textvariable=self.status_var, font=("Segoe UI", 11),
            pady=10, bg="#1a1a1f", fg="#e5e5ea",
        )
        status_label.pack(fill="x")

        self.transcript = scrolledtext.ScrolledText(
            self.root, wrap="word", state="disabled", font=("Segoe UI", 10),
            bg="#111116", fg="#e5e5ea", insertbackground="#e5e5ea", borderwidth=0,
            padx=12, pady=10,
        )
        for tag, color in TAG_COLORS.items():
            self.transcript.tag_config(tag, foreground=color)
        self.transcript.tag_config("speaker", font=("Segoe UI", 10, "bold"))
        self.transcript.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        input_frame = tk.Frame(self.root, bg="#1a1a1f")
        input_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.entry = tk.Entry(input_frame, font=("Segoe UI", 10))
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=5)
        self.entry.bind("<Return>", self._on_send)
        self.entry.focus_set()

        send_btn = tk.Button(input_frame, text="Send", command=self._on_send, width=8)
        send_btn.pack(side="right")

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
                    self.root.after(0, self.root.destroy)
                    return

                assistant.process_turn(text, self.conversation, mode="voice")

            except Exception as e:
                self.root.after(0, self._append, "system", f"Error: {e}")

    def _on_close(self):
        self.shutdown_event.set()
        mem.save_conversation(self.conversation)
        self.root.destroy()


def main():
    root = tk.Tk()
    JarvisGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
