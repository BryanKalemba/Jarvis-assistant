# Jarvis — Personal Voice Assistant (Windows)

A local voice assistant that listens to you, thinks using Google's Gemini
(free tier), and takes real actions on your PC (opens apps, searches the
web, manages files, takes screenshots, reports system status, and more).

> **Note on the free tier:** Gemini's free tier has no cost and no credit
> card requirement, but it does have rate limits (requests per minute/day)
> that vary by model and can change over time — check
> https://ai.google.dev/gemini-api/docs/rate-limits if you hit one. For
> personal use talking to Jarvis a handful of times, you're unlikely to
> come close to the daily cap.

## How it works

```
 Mic → Whisper (local speech-to-text) → Gemini (decides what to do) → runs a Python tool → speaks reply
```

Gemini uses native **tool calling**: you don't need to write any
intent-parsing logic yourself. You just describe each ability in
`tools.py`, and the model decides when to use it based on what you say.

## Setup

1. **Install Python 3.10+** if you don't have it: https://www.python.org/downloads/

2. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```
   > Note: `pyaudio` can be finicky on Windows. If `pip install pyaudio` fails, run:
   > ```powershell
   > pip install pipwin
   > pipwin install pyaudio
   > ```

3. **Get a free Gemini API key:** https://aistudio.google.com/apikey
   No credit card required — Google's free tier gives you a standing daily
   quota on Flash-class models, which is what this project uses by default.

4. **Configure your key:**
   - Copy `.env.example` to `.env`
   - Paste your key into `GEMINI_API_KEY=`
   - The default model (`gemini-3.6-flash`) is a good starting point; check
     https://ai.google.dev/gemini-api/docs/pricing if you want to see current
     free-tier options, since model names/limits do shift over time — Google
     periodically retires older model IDs for new API keys (this happened to
     `gemini-2.5-flash` around mid-2026), so if you ever get a "no longer
     available to new users" error, that page will have the current name

5. **Run it:**
   ```powershell
   python assistant.py
   ```
   The first time you run it, it'll download the Whisper speech model
   (a couple hundred MB) before starting up — that's a one-time thing,
   later runs load it from a local cache and start much faster.

   Jarvis idles quietly until you say **"Hey Jarvis"** (also accepts "Hello
   Jarvis", "OK Jarvis", or just "Jarvis" on its own). You can either:
   - Say the wake word alone, wait for "Yes?", then speak your command, or
   - Say it all in one breath: *"Hey Jarvis, what time is it"*

   > Heads up: with a one-word wake option like "Jarvis," casually saying
   > the name mid-conversation about something unrelated will also wake it.
   > Remove `"jarvis"` from `WAKE_PHRASES` in `assistant.py` if that's
   > more annoying than convenient for you.

   Say "goodbye" after waking it to exit, or Ctrl+C any time.

   > **How this wake word works:** it's continuously transcribing short
   > bursts of speech (locally, via Whisper) and checking if "hey jarvis"
   > appears — not a dedicated offline wake-word model like Alexa uses. It's
   > simple and works well for personal use, but if you want lower latency
   > detection later, look into `openwakeword` or `pvporcupine` (Picovoice)
   > — both support training a custom, always-listening wake-word model.

## What it can do out of the box

- Open/close apps (`open notepad`, `close chrome`, `open spotify`...)
- Open websites by name (`open youtube`, `open gmail`, `go to espn.com`)
- Launch Steam games by voice — see setup step below
- Web search (`search for the weather in London`)
- Weather (`what's the weather in Tokyo`) — needs a free API key, see below
- Tell the time/date, do quick math (`what's 15% of 340`)
- Take a screenshot, open/list files and folders, read/write text files
- Clipboard read/write (`copy this: ...`, `what's on my clipboard`)
- Media control (`pause music`, `skip this song`)
- Report CPU/memory/battery status, lock your PC
- **Remember things** (`remember my wifi password is sunshine123`) and recall
  them later, even after restarting — see Memory section below
- Answer general questions — it's still a full AI model, ask it anything

## Memory & persistence

Jarvis now remembers things across restarts, stored as plain JSON in `data/`
(auto-created, git-ignored since it may contain personal info):

- **`data/memory.json`** — long-term facts. Say "remember my wifi password is
  X" and it's saved forever until you say "forget my wifi password" (or
  delete the file). Saved facts are automatically included in every
  conversation, so Jarvis "just knows" them without you asking it to recall.
- **`data/conversation_history.json`** — the last ~40 messages, saved after
  every turn. Restarting the script picks up roughly where you left off.
  Delete this file any time to start fresh.

## Setting up Steam game launching

1. Open `tools.py` and find `STEAM_GAMES` near the top.
2. For each game, find its **Steam App ID** — either right-click the game in
   your Steam library → Properties → Updates tab, or look at the game's
   store page URL (`store.steampowered.com/app/<ID>/`).
3. Add it to the dictionary:
   ```python
   STEAM_GAMES = {
       "elden ring": "1245620",
       "counter-strike 2": "730",
   }
   ```
4. Say "launch elden ring" — it opens via the `steam://rungameid/` protocol,
   which auto-starts Steam if it isn't already running.

## Improving how well it hears you

**1. Pick the right microphone.** If your PC has more than one mic (laptop
built-in + a USB headset, for example), Jarvis might default to the wrong
one. Run:
```powershell
python list_mics.py
```
Find your mic in the list, then add its number to `.env`:
```
MIC_DEVICE_INDEX=2
```

**2. Physical setup matters more than code.** A cheap USB headset mic will
usually outperform a laptop's built-in mic by a wide margin, especially in
a room with any background noise (fans, TV, etc). Positioning matters too
— closer and slightly off-axis (not directly in front of your mouth) tends
to sound clearer.

**3. Recognizer tuning is already applied** in `assistant.py` — longer
ambient-noise calibration on startup, a pause threshold tuned so it doesn't
cut you off mid-sentence, and dynamic adjustment as background noise
changes. You can tweak these further (`pause_threshold`,
`non_speaking_duration`) near the top of the `listen()` function if it's
still cutting you off too early or waiting too long after you finish.

**4. Speech recognition runs on Whisper, locally.** Jarvis transcribes your
voice with `faster-whisper` running on your own CPU — no internet needed
for the "hearing" part, and noticeably more accurate than a free web API,
especially with accents or background noise. The tradeoff is startup time:
the model loads into memory once when you run `python assistant.py` (a
couple seconds after the first run, which also downloads it). If it's
still mishearing you, try a bigger model in `.env`:
```
WHISPER_MODEL=small.en
```
`tiny.en` is fastest but least accurate, `base.en` is the default balance,
`small.en`/`medium.en` are more accurate but slower on CPU — the right
choice depends on how much your PC can chew through in real time.

## Extending it — add your own abilities

Open `tools.py` and:
1. Write a normal Python function that does the thing you want.
2. Add a matching entry to `TOOL_SCHEMAS` describing it in plain English
   (this is literally what the AI model reads to decide when to call it).
3. Add the function to `TOOL_FUNCTIONS` at the bottom.

That's the whole pattern — no other code changes needed. Ideas to add next:
- **Email**: send/read Gmail via the `google-api-python-client`
- **Calendar**: check/add events via Google Calendar API
- **Smart volume control**: install `pycaw` for precise volume/mute control
- **Better voice**: swap `pyttsx3` (robotic, offline) for `edge-tts`
  (free, much more natural, needs internet) or ElevenLabs (paid, best quality)

## Safety notes

- **Shutdown, restart, and closing apps all require spoken confirmation.**
  If Gemini decides to call `shutdown_computer`, `restart_computer`, or
  `close_application`, Jarvis will ask "Are you sure...?" (naming the
  specific app for close requests) and only proceeds on a clear "yes."
  Anything unclear, silent, or negative is treated as "no" — it fails safe
  rather than assuming consent. This check happens in Python, not by asking
  the model nicely, so it can't be talked around by rephrasing the request.
- To make another tool require confirmation, add its name to
  `DESTRUCTIVE_TOOLS` in `assistant.py`.
- `lock_computer` does **not** require confirmation since it's instantly
  reversible (just unlock with your password) — only genuinely disruptive
  actions are gated.
- If a shutdown/restart is confirmed, it still waits `delay_seconds`
  (default 10s) before actually happening — say "cancel shutdown" in that
  window as a second safety net.
- File-writing tools execute immediately with no confirmation step. Add
  them to `DESTRUCTIVE_TOOLS` too if you want the same protection for e.g.
  overwriting files.
- Keep your `.env` file out of version control (already covered by the
  included `.gitignore`).
- Speech recognition runs locally via Whisper — your voice never leaves
  your PC for the "hearing" part. Your typed-out commands and Jarvis's
  replies still go to Gemini's servers to generate responses, same as any
  cloud AI assistant.
- Saved memory (`data/memory.json`) is plain-text JSON on disk — fine for
  low-stakes info like a wifi password, not a substitute for a real
  password manager.
