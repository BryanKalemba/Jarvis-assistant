"""
tools.py
--------
Everything Jarvis can actually DO on your PC lives here. To add a new
ability:
  1. Write a function below
  2. Add its schema to TOOL_SCHEMAS
  3. Register it in TOOL_FUNCTIONS at the bottom

Whichever AI model assistant.py is currently using reads TOOL_SCHEMAS to
decide when to call each function, then assistant.py runs the matching
Python function here. This file doesn't know or care which model that is.
"""

import os
import subprocess
import datetime
import webbrowser
import platform
import psutil
import pyautogui
import pyperclip
import requests
from pathlib import Path

import memory as mem

# App name -> the actual .exe Windows needs to launch it. Add your own here.
APP_MAP = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "spotify": "spotify.exe",
    "vscode": "code.exe",
    "vs code": "code.exe",
    "task manager": "taskmgr.exe",
    "settings": "start ms-settings:",
    "cmd": "cmd.exe",
    "terminal": "wt.exe",
    "discord": "discord.exe",
    "steam": "steam.exe",
    "slack": "slack.exe",
    "teams": "ms-teams.exe",
    "outlook": "outlook.exe",
    "firefox": "firefox.exe",
}


def open_application(app_name: str) -> str:
    """Open an application by name (e.g. 'notepad', 'chrome', 'spotify')."""
    key = app_name.lower().strip()
    exe = APP_MAP.get(key)
    try:
        if exe and exe.startswith("start "):
            os.system(exe)
        elif exe:
            subprocess.Popen(exe, shell=True)
        else:
            # not in our map — try running it as-is, works for a lot of exes on PATH
            subprocess.Popen(app_name, shell=True)
        return f"Opened {app_name}."
    except Exception as e:
        return f"Couldn't open {app_name}: {e}"


def close_application(app_name: str) -> str:
    """Force-close a running application by name (e.g. 'notepad', 'chrome')."""
    exe = APP_MAP.get(app_name.lower().strip())
    if not exe or exe.startswith("start "):
        return f"I don't know the process name for {app_name}, so I can't close it."
    result = subprocess.run(
        ["taskkill", "/IM", exe, "/F"], capture_output=True, text=True
    )
    if result.returncode == 0:
        return f"Closed {app_name}."
    return f"{app_name} doesn't appear to be running."


def search_web(query: str) -> str:
    """Open a web browser and search for the given query."""
    webbrowser.open(f"https://www.google.com/search?q={query}")
    return f"Searching the web for '{query}'."


# Site name -> URL. Add whatever you visit most often.
WEBSITE_MAP = {
    "youtube": "https://youtube.com",
    "gmail": "https://mail.google.com",
    "email": "https://mail.google.com",
    "github": "https://github.com",
    "netflix": "https://netflix.com",
    "reddit": "https://reddit.com",
    "twitter": "https://twitter.com",
    "x": "https://twitter.com",
    "amazon": "https://amazon.com",
    "claude": "https://claude.ai",
    "steam": "https://store.steampowered.com",
}


def open_website(site: str) -> str:
    """Open a website by common name (e.g. 'youtube', 'gmail', 'github') or a direct URL/domain."""
    key = site.lower().strip()
    if key in WEBSITE_MAP:
        url = WEBSITE_MAP[key]
    elif site.startswith("http://") or site.startswith("https://"):
        url = site
    elif "." in site:  # looks like a domain, e.g. "espn.com"
        url = f"https://{site}"
    else:  # not a known site or domain — fall back to search
        return search_web(site)
    webbrowser.open(url)
    return f"Opening {key}."


# Map friendly game names -> Steam App IDs so you can launch by voice.
# Find a game's App ID either in Steam (right-click game -> Properties ->
# Updates tab) or in its store page URL: store.steampowered.com/app/<ID>/
STEAM_GAMES = {
    # "counter-strike 2": "730",
    # "elden ring": "1245620",
}


def launch_steam_game(game_name: str) -> str:
    """Launch a game through Steam by name. Requires the game's Steam App ID
    to already be added to STEAM_GAMES in tools.py."""
    key = game_name.lower().strip()
    app_id = STEAM_GAMES.get(key)
    if not app_id:
        return (
            f"I don't have a Steam App ID saved for '{game_name}'. Add it to "
            f"STEAM_GAMES in tools.py — find it on the game's store page URL "
            f"(store.steampowered.com/app/<ID>) or in Steam's game Properties."
        )
    os.startfile(f"steam://rungameid/{app_id}")
    return f"Launching {game_name} through Steam."


def get_time() -> str:
    """Get the current date and time."""
    now = datetime.datetime.now()
    return now.strftime("It's %I:%M %p on %A, %B %d, %Y.")


def take_screenshot() -> str:
    """Take a screenshot and save it to the Desktop."""
    desktop = Path.home() / "Desktop"
    desktop.mkdir(exist_ok=True)
    filename = desktop / f"screenshot_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    pyautogui.screenshot(str(filename))
    return f"Screenshot saved to {filename}."


def list_files(directory: str = "~/Desktop") -> str:
    """List files in a directory. Defaults to Desktop."""
    path = Path(directory).expanduser()
    if not path.exists():
        return f"Directory {path} doesn't exist."
    items = sorted(p.name for p in path.iterdir())
    if not items:
        return f"{path} is empty."
    return f"Contents of {path}: " + ", ".join(items[:40])


def open_folder(directory: str) -> str:
    """Open a folder in File Explorer. Accepts paths like ~/Documents or full paths."""
    path = Path(directory).expanduser()
    if not path.exists():
        return f"Folder {path} doesn't exist."
    os.startfile(str(path))  # Windows-only: opens in File Explorer
    return f"Opened {path} in File Explorer."


def read_text_file(filepath: str) -> str:
    """Read and return the contents of a plain text file."""
    path = Path(filepath).expanduser()
    try:
        content = path.read_text(errors="ignore")
        return content[:3000]  # keep responses reasonable
    except Exception as e:
        return f"Couldn't read {filepath}: {e}"


def write_text_file(filepath: str, content: str) -> str:
    """Create or overwrite a text file with given content."""
    path = Path(filepath).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"Wrote {len(content)} characters to {path}."
    except Exception as e:
        return f"Couldn't write {filepath}: {e}"


def copy_to_clipboard(text: str) -> str:
    """Copy the given text to the system clipboard."""
    pyperclip.copy(text)
    return "Copied to clipboard."


def read_clipboard() -> str:
    """Read and return whatever text is currently on the clipboard."""
    content = pyperclip.paste()
    if not content:
        return "Clipboard is empty."
    return content[:1000]


def set_volume(level: int) -> str:
    """Set system volume to a percentage (0-100). Requires pycaw for precision;
    this version uses keyboard media-key simulation as a simple fallback."""
    level = max(0, min(100, level))
    # No pycaw installed, so we can't set an exact percentage — just point
    # the user at their keyboard keys instead of pretending this worked.
    return (
        f"Precise volume control needs the optional 'pycaw' package. "
        f"For now, use your keyboard volume keys. (Requested: {level}%)"
    )


def calculate(expression: str) -> str:
    """Evaluate a math expression, e.g. '15% of 340' or '(12 + 8) * 3'."""
    import re

    # people say things like "12 x 8" or "2^3" out loud, not valid Python
    expr = expression.lower().replace("x", "*").replace("^", "**")
    percent_match = re.match(r"(\d+(\.\d+)?)\s*%\s*of\s*(\d+(\.\d+)?)", expr)
    if percent_match:
        pct, _, base, _ = percent_match.groups()
        return f"{expression} = {float(pct) / 100 * float(base):g}"

    # eval() is dangerous on arbitrary input, so only allow characters that
    # could possibly form a math expression — no letters, no function calls
    if not re.fullmatch(r"[\d\s\.\+\-\*/\(\)%]+", expr):
        return "That doesn't look like a math expression I can safely evaluate."
    try:
        result = eval(expr, {"__builtins__": {}}, {})
        return f"{expression} = {result:g}" if isinstance(result, float) else f"{expression} = {result}"
    except Exception as e:
        return f"Couldn't calculate that: {e}"


def media_control(action: str) -> str:
    """Control media playback. action: play_pause, next, previous, mute, volume_up, volume_down."""
    key_map = {
        "play_pause": "playpause",
        "next": "nexttrack",
        "previous": "prevtrack",
        "mute": "volumemute",
        "volume_up": "volumeup",
        "volume_down": "volumedown",
    }
    key = key_map.get(action.lower().strip())
    if not key:
        return f"Unknown media action: {action}"
    pyautogui.press(key)
    return f"Media action: {action}."


def get_weather(city: str) -> str:
    """Get the current weather for a city. Requires WEATHER_API_KEY in .env
    (free key at https://openweathermap.org/api)."""
    api_key = os.getenv("WEATHER_API_KEY")
    if not api_key:
        return "Weather isn't set up yet — add a free OpenWeatherMap API key to .env as WEATHER_API_KEY."
    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": api_key, "units": "metric"},
            timeout=5,
        )
        data = resp.json()
        if resp.status_code != 200:
            return f"Couldn't get weather for {city}: {data.get('message', 'unknown error')}"
        temp = data["main"]["temp"]
        desc = data["weather"][0]["description"]
        return f"It's {temp:.0f}°C with {desc} in {city}."
    except Exception as e:
        return f"Couldn't fetch weather: {e}"


def system_status() -> str:
    """Get CPU, memory, and battery status."""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory().percent
    battery = psutil.sensors_battery()
    batt_str = f", battery {battery.percent}%" if battery else ""
    return f"CPU usage {cpu}%, memory usage {mem}%{batt_str}."


def shutdown_computer(delay_seconds: int = 10) -> str:
    """Shut down the computer. Includes a short delay as a safety net —
    say 'cancel shutdown' within that window to stop it."""
    os.system(f"shutdown /s /t {delay_seconds}")
    return f"Shutting down in {delay_seconds} seconds. Say 'cancel shutdown' to stop it."


def restart_computer(delay_seconds: int = 10) -> str:
    """Restart the computer. Includes a short delay as a safety net —
    say 'cancel shutdown' within that window to stop it."""
    os.system(f"shutdown /r /t {delay_seconds}")
    return f"Restarting in {delay_seconds} seconds. Say 'cancel shutdown' to stop it."


def cancel_shutdown() -> str:
    """Cancel a pending shutdown or restart."""
    os.system("shutdown /a")
    return "Cancelled the pending shutdown or restart."


def lock_computer() -> str:
    """Lock the Windows workstation."""
    os.system("rundll32.exe user32.dll,LockWorkStation")
    return "Locking the computer."


def remember_fact(fact_key: str, fact_value: str) -> str:
    """Save a fact for later, e.g. key='wifi password', value='hunter2'.
    Use this whenever the user says 'remember that...' or shares something
    they'll likely want recalled later."""
    return mem.remember(fact_key, fact_value)


def recall_fact(fact_key: str) -> str:
    """Look up a previously saved fact by its key."""
    return mem.recall(fact_key)


def forget_fact(fact_key: str) -> str:
    """Delete a previously saved fact."""
    return mem.forget(fact_key)


def list_memories() -> str:
    """List everything currently saved in long-term memory."""
    return mem.list_all_memories()


# Everything below is what the AI model actually sees — not the Python code
# above, just these descriptions. Write them clearly; the model relies on
# them entirely to pick the right tool and fill in the right arguments.
TOOL_SCHEMAS = [
    {
        "name": "open_application",
        "description": "Open a desktop application by name.",
        "input_schema": {
            "type": "object",
            "properties": {"app_name": {"type": "string", "description": "e.g. notepad, chrome, spotify"}},
            "required": ["app_name"],
        },
    },
    {
        "name": "search_web",
        "description": "Search the web for a query in the default browser.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_time",
        "description": "Get the current date and time.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "take_screenshot",
        "description": "Take a screenshot and save it to the Desktop.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_files",
        "description": "List files in a folder. Defaults to Desktop if not specified.",
        "input_schema": {
            "type": "object",
            "properties": {"directory": {"type": "string", "description": "Folder path, e.g. ~/Documents"}},
        },
    },
    {
        "name": "read_text_file",
        "description": "Read the contents of a plain text file.",
        "input_schema": {
            "type": "object",
            "properties": {"filepath": {"type": "string"}},
            "required": ["filepath"],
        },
    },
    {
        "name": "write_text_file",
        "description": "Create or overwrite a text file with given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filepath", "content"],
        },
    },
    {
        "name": "system_status",
        "description": "Report CPU, memory, and battery usage.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lock_computer",
        "description": "Lock the computer screen.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "shutdown_computer",
        "description": "Shut down the computer completely.",
        "input_schema": {
            "type": "object",
            "properties": {"delay_seconds": {"type": "integer", "description": "Safety delay before shutdown, default 10"}},
        },
    },
    {
        "name": "restart_computer",
        "description": "Restart the computer.",
        "input_schema": {
            "type": "object",
            "properties": {"delay_seconds": {"type": "integer", "description": "Safety delay before restart, default 10"}},
        },
    },
    {
        "name": "cancel_shutdown",
        "description": "Cancel a pending shutdown or restart that was just triggered.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "close_application",
        "description": "Force-close a running application by name.",
        "input_schema": {
            "type": "object",
            "properties": {"app_name": {"type": "string", "description": "e.g. notepad, chrome, spotify"}},
            "required": ["app_name"],
        },
    },
    {
        "name": "open_folder",
        "description": "Open a folder in File Explorer.",
        "input_schema": {
            "type": "object",
            "properties": {"directory": {"type": "string", "description": "e.g. ~/Downloads, C:/Projects"}},
            "required": ["directory"],
        },
    },
    {
        "name": "copy_to_clipboard",
        "description": "Copy text to the system clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "read_clipboard",
        "description": "Read the current contents of the clipboard.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "calculate",
        "description": "Evaluate a math expression or percentage, e.g. '15% of 340' or '(12+8)*3'.",
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    {
        "name": "media_control",
        "description": "Control currently playing media (Spotify, YouTube, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play_pause", "next", "previous", "mute", "volume_up", "volume_down"],
                }
            },
            "required": ["action"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "open_website",
        "description": "Open a website by common name (youtube, gmail, github...) or a direct URL/domain.",
        "input_schema": {
            "type": "object",
            "properties": {"site": {"type": "string", "description": "e.g. youtube, github, espn.com"}},
            "required": ["site"],
        },
    },
    {
        "name": "launch_steam_game",
        "description": "Launch a video game through Steam by name.",
        "input_schema": {
            "type": "object",
            "properties": {"game_name": {"type": "string"}},
            "required": ["game_name"],
        },
    },
    {
        "name": "remember_fact",
        "description": "Save a fact to long-term memory for later recall. Use whenever the user says "
        "'remember that...' or shares something they'll want you to recall later "
        "(preferences, passwords, important dates, names, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "fact_key": {"type": "string", "description": "short label, e.g. 'wifi password'"},
                "fact_value": {"type": "string", "description": "the actual value to remember"},
            },
            "required": ["fact_key", "fact_value"],
        },
    },
    {
        "name": "recall_fact",
        "description": "Look up a specific previously saved fact by its key.",
        "input_schema": {
            "type": "object",
            "properties": {"fact_key": {"type": "string"}},
            "required": ["fact_key"],
        },
    },
    {
        "name": "forget_fact",
        "description": "Delete a previously saved fact from memory.",
        "input_schema": {
            "type": "object",
            "properties": {"fact_key": {"type": "string"}},
            "required": ["fact_key"],
        },
    },
    {
        "name": "list_memories",
        "description": "List everything currently saved in long-term memory.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_FUNCTIONS = {
    "open_application": open_application,
    "search_web": search_web,
    "get_time": get_time,
    "take_screenshot": take_screenshot,
    "list_files": list_files,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
    "system_status": system_status,
    "lock_computer": lock_computer,
    "shutdown_computer": shutdown_computer,
    "restart_computer": restart_computer,
    "cancel_shutdown": cancel_shutdown,
    "close_application": close_application,
    "open_folder": open_folder,
    "copy_to_clipboard": copy_to_clipboard,
    "read_clipboard": read_clipboard,
    "calculate": calculate,
    "media_control": media_control,
    "get_weather": get_weather,
    "open_website": open_website,
    "launch_steam_game": launch_steam_game,
    "remember_fact": remember_fact,
    "recall_fact": recall_fact,
    "forget_fact": forget_fact,
    "list_memories": list_memories,
}
