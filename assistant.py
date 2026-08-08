"""
assistant.py — the main Jarvis loop.

Brain: switchable between Google's Gemini (cloud, free tier) and a local
model via Ollama, controlled by AI_PROVIDER in .env. Same tools, same
wake word, same everything else either way — only the "who's thinking"
part changes.
Ears: faster-whisper, running locally on your CPU either way.
Voice: edge-tts, Microsoft's free neural voices, with an automatic
offline fallback if there's no internet connection.

Flow each cycle:
  0. Idle, listening for the wake word ("Hey Jarvis")
  1. Once woken, listen for your command (or use it if said in the same breath)
  2. Send text + tool list to whichever AI provider is configured
  3. If it wants to call a tool, run it and send the result back
  4. Speak the final reply out loud, then go back to idling

You can also just type a message any time, no wake word needed — it runs
on a background thread alongside the voice loop, so both work at once.

Run with: python assistant.py
Press Ctrl+C to quit. Say "goodbye" / "stop listening" (or type it) to exit.

Switching providers mid-project: saved conversation history is shaped
differently for each provider (Gemini uses "parts", Ollama uses
"content"), so history saved under one provider gets detected as
incompatible and cleared automatically if you switch — see
_history_is_compatible() below.
"""

import asyncio
import io
import logging
import os
import sys
import tempfile
import threading
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
import edge_tts
import ollama
import pygame
import pyttsx3
import speech_recognition as sr
from faster_whisper import WhisperModel
from dotenv import load_dotenv
import google.generativeai as genai

from tools import TOOL_SCHEMAS, TOOL_FUNCTIONS
import memory as mem

load_dotenv()

# Everything Jarvis says/hears/does gets logged to a file, not just shown
# on screen — the console or GUI scrolls away, but this sticks around so a
# problem can be diagnosed from the actual log instead of a screenshot.
# Capped size + a few backups so it doesn't grow forever.
LOG_DIR = Path(__file__).parent / "data"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "jarvis.log"

logger = logging.getLogger("jarvis")
logger.setLevel(logging.INFO)
_file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(_file_handler)

AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama").strip().lower()
if AI_PROVIDER not in ("gemini", "ollama"):
    sys.exit(f"AI_PROVIDER must be 'gemini' or 'ollama' in .env — got '{AI_PROVIDER}'.")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

NAME = os.getenv("ASSISTANT_NAME", "Jarvis")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "base.en")
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-GuyNeural")

MIC_DEVICE_INDEX = os.getenv("MIC_DEVICE_INDEX")
MIC_DEVICE_INDEX = int(MIC_DEVICE_INDEX) if MIC_DEVICE_INDEX else None

# Only validate whichever provider is actually selected — no point demanding
# a Gemini key if you're running Ollama, or vice versa.
if AI_PROVIDER == "gemini":
    if not GEMINI_API_KEY or "your-key-here" in GEMINI_API_KEY:
        sys.exit(
            "No Gemini API key found. Copy .env.example to .env and add your free key.\n"
            "Get one at https://aistudio.google.com/apikey\n"
            "(Or set AI_PROVIDER=ollama in .env to run fully locally instead.)"
        )
    genai.configure(api_key=GEMINI_API_KEY)

elif AI_PROVIDER == "ollama":
    try:
        pulled_models = [m["model"] for m in ollama.list()["models"]]
    except Exception:
        sys.exit(
            "Can't reach Ollama. Make sure it's installed and running:\n"
            "  1. Install it from https://ollama.com\n"
            "  2. It runs in the background automatically once installed\n"
            f"  3. Pull a model: ollama pull {OLLAMA_MODEL}\n"
            "(Or set AI_PROVIDER=gemini in .env to use the cloud instead.)"
        )
    if not any(OLLAMA_MODEL in name for name in pulled_models):
        sys.exit(
            f"Model '{OLLAMA_MODEL}' isn't pulled yet. Run this first:\n"
            f"  ollama pull {OLLAMA_MODEL}"
        )

print(f"Loading speech recognition model ({WHISPER_MODEL_SIZE})...")
whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")

pygame.mixer.init()

recognizer = sr.Recognizer()
recognizer.pause_threshold = 0.8
recognizer.non_speaking_duration = 0.5
recognizer.dynamic_energy_threshold = True

SYSTEM_PROMPT_BASE = f"""You are {NAME}, a helpful voice assistant running locally on the
user's Windows PC. Keep spoken replies SHORT (1-3 sentences) since they'll be read
aloud by text-to-speech. Use the available tools whenever the user asks you to DO
something on their computer (open apps, check files, take a screenshot, etc) —
call them through the real tool-calling mechanism, never by writing JSON or
function-call syntax as plain text in your reply. If none of your tools can do
what's being asked, say so plainly instead of inventing one that doesn't exist.
For general questions, just answer directly and conversationally. If the user
shares something worth remembering (preferences, passwords, important facts),
proactively save it with the remember_fact tool."""


def build_system_prompt() -> str:
    # Fold in whatever's been saved to memory so Jarvis already knows it,
    # instead of having to call recall_fact every time it might be relevant.
    facts = mem.all_memories_dict()
    if not facts:
        return SYSTEM_PROMPT_BASE
    facts_block = "\n".join(f"- {k}: {v}" for k, v in facts.items())
    return f"{SYSTEM_PROMPT_BASE}\n\nThings you already know about the user:\n{facts_block}"


def _uppercase_types(schema):
    # Gemini wants JSON-schema types in uppercase ("STRING" not "string").
    # tools.py writes them lowercase, so we convert on the fly here rather
    # than maintain two copies of every schema.
    if isinstance(schema, dict):
        converted = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                converted[key] = value.upper()
            elif isinstance(value, dict):
                converted[key] = _uppercase_types(value)
            elif isinstance(value, list):
                converted[key] = [_uppercase_types(v) if isinstance(v, dict) else v for v in value]
            else:
                converted[key] = value
        return converted
    return schema


GEMINI_TOOLS = [{
    "function_declarations": [
        {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": _uppercase_types(schema.get("input_schema", {"type": "OBJECT", "properties": {}})),
        }
        for schema in TOOL_SCHEMAS
    ]
}]

# Ollama uses the same tool-calling shape OpenAI's API does, which matches
# tools.py's native lowercase schemas — no conversion needed here.
OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema.get("input_schema", {"type": "object", "properties": {}}),
        },
    }
    for schema in TOOL_SCHEMAS
]

EXIT_PHRASES = {"goodbye", "stop listening", "exit", "quit", "that's all"}

# Everything user-facing goes through emit() instead of raw print(), so a
# GUI can capture the whole transcript instead of it only living in a
# console window. Running `python assistant.py` directly with no hook set
# just prints to the console like before — nothing changes for that case.
_output_hook = None
_status_hook = None


def set_output_hook(fn):
    """fn(kind: str, text: str) -> None. kind is one of "system", "user",
    "jarvis", "tool" — a GUI can use this to style each differently."""
    global _output_hook
    _output_hook = fn


def set_status_hook(fn):
    """fn(status: str) -> None. status is one of "idle", "listening",
    "thinking", "speaking" — lets a GUI show what Jarvis is doing right now."""
    global _status_hook
    _status_hook = fn


def set_status(status: str):
    if _status_hook:
        _status_hook(status)


def emit(kind: str, text: str):
    logger.info(f"[{kind}] {text}")
    if _output_hook:
        _output_hook(kind, text)
    else:
        print(text)


async def _synthesize_speech(text: str, filepath: str):
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(filepath)


def _speak_offline_fallback(text: str):
    engine = pyttsx3.init()
    engine.setProperty("rate", 180)
    engine.say(text)
    engine.runAndWait()


def speak(text: str) -> str | None:
    """Speaks the reply out loud. Returns None if it played all the way
    through undisturbed. Returns '' if the wake word cut it off with
    nothing said yet. Returns the leftover text if a whole new command was
    said in the same breath as the interrupting wake word."""
    global _interrupt_remainder
    emit("jarvis", text)
    set_status("speaking")
    audio_path = os.path.join(tempfile.gettempdir(), f"jarvis_reply_{uuid.uuid4().hex}.mp3")
    is_speaking.set()
    interrupt_requested.clear()
    _interrupt_remainder = None
    interrupt_thread = None
    try:
        asyncio.run(_synthesize_speech(text, audio_path))
        pygame.mixer.music.load(audio_path)
        pygame.mixer.music.play()

        # Interrupt-by-voice only runs for voice-triggered replies. If this
        # reply came from typed input, the independent voice loop thread
        # is still out there polling the mic for the wake word — spawning
        # a second mic listener here at the same time would reintroduce
        # the exact contention risk that mode-aware confirmations exist to
        # avoid. Typed commands can just be interrupted by typing another one.
        if current_input_mode == "voice":
            interrupt_thread = threading.Thread(target=_listen_for_interrupt, daemon=True)
            interrupt_thread.start()

        while pygame.mixer.music.get_busy():
            if interrupt_requested.is_set():
                pygame.mixer.music.stop()
                break
            pygame.time.wait(50)
        pygame.mixer.music.unload()  # release the file handle before we delete it below
    except Exception as e:
        emit("system", f"(couldn't reach edge-tts, using the offline voice instead: {e})")
        _speak_offline_fallback(text)  # note: this path can't be interrupted the same way
    finally:
        is_speaking.clear()
        set_status("idle")
        interrupt_requested.set()  # make sure the listener thread notices playback is over either way
        if interrupt_thread:
            interrupt_thread.join(timeout=2)
        try:
            os.remove(audio_path)
        except OSError:
            pass  # best effort — a leftover temp file isn't worth failing a turn over
    return _interrupt_remainder


def listen(timeout: float | None = 6, phrase_time_limit: float | None = 12, calibrate: bool = True) -> str | None:
    """Record one phrase from the mic and transcribe it with Whisper.
    timeout=None waits indefinitely for speech to start — used while idling
    for the wake word. calibrate=False skips the ambient-noise calibration
    step, trading a little accuracy for lower latency — used while
    listening for an interrupt during playback, where every extra second
    of delay is more noticeable."""
    with sr.Microphone(device_index=MIC_DEVICE_INDEX) as source:
        if calibrate:
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
        try:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            return None

    wav_bytes = audio.get_wav_data()
    segments, _ = whisper_model.transcribe(
        io.BytesIO(wav_bytes),
        language="en",
        # Short, isolated words like a bare "Jarvis" are exactly where
        # Whisper tends to mishear or even hallucinate something else
        # entirely — nudging it with the words we actually expect to hear,
        # and filtering out silence/noise, both measurably help with that.
        initial_prompt="Hey Jarvis. Jarvis.",
        vad_filter=True,
    )
    text = "".join(segment.text for segment in segments).strip()

    if not text:
        return None
    emit("user", text)
    return text


WAKE_PHRASES = ["hey jarvis", "hello jarvis", "ok jarvis", "jarvis"]

# Holds whatever leftover text the interrupt listener captured in the same
# breath as the wake word (e.g. "hey jarvis, actually..."), so process_turn
# can pick up right where the interruption happened instead of just going
# back to idle. Only ever touched by _listen_for_interrupt() and read by
# speak() after that thread has been joined — see the note in speak().
_interrupt_remainder = None


def _listen_for_interrupt():
    """Runs on its own thread only while Jarvis is speaking. If it hears
    the wake word, it stops playback immediately instead of making you
    wait for the reply to finish. Heads up: there's no hardware echo
    cancellation here, so this mic is also picking up Jarvis's own voice
    while it plays — a false interrupt is possible if a reply happens to
    say something close to the wake word."""
    global _interrupt_remainder
    while is_speaking.is_set() and not interrupt_requested.is_set():
        try:
            text = listen(timeout=1, phrase_time_limit=4, calibrate=False)
        except Exception:
            return  # a transient mic hiccup here just means no interrupt this time, not a crash
        if not text:
            continue
        lowered = text.lower()
        for phrase in WAKE_PHRASES:
            if phrase in lowered:
                _interrupt_remainder = lowered.split(phrase, 1)[1].strip(" ,.")
                interrupt_requested.set()
                return

# Anything in here gets a spoken "are you sure?" before it actually runs.
DESTRUCTIVE_TOOLS = {"shutdown_computer", "restart_computer", "close_application"}
CONFIRM_WORDS = {"yes", "yeah", "yep", "yup", "confirm", "do it", "go ahead", "sure", "affirmative"}

# Only one turn (voice or typed) gets processed at a time — this stops a
# typed message and a spoken command from talking over each other, or both
# trying to mutate the conversation list at once.
turn_lock = threading.Lock()

# Since turn_lock guarantees only one turn runs at a time, a plain variable
# is enough here — no need for anything fancier. Set at the start of each
# turn so confirm_action knows whether to ask out loud, read the console,
# or wait on a GUI text box.
current_input_mode = "voice"

# Lets the wake word interrupt Jarvis mid-sentence. is_speaking tracks
# whether audio is actively playing right now; interrupt_requested is set
# by whoever wants to cut it off, and speak()'s playback loop checks it.
is_speaking = threading.Event()
interrupt_requested = threading.Event()

# For confirmations triggered from a GUI text box, there's no console to
# block on with input() — instead we park here until the GUI calls
# submit_gui_text() with whatever the user typed.
awaiting_gui_response = threading.Event()
_gui_response_event = threading.Event()
_gui_response_value = None


def wait_for_gui_text(prompt: str, timeout: float = 30) -> str | None:
    global _gui_response_value
    emit("jarvis", f"{prompt} (type your answer)")
    _gui_response_value = None
    _gui_response_event.clear()
    awaiting_gui_response.set()
    got_response = _gui_response_event.wait(timeout)
    awaiting_gui_response.clear()
    return _gui_response_value if got_response else None


def submit_gui_text(value: str):
    """Called by the GUI when the user submits text while a confirmation
    is pending — routes it back to wait_for_gui_text() instead of starting
    a new turn."""
    global _gui_response_value
    _gui_response_value = value
    _gui_response_event.set()


def confirm_action(prompt: str) -> bool:
    """Ask a yes/no question and wait for the answer — out loud for voice,
    typed for text/GUI. Keeping typed confirmations off the microphone
    avoids the text thread ever needing it at all, which sidesteps a nasty
    hang: the voice loop can sit blocked on the mic indefinitely while
    idling for the wake word, so a typed confirmation reaching for the mic
    too could end up waiting forever for a turn that never comes."""
    if current_input_mode == "gui_text":
        response = wait_for_gui_text(prompt)
    elif current_input_mode == "text":
        emit("jarvis", f"{prompt} (type yes/no)")
        response = input().strip()
    else:
        speak(prompt)
        response = listen(timeout=6, phrase_time_limit=6)

    if not response:
        return False
    return any(word in response.lower() for word in CONFIRM_WORDS)


def wait_for_wake_word() -> str:
    """Block until 'hey jarvis' (or a variant) is heard. If the whole
    request was said in one breath ('hey jarvis what time is it'), return
    the leftover text so we can skip listening a second time. Returns ''
    if only the wake word itself was said."""
    set_status("idle")
    emit("system", "Waiting for wake word — say 'Hey Jarvis'...")
    while True:
        text = listen(timeout=None, phrase_time_limit=10)
        if not text:
            continue
        lowered = text.lower()
        for phrase in WAKE_PHRASES:
            if phrase in lowered:
                remainder = lowered.split(phrase, 1)[1].strip(" ,.")
                return remainder
        # not the wake word — ignore it and keep waiting


def _run_tool(name: str, args: dict) -> str:
    """Shared by both providers: look up the tool, gate it behind spoken
    confirmation if it's destructive, run it, and return the result as text."""
    func = TOOL_FUNCTIONS.get(name)

    if not func:
        return f"Unknown tool: {name}"

    if name in DESTRUCTIVE_TOOLS:
        if name == "close_application" and "app_name" in args:
            action_label = f"close {args['app_name']}"
        else:
            action_label = name.replace("_", " ")
        if not confirm_action(f"Are you sure you want to {action_label}?"):
            return "The user did not confirm — action cancelled."

    try:
        return func(**args)
    except Exception as e:
        return f"Error running {name}: {e}"


def ask_gemini(conversation: list) -> str:
    """conversation entries look like {"role": "user"/"model", "parts": [...]}.
    Tool results get sent back as a "user" turn — Gemini's current API
    doesn't accept a dedicated "function" role like older docs suggest."""
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=build_system_prompt(),
        tools=GEMINI_TOOLS,
    )
    response = model.generate_content(conversation)

    while True:
        candidate = response.candidates[0]
        parts = candidate.content.parts
        function_calls = [p.function_call for p in parts if p.function_call.name]

        if not function_calls:
            reply = "".join(p.text for p in parts if p.text)
            conversation.append({"role": "model", "parts": [{"text": reply}]})
            return reply.strip()

        conversation.append({
            "role": "model",
            "parts": [{"function_call": {"name": fc.name, "args": dict(fc.args)}} for fc in function_calls],
        })

        function_response_parts = []
        for fc in function_calls:
            name = fc.name
            args = dict(fc.args)
            result = _run_tool(name, args)
            emit("tool", f"{name}({args}) -> {result}")
            function_response_parts.append({
                "function_response": {"name": name, "response": {"result": str(result)}}
            })

        conversation.append({"role": "user", "parts": function_response_parts})
        response = model.generate_content(conversation)


def ask_ollama(conversation: list) -> str:
    """conversation entries look like {"role": "user"/"assistant"/"tool",
    "content": "..."} — the same shape OpenAI-compatible chat APIs use."""
    messages = [{"role": "system", "content": build_system_prompt()}] + conversation
    response = ollama.chat(model=OLLAMA_MODEL, messages=messages, tools=OLLAMA_TOOLS)

    while True:
        message = response["message"]
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            reply = message.get("content", "")
            conversation.append({"role": "assistant", "content": reply})
            return reply.strip()

        # Ollama hands back tool_calls as its own object type, not a plain
        # dict. If we store that as-is, saving it to JSON (memory.py) has
        # no idea how to serialize it and silently falls back to a garbled
        # string representation — which then fails validation the moment
        # it's loaded back in and sent to Ollama again. Converting to a
        # plain dict up front means it survives a save/reload round-trip intact.
        plain_tool_calls = [
            {"function": {"name": call["function"]["name"], "arguments": dict(call["function"]["arguments"])}}
            for call in tool_calls
        ]
        assistant_turn = {"role": "assistant", "content": message.get("content", ""), "tool_calls": plain_tool_calls}
        conversation.append(assistant_turn)
        messages.append(assistant_turn)

        for call in plain_tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            result = _run_tool(name, args)
            emit("tool", f"{name}({args}) -> {result}")
            tool_turn = {"role": "tool", "content": str(result), "name": name}
            conversation.append(tool_turn)
            messages.append(tool_turn)

        response = ollama.chat(model=OLLAMA_MODEL, messages=messages, tools=OLLAMA_TOOLS)


def _looks_like_leaked_tool_call(text: str) -> bool:
    """Weaker local models occasionally write out something that LOOKS
    like a tool call as plain text instead of actually using the real
    tool-calling mechanism — sometimes even inventing a tool name that
    doesn't exist anywhere in tools.py. Speaking that raw JSON out loud
    would be a bad experience, so we catch the pattern here."""
    stripped = text.strip()
    return stripped.startswith("{") and stripped.endswith("}") and '"name"' in stripped


def ask_ai(conversation: list) -> str:
    """Dispatches to whichever provider is configured. Everything above
    and below this function doesn't need to know or care which one runs."""
    if AI_PROVIDER == "gemini":
        reply = ask_gemini(conversation)
    else:
        reply = ask_ollama(conversation)

    if _looks_like_leaked_tool_call(reply):
        # The raw JSON-looking reply is still saved to conversation history
        # as-is (useful context for the model itself later) — we just
        # don't hand the garbled text back to be spoken at the user.
        return "Sorry, I got a bit confused trying to do that — could you try rephrasing?"
    return reply


def _user_turn(text: str) -> dict:
    if AI_PROVIDER == "gemini":
        return {"role": "user", "parts": [{"text": text}]}
    return {"role": "user", "content": text}


def _history_is_compatible(conversation: list) -> bool:
    # Gemini's turns carry a "parts" list; Ollama's carry a "content" string.
    # Saved history from the other provider won't have the right key, so we
    # can detect a mismatch and start fresh instead of crashing on it.
    required_key = "parts" if AI_PROVIDER == "gemini" else "content"
    if not all(isinstance(m, dict) and required_key in m for m in conversation):
        return False

    # Also catch history saved before the tool_calls serialization fix —
    # those turns have tool_calls stored as garbled strings instead of
    # proper dicts (see the note in ask_ollama), and Ollama will reject
    # them the moment they're sent back. Same fix: treat as incompatible,
    # start over, rather than fail on every single turn forever.
    for m in conversation:
        tool_calls = m.get("tool_calls")
        if tool_calls and not all(isinstance(tc, dict) for tc in tool_calls):
            return False

    return True


def process_turn(text: str, conversation: list, mode: str = "voice") -> str:
    """Handles one full exchange: send text to the AI, speak the reply,
    save history. If the wake word interrupts the reply mid-sentence, this
    picks up right where that left off instead of just going quiet — all
    still inside one locked turn, so voice/text input from elsewhere waits
    its turn cleanly rather than racing this one."""
    global current_input_mode
    with turn_lock:
        current_input_mode = mode
        current_text = text
        reply = ""
        while True:
            conversation.append(_user_turn(current_text))
            set_status("thinking")
            reply = ask_ai(conversation)
            mem.save_conversation(conversation)

            leftover = speak(reply)
            if leftover is None:
                break  # played all the way through, nothing more to do
            if leftover:
                current_text = leftover  # a whole new command, said in the same breath
                continue
            # just the wake word alone, nothing said yet — ask and listen for it
            speak("Yes?")
            set_status("listening")
            new_text = listen(timeout=6, phrase_time_limit=12)
            if not new_text:
                break
            current_text = new_text
    return reply


def text_input_loop(conversation: list):
    """Runs on a background thread the whole time Jarvis is up. Lets you
    just type a message and hit Enter — no wake word needed, since typing
    already tells us you mean it. Runs independently of the voice loop;
    process_turn's lock keeps the two from colliding."""
    while True:
        try:
            text = input()
        except EOFError:
            return  # stdin closed — nothing more to read

        text = text.strip()
        if not text:
            continue

        if text.lower().strip(" .!") in EXIT_PHRASES:
            with turn_lock:
                speak("Goodbye.")
                mem.save_conversation(conversation)
            os._exit(0)  # blunt, but this thread can't cleanly stop main()'s mic loop

        process_turn(text, conversation, mode="text")


def main():
    model_label = GEMINI_MODEL if AI_PROVIDER == "gemini" else OLLAMA_MODEL
    logger.info(f"=== Jarvis starting (console) — provider={AI_PROVIDER}, model={model_label} ===")
    print(f"{NAME} is online (running on {model_label} via {AI_PROVIDER}).")
    print("Say 'Hey Jarvis' to wake me, or just type a message any time. Ctrl+C to quit.\n")
    conversation = mem.load_conversation()

    if conversation and not _history_is_compatible(conversation):
        print("(previous conversation isn't compatible with this session — starting fresh)")
        conversation = []
        mem.clear_conversation()
    elif conversation:
        print(f"(resuming previous conversation — {len(conversation)} messages loaded)")

    threading.Thread(target=text_input_loop, args=(conversation,), daemon=True).start()

    while True:
        try:
            leftover_command = wait_for_wake_word()

            if leftover_command:
                text = leftover_command
            else:
                speak("Yes?")
                text = listen(timeout=6, phrase_time_limit=12)
                if not text:
                    speak("I didn't catch that.")
                    continue

            if text.lower().strip(" .!") in EXIT_PHRASES:
                speak("Goodbye.")
                mem.save_conversation(conversation)
                logger.info("=== Jarvis shutting down (said goodbye) ===")
                break

            process_turn(text, conversation, mode="voice")

        except KeyboardInterrupt:
            mem.save_conversation(conversation)
            logger.info("=== Jarvis shutting down (Ctrl+C) ===")
            print("\nShutting down (conversation saved).")
            break
        except Exception as e:
            logger.exception("Error in main loop")
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
