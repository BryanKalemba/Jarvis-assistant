"""
memory.py
---------
Two kinds of persistence, both plain JSON files on disk — easy to inspect,
back up, or wipe by just deleting a file.

1. Long-term facts   -> data/memory.json
   Things you explicitly ask Jarvis to remember, e.g. "remember my wifi
   password is X". These get injected into every conversation's system
   prompt, so Jarvis knows them without you having to ask.

2. Conversation history -> data/conversation_history.json
   The last N messages of chat, saved after every turn, so restarting
   the script doesn't wipe its short-term memory of what you were
   just talking about.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
MEMORY_FILE = DATA_DIR / "memory.json"
HISTORY_FILE = DATA_DIR / "conversation_history.json"

# Keep saved history bounded so the context sent to the model — and your
# API usage — doesn't grow forever. Raise this if you want it to remember further back.
MAX_HISTORY_MESSAGES = 40


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, default=str))


# --- long-term facts ---
def remember(key: str, value: str) -> str:
    facts = _load_json(MEMORY_FILE, {})
    facts[key.lower().strip()] = value
    _save_json(MEMORY_FILE, facts)
    return f"Got it, I'll remember that {key} is {value}."


def recall(key: str) -> str:
    facts = _load_json(MEMORY_FILE, {})
    value = facts.get(key.lower().strip())
    return value if value else f"I don't have anything saved for '{key}'."


def forget(key: str) -> str:
    facts = _load_json(MEMORY_FILE, {})
    key = key.lower().strip()
    if key in facts:
        del facts[key]
        _save_json(MEMORY_FILE, facts)
        return f"Forgot '{key}'."
    return f"I didn't have anything saved for '{key}'."


def list_all_memories() -> str:
    facts = _load_json(MEMORY_FILE, {})
    if not facts:
        return "I don't have any saved memories yet."
    return "; ".join(f"{k}: {v}" for k, v in facts.items())


def all_memories_dict() -> dict:
    return _load_json(MEMORY_FILE, {})


# --- conversation history ---
def load_conversation() -> list:
    return _load_json(HISTORY_FILE, [])


def save_conversation(conversation: list):
    _save_json(HISTORY_FILE, conversation[-MAX_HISTORY_MESSAGES:])


def clear_conversation():
    _save_json(HISTORY_FILE, [])
