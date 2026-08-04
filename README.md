# Jarvis — Personal Voice Assistant (Windows)

A voice assistant that listens to you, thinks, and takes real actions on
your PC (opens apps, searches the web, manages files, takes screenshots,
reports system status, and more). It can think using either **Google's
Gemini** (cloud, free tier) or **a local model via Ollama** — switch
between them with one line in `.env`, everything else stays identical.

|  | Gemini (`AI_PROVIDER=gemini`) | Ollama (`AI_PROVIDER=ollama`) |
|---|---|---|
| Cost | Free tier, no card needed | Free forever |
| Setup | API key only | Install Ollama + pull a model (multi-GB) |
| Hardware | None — runs in the cloud | 8GB+ free RAM for a usable model |
| Tool-calling reliability | Solid | Noticeably weaker — expect more wrong-tool moments |
| Internet required | Yes, every request | No — fully offline once set up |
| Rate limits | Yes | None |

Not sure which to use? Gemini for the smoothest experience, Ollama if you
want zero dependency on any cloud account. You can switch any time.

## How it works

```
 Mic → Whisper (local speech-to-text) → Gemini or Ollama (decides what to do) → runs a Python tool → edge-tts speaks the reply
```

Both providers use native **tool calling**: you don't write any
intent-parsing logic yourself. You just describe each ability in
`tools.py`, and whichever model is active decides when to use it based on
what you say.

> **Switching providers later:** saved conversation history is shaped
> differently between the two (Gemini needs `"parts"`, Ollama needs
> `"content"`), so if you switch `AI_PROVIDER` after having used the other
> one for a while, Jarvis will print a note and start the conversation
> fresh rather than crash on the old shape. Your saved memory facts in
> `data/memory.json` aren't affected either way — those aren't tied to a
> provider.

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

3. **Copy `.env.example` to `.env`**, then set `AI_PROVIDER` to whichever
   you want to use, and follow the matching setup below.

   **If `AI_PROVIDER=gemini`:**
   - Get a free key (no credit card) at https://aistudio.google.com/apikey
   - Paste it into `GEMINI_API_KEY=`
   - `GEMINI_MODEL` defaults to `gemini-3.6-flash`; Google occasionally
     retires model names, so if you get a "no longer available" error,
     check https://ai.google.dev/gemini-api/docs/pricing for the current one

   **If `AI_PROVIDER=ollama`:**
   - Install Ollama from https://ollama.com — it installs like a normal
     app and runs quietly in the background afterward
   - Pull a model that supports tool calling:
     ```powershell
     ollama pull llama3.1
     ```
     This is an ~4.7GB download and needs roughly 8GB of free RAM. If
     that's too much for your PC, `ollama pull llama3.2` is a much lighter
     3B model — faster, but noticeably worse at picking the right tool. If
     your PC can handle more, `qwen2.5:14b` or `mistral-nemo` tend to be
     more reliable at tool calling than either of the above.
   - Make sure `OLLAMA_MODEL` in `.env` matches whatever you pulled

4. **Run it:**
   ```powershell
   python assistant.py
   ```
   The first run also downloads the Whisper speech model (a couple
   hundred MB) before starting up — a one-time thing, cached after that.

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

## Changing how it sounds

Jarvis speaks with `edge-tts` — Microsoft's free neural voices, the same
tech behind "Read Aloud" in Edge. Much more natural than the classic
robotic Windows TTS voice it used to run on.

To change the voice, set `TTS_VOICE` in `.env`. A few solid options are
listed there already; to see the full list, run:
```powershell
edge-tts --list-voices
```

**Heads up on internet dependency:** Whisper (hearing) always runs fully
offline, no matter which `AI_PROVIDER` you're using. TTS output goes out
to Microsoft's servers, and if `AI_PROVIDER=gemini`, the "thinking" part
needs internet too — only `AI_PROVIDER=ollama` gets you a fully offline
setup, and even then only if edge-tts also has no connection (it falls
back automatically rather than failing silently). If edge-tts can't reach
the internet for any reason, Jarvis uses the offline `pyttsx3` voice
instead — you'll see a note in the console, and it'll sound noticeably
more robotic until the connection's back. If you want Jarvis fully
air-gapped, use `AI_PROVIDER=ollama` and skip installing `edge-tts`/`pygame`
— the `try` block will fail immediately and fall through to the offline
voice every time.

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
- **Even better voice**: `edge-tts` is already wired in, but if you want
  the best quality available, ElevenLabs (paid) is a further step up

## Safety notes

- **Shutdown, restart, and closing apps all require spoken confirmation.**
  If the model decides to call `shutdown_computer`, `restart_computer`, or
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
- Speech recognition always runs locally via Whisper — your voice never
  leaves your PC regardless of `AI_PROVIDER`. Your transcribed commands do
  go to Google's servers if `AI_PROVIDER=gemini`; with `AI_PROVIDER=ollama`
  they stay on your machine. edge-tts's voice output talks to Microsoft's
  servers either way, unless it falls back to the offline voice (see the
  voice section above).
- Saved memory (`data/memory.json`) is plain-text JSON on disk — fine for
  low-stakes info like a wifi password, not a substitute for a real
  password manager.
