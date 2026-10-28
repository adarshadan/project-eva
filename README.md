# Project EVA - Everything Virtual Assistant

A Windows desktop assistant: an animated 3D character who lives on your desktop, listens, speaks,
and operates the computer for you — apps and windows, any on-screen control, the web, files,
reminders — asking you only before something destructive or something that spends money.

## Setup and run

Windows 10/11, Python 3.10+.

1. Clone the repository (`git clone https://github.com/adarshadan/project-eva`), or extract the
   zip to a **fresh, empty** folder (never over an older copy).
2. Double-click `run.bat`. The first run creates a virtual environment and installs everything.
3. First time only: `playwright install chromium` inside that environment, for browser tasks.
4. Give Eva your NVIDIA API key: right-click her tray icon → **Set API key** (stored encrypted for
   your Windows user, so it survives updates), or run `set_key.bat`.

Talk to her, or right-click the tray icon → **Chat** to type. Say **"stop"** at any time.

| Tray item | What it does |
|---|---|
| Stop | Stops speech, the running task, and anything queued |
| Pause Eva / Resume Eva | She stops and ignores everything until resumed (or say "Eva, resume") |
| Status | One-glance health: microphone, model, browser, render, what she is doing |
| Activity and memory | Every action she took (failures in red), and what she remembers (forget any of it) |
| Skills folder | Opens `%USERPROFILE%\.eva\skills` — drop skill folders here; her learned skills are in `learned\` |
| Set API key / Restart / Quit | |

## What she can do beyond the desktop

- **Skills** — procedures in the open Agent Skills format (`SKILL.md`). Six of Eva's own ship in
  `skills\` (web research, YouTube summaries, daily briefing, tidying a folder, writing a
  document, blocked pages), plus a **library of 479** in `skills\library\`: 168 from Hermes Agent,
  15 from superpowers, 282 specialist personas from agency-agents and 14 from Anthropic — Word,
  Excel, PowerPoint and PDF work, finance models, research, writing, design, coding, marketing
  and more. She searches it with `find_skill`. Licences and what was left out (and why) are in
  `skills\library\NOTICE.md`. More can be dropped into the Skills folder. She **learns** new ones from tasks that took real work or that
  you corrected; every save appears in Activity, and the previous version is kept.
- **Connected services (MCP)** — list servers in `%USERPROFILE%\.eva\connections.json`. Exa's
  free search is connected by default, which gives her web search. Read-only tools run freely;
  anything that may be destructive asks you first. Tokens go in the encrypted store, not the file.
- **Fast reading** — web pages without opening a browser, YouTube transcripts, RSS feeds.
- **Repeating schedules** — "every weekday at 9, give me my briefing". A schedule that keeps
  failing, or fails because of your API key, pauses itself and tells you.
- **Undo** — any file she wrote, overwrote, created or moved can be put back ("undo that").
- **Keeps going on her own** — "keep scrolling until I say stop", "scroll to the bottom", "press
  the right arrow every 2 seconds", "click Next 20 times", "play tic-tac-toe / Connect Four / 2048
  with me", "play Flappy Bird", "play the dinosaur game", "press space the moment it turns green".
  The model only starts the activity; a local controller then watches the screen and acts many
  times a second (no model call per step), in any program or browser. Say "stop", or press
  Win+Shift+X, to end it; "faster" / "go up now" changes a running repeat. She keeps out of the
  game's area, pauses when you switch to another window, and says how it ended (won, lost, score).
- **Finds things on screen** — in any window: first the structure the window publishes (UI
  Automation: exact names and positions in every app, every browser page, the desktop and the
  taskbar), then the words on screen (the PC's own OCR, forgiving of misheard words), then the
  vision model for pictures and icons; tell her where ("above your head",
  "on the right") to narrow it. The vision model's coordinate format is measured once per model.
- **Understands misheard speech** — she sees how sure speech recognition was and what else it might
  have heard, corrects by sound and by what is on screen, and asks only when a wrong guess matters.
- **Finds files instantly** — through Windows' own search index (everything indexed, also words
  INSIDE documents: "the PDF that mentions the lease"), with her own index as the fallback.
- **Offers your usual routines (opt-in)** — tray > Settings > Initiative. She notices, on this PC
  only, which programs you first open at about the same time each day (and sites she opened for
  you) and offers them: "You usually open Outlook around 9:00 AM. Shall I open it?" "Yes" opens it,
  "no" counts against it, "don't ask again" stops it. She only offers, never acts on her own, and
  never records or offers anything to do with money (banking, payments, trading, tax, accounting).
- **Shows you what went wrong** — a task that failed keeps a step-by-step replay with a picture of
  the screen after each on-screen step (tray > Failed tasks; "what went wrong earlier?").
- **Talk over her** — speaking two or more words that are not her own stops her and is taken
  as your next request; her own voice coming back through the mic is recognised and ignored.

If something is wrong, run `doctor.bat` and send the `eva-doctor-report.txt` it writes, plus `eva.log`.

## How it is built

```
main.py                    wiring only: Qt app, avatar, tray, microphone, timers
core/
  registry.py              the one tool registry
  toolset/                 every capability, declared ONCE: schema, implementation,
                           risk class, deadline, how she performs it, what it feeds
  agent.py                 the conversation and the single dispatch pipeline
  runtime.py               front lane (instant: stop, hello, "what are you doing")
                           + task lane (one worker, one task at a time)
  speaker.py               her voice, on its own thread (sentence-pipelined, stops at once)
  llm.py, prompt.py        model client (retry, leak guards, plain-English errors), system prompt
  presence_impl.py, gui.py gestures/walking; the one GUI-thread bridge
  skills.py, learning.py   the skill library (open format) and learning from experience
  connections.py           MCP services as tools          web_reach.py   pages, YouTube, feeds
  checkpoints.py           undo for file changes
  elevation.py             administrator windows: refuse plainly instead of trying (Windows blocks it)
  longform.py              replies over LONG_REPLY_CHARS: she says one line, the bubble and chat window show it all
  activity.py              which pose fits the tool or skill she is running
activities.py              one continuous activity at a time: start, stop, pause, time limit, how it ended
perception.py              fast screen capture (dxcam / mss), local OCR, input with no built-in delays
loops.py                   repeat-until: scroll, keys, clicks, typing; until stopped / count / end / text
games.py                   turn-based games: the board read from the screen, moves solved exactly
reflex.py                  real-time play (Flappy Bird, runner games) and "react when X appears"
settings_store.py, settings_ui.py   the person's settings (~/.eva/settings.json) and the Settings window
game_play.py               any other game: learn the rules from the web, play fairly with a notebook
structure.py               finding controls by the structure windows publish (UI Automation), first
screen_text.py             finding text on screen by OCR (fuzzy, sound-alike)   core/hearing.py   what speech recognition was unsure of
predictive.py              routines noticed on this PC and offered (opt-in; never money)
core/replay.py             step-by-step replays of failed tasks   core/vitals.py   long-session health (leaks)
skills/                    the skills shipped with Eva
chat_ui.py, chat_store.py  her message bubble (typing dots), the chat window, the status pill; the kept conversation
permissions.py, finance_guard.py, command_policy.py   who must be asked, and how the question reads
desktop_control.py, ui_navigate.py, app_resolver.py, uia_worker.py, vision.py   the desktop
browser_agent.py, backend_tools.py, system_tools.py, scheduler.py, recorder.py  web, files, time
voice.py, stt_local.py     speech in and out        memory.py, audit.py   memory and the action log
avatar_ui.py, avatar_renderer.py, embodiment.py, web/avatar_3d.html            the character
```

**What she says is her reply.** The model's reply text is spoken and shown in the bubble by the
framework; there are no speech tools. **To add a capability**, add one `register(...)` call in
`core/toolset/` — the permission gate, deadline watchdog, body language, recorder, audit log and
the model's tool list all read from it. `python run_checks.py` verifies the whole set.

**Her body follows the work.** `web/avatar_3d.html` holds one eased pose per activity (reading,
writing, browsing, thinking, searching, organizing, presenting, listening, idle), short procedural
gestures that mean the action (reach-and-tap before a click on the target's side, press-down to
minimise, frame-and-slide to arrange, nod on a verified result), a blended walk, and a crossfade
whenever a clip starts or stops. The seven VRMA clips (twirl, hello, peace, finger_gun, spin_pose,
model_pose, stretch) play only when asked for or while idle. Bone axes were measured by rendering
the rig; `node tools/motion_test.js` checks the motion maths.

## Settings worth knowing (config.py)

Most everyday choices are in **tray > Settings** (or ask her: "talk less", "make the bubbles
green", "be smaller", "open your settings"): size, gesture speed, how she acts out her work,
voice, how much she says aloud, bubble colours, thinking effort, initiative (predictive help,
hints, remarks), replay privacy and hotkeys. They are saved in `%USERPROFILE%\.eva\settings.json`
and override `config.py`; those marked * apply after a restart. What asks first
(`CONFIRM_EFFECTS`) and the API key are deliberately not settings there.

- `VISION_FALLBACK_MODELS`, `VISION_COORDS`, `VISION_REQUEST_TIMEOUT`, `VISION_LOCATE_BUDGET` — vision models to try when the main one is busy or retired, how their boxes are read (`"auto"` = measured), and time limits.
- `MODEL_ID`, `VISION_MODEL_ID`, `REASONING_EFFORT` — the models and how long they think.
- `CONFIRM_EFFECTS` — what asks first (default: destructive and financial only).
- `PROACTIVE_COMMENTARY_ENABLED`, `PROACTIVE_SUGGESTIONS` — unprompted remarks; off by default.
- `STT_BACKEND` — `"google"` (default) or `"phonon"` (Phonon-2 on this PC via `start_phonon.bat`).
- `RENDER_MODE`, `QT_GRAPHICS_API`, `AVATAR_PRESERVE_BUFFER`, `AVATAR_FPS` — how the avatar is drawn. Run `flicker_test.bat` (Eva closed) to measure each on your screen; the best is saved to `%USERPROFILE%\.eva\render.json` and used automatically.
- `HOTKEY_CHAT`, `HOTKEY_REGION` — Win+Shift+E opens chat anywhere (with the text you selected); Win+Shift+W asks about part of the screen.
- `HOTKEY_STOP` — Win+Shift+X stops whatever she is doing, including a running game or repeat.
- `HELPER_MAX` — how many background helpers may work at once (default 3).
- `IDLE_LIFE`, `SIT_DROP` — her own life when left alone; how far she sinks when sitting.
- `AVATAR_SCALE` — her size (1.0 = 200 x 380 px; default 0.5).
- `LONG_REPLY_CHARS` — replies longer than this (default 100) are shown in her bubble and the chat window, not spoken in full. `LONG_REPLY_TARGET = "notepad"` sends them to Notepad instead.
- `QT_COMPOSITION` — `"gpu"` (default). `"software"` works only with `RENDER_MODE = "software"`; with a GPU mode it would switch WebGL off, so it is ignored.
- `BUBBLE_COLOURS` — `"blue"` (Eva blue, you green; default), `"green"` (swapped) or `"grey"` (iMessage).
- `POSE_TUNING` — pose overrides from the pose lab (`pose_lab.bat`); empty means the measured defaults.

## Tools for checking her on your PC

- `doctor.bat` — what works on this PC (windows, typing, browser, audio).
- `scorecard.bat` — 12 everyday tasks through the real Eva, scored by what happened; history in `%USERPROFILE%\.eva\scorecard-history.csv`.
- `python tools\model_probe.py` — measures the configured models on your key (reply speed, tool calls, how exactly they locate things on screen) before you switch one.
- `python tools/soak_test.py [--turns 50000]` — thousands of turns through the real pipeline; fails if memory or threads keep growing.
- `play_lab.bat` — plays her game controllers against local test games (tic-tac-toe, Connect Four, 2048, Flappy, runner, scrolling) and prints how each went.
- `flicker_test.bat` — measures avatar flicker per render setting and saves the best.

## Checks

`python run_checks.py` runs anywhere (Windows libraries are stubbed) and drives whole turns
through the real agent with a scripted model. It cannot verify the screen, microphone, audio or a
real browser — only running Eva on Windows can.

## Licence

Copyright 2026 Adarsha Dan. Licensed under the Apache License, Version 2.0 -- see [LICENSE](LICENSE).
The skills in `skills/library/` keep their own authors' licences (MIT and others), listed in
`skills/library/NOTICE.md`.
