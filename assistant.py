"""
assistant.py — the main Jarvis loop, running on Google's Gemini API (free tier).

Flow each cycle:
  0. Idle, listening for the wake word ("Hey Jarvis")
  1. Once woken, listen for your command (or use it if said in the same breath)
  2. Send text + tool list to Gemini
  3. If Gemini wants to call a tool, run it and send the result back
  4. Speak Gemini's final reply out loud, then go back to idling

Run with: python assistant.py
Press Ctrl+C to quit. Say "goodbye" / "stop listening" after waking it to exit.
"""

import os
import sys
import pyttsx3
import speech_recognition as sr
from dotenv import load_dotenv
import google.generativeai as genai

from tools import TOOL_SCHEMAS, TOOL_FUNCTIONS
import memory as mem

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
NAME = os.getenv("ASSISTANT_NAME", "Jarvis")

if not API_KEY or "your-key-here" in API_KEY:
    sys.exit(
        "No API key found. Copy .env.example to .env and add your free Gemini API key.\n"
        "Get one at https://aistudio.google.com/apikey"
    )

genai.configure(api_key=API_KEY)

SYSTEM_PROMPT_BASE = f"""You are {NAME}, a helpful voice assistant running locally on the
user's Windows PC. Keep spoken replies SHORT (1-3 sentences) since they'll be read
aloud by text-to-speech. Use the available tools whenever the user asks you to DO
something on their computer (open apps, check files, take a screenshot, etc).
For general questions, just answer directly and conversationally. If the user
shares something worth remembering (preferences, passwords, important facts),
proactively save it with the remember_fact tool."""


def build_system_prompt() -> str:
    """Append any previously saved facts so Jarvis 'just knows' them without
    having to call recall_fact every time."""
    facts = mem.all_memories_dict()
    if not facts:
        return SYSTEM_PROMPT_BASE
    facts_block = "\n".join(f"- {k}: {v}" for k, v in facts.items())
    return f"{SYSTEM_PROMPT_BASE}\n\nThings you already know about the user:\n{facts_block}"


def _uppercase_types(schema):
    """Gemini's function-calling schema expects JSON-schema 'type' values in
    UPPERCASE (STRING, OBJECT, INTEGER...), unlike Anthropic's lowercase
    convention that tools.py was originally written for. This recursively
    converts one to the other so tools.py itself never has to change."""
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


# Build Gemini's tool format once at startup from the same TOOL_SCHEMAS used
# elsewhere — tools.py doesn't need to know or care which AI provider is used.
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
    """Record one phrase from the mic and transcribe it. timeout=None waits
    indefinitely for speech to start (used while idling for the wake word)."""
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        try:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            return None
    try:
        text = recognizer.recognize_google(audio)
        print(f"You: {text}")
        return text
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        print(f"Speech recognition error: {e}")
        return None


WAKE_PHRASES = ["hey jarvis", "hello jarvis", "ok jarvis"]

# Tools that need a spoken "yes" before they actually run — irreversible or
# disruptive actions. Add tool names here as you add more risky tools.
DESTRUCTIVE_TOOLS = {"shutdown_computer", "restart_computer"}
CONFIRM_WORDS = {"yes", "yeah", "yep", "yup", "confirm", "do it", "go ahead", "sure", "affirmative"}


def confirm_action(prompt: str) -> bool:
    """Speak a yes/no question and listen for an affirmative response.
    Anything unclear or negative is treated as 'no' — better to fail safe."""
    speak(prompt)
    response = listen(timeout=6, phrase_time_limit=6)
    if not response:
        return False
    lowered = response.lower()
    return any(word in lowered for word in CONFIRM_WORDS)


def wait_for_wake_word() -> str:
    """Block until 'hey jarvis' (or a close variant) is heard. If the user
    said their whole request in the same breath ('hey jarvis what time is
    it'), return the leftover command text so we can skip listening again.
    Returns '' if only the wake phrase was said."""
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
        # Heard speech, but not the wake word — ignore it and keep waiting


def ask_gemini(conversation: list) -> str:
    """Send conversation to Gemini, handle any tool calls, return final text reply.

    conversation is a list of Gemini "Content" dicts:
      {"role": "user"/"model"/"function", "parts": [...]}
    This differs from Anthropic's format, which is why memory.json's shape
    will look a little different than it did under Claude — that's expected.
    """
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
            # Plain text reply — we're done
            reply = "".join(p.text for p in parts if p.text)
            conversation.append({"role": "model", "parts": [{"text": reply}]})
            return reply.strip()

        # Gemini wants to use one or more tools. Record its request, then
        # execute each call and package the results as a "function" turn.
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

        conversation.append({"role": "function", "parts": function_response_parts})
        response = model.generate_content(conversation)
        # loop again so Gemini can respond to the tool result


def main():
    print(f"{NAME} is online. Say 'Hey Jarvis' to wake me, or Ctrl+C to quit.\n")
    conversation = mem.load_conversation()
    if conversation:
        print(f"(resuming previous conversation — {len(conversation)} messages loaded)")

    while True:
        try:
            leftover_command = wait_for_wake_word()

            if leftover_command:
                # User said the command in the same breath as the wake word
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
            mem.save_conversation(conversation)  # save after every turn, not just on exit

        except KeyboardInterrupt:
            mem.save_conversation(conversation)
            print("\nShutting down (conversation saved).")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
