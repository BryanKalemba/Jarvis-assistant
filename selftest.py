"""
selftest.py — checks that Jarvis is actually ready to run, without
starting it up for real. Run this any time something feels off, right
after changing your .env, or before reporting a bug — it'll tell you
exactly what's wrong instead of you having to guess from a crash.

Usage: python selftest.py
"""

import importlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CHECKS = []  # (name, function) pairs, populated by the @check decorator below


def check(name):
    """Registers a function as a self-test check. The function itself
    doesn't run until main() calls it — this decorator just adds it to
    the list, so checks run in a predictable order with live progress
    output instead of everything happening silently up front."""
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator


@check("Python version")
def _():
    version = sys.version.split()[0]
    if sys.version_info >= (3, 10):
        return "pass", version
    return "fail", f"{version} — Jarvis needs Python 3.10 or newer"


@check("Required packages")
def _():
    required = [
        "edge_tts", "ollama", "pygame", "pyttsx3", "speech_recognition",
        "faster_whisper", "dotenv", "google.generativeai", "pyautogui",
        "psutil", "pyperclip", "requests",
    ]
    missing = [pkg for pkg in required if not _can_import(pkg)]
    if missing:
        return "fail", f"missing: {', '.join(missing)} — run: pip install -r requirements.txt"
    return "pass", "all installed"


def _can_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


@check(".env file")
def _():
    if not Path(".env").exists():
        return "fail", "not found — copy .env.example to .env and fill it in"
    return "pass", "found"


@check("tools.py — tool registry")
def _():
    import tools
    schema_names = {s["name"] for s in tools.TOOL_SCHEMAS}
    func_names = set(tools.TOOL_FUNCTIONS.keys())
    if schema_names != func_names:
        missing_funcs = schema_names - func_names
        missing_schemas = func_names - schema_names
        return "fail", (
            f"mismatch — schemas without a function: {missing_funcs or 'none'}, "
            f"functions without a schema: {missing_schemas or 'none'}"
        )
    return "pass", f"{len(schema_names)} tools registered correctly"


@check("memory.py — read/write")
def _():
    import memory as mem
    test_key = "__selftest_probe__"
    mem.remember(test_key, "ok")
    value = mem.recall(test_key)
    mem.forget(test_key)
    if value != "ok":
        return "fail", "wrote a value but couldn't read it back correctly"
    return "pass", "data/memory.json is writable and readable"


@check("AI provider")
def _():
    provider = os.getenv("AI_PROVIDER", "ollama").strip().lower()
    if provider not in ("gemini", "ollama"):
        return "fail", f"AI_PROVIDER is '{provider}' — must be 'gemini' or 'ollama'"
    return _check_gemini() if provider == "gemini" else _check_ollama()


def _check_gemini():
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or "your-key-here" in key:
        return "fail", "GEMINI_API_KEY is missing — get one at https://aistudio.google.com/apikey"
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model_name = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        print("   (making one small real request to confirm the key actually works...)")
        model = genai.GenerativeModel(model_name=model_name)
        response = model.generate_content("Reply with just the word OK.")
        if response.text:
            return "pass", f"connected to {model_name}, got a real response back"
        return "warn", "connected but got an empty response — probably fine, worth a manual check"
    except Exception as e:
        return "fail", f"couldn't reach Gemini: {e}"


def _check_ollama():
    try:
        import ollama
        models = [m["model"] for m in ollama.list()["models"]]
    except Exception as e:
        return "fail", f"can't reach Ollama — is it installed and running? ({e})"
    configured = os.getenv("OLLAMA_MODEL", "llama3.1")
    if not any(configured in m for m in models):
        return "fail", f"model '{configured}' isn't pulled — run: ollama pull {configured}"
    return "pass", f"Ollama is running, '{configured}' is pulled ({len(models)} model(s) total)"


@check("Microphone")
def _():
    import speech_recognition as sr
    mics = sr.Microphone.list_microphone_names()
    if not mics:
        return "fail", "no microphones detected at all"

    mic_index_raw = os.getenv("MIC_DEVICE_INDEX", "").strip()
    if not mic_index_raw:
        return "pass", f"{len(mics)} microphone(s) found, using the system default"

    try:
        idx = int(mic_index_raw)
    except ValueError:
        return "fail", f"MIC_DEVICE_INDEX='{mic_index_raw}' isn't a number"
    if idx < 0 or idx >= len(mics):
        return "fail", f"MIC_DEVICE_INDEX={idx} is out of range — only {len(mics)} mic(s) found. Run: python list_mics.py"
    return "pass", f"using mic #{idx}: {mics[idx]}"


@check("Whisper speech model")
def _():
    from faster_whisper import WhisperModel
    size = os.getenv("WHISPER_MODEL", "base.en")
    print(f"   (loading '{size}' — downloads on first run, can take a moment)")
    WhisperModel(size, device="cpu", compute_type="int8")
    return "pass", f"'{size}' loads correctly"


@check("Text-to-speech (edge-tts)")
def _():
    import asyncio
    import tempfile
    import edge_tts
    voice = os.getenv("TTS_VOICE", "en-US-GuyNeural")

    async def _try():
        path = os.path.join(tempfile.gettempdir(), "jarvis_selftest.mp3")
        await edge_tts.Communicate("test", voice).save(path)
        size = os.path.getsize(path)
        os.remove(path)
        return size

    try:
        size = asyncio.run(_try())
        if size > 0:
            return "pass", f"'{voice}' reachable, generated real audio"
        return "warn", "connected but got an empty file back — worth a manual check"
    except Exception as e:
        return "warn", f"couldn't reach edge-tts ({e}) — Jarvis will use the offline voice instead, which still works fine"


@check("Audio playback (pygame)")
def _():
    import pygame
    pygame.mixer.init()
    return "pass", "audio output initialized"


@check("Weather tool (optional)")
def _():
    key = os.getenv("WEATHER_API_KEY", "").strip()
    if not key:
        return "warn", "not configured — get_weather won't work until you add a free key from openweathermap.org/api"
    return "pass", "configured"


def main():
    print(f"Jarvis self-test — running {len(CHECKS)} checks...\n")
    icons = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}
    results = []

    for name, fn in CHECKS:
        try:
            status, detail = fn()
        except Exception as e:
            status, detail = "fail", f"unexpected error: {e}"
        results.append((name, status))
        print(f"{icons[status]} {name}: {detail}")

    fails = [r for r in results if r[1] == "fail"]
    warns = [r for r in results if r[1] == "warn"]

    print()
    if fails:
        print(f"{len(fails)} problem(s) need fixing before Jarvis will work correctly — see the ❌ lines above.")
    elif warns:
        print(f"All critical checks passed. {len(warns)} minor thing(s) worth a look (⚠️  lines above), but Jarvis should run fine.")
    else:
        print("Everything checks out — Jarvis is ready to go.")


if __name__ == "__main__":
    main()
