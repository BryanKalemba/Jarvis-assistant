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

Run with: python assistant.py
Press Ctrl+C to quit. Say "goodbye" / "stop listening" after waking it to exit.

Switching providers mid-project: saved conversation history is shaped
differently for each provider (Gemini uses "parts", Ollama uses
"content"), so history saved under one provider gets detected as
incompatible and cleared automatically if you switch — see
_history_is_compatible() below.
"""

import asyncio
import io
import os
import sys
import tempfile
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
something on their computer (open apps, check files, take a screenshot, etc).
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


async def _synthesize_speech(text: str, filepath: str):
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(filepath)


def _speak_offline_fallback(text: str):
    engine = pyttsx3.init()
    engine.setProperty("rate", 180)
    engine.say(text)
    engine.runAndWait()


def speak(text: str):
    print(f"{NAME}: {text}")
    audio_path = os.path.join(tempfile.gettempdir(), "jarvis_reply.mp3")
    try:
        asyncio.run(_synthesize_speech(text, audio_path))
        pygame.mixer.music.load(audio_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.wait(50)
    except Exception as e:
        print(f"(couldn't reach edge-tts, using the offline voice instead: {e})")
        _speak_offline_fallback(text)


def listen(timeout: float | None = 6, phrase_time_limit: float | None = 12) -> str | None:
    """Record one phrase from the mic and transcribe it with Whisper.
    timeout=None waits indefinitely for speech to start — used while idling
    for the wake word."""
    with sr.Microphone(device_index=MIC_DEVICE_INDEX) as source:
        recognizer.adjust_for_ambient_noise(source, duration=1.0)
        try:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            return None

    wav_bytes = audio.get_wav_data()
    segments, _ = whisper_model.transcribe(io.BytesIO(wav_bytes), language="en")
    text = "".join(segment.text for segment in segments).strip()

    if not text:
        return None
    print(f"You: {text}")
    return text


WAKE_PHRASES = ["hey jarvis", "hello jarvis", "ok jarvis", "jarvis"]

# Anything in here gets a spoken "are you sure?" before it actually runs.
DESTRUCTIVE_TOOLS = {"shutdown_computer", "restart_computer", "close_application"}
CONFIRM_WORDS = {"yes", "yeah", "yep", "yup", "confirm", "do it", "go ahead", "sure", "affirmative"}


def confirm_action(prompt: str) -> bool:
    """Ask a yes/no question out loud and listen for the answer. Anything
    unclear, silent, or negative counts as 'no' — better to fail safe."""
    speak(prompt)
    response = listen(timeout=6, phrase_time_limit=6)
    if not response:
        return False
    lowered = response.lower()
    return any(word in lowered for word in CONFIRM_WORDS)


def wait_for_wake_word() -> str:
    """Block until 'hey jarvis' (or a variant) is heard. If the whole
    request was said in one breath ('hey jarvis what time is it'), return
    the leftover text so we can skip listening a second time. Returns ''
    if only the wake word itself was said."""
    print(f"\n💤 Waiting for wake word — say 'Hey Jarvis'...")
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
            print(f"  [tool] {name}({args}) -> {result}")
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

        assistant_turn = {"role": "assistant", "content": message.get("content", ""), "tool_calls": tool_calls}
        conversation.append(assistant_turn)
        messages.append(assistant_turn)

        for call in tool_calls:
            name = call["function"]["name"]
            args = dict(call["function"]["arguments"])
            result = _run_tool(name, args)
            print(f"  [tool] {name}({args}) -> {result}")
            tool_turn = {"role": "tool", "content": str(result), "name": name}
            conversation.append(tool_turn)
            messages.append(tool_turn)

        response = ollama.chat(model=OLLAMA_MODEL, messages=messages, tools=OLLAMA_TOOLS)


def ask_ai(conversation: list) -> str:
    """Dispatches to whichever provider is configured. Everything above
    and below this function doesn't need to know or care which one runs."""
    if AI_PROVIDER == "gemini":
        return ask_gemini(conversation)
    return ask_ollama(conversation)


def _user_turn(text: str) -> dict:
    if AI_PROVIDER == "gemini":
        return {"role": "user", "parts": [{"text": text}]}
    return {"role": "user", "content": text}


def _history_is_compatible(conversation: list) -> bool:
    # Gemini's turns carry a "parts" list; Ollama's carry a "content" string.
    # Saved history from the other provider won't have the right key, so we
    # can detect a mismatch and start fresh instead of crashing on it.
    required_key = "parts" if AI_PROVIDER == "gemini" else "content"
    return all(isinstance(m, dict) and required_key in m for m in conversation)


def main():
    model_label = GEMINI_MODEL if AI_PROVIDER == "gemini" else OLLAMA_MODEL
    print(f"{NAME} is online (running on {model_label} via {AI_PROVIDER}). Say 'Hey Jarvis' to wake me, or Ctrl+C to quit.\n")
    conversation = mem.load_conversation()

    if conversation and not _history_is_compatible(conversation):
        print("(previous conversation was saved under a different AI provider — starting fresh)")
        conversation = []
        mem.clear_conversation()
    elif conversation:
        print(f"(resuming previous conversation — {len(conversation)} messages loaded)")

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
                break

            conversation.append(_user_turn(text))
            reply = ask_ai(conversation)
            speak(reply)
            mem.save_conversation(conversation)

        except KeyboardInterrupt:
            mem.save_conversation(conversation)
            print("\nShutting down (conversation saved).")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
