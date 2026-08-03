"""
assistant.py — the main Jarvis loop.

Brain: Google's Gemini API (free tier).
Ears: faster-whisper, running locally on your CPU — no internet needed
for speech recognition, and noticeably more accurate than the free web
API this used to run on.

Flow each cycle:
  0. Idle, listening for the wake word ("Hey Jarvis")
  1. Once woken, listen for your command (or use it if said in the same breath)
  2. Send text + tool list to Gemini
  3. If Gemini wants to call a tool, run it and send the result back
  4. Speak Gemini's final reply out loud, then go back to idling

Run with: python assistant.py
Press Ctrl+C to quit. Say "goodbye" / "stop listening" after waking it to exit.
"""

import io
import os
import sys
import pyttsx3
import speech_recognition as sr
from faster_whisper import WhisperModel
from dotenv import load_dotenv
import google.generativeai as genai

from tools import TOOL_SCHEMAS, TOOL_FUNCTIONS
import memory as mem

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
NAME = os.getenv("ASSISTANT_NAME", "Jarvis")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "base.en")

MIC_DEVICE_INDEX = os.getenv("MIC_DEVICE_INDEX")
MIC_DEVICE_INDEX = int(MIC_DEVICE_INDEX) if MIC_DEVICE_INDEX else None

if not API_KEY or "your-key-here" in API_KEY:
    sys.exit(
        "No API key found. Copy .env.example to .env and add your free Gemini API key.\n"
        "Get one at https://aistudio.google.com/apikey"
    )

genai.configure(api_key=API_KEY)

# Downloads on first run (a couple hundred MB depending on model size), then
# gets cached locally, so later startups are quick.
print(f"Loading speech recognition model ({WHISPER_MODEL_SIZE})...")
whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")

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
    # tools.py writes schemas the way Anthropic expects (lowercase types like
    # "string"). Gemini wants the same info but uppercase ("STRING"). Rather
    # than maintain two versions of every schema, we just convert on the fly.
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

EXIT_PHRASES = {"goodbye", "stop listening", "exit", "quit", "that's all"}


def speak(text: str):
    print(f"{NAME}: {text}")
    engine = pyttsx3.init()
    engine.setProperty("rate", 180)
    engine.say(text)
    engine.runAndWait()


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

    # Whisper wants raw audio, not a SpeechRecognition AudioData object, so
    # we hand it a WAV in memory instead of writing a temp file to disk.
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


def ask_gemini(conversation: list) -> str:
    """Send the conversation to Gemini, run any tools it asks for, and
    return its final spoken-friendly reply.

    conversation entries look like {"role": "user"/"model", "parts": [...]}.
    Tool results get sent back as a "user" turn — Gemini's current API
    doesn't accept a dedicated "function" role like older docs suggest."""
    model = genai.GenerativeModel(
        model_name=MODEL,
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
            func = TOOL_FUNCTIONS.get(name)

            if not func:
                result = f"Unknown tool: {name}"
            elif name in DESTRUCTIVE_TOOLS:
                if name == "close_application" and "app_name" in args:
                    action_label = f"close {args['app_name']}"
                else:
                    action_label = name.replace("_", " ")
                if confirm_action(f"Are you sure you want to {action_label}?"):
                    try:
                        result = func(**args)
                    except Exception as e:
                        result = f"Error running {name}: {e}"
                else:
                    result = "The user did not confirm — action cancelled."
            else:
                try:
                    result = func(**args)
                except Exception as e:
                    result = f"Error running {name}: {e}"

            print(f"  [tool] {name}({args}) -> {result}")
            function_response_parts.append({
                "function_response": {"name": name, "response": {"result": str(result)}}
            })

        conversation.append({"role": "user", "parts": function_response_parts})
        response = model.generate_content(conversation)
        # ask again so Gemini can react to what the tool returned


def main():
    print(f"{NAME} is online. Say 'Hey Jarvis' to wake me, or Ctrl+C to quit.\n")
    conversation = mem.load_conversation()

    # If this history was saved under a different AI provider (different
    # message shape), don't crash on it — just start over.
    if conversation and not all(isinstance(m, dict) and "parts" in m for m in conversation):
        print("(previous conversation was saved in an incompatible format — starting fresh)")
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

            conversation.append({"role": "user", "parts": [{"text": text}]})
            reply = ask_gemini(conversation)
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
