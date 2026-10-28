# Eva Desktop Avatar — Handoff

Working notes for continuing this project in a new conversation. Read this
first, then `web/assets/README.md` for asset licensing.

**User:** Adarsha Dan. Prefers formal, concise communication. Local project
path: `D:\eva-avatar\eva-avatar\`.

---

## 1. What Eva is

A Windows desktop AI companion: an anime-style 3D character that lives on
the desktop (no window chrome, click-through around the character), listens
continuously, speaks, and takes real actions on the machine — desktop
control, browser automation, file work, reminders.

**Stated vision:** a general-purpose AI companion that is lifelike and acts
like a real assistant on the desktop. Referenced Meta's Muse Agent as a
directional example, but wants general-purpose rather than narrow.

---

## 2. Current architecture

Rebuilt 2026-10-10 (section 18). `main.py` is wiring only; decisions live in `core/`.

| File | Role |
|---|---|
| `core/registry.py`, `core/toolset/*` | Every capability declared ONCE: schema, implementation, effect, deadline, embodiment, recorder/memory flags |
| `core/agent.py` | Conversation history, the turn loop, the single dispatch pipeline (args filter → gate → deadline → performer → result shaping → audit → procedure memory) |
| `core/runtime.py` | Front lane (stop, hello, "what are you doing" -- instant, no model) + task lane (one worker, priority queue, merging) |
| `core/speaker.py` | Voice on its own thread: bubble + speech, sentence pipelining, instant stop, mic-echo window |
| `core/llm.py`, `core/prompt.py` | Model client (streaming, retry, leak/think/envelope guards, `explain_error`), system prompt |
| `core/presence_impl.py`, `core/gui.py` | Gestures/walking bound to the avatar; the GUI-thread bridge |
| `capabilities.py` | Thin lookups over the registry, for `permissions.py` and `embodiment.py` |
| `avatar_ui.py` | `AvatarWidget` (hit box), `DesktopStage` (WebGL surface), bubble, status, radial menu, `ClickForwarder` |
| `web/avatar_3d.html` | Three.js + VRM scene, gestures, walk cycle, life layer, WebGL recovery |
| `vision.py` | **Vision fallback** — screenshot → VL model → answers/coordinates |
| `desktop_control.py`, `ui_navigate.py`, `app_resolver.py`, `uia_worker.py` | Windows/UIA, mouse, keyboard, app launch, UI ladder, crash-isolated UIA reads |
| `browser_agent.py` | Playwright agent loop (Eva's own browser window, or a named one) |
| `system_tools.py`, `backend_tools.py` | Clipboard, notifications, files, commands, processes |
| `scheduler.py` | Reminders + deferred tasks, persisted to `scheduled.json` |
| `voice.py`, `stt_local.py` | edge-tts synth + MCI playback helper; Google or local Phonon STT |
| `permissions.py`, `finance_guard.py`, `command_policy.py`, `audit.py`, `memory.py`, `secret_store.py` | Who is asked and how; action log; memory; encrypted key |
| `idle_behavior.py`, `recorder.py`, `overlay_effects.py`, `logger.py`, `eva_doctor.py` | Idle life, teach-and-repeat, highlights, logging, on-PC diagnostic |

**Models** (`config.py`, NVIDIA `integrate.api.nvidia.com/v1`):
- Reasoning: `nvidia/nemotron-3-super-120b-a12b`
- Vision: `nvidia/nemotron-nano-12b-v2-vl`
- `REASONING_EFFORT = "low"` — Nemotron 3 supports a real reasoning budget;
  this is the lever on the long-standing overthinking problem.

**Run:** `run.bat` (creates venv if absent, syncs `requirements.txt` every
run, launches). Key via `NVIDIA_API_KEY` env var or `config.py`.

---

## 3. Two-window design (important)

The character is **two** top-level windows:
- `AvatarWidget` — exactly character-sized, painted alpha 1/255 (invisible
  but click-catching). This is the hit box.
- `DesktopStage` — larger transparent WebGL surface, `WindowTransparentForInput`,
  so clicks pass through. Follows the hit box via `_sync_stage`.

`STAGE_PAD_X/Y` **must match** between `avatar_ui.py` and `avatar_3d.html`
(currently 1.6 / 1.15).

**Do not attempt a fullscreen transparent stage.** It was tried and made the
avatar vanish entirely on this user's GPU.

---

## 4. Hard-won lessons — do not regress these

Each was a real, confirmed bug. Re-introducing any would be a regression.

1. **`play_animation` must report truth.** It once returned `{"ok": True}`
   unconditionally. `walk_taskbar` is *screen locomotion*, not a clip —
   routes to `avatar.walk_along_taskbar()`. JS `playClip()` returns
   true/false; Python waits on a `threading.Event` for the real answer.
2. **Never auto-stop after a speech/bubble-only round.** A `TERMINAL_ONLY_TOOLS`
   optimisation silently swallowed follow-through when the model said
   "let me speak first, then act."
3. **Keep streaming** (`stream=True`). User explicitly wants live terminal output.
4. **Duplicate speech.** (Structurally gone since the rebuild: speech is the reply, not a tool.
   The agent still never repeats a line already said in the same turn.) Model called `tts_speak("X")` then returned "X" as final
   content, which `main.py` spoke again. `Eva.spoke_this_turn` guards this.
5. **Retry must cover provider overload.** `APIError("Service temporarily
   overloaded")` is raised mid-stream and matches no timeout/connection
   check — it killed 3 of 4 interactions in one session. Now classified by
   message substring; genuine errors (bad model, bad schema) still raise fast.
6. **Roll back history on a failed turn.** A stranded user message caused a
   later *observation* turn to answer a stale voice request.
7. **Mic feedback loop.** Two layers: stop mic with `wait_for_stop=True`
   before TTS, plus a `_mic_ignore_until` window. Never call
   `stop_always_listening(wait_for_stop=True)` from the listener thread —
   it joins itself and silently kills listening for the session.
8. **TTS via env var, not string interpolation.** Apostrophes/smart quotes
   broke PowerShell repeatedly; now edge-tts anyway.
9. **One system owns the skeleton per frame.** A finished clip
   (`clampWhenFinished`) and the rest-pose code both writing the same bones
   = visible jitter. `endVrmGesture()` hands over cleanly; no
   `resetNormalizedPose()` (causes a one-frame T-pose flash).
10. **Avoid `setGeometry` on the stage for pure moves** — it reallocates the
    WebGL drawing buffer. `_sync_stage` uses `move()` when size is unchanged.
11. **Single serialized worker.** Concurrent turns corrupted shared state
    ("doom loop"). Queue priority: scheduled → newest person message →
    observation (dropped if anything else waits).
12. **Confirmation persists per turn**, not per tool call. Per-call reset
    meant one task demanded five confirmations.

---

## 5. Known open issues

- **Flickering (partly unresolved).** WebGL context losses are fixed (zero in
  recent logs). Residual flicker is suspected to come from **seven**
  always-on-top translucent windows composited independently by DWM.
  Structural fix = consolidate them. Mitigations applied: overlay is no
  longer a permanent fullscreen 60Hz repaint; stage uses `move()` not
  `setGeometry`; redundant `resizeScene` suppressed.
- **Flicker, second hypothesis (2026-09-30, unverified).** Chromium's native
  window-occlusion tracking can hide/re-show a transparent click-through
  always-on-top WebGL window each time another topmost window (bubble, overlay,
  status) appears. `config.WEBENGINE_FLAGS` now disables it (applied in
  `main()` before `QApplication`). If flicker persists, set `WEBENGINE_FLAGS = ""`
  to rule it out, then try `--disable-gpu-compositing`.
- **Audio clipping (2026-09-30, unverified).** Two suspected causes, both
  addressed in `voice.py`: the start is swallowed while an idle output device
  wakes (now a silent clip is played during synthesis: `TTS_PRIME`), and the end
  is cut because MCI is closed the moment it reports "stopped" (now kept open
  `TTS_TAIL_SECONDS`). If clipping remains, raise the tail to 0.8.
- **Overthinking.** Much improved with Nemotron's reasoning budget, but the
  model still takes extra round trips (e.g. `tts_speak` → `show_bubble` →
  final). Prompt tells it to combine tools into one turn.
- **STT accuracy.** Google Speech mangles proper nouns ("Yuva awards" for
  "you've got the wrong answer"). Prompt instructs Eva to check garbled
  `[voice]` text against context. Faster-Whisper was considered and rejected —
  user has no capable GPU.
- Arm rest-pose constants: RESOLVED 2026-10-10 -- every axis was measured by rendering (section 22).

---

## 6. Agreed roadmap (researched, phases 1–2 partly done)

Based on research into UFO², OpenAI Operator, Claude computer use, Project
Mariner. Key finding: **hybrid perception** (accessibility tree + vision,
fused) is the industry consensus; perception cost drives the design
(a11y ~50ms, DOM ~100ms, vision LLM 2–5s), so vision must be a *fallback*.

| Phase | Status |
|---|---|
| 1. Consolidate windows into one surface | **Partial** — deferred, fullscreen approach known to fail on this GPU |
| 2. Hybrid perception (UIA + vision) | **Done** — `vision.py`, `look_at_screen`, `locate_on_screen` |
| 3. Unified action layer (prefer native API, fall back to GUI) | Not started |
| 4. Speculative multi-action (UFO² reports up to 51% fewer LLM calls) | Not started |
| 5. Tiered permissions + audit log (replaces binary confirm gate) | Not started |
| 6. Persistent memory / execution traces | Not started |
| 7. Two-tier orchestration (coordinator + per-app specialists) | Not started |

Reality check to keep expectations honest: on OSWorld, humans score ~72%,
best agents ~12%. This frontier is unsolved.

---

## 7. Security notes

- **API keys have leaked twice** in this project — once in `config.py`, once
  in a `config.py.bak` inside an uploaded zip. Never commit or zip a real key;
  `config.py` must keep the `PASTE_YOUR_KEY_HERE` placeholder.
- Eva once read a password aloud from `Credentials.txt`. System prompt now
  forbids reading out or displaying credential-like file contents.
- `move_file`, `browser_act`, clicks/typing and window actions are
  confirm-gated.

---

## 8. Working practices that have paid off

- **Always ask for `eva.log`.** Nearly every real root cause came from it,
  and several "obvious" hypotheses were wrong until checked.
- **Verify before claiming.** Syntax-check every edit; unit-test tricky logic
  in isolation (coordinate rescaling, retry classification, queue draining).
- **State uncertainty plainly.** Cannot render the UI, run Windows, or view
  MP4 uploads here — say so rather than guessing.
- **Deliver the full zip and tell the user to extract fresh.** Mixing old and
  new files has caused real, confusing failures (e.g. a `brain.py`/`main.py`
  signature mismatch that crashed every message).
- Third-party/other-AI contributions (e.g. "Hermes") must be **diffed and
  reviewed**, not merged wholesale — one such zip contained genuinely good
  fixes *and* would have reverted items 1, 2 and 3 above.

---

## 9. Assets

`web/assets/character.glb` — VRoid "AvatarSample_B" (commercial use,
modification, redistribution allowed; **no credit required**). Has zero baked
animations.

`web/assets/vrma/*.vrma` — VRoid official motion pack, 7 gestures.
**Credit IS required on commercial use:** "Animation credits to pixiv Inc.'s
VRoid Project". No walk clip exists — the walk cycle is procedural, in
`tick()`.

---

## 10. The capability framework (read before adding any tool)

Everything Eva knows about a tool is declared ONCE in `capabilities.py`:
`surface` (what it acts on), `effect` (observe / interact / modify / destroy /
financial / external), `verb`, `locus` (element / coords / window /
foreground) and `approach` (walk / gaze / none). Everything else derives:

| Derived from the manifest | Where it is used |
|---|---|
| whether the person is asked | `permissions.PermissionGate.authorize` (effect + real arguments/target; `config.CONFIRM_EFFECTS = (DESTROY, FINANCIAL)`) |
| whether she walks / looks / highlights | `embodiment.Performer` |
| the status line ("Reading agent_core.py...") | `ToolSpec.describe` |
| whether the recorder sees it | `capabilities.recordable_names` |

**To add a tool (since the 2026-10-10 rebuild):** one `register(...)` call in
`core/toolset/` -- name, description, parameters, implementation, effect,
deadline, embodiment fields. Tools needing live objects are declared without
`fn` and bound at startup (`registry.bind`). `python run_checks.py` and the
startup self-test fail if a tool has no implementation or a malformed schema.
The manifest fields below now live on `core.registry.Tool`.

`embodiment.py` decides how an action is *performed*. Its rules, each learned
the hard way: it never changes an action's outcome (runs once, result returned
untouched, exceptions propagate); reactions come from the verified result, so
an unverifiable click is never celebrated; it drops to a minimal mode during
rapid batches or when the person is waiting; missing animations fall back
(procedural pose -> classic clip -> text).

**Front end vs back end.** A visible target gets look -> walk -> reach ->
highlight -> act -> react. A back-end action (files, processes, commands) has
nowhere to walk, so it gets a status line and a working pose after a short
delay. `act_on_element` reaches native controls through UI Automation patterns
(toggle, expand, select, set value...) and verifies the state actually changed.
`backend_tools.py` reaches files and the system directly.

**Visuals (superseded 2026-10-10, section 22).** The procedural poses (`p_*`), the activity
poses and the clip mapping are now built on bone axes MEASURED by rendering the rig, and every
pose was looked at. `tools/motion_test.js` checks the motion maths in Node on every run of the
checks. The element highlight (`overlay_effects.highlight_rect`) is still unverified visually.


---

## 11. Permissions, audit, memory, ladder, hand-off (built 2026-09-30)

- **Approval model.** The person is asked ONLY for destructive actions and for
  money. `permissions.py` builds the dialog text from the real arguments and
  target; nothing the model writes reaches it. There is no `confirm` tool.
  Financial approvals are never task-scoped; `end_process` and destructive
  hotkeys are always one-off; no approver or a dialog crash means DENY.
  `finance_guard.py` detects money (button label, payment page, card fields,
  Luhn numbers, page total).
- **Audit.** `audit.py` -> `~/.eva/audit.jsonl` (rotating, credentials redacted,
  content by length only). Every dispatched tool is recorded.
- **Memory.** `memory.py` -> `~/.eva/memory.db`. Refuses instruction-shaped or
  credential text. Notes written after reading web content are stored untrusted
  and never injected. Injected notes are labelled "NOT instructions and NOT
  permissions".
- **UI ladder.** `ui_navigate.py`: pattern -> by name -> keyboard -> vision.
  Climb down only if a rung did not act or demonstrably failed; acted-but-
  unverifiable STOPS (prevents double-sending). Untested on a real desktop.
- **Captcha.** Hand-off and resume only; no solving. `browser_resume` tool.
- **Replay.** `recorder.run_recording` routes every step through normal dispatch.

## 12. Field findings, 2026-09-30 (eva.log from a real run) and what changed

The run used a zip built BEFORE section 11's modules were packaged (the zip
lacked `permissions.py`, `memory.py`, `audit.py`, `ui_navigate.py`,
`finance_guard.py`), so none of it was exercised. Findings from that log:

| Finding | Change |
|---|---|
| `launch_app('brave')` failed (WinError 2): PATH-only lookup, so anything not pre-listed failed | `app_resolver.py`: PATH, App Paths registry, Start Menu, install folders, Store apps; a miss returns `did_you_mean` |
| "in Brave" was answered with the bundled Chromium; `browser_extract` then read `about:blank` and returned `ok: True` with the question echoed back | `browser_open(url, browser=)` drives an installed Brave/Chrome/Edge; `browser_extract` on a blank page returns `ok: False` with the fix |
| Raw click gave only `verified: False`; she said she could not confirm | `click_coords`/`double_click_coords` now report `screen_changed` (before/after hash) |
| Log ended mid-action with no traceback (native crash or external kill) | `faulthandler` -> `~/.eva/crash.log`, a run marker that flags an unclean previous run at startup (`CRASH REPORT:` in eva.log), Qt messages routed to eva.log |
| Test runs wrote into the shipped `eva.log` | `EVA_LOG_PATH`; zip excludes `eva.log` |
| Browser was silently relaunched (blank) between calls | cause NOT found; see the log line "Browser started" appearing more than once per session |

**Crash cause is unproven.** The last logged action was `ui_tree` of a Chrome
window followed by a raw click. UI Automation on a browser tree from a worker
thread is one candidate; nothing in the log confirms it. Read `crash.log` and
the `CRASH REPORT` line after the next crash before theorising.

## 13. Second field run, 2026-09-30 20:12-20:18 (new build confirmed running)

Evidence the new build ran: `found_via` in launch_app results, the approval
dialog and focus-restore log line. The first start had no NVIDIA_API_KEY (401s).

| Finding | Change |
|---|---|
| Log ends in `ui_tree` on the Windows Settings window: no result, no traceback (second run in a row ending right after UI Automation work) | `ui_tree` and `find_elements_by_text` now run in a child process (`uia_worker.py`, 25s timeout, 500-element cap). A native crash becomes a tool failure. **Cause of the crash itself is still unproven** -- read `~/.eva/crash.log` |
| Avatar showed "Couldn't move that: handle not found" | `close_window` was declared with the verb "tidy" (-> "move that"). Now verb "close"; stale-handle errors say the window no longer exists and to call `list_windows` |
| That message stayed on screen | the flash was cleared only if still the current action; now cleared by its own token |
| Bubble showed walls of `[voice] ...` text | model echoed the person's messages ahead of its answer; `strip_echoed_input` removes them |
| Flicker persists | `config.RENDER_MODE` = default / no_gpu_comp / software / angle_d3d11. Change one, restart, watch. Cause unknown |

## 14. Third field run, 2026-09-30 20:28-20:29

Working in this run: speech, walking, list_windows, the new renderer-mode log
line. Nothing regressed; what stopped was the process at
`minimize_window` on the console window (log ends on the Tool call line).

**What "CRASH REPORT" means.** It only says the previous run did not reach a
clean exit. Closing the console window or killing the process produces the
same line, so it is NOT proof of a crash. In this log the 20:28:46 report
belongs to a run that shows a normal Qt teardown message (someone closed it).

**Working theory (unproven):** three runs in a row ended on a Tool call with no
result: click_coords, ui_tree (Settings), minimize_window (console). All three
wait on another process's UI thread. One hung call blocks the single worker
thread, so Eva stops answering while the avatar stays -- it looks like a crash.

| Change | Why |
|---|---|
| `deadline.py`: native/cross-process tools run under a time limit (`TOOL_DEADLINES`); on timeout the model gets a failure, eva.log gets the stuck thread's stack (also logged after 8s) | bounds the hang and records WHERE it hangs. **Next time the log shows a `did not finish` / `still running` block with a stack, that is the answer** |
| minimize/maximize/restore use `ShowWindowAsync` and poll for the result | the synchronous ShowWindow waits on the target window's thread |
| console QuickEdit switched off at start | a click in the console freezes every write to it, and logging holds a lock, so all logging threads stall |

## 15. Verification tools and the life layer (built 2026-09-30, after the third field run)

- **`doctor.bat` / `eva_doctor.py`**: drives a real Notepad through launch, focus,
  minimise, restore, maximise, move/resize, typing + read-back, accessibility tree,
  raw click, close; opens example.com in the bundled browser and in Brave/Chrome if
  installed; checks the model's tool calling, the microphone, speech synthesis and
  playback (two yes/no questions about clipped audio); reports GPU and driver.
  Every check has a time limit, so a hang is reported as `HUNG`. Output:
  `eva-doctor-report.txt`. **Ask for this file before theorising about a fault.**
- **`ACCEPTANCE.md`**: the five scenarios every change is judged by.
- **Life layer** (`web/avatar_3d.html`, `config.AVATAR_LIFE`): blinking, mouth
  movement and head motion while she speaks (`voice.is_speaking()` polled every 120 ms
  by main.py -> `avatar.set_speaking`). The mouth is a believable envelope, NOT true
  lip-sync (audio plays in another process). Unverified visually.
- **Pose lab** (`pose_lab.bat` -> http://localhost:8765/avatar_3d.html?lab=1): buttons
  for every pose, expression and the mouth; sliders for the sign constants, pose gain,
  rest-arm angles and mouth gain; "Show settings" prints a `POSE_TUNING = {...}` block
  for `config.py`. Tunables are read through `T(key, default)` from `window.EVA_TUNE`.
- Tests: `tools/life_test.js` (blink rate, mouth range, gain), `tools/motion_test.js` (section 22).
- Known weakness: the page loads Three.js and three-vrm from a CDN at start-up; no
  internet means no avatar. Vendoring them was NOT done because it cannot be tested
  here and a wrong import path would blank the avatar. Do it only after `doctor.bat`
  exists on the target machine to confirm.

## 16. Local speech recognition (Phonon-2), added 2026-10-05

Source: github.com/fermionresearch/phonon (docs/server.md, docs/cpu.md). Phonon-2 is a
164 MB English model (CC-BY-4.0 weights, Apache-2.0 CLI) that runs on x86-64 Windows CPUs
and serves an OpenAI-compatible `POST /v1/audio/transcriptions` on 127.0.0.1:8000.
The vendor reports 5.21 % average WER on the Open ASR Leaderboard's English sets; that is
the vendor's measurement, NOT a measurement on this user's voice or microphone.

- `stt_local.py` client, `voice.recognize()` single entry point (local first, Google fallback;
  a dead server is probed once per `STT_LOCAL_RETRY_SECONDS`), `start_phonon.bat` (own venv).
- Off by default: `config.STT_BACKEND = "google"`. Set `"phonon"` after `start_phonon.bat` works.
- Tested here against a fake server only (contract, fallback, cool-down). NOT tested against the
  real server or on Windows. `doctor.bat` has an INFO line for it.
- The server is single-request: a second utterance queues behind the first.
- Evaluated and NOT adopted: Cactus Whistle/Needle (on-device STT 16.9 MB / tool-calling model;
  only macOS binaries are shown in its docs, 30 s clips, no word-error figures we could verify;
  Needle's tool-calling could be a cheap router later). Open Dots (Anil-matcha) and CopilotKit
  OpenDots are web-app workspaces (Next.js/React + Docker), not desktop avatars; useful as
  design references for approvals/audit and per-agent computers, not as code to merge.

## 17. Components taken from the two OpenDots repos (2026-10-05)

Reviewed: Anil-matcha/open-dots (FastAPI + Next.js self-hosted workspace; prototype) and
CopilotKit/OpenDots (React/AG-UI template; alpha). Neither is a desktop avatar, so no code
was copied; three PATTERNS were reimplemented for Eva in Python/PySide6:

| Pattern (source) | Eva implementation |
|---|---|
| Credentials encrypted at rest (Open Dots) | `secret_store.py`: Windows DPAPI via ctypes, `~/.eva/secrets.bin`, no plaintext fallback; `config.NVIDIA_API_KEY` reads env, then the store. `set_key.bat`, tray "Set API key". Fixes the key being wiped by every folder replacement (eva.log 20:28 showed 401s for that reason) |
| Visible activity records and human control (CopilotKit Dot computer panel; Open Dots approval/audit view) | `activity_ui.py`, tray "Activity and memory": live audit list (failures in red), Stop button (same `do_stop()` as the spoken stop phrase) |
| User-managed memory (CopilotKit Memory) | same window, Memory tab: view facts (web-derived notes flagged), forget one or all (`Memory.list_facts`) |

NOT taken, and why:
- Conversation/call split ("realtime speech plus a separate compute agent so the conversation
  continues while longer work runs"): the most valuable idea for Eva, because one worker thread
  currently serves everything and a stuck tool silences her. It is a rewrite of the worker loop
  in main.py/brain.py; do it only after `doctor.bat` shows the current loop's real behaviour.
- Per-agent Docker computers, Composio connectors, Slack, Spaces/pages, multi-user: not
  applicable to a single-user desktop avatar.
- Parallel Search MCP (web search): unverified protocol and sends queries to a third party.
Untested on Windows: DPAPI path (tested here with a stand-in cipher), both Qt windows/dialogs.


---

## 18. The rebuild (2026-10-10): one coherent core

**Why.** The person judged the integration "not cohesive", and the 2026-10-10 log measured it:
6 utterances cost 16 model calls, and 8 of 10 tool calls were Eva *talking* (`tts_speak`,
`show_bubble`). Every capability was listed in up to six places (`brain.TOOLS`,
`main.build_tool_impl`, `capabilities._add`, `deadline.TOOL_DEADLINES`, `brain.LEARNABLE`/
`WEB_CONTENT_TOOLS`, `record_gated_step`). Speech blocked the single worker for the whole
playback, so nothing could happen while she spoke.

**What was rebuilt** (`core/`, new `main.py`; `brain.py` deleted):
1. **One registry.** `core/toolset/*` declares each tool once. Parity with the old manifest was
   checked field by field: identical except the three speech tools (removed) and deadlines added
   to seven tools that also wait on other processes (`get_window_rect`, `restore_foreground`,
   `move_mouse`, `recover_input`, `screenshot`, and the two vision tools at 90 s).
2. **Speech is the reply.** The model's text is spoken and bubbled by the framework. Removed with
   it: `spoke_this_turn`, comms-only-round nudges (whose "[user]" wording the model mistook for
   the person), futile-bubble detection. A line said while working is not repeated as the final
   reply (exact or near match).
3. **Two lanes.** Front lane answers stop / hello / "what are you doing" instantly without the
   model; task lane runs one task at a time. Stop is honoured between EACH tool call (skipped
   calls get a result so history stays valid), not only between model rounds.
4. **Speaker thread.** The worker never waits for audio. Long text is split at sentences; part
   n+1 is synthesised while part n plays. Stop drops the queue and kills playback.
5. **Undeclared arguments are dropped** before a tool runs (the model can no longer pass
   internal options such as `focus_window(allow_own=True)`).
6. **Framework notes are tagged `[system]`**, and the prompt says they are not the person.
7. **Plain-English failures** (`llm.explain_error`): 401 → set the key; 403 → key lacks access to
   the model (the 12:43 failure); 404 model; 429; network; 5xx. Full detail stays in eva.log.
8. **Quiet by default**: `PROACTIVE_COMMENTARY_ENABLED = False`, new `PROACTIVE_SUGGESTIONS = False`.
9. **Gestures never refused**: unknown names ("moonwalk") map to the nearest real gesture and the
   result says so honestly. `play_animation` defaults to `loop=False`.

**Kept unchanged** (proven): permissions/finance guard, audit, memory, UI ladder, UIA isolation,
deadlines, app resolver, browser agent, scheduler, recorder, embodiment performer, avatar page,
voice playback helper, STT, secret store, doctor. The model client's retry/leak/envelope code and
the result helpers were moved verbatim into `core/llm.py` and `core/results.py`.

**Verified here:** 102 checks, stable over 8 runs, including whole scripted turns through the real
agent/runtime/speaker. **Not verifiable here:** anything on Windows -- Qt wiring in `main.py`
(cross-checked against the widget APIs by script), real audio pipelining gaps between sentences
(each part keeps the 0.45 s tail drain; raise `CHUNK_MIN_CHARS` in `core/speaker.py` if the
pauses feel long), real model behaviour without speech tools.

**First thing to ask for after this build:** `eva.log` from a short session (hello, open an app,
a question with a longer answer, "stop" mid-answer) and `eva-doctor-report.txt`.


---

## 19. Functionality drawn from eight reference repositories (2026-10-10)

The person asked for useful functionality from: CopilotKit/OpenDots, Anil-matcha/open-dots,
NousResearch/hermes-agent, obra/superpowers, msitarzewski/agency-agents, Fosowl/agenticSeek,
Panniantong/Agent-Reach, anthropics/skills. All eight were cloned and read (code, not READMEs).

| Built | Source idea | Where |
|---|---|---|
| Skills in the open Agent Skills format; catalogue in the prompt, body on demand (`load_skill`, `read_skill_file`); single-file personas accepted | anthropics/skills, OpenDots learned skills, agency-agents | `core/skills.py`, `skills/` (6 shipped) |
| Learning loop: background review after >= 4-step tasks or a correction; may save/improve a skill | Hermes `background_review.py`, `skill_manager_tool.py` | `core/learning.py` |
| MCP services as tools; effect from annotations (readOnly -> free, destructiveHint false -> free, otherwise asks); tokens in secret store; Exa free search by default | OpenDots `connections.ts`; Agent-Reach (Exa MCP) | `core/connections.py`, `~/.eva/connections.json` |
| Page reading (Jina Reader -> direct), challenge-page detection, YouTube transcripts, RSS | Agent-Reach channels | `core/web_reach.py` |
| Repeating schedules; next run from now (no backlog); pause after 3 failures or at once on a key/login problem; `resume_scheduled` | open-dots `schedule_service.py`, Hermes cron | `scheduler.py` |
| Undo for write_file / move_file (snapshot first, kept only on success; undo is itself undoable) | Hermes checkpoint manager | `core/checkpoints.py`, `checkpoint=True` in the registry |
| Echo filter by words + talk-over (barge-in) | agenticSeek `echo_filter.py` (idea only: GPL) | `core/speaker.py`, `core/runtime.py`, `config.BARGE_IN` |
| Evidence before claims: a "done" reply after a failed/unverified/declined last action is sent back once | superpowers verification-before-completion | `core/agent.py` `unsupported_claim` |
| Pause switch (tray / "Eva, resume") | OpenDots `settings.paused` | `core/runtime.py` |

Not taken, with reasons: messaging gateways (Slack/Telegram/Discord), per-agent Docker computers,
multi-user; parallel subagents (one mouse and keyboard); OpenAI realtime voice (needs OpenAI);
classifier routers (need a GPU); LLM-judged approvals (the person decides); Anthropic's
docx/pdf/pptx/xlsx skills (proprietary licence); any agenticSeek code (GPL-3.0).

Safety properties, each with a check that was mutation-tested (the check fails when the guard is
removed): skills saved from a web-tainted turn are refused; unannotated MCP tools ask first; a
success claim contradicted by results is challenged; repeated schedule failures pause.
Skills never grant permission: `permissions.py` does not read them; `save_skill` refuses wording
about bypassing approval and anything credential-like.

New requirements: `mcp>=1.9,<2` (the 2.x client API differs), `feedparser`,
`youtube-transcript-api>=1.0`. Each feature degrades to a clear message if its package is missing.

Verified here: 112 checks, stable over 6 runs, including a real MCP server round trip and a
scripted learning review. Not verifiable here: Exa and Jina from the person's network (the sandbox
proxy blocks them; `doctor.bat` checks page reading), YouTube from his network, barge-in with
real audio (tune with `BARGE_IN` and `ECHO_WINDOW_SECONDS` in `core/speaker.py`).

---

## 20. The skill library (2026-10-10)

The person asked for all skills found in the reference repositories. `skills/library/` now holds
479, built by `tools/import_skills.py` (re-run against fresh clones to update):

| Source | Imported | Licence |
|---|---|---|
| NousResearch/hermes-agent `skills/` + `optional-skills/` | 168 | MIT (a few Apache-2.0) |
| obra/superpowers | 15 | MIT |
| msitarzewski/agency-agents (single-file personas) | 282 | MIT |
| anthropics/skills | 14 | Apache-2.0 |

Excluded (all recorded in `skills/library/NOTICE.md`): Anthropic docx/pdf/pptx/xlsx (proprietary)
and doc-coauthoring (no licence) -- Hermes' own MIT docx/xlsx/powerpoint/pdf skills cover Office
work instead; 40 skills declaring no Windows support; `godmode` (a jailbreak kit for bypassing AI
safety filters) and `sherlock` (tracks a person's accounts across 400+ sites). Content was scanned
for jailbreak/OSINT/CAPTCHA-solving material; `osint-investigation` (public-records due diligence),
`unbroker` (consent-based removal of your own data) and `1password` (your own vault; results are
redacted) were reviewed and kept.

How it scales (`core/skills.py`): Eva's own, learned and installed skills are listed in the prompt;
the library is summarised by category (~1.8k chars total) and searched with `find_skill`
(stemming, IDF weighting, synonyms such as Word->docx, ~20 ms). The library is read once from
INDEX.json; per-turn refresh scans only the small folders. Name clashes: Eva's own wins, then
anthropic > superpowers > hermes > agency-agents; the loser keeps a source-prefixed name
(`hermes-systematic-debugging`). `load_skill` on a library skill adds an `adapt` note mapping
other agents' tools (terminal, file edit, subagents...) to Eva's, and a `persona` note for
agency-agents files ("use its expertise, stay Eva"). Library skills cannot be overwritten by
save_skill. Check: "the skill library is complete, licensed, excludes what it must, and is
searchable" (113 checks in total).

Honest limit: many library skills were written for coding agents with a shell; Eva can follow
their method, but steps that need programs she does not have will be reported, not faked.

---

## 21. Activity-driven animation (2026-10-10) -- SUPERSEDED by section 22

Kept for history. The controller below was written without seeing the rig; rendering it showed
the poses were nearly identical (arms hanging), the per-frame `sync` let additive head layers
accumulate, the walk's reset wiped the torso lean every frame, and several clip names were wrong.

The person's complaint: animation glitched, was limited, and played unrelated clips instead of
reflecting the task. Cause: the idle block hard-SET arm/spine/head rotations every frame, and
one-shot gestures set them too, so rest <-> gesture <-> walk <-> work snapped.

Fix -- an activity pose controller in `web/avatar_3d.html` (`window.setActivity`, `ACTIVITIES`,
`poseController`):
- One owner of the idle upper body. Each activity (idle, listening, thinking, reading, searching,
  writing, browsing, organizing, presenting, plus hover/nap via currentAnim) is a set of bone
  deltas from the relaxed rest pose plus gentle ambient motion.
- Every channel EASES toward its target (`POSE_EASE`, ~0.25 s), and at the end of each frame the
  controller re-reads the bones (`sync`) so it always eases FROM what actually rendered -- after a
  walk or a one-shot gesture there is no snap. This is the structural fix for the "not smooth".
- Gated by `window.EVA_ACTIVITY`; off falls back to the plain relaxed rest pose (never a T-pose).
- The VRMA mixer clips, walk cycle, blink/mouth life layer, gaze and the p_* one-shot gestures are
  unchanged and compose on top.

Driven by the task (`core/activity.py`): `for_tool(name)` maps a running tool's verb/surface to a
pose (read->reading, type->writing, move_file->organizing, browser->browsing...); `for_skill` maps
a loaded skill to a task-level pose held between its actions. `embodiment.Performer` sets the
per-tool pose around each call and restores the base; `core/agent.py` sets the base ("thinking"
during a turn, the skill's pose when one loads, "idle" at the end); `AvatarRenderer.set_activity`
-> `AvatarWidget.set_activity` -> `window.setActivity` (change-only, like set_speaking).

Verified: `tools/activity_test.js` (easing not jumping, bounds, distinct poses, no snap after an
external pose, idle keeps breathing, off writes nothing) -- it caught a real bug (organizing/
presenting written as absolute instead of deltas, doubling the arm past the limit). life_test and
pose_test still pass. Python: "the animation pose follows the task" and "the agent drives the
activity pose" (115 checks total). NOT verifiable here: how any of it actually looks on the rig --
the pose lab (`pose_lab.bat`, button per activity, POSE_EASE slider) is how to see and tune it.
New tunable: POSE_EASE (config.POSE_TUNING). Honest limit: the bone-space sign estimates
(ARM_DOWN_Z etc.) are unchanged and still need the lab to confirm against the real model.


---

## 22. Field log 2026-10-10 (01d60aec) and the motion rewrite

The person's report with that log: she glitches over other windows and drops behind them while
working; the animations bear no relation to the task; she narrates huge paragraphs.

### Seeing the avatar (new capability)
The rig is now RENDERED here: Playwright + Chromium with SwiftShader WebGL loads
`web/avatar_3d.html?lab=1` against local copies of three / three-vrm, and screenshots any pose.
Lab-only hooks (`?lab=1` only, never inside Eva): `window.__eva.seek(clip, t)` holds a clip at an
exact second, `window.__eva.hold(gesture, p)` holds a procedural gesture at phase p,
`window.__labYaw` turns her for a side view, `EVA_TUNE.POSE_EASE = 1000` snaps poses.
Every claim below about how something looks was checked on a rendered frame.

### What rendering found
| Finding | Fix |
|---|---|
| Arm axes were guesses; poses moved arms only sideways (Z), so every activity looked like "arms hanging" | Measured: upperArm.x + = arm forward; lowerArm.y - / + = left / right elbow bends forward; upperArm.y turns forearms in; spine/chest/head x + = lean BACK (the channels flip it, so + means forward/down) |
| "Look down at the page" actually tilted her head back | sign flipped in one place (`writePose`) |
| Clip names were wrong: VRMA_07 "squat" is an arm stretch; VRMA_02 "greeting" starts crouched on the floor and jumps; "fullbody" is a twirl; "shoot" a finger-gun | renamed by content: twirl, hello, peace, finger_gun, spin_pose, model_pose, stretch. "squat" is now a procedural squat (feet stay planted). "hello" starts at 2.4 s, past the crouch |
| Desktop work played those clips: a click played the finger-gun, minimise the crouch-and-jump, success the 7 s jump-and-wave, every walk ended with it | work has its own motions (`p_reach_l/r` with the hand on the target's side and the head turned to it, `p_tidy` press-down, `p_arrange` frame-and-slide, `p_nod`, `p_shrug`, `p_think`, `p_beckon`, `p_wave`); playful clips only when asked for or idle; a work gesture ends a playful clip, blended |
| A blanket 3.5 s revert cut 7-12 s clips mid-motion and, firing during a walk, froze the legs while she slid | clips and gestures end themselves; only held states (nap, hover) use a timer |
| Clip start/end snapped (mixer fade blends toward bind pose; the end left bones in the clip's last frame) | crossfade layer: the pose is captured and every bone is slerped from it (0.35 s in, 0.5 s out); bones the clip moved return to rest |
| Bones were written AFTER `vrm.update()`, so what rendered was a frame late | order is now clip/controller -> crossfade -> `vrm.update()` |
| Exponential easing moved fastest in the first frame (a jerk on every activity change) | a critically damped spring per channel |
| The walk: 10 fps window moves (~45 px jumps), sideways arm swing, no knees, no turn | 33 ms ticks, forward arm counter-swing, knee bend, eased walk weight, she turns 3/4 toward where she is going |

Activities now (each distinct, hands where the work is): idle, listening (hands together, head
tilted), thinking (hand to chin), reading (page held in both hands, eyes sweeping lines),
writing (pad on the left forearm, right hand writing), browsing (hands low and forward, typing),
searching (hand shading the eyes, other hand on the hip), organizing (both hands sweeping),
presenting (right hand open toward the result), nap, hover.

### Other fixes from the log
| Finding | Fix |
|---|---|
| A 1,500-word passage and lists of shops were read aloud, despite "anything over 100 characters, write it in a Notepad" | `core/longform.py`: the framework (not the model) writes any reply over `LONG_REPLY_CHARS` (100) to `Documents\Eva\<date time> - <words>.txt`, opens Notepad, and says one line; trailing filler is removed; long working lines are not spoken |
| "stop stop, whatever you are doing" was queued as a request; a tail word could make "cancel my meeting" a stop | `STOP_ANYWHERE` catches stops inside a phrase; the plain stop form accepts only filler after it |
| A new request sharing "in West Bengal" with her last reply was dropped as echo | echo needs 60 % of the heard words covered by runs of her own words, within 3 s |
| "Open settings" found "WSL Settings"; Windows Update could not be opened | `app_resolver.SYSTEM_TARGETS` (ms-settings: pages, Store, Explorer...) and allowed URI schemes |
| `focus_window` failed with "Error code from Windows: 0" | `_force_foreground`: restore, SetForegroundWindow, AttachThreadInput, Alt-tap, each verified |
| She dropped behind windows she opened | `avatar_ui.keep_on_top()` re-asserts HWND_TOPMOST with SWP_NOACTIVATE (never takes focus) whenever the foreground window changes or her topmost bit is lost (main.py, every 400 ms) |

### Verification
`run_checks.py`: 123 checks pass, including 8 new ones pinning each fix above and
`tools/motion_test.js` (distinct activities, nothing moves more than 0.2 rad per frame at 30 fps
through any activity change, gesture or walk, every gesture starts and ends on the live pose,
measured reach sides, honest refusals, and every gesture name Python offers maps to a motion
that exists). `tools/pose_test.js` and `tools/activity_test.js` were retired with the code they
tested. Not verifiable here: z-order and focus behaviour on real Windows, real speed of the
30 fps walk on his GPU, how the clips feel at full frame rate (renders here are stills).

---

## 23. Field log 2026-10-11 (7069a826): flicker and a crash

The person reported the animation improved, but flicker continues and Eva crashed.

**What the log shows.** At 01:40:43 `close_window` started its walk to the Notepad window; the
walk never arrived (no tool result), the page's heartbeat was missing at 01:40:54, the recovery
reload ran, and the log ends with no traceback. Two earlier `close_window` walks in the same run
finished in 1-3 s. No WebGL context loss was logged. The page's heartbeats and frames reach
Python through the GUI thread, so the 8 s silence fits a GUI-thread stall as well as a dead
renderer -- the log cannot tell them apart, and the native crash itself is in `~/.eva/crash.log`
(faulthandler), not in eva.log. **Ask for crash.log.**

| Change | Why |
|---|---|
| `gui_watchdog.py`: the GUI thread beats every 250 ms; a background thread logs every thread's stack (GUI thread first) once when it has not run for 3 s, and logs the stall's length when it resumes | next time, the blocking line is in eva.log |
| the avatar watchdog no longer reloads the page when the silence was Eva's own stall (its own 3 s timer late by 6+ s, or a stall in the last 10 s) | the 01:40 reload ran right after such a stall and the process died |
| z-order: re-order only when a window that is not hers overlaps her from above, or her topmost flag is lost; one atomic `DeferWindowPos` batch (menu/chat, bubble, status, hit box, stage); the full-screen highlight overlay is no longer raised with her; after 3 tries against the same self-raising window in 10 s she leaves it alone | the previous build re-asserted on every foreground change and raised the full-screen translucent overlay above the WebGL stage -- likely flicker sources introduced by section 22 |

Checks: 124 pass ("GUI stalls are found and not mistaken for a dead page", z-order rules).
Not verifiable here: whether flicker is gone on his GPU; if it remains, `config.RENDER_MODE`
(section 13) is the next lever, and the stall report or crash.log decides the next step for the crash.

---

## 24. The flicker, measured (screen recording 2026-10-11 11:00)

**What the recording shows** (19.8 s, 30 fps, analysed frame by frame): the character is missing
from 108 of 595 frames (18 %), in bursts of 1-3 frames (one burst of 11), at idle, over the
desktop, a dark window and the console alike. The "Listening" label -- a separate, plainly painted
Qt window -- is present in every frame. So the defect is the 3-D stage presenting EMPTY frames;
window order, walking and other windows are not involved.

**Changes**
| Change | Why |
|---|---|
| `preserveDrawingBuffer` on the WebGL canvas (page option `pdb=1`, `config.AVATAR_PRESERVE_BUFFER`) | the canvas keeps its last image, so a compositor pass that falls between two draws shows her instead of an empty buffer -- the most likely cause of exactly this pattern |
| frame pacing: `1000/fps - 2 ms` (page option `fps`, `config.AVATAR_FPS`) | the old `< 33.3 ms` test skipped a whole vsync on normal jitter, giving uneven 20-30 fps |
| `config.QT_GRAPHICS_API` -> `QSG_RHI_BACKEND` ("opengl", "d3d12", "software") | how Qt composes the translucent WebEngine window is the other candidate, and depends on the GPU driver |
| `flicker_probe.py`: 25 s after start, while idle, photographs her body region 90 times (mss, what the compositor shows) and logs `Flicker probe: N of 90 frames (P%) showed the character missing (...)` | an objective number in every eva.log. Validated on the recording itself: it found all 108 missing frames, no false ones |
| `flicker_test.bat` / `tools/flicker_bench.py`: opens the real stage once per setting (8 settings, a fresh process each), measures missing frames against the desktop photographed before she appears, writes `eva-flicker-report.txt`, and saves the winner to `~/.eva/render.json` if clearly better; `config.py` reads that file (known keys, type-checked) | the cure is chosen on his machine by measurement, not guessed. Tested here end to end under a virtual display: the real page measured 0/90 missing, a page that draws nothing 90/90 |

Checks: 125 pass. Not verifiable here: which setting wins on his GPU -- that is what the bench is for.

### Result on his PC (eva-flicker-report.txt, 2026-10-11 11:25-11:27)
| Setting | Missing frames |
|---|---|
| previous build | 18/90 (20 %) -- matches the recording's 18 % |
| keep last image (new default) | 3/90 |
| keep last image, 60 fps | 5/90 |
| Qt on OpenGL | 5/90 |
| Qt on Direct3D 12 | did not start (no WebGL context) |
| Chromium ANGLE D3D11 | 3/90 |
| **no GPU compositing** | **0/90** (bench winner; saved to ~/.eva/render.json) |
| software | 0/90 |

Inside Eva with the new default, before the bench: `Flicker probe: 2 of 90 frames (2%)`.
Conclusion: the empty frames come from Chromium's GPU compositing of the translucent stage on this
GPU; drawing stays on the GPU in the winning setting. Follow-ups made: bench children now log to
`eva-flicker-bench.log`, not eva.log; the stall detector stops at shutdown (the goodbye waits for
speech on the GUI thread by design and was reported as a stall at 11:25:10).

---

## 25. 2026-10-11 11:40: an old request coming back, half size, the "self review"

**Flicker** confirmed solved by the person; eva.log 11:33: `Flicker probe: 0 of 90 frames (0%)` with
`no_gpu_comp` from render.json.

**Yesterday's Kolkata tour search kept coming back (Chrome + Notepad).** The run in which it
happened was not in the logs sent, so the cause is not proven. Yesterday's log shows that request
ended on a Google "prove you are human" page, a hand-off, then "stop" and a shutdown that failed
to close the browser cleanly ("cannot schedule new futures after shutdown"). Every path by which
it could return was closed:
| Path | Fix |
|---|---|
| Eva's browser profile marked "Crashed" restores old tabs on the next start, and pages[0] (the old Kolkata search) was then used as the current page | `_mark_profile_clean` before launch (exit_type Normal; "continue where you left off" -> new tab), `--hide-crash-restore-bubble`, `_fresh_page`: extra tabs closed, the kept tab goes to about:blank |
| a cleared verification page sent "[system] Carry on with the task you were doing" -- naming no task | the hand-off records the request that hit it; on clearing, she resumes THAT request by name, only within 10 min and only if the person has said nothing (and not stop) since (`runtime.person_seq`) |
| a stop left the hand-off waiting | `browser_agent.request_abort` clears it |
| the remembered preference "long_response_to_notepad" could make the model open Notepad itself on top of the framework's copy | prompt: never open Notepad and type a reply yourself |

**Half size.** `config.AVATAR_SCALE = 0.5` (hit box 100 x 190; the stage is a fixed multiple). Dragging
her corner still resizes her freely.

**The code review she produced was not of Eva.** Asked to review "your source code", she searched the
disk and reviewed `Documents\VSCode\desktop-avatar-agent` (avatar/actions.py, agent_core.py,
movement.py, overlay.py) -- a different project. Its findings do not apply to Eva. Fix: the system
prompt now states her install folder ("Your code always means that folder ... never search the disk
for yourself").

Checks: 128 pass.

---

## 26. The ring menu (2026-10-11)

The person: the menu (a column of buttons beside her) was hard to read over other windows and
opened off-screen at a desktop edge; he asked for a selection wheel around Eva, as in The Witcher 3
or Call of Duty.

`RadialMenu` (avatar_ui.py) is now a ring of 7 segments around her -- Chat, Mute, Stop, Restart,
Quit, Look, Pause, clockwise from 12 o'clock -- drawn on a dark, shadowed, nearly opaque surface
with a bright rim, so it reads over any window. Hover lights a segment (Quit in red); click
chooses; arrows/Tab move, Enter chooses, 1-7 choose directly, Esc closes; clicking Eva again,
clicking another program, or 12 s idle closes it. Quit needs a second click within 2.5 s. Opens
with a 170 ms scale-and-fade.

Placement (`ring_layout.py`, Qt-free and tested): centred on her when the whole ring fits; at an
edge the segments are spread over the on-screen part of the ring only (an arc above her when she
stands on the taskbar, a half ring at a side), provided each stays >= 24 deg; otherwise (a
corner) the ring slides just far enough to fit whole. Stop and Pause are new on the ring (they
were tray-only). Pause state is read from the runtime each time the ring opens.

Rendered here with PySide6 under a virtual display over a busy background, in the centre, on the
taskbar and at the right edge (eva-ring-menu-preview.png). Check: "the ring menu is always on
screen and around her" (129 checks). Not verifiable here: the Segoe icon glyphs (Windows fonts;
the render used fallback symbols).

**Chat box (2026-10-11 12:07).** White text on a white field: only its background was styled, so
with Windows in dark mode Qt drew the text light. Now dark like the ring (all colours set, plus the
placeholder via the palette), and it opens fully on screen -- to her left when there is no room on
her right. Rendered under a forced dark-mode palette to confirm.

---

## 27. Reliability, helpers, reach-anywhere, everyday tools, a life of her own (2026-10-11)

The person approved the four-step plan and asked that everything she does reads as human: reach
out and press a window's close button; sit on a window or the taskbar when idle; "think of other
things Eva would do like a human would".

**Step 1a -- learned skills wait for approval** (`core/skills.py`, Activity window "Skills" tab, tray
"Skills to review (n)"). What the background review writes goes to `~/.eva/skills/.pending/` and is
never offered to the model until approved; a save in a live turn goes straight into use only if the
person asked for it ("remember how...", "save this as a skill" -- `agent.ASKED_TO_LEARN`). One time,
skills Eva had learned on her own were moved to the queue (`.review-migrated` marker); the person's
own are left alone. Removing keeps a copy in `.removed/`.

**Step 1b -- task scorecard** (`scorecard.bat`, `tools/scorecard.py`): 12 everyday requests through
the real agent and model, scored by the PC's state (folder exists, file content, window opened,
reply length, refusal held), inside `Documents\Eva-scorecard` only (approvals granted only there).
Writes `eva-scorecard.txt` and appends `~/.eva/scorecard-history.csv` to compare builds.

**Step 2 -- helpers** (`core/workers.py`; tools delegate / helpers_status / cancel_helper). Up to
`HELPER_MAX` (3) background workers with an allow-list of read-only tools plus writing into their
own folder `Documents\Eva\helpers\<job>`; no screen, no changes to the person's files, so no
approvals; 14 steps / 5 minutes each; "stop" cancels them; every step audited as "helper #n"; the
report arrives as a tainted `[system] Helper #n ...` note that is never learned from. Body: she
hands the task over (`p_delegate`, palm out) and one small blue light circles her head per working
helper (`window.setHelpers`).

**Step 3 -- reachable from anywhere** (`ubiquity.py`): `HOTKEY_CHAT` (Win+Shift+E) opens chat at the
cursor with the text selected in the app in front (UI Automation TextPattern -- never a simulated
Ctrl+C), shown as a chip; `HOTKEY_REGION` (Win+Shift+W) drags a box (`RegionPicker`), she looks and
points at it and `look_at_screen` now takes left/top/right/bottom; `get_selected_text` gives the
selection plus the clipboard and how long ago it changed. Tray shows both shortcuts.

**Step 4 -- everyday tools** (`everyday_tools.py`, `file_index.py`, `core/toolset/everyday.py`, six
new shipped skills: pc-health-check, focus-session, documents-toolkit, email-and-calendar,
window-layouts, research-with-helpers): find_files (SQLite index of NAMES in the person's folders,
rebuilt every 30 min), merge/split PDF, Word->PDF (needs Word), PDF tables->CSV, disk space, largest
and duplicate files, startup apps, battery -- all reports or NEW files, never overwrite or delete;
draft_email (mailto: draft; the person presses Send) and add_calendar_event (.ics opened in their
calendar; they save it); save/restore/list window layouts; focus_start/focus_end. Reading an inbox
or calendar needs an MCP connection with the person's own account credentials -- their decision.

**Human-like body** (`web/avatar_3d.html` body state, `avatar_ui.py`, `life.py`):
| Situation | What she does |
|---|---|
| close / minimise / maximise a window | flies up beside the real title-bar button (DPI-scaled), hand on the target's side, presses it; the button is ringed (`caption_*` loci, `reach_spot`) |
| anything high on screen | flies there, legs trailing (`fly`, airborne); walks only along the taskbar |
| left alone 90 s+ | every 40-95 s: sits on a window's top edge (never the one in use; rides along if it moves, hops down if it closes, minimises or maximises), sits on the taskbar, strolls, looks around, stretches, yawns (more after 23:00), the odd playful clip |
| away 10 min | dozes off sitting; on return stretches and waves |
| you come back after 5 min | a wave; if she sits on the window you now use, she gets off it |
| cursor comes near | she glances at it |
| picked up (dragged) | hands up, startled, legs dangling and kicking |
| dropped | lands with a little knee bend -- or, dropped on a window's top edge, sits on it |
Sitting: thighs over the edge, shins swinging, hands folded in her lap (rendered and checked).
Tunable: `SIT_DROP` (how far she sinks), `IDLE_LIFE` (off = no idle behaviour).

Checks: 137 pass, including new ones for each item. Rendered here: sitting (front, side, 3/4),
sitting while reading, napping, hovering, reaching up left/right, carried, yawn, big stretch, the
hand-off with three helper lights, the region picker and chat chips. Not verifiable here: the real
title-bar positions on his windows, UI Automation selection in his apps, the hotkeys on his
keyboard layout, Word conversion, and how the idle life feels over a real afternoon.

---

## 28. 2026-10-11 13:13 log, screenshot, size

| Finding | Fix |
|---|---|
| After Restart the OLD avatar hung on screen as a black box beside the new one (screenshot): `os.execv` on Windows starts the new process and leaves the old to die on its own, windows and web engine still up | restart hides every window at once, quits Qt cleanly (no goodbye speech), and exits with code 75; `run.bat` loops on 75 (`EVA_LAUNCHER=run.bat`); started any other way, main() launches the new process only after Qt is gone |
| Native crash at 12:33-run while closing a full-screen video; the CRASH REPORT kept only the last 40 lines, which are the idle threads -- the crashing thread is at the top | the report keeps the first 80 lines (exception + crashing thread) and the last 40. Still unproven; `~/.eva/crash.log` has the full trace |
| (same) | a full-screen window gets no title-bar flight (no caption locus; she looks instead); z-order is not re-asserted while a full-screen app is in front |
| navigate_browser overwrote the person's current page ("you should not have overwritten the browser URL") | opens a NEW tab by default (`new_tab=false` to reuse); verified by the window title changing |
| Five wrong tab clicks by screen position (13:20-13:22) | `switch_browser_tab(window, title_contains / index)`: Ctrl+Tab until the window title (= active tab) matches, or Ctrl+1..9; lists the titles if none matches; prompt says never click tab positions |
| A two-sentence reply to the person went to Notepad (13:16), stacking windows | `longform.is_conversational`: up to two sentences, no list, <= `SPOKEN_MAX_CHARS` (240) is spoken; lists, paragraphs and essays still go to Notepad |
| Too small | `AVATAR_SCALE` 0.5 -> 0.575 (115 x 218 px, +15%) |
Not addressed (outside Eva): NVIDIA's vision endpoint returned 503 "request limit reached (16/16)" and a 90 s stall; retries already cover it.


## 29. Message bubbles, the chat window, long replies, scorecard (2026-10-11)

| Finding | Fix |
|---|---|
| Her text and "thinking" on screen were hard to read: a half-transparent QLabel stylesheet on a translucent window, over whatever was behind | `chat_ui.py`, iMessage style, every surface PAINTED with explicit colours: `FloatingBubble` (opaque light-grey bubble, dark text, soft shadow, tail to her head; three animated typing dots while she thinks), `StatusPill`, and `ChatPanel` (the chat window: the conversation as bubbles, the person's in blue on the right, Eva's in grey on the left, selectable, with the selected-text / marked-area chip). Contrast is at least 4.5:1 for every text colour (checked); the iOS blue is one shade deeper (#0A6CDB) so white text passes |
| Long replies went to Notepad; wanted in a bubble that persists and in the chat window | `LongForm.shape_ex` returns (said, shown): she says one short line (the list's lead-in, or "Here you go."); the bubble shows the start of the text with **Show all** (opens the chat window at it) and **Notepad** (save and open, on request). A long bubble stays up to 60 s and while the pointer is over it. `LONG_REPLY_TARGET = "notepad"` restores the old behaviour |
| The chat window should keep the conversation | `chat_store.ChatHistory` (`~/.eva/chat-history.jsonl`, newest 500): Runtime's new `on_message` hook records every person message (without the attached selection/region block) and every Eva answer in full; the panel loads the last 80 at start |
| `scorecard.bat` crashed: `KeyError: cannot bind unknown tool 'remember'` | `build_agent` imports `core.toolset` first, and runs inside the task's `try` so a broken build fails one task instead of the run. A check builds the agent in a FRESH interpreter, as the .bat does. The `longform` task now expects one short line said, 600+ characters shown, and no new Notepad window |

## 30. Coloured bubbles; 2026-10-11 13:45 log (Windows Update, Registry Editor)

| Finding | Fix |
|---|---|
| Request: coloured bubbles, "maybe green or blue" | `config.BUBBLE_COLOURS`: `"blue"` (default; Eva blue #0A6CDB, you green #1E7E34, white text, typing dots white), `"green"` (swapped), `"grey"` (iMessage). Fill is a top-to-bottom gradient that only darkens, so contrast stays at 5:1 or better; every scheme's text, links and dots are checked at 4.5:1 or more |
| Registry Editor (opened via Win+R + UAC) runs as administrator; for five minutes nothing reached it: ui_tree saw only its title bar, clicks changed nothing, close/Alt+F4 left it open, and she said "Expanded all registry folders" | `core/elevation.py`: before a click, keystroke, UI read or close, the dispatcher checks whether the target window (by handle, by the point clicked, or the foreground window) belongs to an elevated process while Eva is not; if so the tool is not run and the result explains, in words she can repeat, that Windows does not allow it and the person must do that step. `launch_app` on WinError 740 says the same before anything is opened |
| "Expanded all registry folders." after a keypress that changed nothing went unchallenged | the success-claim check knows more verbs (expanded, clicked, pressed, selected, switched, launched, enabled, updated, downloaded, ...); "nothing changed" counts as an honest hedge |
| "The update check finished ... up to date" while read_window_text returned only "Settings" (the title bar) four times; a pending update existed | `read_window_text` falls back to the window's visible labels (text, links, buttons, list items) when it has no document text; the result says `source: labels` |
| `close-window-by-click` saved as a skill from clicks that never closed the window | a turn whose last action failed or could not be verified is never reviewed for a skill (`ended_unverified`) |
| locate_on_screen: "could not read the vision model's reply: [{"bbox_2d": [...], "label": ...}]" | `vision.parse_box_reply` reads that grounding format (and fenced JSON); the 0-1000 conversion still applies |
| Six identical `keep_on_top failed` tracebacks in 3 s: pygetwindow raised 1400 "Invalid window handle" for the closing Run dialog | `get_foreground_app` reads the foreground window straight from Win32 and never raises; the z-order timer logs a repeated failure once, then only counts it |
| Restart at 13:55:23 | worked: old Eva closed, new one up in 2 s, no black box |

## 31. 2026-10-11 14:07 log: the bubble, sitting on a window, jumping (scorecard 12/12)

| Finding | Fix |
|---|---|
| The bubble stayed where it was when she moved | `FloatingBubble.follow(anchor)`, called from `AvatarWidget.moveEvent` (every walk, glide, jump, perch ride and drag); the tail is re-aimed at her head |
| "Sit on top of the command window": she had no way to sit -- walk_to_screen only stands, so she hovered at its top edge, said "I'm sitting on top" anyway, and idle life flew her back to the taskbar 8 s later | new tools `sit_on_window(window_handle, spot)` (checks the window exists, is not minimised or maximised and is wide enough, walks there, sits on the title bar and rides along; the result is `verified` from her actual body state), `sit_down` (on the window edge she stands on, or where she is) and `stand_up(come_down)`; prompt and tool text say never to use walk_to_screen for sitting |
| (same) | "placed" state: walk_to_screen, sit_on_window, sit_down and a manual drop mark where the person put her. For 10 minutes idle life leaves her there (only looks around, stretches, yawns): no descend, no strolling, no other perch, no getting off the window you use, no mid-air nap |
| "Can you jump": she danced, squatted and cheered, then offered to "move up and down" | `jump` is a real gesture: crouch, the window rises a third of her height, lands (`AvatarWidget.jump(times)`); "hop", "bounce", "up and down" map to it |
| look_at_screen answered "<box>[[888, 956, 995, 999]]</box>" to "what is on the right side" | a box-only answer is asked again in words; `<box>` tags are also read as a box |
| A needless avatar "recovery" 6 s after a slow start (14:07:54): the first heartbeat only comes once the model has loaded | until the first heartbeat after each page load the watchdog allows 45 s, then 8 s as before |
| Scorecard 12/12, including the new longform task (one line said, 1,671 chars shown, no Notepad) | -- |

## 32. The crash over a playing video (eva.log 2026-10-11 15:34); more animations

**What the log shows.** "Click on the video called Google's new carbon": locate_on_screen, then
click_coords(697, 906) -- she walks over to the thumbnail and clicks; YouTube starts the video
under her. Then nothing for 13 s, a `GUI thread ran again after a 3.0 s stall`, and the log ends:
no Python error, no shutdown. The same shape as the 13:13 crash closing a full-screen video. A GUI
thread blocked ~3 s and dead the moment it resumed is how a GPU driver reset (Windows TDR) looks
from a process holding a Direct3D device: Qt composes Eva's translucent window with Direct3D 11 by
default, and that device is lost with the driver. Not proven -- the faulting module is in Windows'
error log, which is now read automatically (below).

| Change | Why |
|---|---|
| `config.QT_COMPOSITION = "software"` (new default; `render_setup.apply_qt_graphics`): Qt Quick's software scene graph (`QT_QUICK_BACKEND=software`) composes her windows | no Direct3D device in Eva's process, so a driver reset cannot take her down; Chromium still draws her with WebGL on the GPU and recovers on its own (context-loss reload). Her window is 115 x 218 px, so CPU composition costs nothing. Verified here: the real stage under a virtual display drew 90/90 frames, 0 missing, the same as GPU composition. `"gpu"` restores the old behaviour |
| `QT_GRAPHICS_API = "software"` used to be passed as an RHI backend (`QSG_RHI_BACKEND`), which silently did nothing | now mapped to `QT_QUICK_BACKEND` |
| `crash_probe.py`: on the start after an unclean exit, reads the last Windows "Application Error" events (wevtutil, event 1000) for Eva's python and logs `faulting module X (exception 0x...)`, flagging GPU driver modules | the next eva.log after a crash names the DLL, not just the Python stacks |
| flicker bench: a `QT_COMPOSITION` column and the variant "no GPU compositing, crash-safe (new default)"; on a tie (within 0.5 %) crash-safe wins and is what gets saved | the cure stays measured on his screen |
| `watch_video`: when she watches a video with you she moves BESIDE it on the taskbar, never over it | does not depend on the theory above |

**More animations** (all procedural, verified by the motion checks and rendered; sheet in eva-new-gestures.png):

| Gesture | When |
|---|---|
| peer (leans in, hand shading the eyes) | locate_on_screen, look_at_screen, screenshot |
| check_watch | get_current_time, reminders, calendar, list_scheduled; also while waiting |
| impatient (hands on hips, weight shifting) | any action still running after 7 s (alternates with check_watch) |
| salute / tap_head (fingertip to the temple) | schedule_task / remember |
| carry (a box in both arms) | move_file, merge/split PDFs |
| paper_plane | draft_email, notify, agent_message |
| checklist | system_info, disk_space, battery, startup apps, processes, largest/duplicate files |
| shoo / rewind | end_process / undo_last_change |
| shh (finger to the lips) | focus_start, muting the mic, pausing |
| nod, fist_pump, tada (in turn) | a verified change |
| wipe_brow ("phew") | a verified change after a job of 15 s or more |
| shrug, facepalm (in turn) | a failed action; facepalm also when a request fails with an error |
| heart (hands to the heart) | "thanks", "thank you", "you're the best" |
| wave | "hello" (and resuming) |
| scratch_head | when she has to ask what you meant |
| clap, hum, sneeze, check_watch | asked for, or idle life (hum often, a sneeze rarely) |
| watching (activity) | a video is in front (YouTube watch page, Netflix, VLC...) and you have not touched anything for 8 s: she moves beside it, sits, watches, nods along; no napping through it |

All are also available by name through play_animation, with aliases ("applause", "oops", "phew",
"achoo", "go away", "undo"...).


## 33. Correction, 2026-10-11 16:59: invisible with software composition; the real crash cause

**What happened.** With section 32's default (`QT_COMPOSITION = "software"`) Eva was invisible:
`THREE.WebGLRenderer: Error creating WebGL context ... GL_VENDOR = Disabled`. On Windows, Qt
Quick's software scene graph makes Qt WebEngine switch Chromium's GPU off altogether, so WebGL
cannot start in any GPU render mode. My verification here used the SwiftShader render mode (the
only one that works on the virtual display), where WebGL never needs the GPU -- so it passed and
the real configuration was never tested. That was my error.

**What the crash actually was.** The new crash probe read Windows' error log on that start:
`faulting module Qt6Core.dll (exception 0xc0000005)` -- not a GPU driver. faulthandler's trace
(now in the CRASH REPORT) shows the crashing thread was the TASK thread, at
`embodiment._anticipate -> _safe(self.r.reach, ...)`, with no frame for `reach` itself: the access
violation happened in native code while calling into it. That is what a cyclic garbage collection
looks like when it runs on a worker thread (Python runs it on whichever thread allocates) and
destroys a Qt object there -- Qt objects may only be destroyed on their own (GUI) thread.

| Change | Why |
|---|---|
| `QT_COMPOSITION = "gpu"` again; `"software"` (and `QT_GRAPHICS_API = "software"`) is now honoured only with `RENDER_MODE = "software"`, otherwise ignored with a log line | never again a setting that switches WebGL off |
| `gc.disable()` at start; a GUI-thread timer runs `gc.collect` (gen 0 every second, gen 1 every 10 s, gen 2 every minute); `gc.freeze()` 20 s after start | cyclic garbage -- including Qt objects -- is only ever destroyed on the GUI thread. Reference counting frees everything else immediately, as before |
| the flicker probe's thread and `sit_on_window` asked the avatar widget for its geometry directly | both now ask the GUI thread (`run_sync`) |
| WebGL cannot be created -> `~/.eva/render-fallback.json` and ONE restart with software drawing (SwiftShader), logged; the file is used once, the next start tries the GPU again; if software drawing also fails, a tray message says to run doctor.bat | a renderer failure can never again leave her silently invisible |
| flicker bench: the "crash-safe" variant removed (it would have measured an invisible avatar on his PC) | -- |

Unproven until it survives the video test on his PC; if Qt6Core crashes again, the next start's
eva.log will name it again, and the trace will show which thread.

## 34. 17:10: crashed again clicking a Picture-in-Picture video

Same shape as 13:13 and 15:34: `click_coords` on a playing video (the PiP window, a topmost
window) -> she walks over to it -> 12 s later `GUI thread ran again after a 3.0 s stall` -> dead.
The GUI-thread garbage collection (section 33) did not prevent it, so that was not the cause (it
stays: it is correct). The stall detector's own stack report never reached the log: the process
died within milliseconds of the GUI thread resuming.

| Change | Why |
|---|---|
| `media.py` (`VIDEO_TITLE` incl. "Picture in Picture", `video_at(x, y)`); the performer never walks or flies onto a video window: she looks at it and points from where she is (`_on_a_video`) | the one trigger all three crashes share is her body moving onto a playing video |
| `zorder_tick` does not re-raise her above a video / PiP window that covers her | two always-on-top windows re-raising each other over a playing video |
| `logger.dump_all_threads()` -- faulthandler, C level -- called by the stall detector the instant the GUI thread freezes, BEFORE the slow logged report; the crash report keeps 150 head + 70 tail lines | if anything still dies, crash.log shows what the GUI thread was doing during the freeze |

Still open: the native cause (driver vs Qt). Next crash -> the next start's eva.log + ~/.eva/crash.log.

## 35. Root-cause work on the video crash (crash.log, 2026-10-11 17:15)

**Evidence.** crash.log has four access violations with the SAME faulting Python state: the task
thread, `embodiment._anticipate -> _safe(self.r.reach, ...)` at `return fn(*args)`, no frame for
`reach` (01:31, 12:33, 15:34, 17:10; the last with GC confined to the GUI thread, so GC is ruled
out). Tested here with Python 3.11: faulthandler DOES show the callee's frames for a fault inside a
nested call, so the fault is not inside `reach`/`fire`/`emit`; it is in the C code `_safe` runs
around the call -- in 3.11, a function called through `fn(*args)` has its frame CLEARED (locals
released) after the outer frame is already current again, which matches exactly. Windows' log
names Qt6Core.dll for 15:34. The exact Qt function is still unknown.

**Diagnostics added (no behaviour change)**
| Change | Why |
|---|---|
| crash_probe reads `Fault offset` and `Faulting module path` from Windows' event 1000 and resolves the offset to the nearest EXPORTED function of that DLL (`pe_exports`, a small PE export-table reader, + `UnDecorateSymbolName`) -- tested on the real Qt6Core.dll 6.12 (8,570 exports, 0.03 s) | names the Qt function that faulted |
| runs on EVERY start and reports each Windows-recorded crash once (`~/.eva/crash_probe.json`) | the four past crashes are named at the next start |
| `enable_crash_dumps.bat` (opt-in, asks for admin): WER LocalDumps for python.exe -> `~/.eva/dumps` (minidumps, 5 kept); `off` undoes it | the native stack of the faulting thread, for the case the export name is not enough |

The section 34 measures (no walking onto videos, no z-order fight with video windows) are
mitigations and come out once the cause is fixed.

## 36. ROOT CAUSE FOUND AND FIXED: the "video" crash (2026-10-11 17:24 log)

**Evidence.** The crash probe resolved Windows' fault address for 15:34 and 17:10 -- identical:
`Qt6Core.dll + 0xea959 = QMetaObject::Connection::~Connection + 0x19` (Qt 6.11.2). A Qt
signal-connection destroyed on the wrong thread.

**Cause.** `AvatarRenderer.reach` (task thread) created `done = threading.Event()` and passed
`on_ready=done.set` -- a BOUND METHOD -- to `perform_gesture_at`, which did
`QTimer.singleShot(450, on_ready)`. PySide keeps a bound method's object by weak reference and owns
a Qt connection for it; when `reach` returned, the task thread released the last reference to the
Event and PySide tore that connection down ON THE TASK THREAD. That is exactly the Python trace of
all four crashes (01:31, 12:33, 15:34, 17:10): the task thread inside `_safe(self.r.reach, ...)` with
no deeper frame (the callee's frame is being cleared). Videos were incidental: what they changed
was the timing (a 3 s GUI stall over hardware video makes the GUI thread and the task thread
release their references at the same moment).

**Proof.** `tools/qt_callback_test.py` reproduces it with PySide6 6.12: `bound` crashes 3/3 runs on
the worker thread (same signature), `wrapped` survives 3,000 reaches in 4 s.

**Fix.** `avatar_ui._gui_owned(fn)`: every outside callback is wrapped in a plain function made
on the GUI thread before Qt holds it (perform_gesture_at, jump, runJavaScript callbacks, the
WebGL-failure callback); the call sites pass closures (`lambda: done.set()`) instead of bound
methods. A check scans the sources for any `QTimer.singleShot(.., on_x)` / `runJavaScript(.., on_x)`
holding an outside callable unwrapped and any `=done.set`.

**Workaround removed:** she walks to and reaches for videos like anything else again. Kept because
they are right on their own: no z-order tug of war with a Picture-in-Picture window; GC on the
GUI thread (the same class of bug via cyclic garbage); stacks to crash.log at a GUI freeze; the
crash probe.

**Finding things on screen (same log)**
| Finding | Fix |
|---|---|
| "The last two tabs are the YouTube ones": she never reached them. Opera's Ctrl+Tab cycles RECENTLY USED tabs (only ever two), Ctrl+9/Ctrl+8 did nothing | `switch_browser_tab` reads the real tab strip from UI Automation (TabItems, in order, by name), selects the one wanted (SelectionItem / Invoke / its exact rectangle), by words (fuzzy: "deep seek 4.1" finds "DeepSeek-V4.1") or by index (1 = first, -1 = last, -2 = second to last); fallback: Ctrl+PageDown (strip order in every browser) |
| Vision's precise pass failed 3x with 503 "request limit reached (16/16)"; she clicked the rough guess and hit the 1337x tab | `vision._ask` waits out a busy service (1.5 / 3 / 5.5 s) instead of dropping precision |
| Hunting for YouTube's play button (hidden until hover) | new `media_control` tool: play/pause, next, previous, stop, mute, volume up/down via the media keys -- works for a YouTube tab, Picture-in-Picture, Spotify, VLC; prompt says to use it |

## 37. Continuous activity: repeat-until, turn-based games, real-time play (2026-10-11)

**Problem.** She did one thing per model call. "Keep scrolling until I say stop", Flappy Bird or
tic-tac-toe need a decision every frame or every turn; a model round-trip (1-5 s) per step cannot
keep up, and the model guesses moves it should compute.

**Design.** The model only STARTS an activity (one tool call); a local controller thread then
watches the screen and acts on its own, and the result is spoken when it ends.

| Module | What it does |
|---|---|
| `activities.py` | `Manager`: one activity at a time (a new one replaces the old), stop on "stop" / Win+Shift+X (`HOTKEY_STOP`) / `stop_activity`, `max_seconds` limit, PAUSES while another window is in front (keys must not land there; gives up after 120 s), live progress in the status pill, `on_end` -> spoken result + mood gesture (won: cheer, lost: facepalm, draw: shrug). She walks clear of the activity's region (`avatar.keep_clear_of`) and takes the new "playing" pose |
| `perception.py` | `Grabber` (dxcam Desktop Duplication on Windows, mss fallback; region clipped to the screen), `changed()` frame diff, `playfield()` trims page margins / borders, local OCR (winocr, else Tesseract), `Hands` (no pyautogui pauses, pydirectinput scan codes for DirectX games, wheel puts the pointer back) |
| `loops.py` | `LoopActivity`: scroll / keys / hold / click / type / sequence, every N s, until stopped / count / seconds / end (3 repeats with no change) / text appears / text gone. `adjust` (faster, slower, direction, amount) while running. Closing / deleting keys are refused (Alt+F4, Ctrl+W, Delete, Win+L, ...) |
| `games.py` | Turn-based, any app or site: board read from pixels (tic-tac-toe grid lines by morphology, Connect Four holes by Hough circles, 2048 by tile colour with OCR fallback); "mine" = the cells her own clicks filled, so mark style does not matter; exact solvers (minimax; negamax alpha-beta depth 6; expectimax depth 2). Waits for the opponent's move by watching the board |
| `reflex.py` | Real time. Flappy: half-resolution detection at ~56 fps, pipes and gap (longest open run at three columns, tracked through occlusion), bird (saturated, not background or pipe colour); gravity, flap speed and input lag MEASURED online; each decision simulates flap vs no-flap over 0.8 s with the real decision rate and lag, with a margin that grows with prediction time. Runner (Chrome dino and clones): ground + runner found, low obstacle -> jump, high -> duck, look-ahead grows with speed. `ReactActivity`: on change / colour / text -> key or click |
| `core/toolset/activities.py` | Tools `start_repeating`, `play_game`, `react_when`, `adjust_activity`, `stop_activity`, `activity_status`; the prompt sends anything continuous here instead of repeated scroll / hotkey calls. The three acting tools are also checked against administrator windows (`elevation.py`) |

**Libraries.** `numpy`, `opencv-python` (requirements.txt); `dxcam`, `winocr`, `pydirectinput`
(`requirements-play.txt`, Windows only, optional -- everything falls back to mss / Tesseract /
pyautogui). `run.bat` installs both; a failure of the optional set does not stop start-up.

**Lab** (`play_lab.bat` / `tools/play_lab/lab.py`: real capture and real input against local
copies of each game in Chromium; Linux/Xvfb here, so Windows timing will differ):
| Activity | Result |
|---|---|
| Scroll to the end (until=end) | reached the end in 4 s (21 repeats) and stopped by itself |
| Tic-tac-toe vs the lab's perfect opponent | draw (the best possible result); earlier run vs a weaker one: won |
| Connect Four (Eva first) | won in 15 s |
| 2048 | 606 moves, best tile 512, score 8,592 in 219 s |
| Runner (dino clone) | still running at the 90 s limit |
| Flappy Bird, 8 games | 16, 0, 9, 11, 18 and 18 (both alive at the 30 s limit), 4, 49 (80 s). Median ~14 pipes |

Lab bug fixed on the way: a long page's region was its whole body (off screen); it now uses the viewport.
Reporting fix: a real-time game still running at the time limit now says "time's up and still going",
not "game over".

**Limits, honestly.** Flappy is the hardest: scores vary run to run (an early death is still
possible when the first pipe arrives before gravity and lag are measured). Games are recognised by
their look: an unusual skin (e.g. a 2048 theme with other colours) falls back to OCR or may not be
read. A game rendered with exclusive full screen may not be capturable by mss (dxcam handles it).

## 38. Iteration from Eva's own suggestions: items 1-7 plus predictive help (2026-10-11)

Eva proposed 20 improvements; several already existed (log rotation, skill hot-reload by
mtime, drop-in skills + MCP as the plugin system, cross-session memory, multi-monitor
reporting, learned skills + recorder). Approved and built: items 1-7 and predictive help for
non-financial actions. Not built: a skill marketplace (needs hosting, and lets other people's
instructions steer a program that controls the PC), voice training (Google STT cannot be
trained), a VM sandbox (undo + confirmation already cover it), accessibility (needs the
person's requirements).

| # | What | How |
|---|---|---|
| 1 | Instant file search | `file_index.find_files` asks Windows' own index first (ADODB `Search.CollatorDSO`, `SystemIndex`, through pywin32, which pywinauto already needs): every indexed location, ranked, and `contents=` searches INSIDE documents (`CONTAINS(System.Search.Contents, ...)`). Words are reduced to letters/digits before the SQL is built (no injection). Unavailable (service off) -> Eva's own index, and `WS["ok"]=False` so it is not retried that session |
| 2 | Settings window | `settings_store.py`: the 23 settings the person may change, typed and range-checked, saved to `~/.eva/settings.json` and applied by `config._apply_user_settings()` (the LAST line of config.py). Live where the code reads config at use time; others are marked * "after a restart". `settings_ui.py` builds the window from that list (tray > Settings). Tools `open_settings`, `change_setting`, `list_settings` ("talk less"). Safety (CONFIRM_EFFECTS), the API key and command limits are NOT settings, so neither the file nor a request can loosen them. New: `ANIMATION_SPEED` (scales the page's dt; `window.EVA_SPEED`) |
| 3 | Vision cache | `vision.look_at_screen` / `locate_on_screen` reuse an answer only for the same question on the SAME pixels: the key holds a hash of the image sent (1/4 size, 5-bit grey) with Eva's own windows blanked (`vision.SELF_RECTS`, refreshed every 400 ms on the GUI thread from the top-level widgets, DPI-scaled) -- she is always moving and would otherwise make every screen new. 120 s, 48 entries; only refined locations and non-empty answers are kept |
| 4 | Failed-task replay | `core/replay.py`: every turn is traced (tool, summarised args, result, ms) and a 960-px JPEG is taken 0.15 s after each on-screen action -- never after SENSITIVE or FINANCIAL steps. If the task's last action failed or was unconfirmed (`unsupported_claim`) the trace becomes `~/.eva/replays/<when>-<task>/index.html` (newest 10); a good task is discarded. One FIFO worker does screenshots and saves in order, off the reply path. Tools `failed_tasks`, `show_failed_task`; tray > Failed tasks |
| 5 | Long-session health | `core/vitals.py`: every 5 min RSS, threads, handles, GC objects to eva.log; a straight-line fit over the last hour flags growth (memory > 60 MB/h and +200 MB, threads > 150 or +20/h, handles +3000/h); on a finding it drops the vision cache and asks the GUI timer for a full collection (`FULL_GC_REQUESTED` -- never gc on its own thread, see 36), then warns hourly. Tray > Status shows it; tool `eva_health`. `tools/soak_test.py` |
| 6/7 | Element tools, tests, helpers | `desktop_control._element()` is the one start for click_element, type_into_element, act_on_element, resolve_element_center: own window, stale handle (`_window_alive`), connect, find; a missing element says "the window changed since ui_tree -- call ui_tree again". `uia_error()` turns raw COM/pywinauto failures into what to do (disabled -> greyed out, not visible -> scroll into view, timeout, access denied, element vanished). New check drives both tools through every failure path and the read-back verification (append/replace, spliced text, text that never arrived) |
| + | Predictive help | `predictive.py`, opt-in (`PREDICTIVE_HELP`, Settings > Initiative). Records, on this PC only, the first time each day a program comes to the front (program only) and sites she opened via navigate_browser (site name only), 28 days. A routine = first opened on >= 3 days within 45 min of the same time (weekdays only if never seen at a weekend). Offered once a day, at most one offer per 20 min, never while she is busy / a game or repeat runs / full screen / the person away > 2 min. Runtime: the next short "yes" runs the stored `launch_app` through `agent.dispatch` (the normal gate); "no" twice mutes for 14 days; "don't ask again" stops it; anything else lets it lapse. Money is never recorded or offered: FINANCIAL_WORDS (banking, UPI, wallets, trading, crypto, tax, accounting...) + the finance guard's payment patterns; the only offerable action is opening a program or site. Tools `my_routines`, `forget_routine` |

**Bugs found on the way (by the new checks and the soak test).** `os.path.basename` does not
split Windows paths off Windows, so every program looked new (fixed with an explicit split).
The first replay save started a thread per task that waited for its screenshot: 600 fast turns
left 404 threads (fixed: one ordered worker; soak now flat: +0 threads, +0.0 MB). The first
cut finished the trace before returning the reply, adding ~0.2 s to every task (moved off the
reply path).

**Verified here:** all checks (3 new), soak test 1,000 + 600 turns flat, Settings window
rendered offscreen, a replay page rendered at desktop and phone width. **Only on Windows:**
Windows Search results, the Settings window over the real desktop, real screenshots in
replays, foreground-program observation for routines.

## 39. Finding things on screen, hearing right, the vision "crashes" (eva.log 2026-10-11 19:20-20:08)

**What the log showed.** No Python crash: the modules that "crash" are the vision tools --
`locate_on_screen` abandoned 3 times at its 90 s deadline and "still running" 27 times, plus
clicks landing in the wrong place again and again (the deer video, "Back to tab", Wordle's Play).

| Finding (evidence) | Root cause | Fix |
|---|---|---|
| Clicks pulled toward the top-left. "man holding deer" clicked at (985, 984); the thumbnail was at (1533, 853). Same for "Japan's dear City" (1000, 979) and Wordle "Play" (720, 739 -> really ~1106, 638) | The vision model answers in coordinates **normalised to 0-1000** (Qwen-VL grounding convention; the log also shows `<point>[(880, 720)]</point>`, `bbox_2d`). On a 1536 x 864 image those numbers usually fit, so they were read as pixels: 798/1000 x 1920 = 1533, 790/1000 x 1080 = 853 -- exactly the real place. The old check could only catch it when a number exceeded the image | Prompt asks for 0-1000 explicitly. `vision.calibrate(model)`: once per model a synthetic picture with a red "7" disc at a known place decides the convention (0-1000, 0-1000 y-first, 0-1, pixels; ~5 s, `~/.eva/vision_calibration.json`, started 25 s after launch); until measured, 0-1000 is assumed. `config.VISION_COORDS` forces one. Point answers handled. tray > Status shows the result |
| Most targets were TEXT ("Back to tab", "Accept all", "Play", "Log in", video titles) but always went to the 5-90 s vision model | No text path | `screen_text.py`: the PC's own OCR (winocr = Windows.Media.Ocr; Tesseract fallback) gives every word's rectangle in < 1 s; fuzzy + **sound-alike** matching (dear/deer -> "dr", loss/laws -> "ls"), several readings of the description ("the Back to tab button" -> "Back to tab"), whole-line labels preferred, other equal matches reported (`also_at`). Passes: as is, inverted grey, then each button-like region alone (Tesseract drops light pills on dark pages -- tested on a rendered page). Words inside Eva's own windows ignored (her bubble repeats the words). Only non-text targets ("man holding a deer") go to vision |
| 90 s hangs; one 503 took 91 s to arrive | The OpenAI client retried 503s on its own (max_retries=2) under Eva's own retries; 90 s per request | `max_retries=0`; `VISION_REQUEST_TIMEOUT` 25 s per request; whole locate within `VISION_LOCATE_BUDGET` 60 s; one retry while busy, then `VISION_FALLBACK_MODELS` (qwen/qwen3.5-122b-a10b, google/gemma-4-31b-it -- from NVIDIA's catalogue, not verifiable on this key from here; 404/410 -> skipped for the session); "thinking" switched off for these calls where accepted (refused -> asked again without); a busy service returns a plain "busy" result |
| "It's to your right", "just on top of your head" | No way to narrow the search | `locate_on_screen(area=near_me / left_half / right_half / top_half / bottom_half, or a box)`; `vision.AVATAR_RECT` kept by main.py |
| Misheard: "video deposit" (to pause it), "schooling" (scrolling), "Tyres too slow", "model game" (Wordle), "dear" (deer), "no loss" (laws), "w o r d l e" | The model saw only the recogniser's single best guess | `voice.recognize` uses Google's `show_all`: confidence and other hearings go with the text (`core/hearing.py`: `[speech-to-text, may be misheard: confidence 0.58; also heard as "..."; spelled out: WORDLE]`); the open windows' titles are in every turn's context (best evidence for a misheard name); prompt "HEARD SPEECH IS A GUESS": read by sound and context, search the screen for the corrected words, ask one short question naming the best guess only when readings lead to different actions |
| "Dewas top", "ok you are stop" did not stop her | Exact stop phrases only | a short utterance (<= 4 words) ending in "stop" when joined stops her (not "don't stop", "bus stop", "nonstop") |
| "please pause it" -> "The video is paused." with no tool called | The claim check only looked at tool results; there were none | `claim_without_action`: an action request answered with a done-claim and no tool call is challenged once ("no tool was called this turn"); "is paused / is playing / should now be" are claims |
| "(No response needed; waiting for user to say stop.)" spoken | A stage direction passed as a reply | a reply that is entirely in brackets is silence |

**Verified here:** checks (2 rewritten/new, built from the log's numbers); a rendered dark page
read by real Tesseract: "Japan's dear City" -> "Japan's Deer City", "city with no loss" ->
"... With No Laws", "the Accept all button" -> the pill (third pass), "man holding a deer" -> not
text (vision). **Only on Windows:** winocr results, the calibration answer of the real model,
whether the fallback model names resolve on the key.

## 40. eva.log 2026-10-11 22:49-23:02 (after section 39)

Working as intended: the vision model was calibrated on first use (`norm1000`, off by 4 px);
text targets were found by OCR in 16-530 ms (Steam, LIBRARY, Scan); a busy vision service
returned "busy" within the 60 s budget instead of hanging; administrator windows were refused
honestly.

| Finding | Fix |
|---|---|
| `STARTUP CHECK FAILED -- tool 'open_settings' has no implementation bound` (twice) | The check ran before the tray section binds open_settings; it now runs right after the last binding |
| A voice turn ended in `TypeError: Object of type Decimal is not JSON serializable` (find_files via Windows Search) | ADODB returns sizes as Decimal: converted in `file_index`; and at the root, `core.results._normalise_result` makes EVERY tool result JSON-safe (Decimal, datetime, set, Path...) -- an unserialisable result also used to slip past secret redaction |
| "team shortcut" (misheard Steam) matched "- Shortcut" at 0.85 and was clicked | Phrase scoring: punctuation is not a word; most of the words said must be present (all of 1-2, three quarters of more); whole-string similarity alone counts only at >= 0.9; "shortcut", "app", "file", "folder", "program" are kind-words. "steam shortcut" now finds "Steam" |
| "Scan" not found for 56 s while the vision service was busy; found by its text 94 ms later | After a vision failure the screen's words are read once more before giving up |
| A spoken question ("if I run you as administrator will that work") was answered only "Here you go." (answer shown, 261 chars) | A prose reply up to 600 characters with no list is an answer: she says its first sentence or two (within SPOKEN_MAX_CHARS); essays and lists keep the hand-over line |
| Epic Games Launcher and WizTree run as administrator; she could do nothing in them and said she cannot elevate herself | tray > Restart as administrator and tool `restart_as_administrator` (offered, never unasked): ShellExecute "runas" after Qt has shut down; Windows' UAC prompt decides; declined -> she starts again as before. The administrator refusal now mentions this option |
| `qwen/qwen3.5-122b-a10b` answered 410 Gone | removed from VISION_FALLBACK_MODELS (gemma-4-31b-it exists; it timed out once) |
| Replays titled "...speech-to-text-may-be..." | the trace keeps the request, not the hearing note |

## 41. eva.log 2026-10-11 23:12-23:16: the right words, the wrong place

The positions were exact this time; WHAT was matched was wrong. "Play button" -> a small "Play"
link (13 px high) near the bottom of the Wordle page, not the Play button (white text on a
black pill, missed by the first OCR pass -- the later passes only ran when the first found
nothing); "Wordle" -> "Wordle," inside a sentence. Both clicks opened the wrong thing.

| Fix (screen_text.py) | |
|---|---|
| Controls read every pass | A control ("... button", or a label of up to 3 words) is looked for in all three OCR passes (as is, inverted, button regions), cached per unchanged screen; titles and passages still stop at the first hit |
| Labels beat running text | A match that is its own line (+0.05) beats the same words inside a longer line (-0.15 for controls); ties go to the larger text; the bonus ranks only -- the match itself must still clear the bar |
| Buttons read properly | Button regions = filled blocks lighter/darker than the page AND outlined boxes (edges); each is read from its INSIDE (rounded ends and border removed: "C conirue" -> "Continue"), white-on-dark inverted, padded |
| Alignment | A weak word guess no longer takes the slot another word needs ("I visited America city with no loss" vs an OCR line starting with "|") |
| Alternatives | Other places with the same label are returned (`also_at`) with a note: the given one is the most button-like; try the next if the click does not do it |

Verified on a rendered Wordle-like page with real Tesseract: Play -> the button (1063, 437)
exactly, the link reported as also_at; Continue (912, 840), Log in (858, 438), Wordle heading.

## 42. Structure first, in every window (2026-10-11, request: "a core tool, not opt in ... other windows and desktop apps as well")

`locate_on_screen` now looks in this order, for every window: **0. the window's own structure**
(`structure.py`) -> 1. the words on screen (OCR) -> 2. the vision model.

**What "structure" is.** UI Automation: the tree every Windows program publishes -- Win32, WPF,
WinUI/UWP, Qt, Office, Electron (VS Code, Teams, Slack, Discord, Spotify), the desktop's icons, the
taskbar, and every browser's PAGE (Chrome, Edge, Opera, Brave, Firefox expose links, buttons,
headings, fields with the names and roles the DOM gives them). Exact names, roles, rectangles.

**Why it did not work before.** `ui_tree` / `find_elements_by_text` walked controls one at a time
through pywinauto (several cross-process calls each) and stopped at 400-500 elements or 3-8 s. In
a browser that budget ended inside the tab strip and Opera's hidden "Speed Dial" page.

**Now.** One `FindAllBuildCache` call (in the crash-isolated worker, DPI-aware) returns every
ON-SCREEN, NAMED element with name, role, rectangle, enabled, automation id and help text cached;
a Chromium/Electron window that answers with only its frame (its page tree is built on first
request) is asked again after 0.6 s. Snapshots are cached per unchanged screen. Matching reuses
the OCR matcher (spelling + sound, readings of the description, spelled letters joined) and adds:
interactive roles first; page content before the browser's tabs/address bar (unless those are
asked for); larger of equal matches (the button, not a footer link); disabled controls last and
flagged; Eva's own windows excluded. Windows read: the one in front; the desktop when named or in
front; the taskbar when named; with an area, the windows under it. Administrator windows skipped
(Windows blocks reading them).

**Why not the browser debugging protocol (CDP).** Chrome 136+ ignores --remote-debugging-port on
the person's own profile (security change; it only works on a separate signed-out profile), so it
cannot reach the browser being used. UI Automation reads the page on screen in every browser.

**Not covered.** Java (Swing) apps without the Java Access Bridge, games, canvas-drawn UIs: those
fall through to OCR and vision as before.

Checks: the worker's UI Automation read against a fake IUIAutomation (API names, the sparse-tree
retry, the on-screen/named condition); matching cases from the Wordle and desktop logs; ordering
(structure -> no OCR, no vision; none -> OCR). The checks never read the real desktop.

## 43. She notices when she is stuck; the bubble says what she is doing (eva.log 2026-10-11 23:38-23:45)

Structure-first worked: "Continue", "enter", "1st letter, empty" were found in Opera's page
structure (81-165 elements in ~100 ms). What went wrong was persistence without looking.

| Finding | Fix |
|---|---|
| Wordle: typed, clicked a tile, clicked Enter, typed again -- 14 actions that moved nothing, then MAX_TOOL_ROUNDS ("I got stuck partway") | `Agent._note_progress` judges every acting step (failed, screen_changed=false, or unverified with an error = moved nothing; observing does not count). After 3 in a row, a **STUCK CHECK** is added before the next model call: which steps did nothing, and **what is on screen now** (`structure.describe_screen`: the window, dialogs/panels in it, text, the controls on offer with disabled ones marked; OCR text if the structure has nothing; never the vision model, which may be what is busy). She must say what is blocking her and remove that first, or ask. At most 2 per turn, each earns 4 more rounds. Prompt: NOTICE WHEN YOU ARE NOT GETTING ANYWHERE |
| Every Wordle guess "did NOT reach the target window" (a game board publishes no text) | `_verify_typed`: the window was focused before typing; if its text did not grow but the screen changed while typing, the keys arrived (`screen_changed: true`, unverified, "look at the screen") |
| Clicks that changed the page reported "nothing changed" | `screen_changed` used an 8x8 average of the whole screen: a letter in a tile or a menu opening was invisible to it. Now also a 320x180 grey comparison (`screen_small` / `screen_moved`) with Eva's own windows masked out |
| "Thinking..." under her and the typing dots above her said the same thing twice | The blue bubble now SAYS what she is doing, with moving dots ("Thinking ...", "Looking for the Continue button ..."); the line under her shows only the microphone (Listening / Muted / Paused) and Speaking. A message she has just said is never covered: the status waits until it has been read |

## 44. The doom loop (eva.log 2026-10-12 00:01-00:06)

Two failures fed each other while she played Wordle "fairly":

| Finding | Root cause | Fix |
|---|---|---|
| Six corrections ("press Enter first", "the next free row") each logged "Turn interrupted"; she restarted, lost the board state to history trimming (74 -> 56 messages) and repeated the same mistakes | `Runtime.should_abort` ended the running task whenever ANY person message was queued | Only a stop ends a task. Words said while she works are folded into the RUNNING turn at the next step (`Runtime.take_interjections` -> `Agent._fold_in_interjections`: "[voice, said while you were working] ..."). A plan made before they arrived is not acted on: the model is asked again with them (also cut mid-stream), remaining tool calls of that plan are skipped with "re-plan". An unprompted observation still gives way to the person |
| The last model call ran 70 s with no answer -- reasoning in circles ("Wait row3 seems incomplete... Actually...") -- until Eva was closed; stop could not interrupt it | The stream had no limit: the client timeout only fires when nothing arrives | `llm._call_model` checks between chunks: a stop or new words from the person (`llm.set_abort`, set by the agent) end the stream at once; reasoning longer than REASONING_MAX_SECONDS (45) before any answer, longer than REASONING_MAX_CHARS (16000), or repeating its own tail three times is cut ("runaway"). The agent then says "decide NOW from what you know: one action, or one short question"; a second runaway in the same turn ends it plainly ("I'm going round in circles on this one...") |

Checks: a repeating reasoning stream is cut and closed; a stop ends a stream mid-way; time limit;
normal answers untouched; one recovery then a plain stop; a correction said during a decision
replaces the stale plan (WHELP not typed, Enter pressed); queued speech does not abort a task, a
stop does, observations give way. Soak test unchanged (flat).

## 45. Learning any game, then playing it fairly (2026-10-12: "first lookup the rules ... generic than specific")

`game_play.py` + tools in `core/toolset/gameplay.py`; the built-in players (games.py, reflex.py)
stay for the games they cover.

| Part | What |
|---|---|
| Rules card | `game_rules(name)`: a saved card from `~/.eva/games/`, or how to learn one: look up the RULES (a how-to-play page) -- never answers -- and `save_game_rules`: GOAL; HOW A MOVE IS MADE here (keys/clicks, what confirms it); HOW TO READ THE STATE; HOW IT ENDS; BASIC STRATEGY. Reused next time |
| Notebook | `start_game` ... `end_game`; `game_note` after every move (the move and what its result showed). The rules card and the notebook are put in front of the model on EVERY call (`Agent._context_now` -> `game_play.prompt_block`), so trimming the conversation cannot lose the board (the 00:05 failure) |
| Fair play | While a game is on, a search / page read / navigation whose words ask for answers, solutions, hints, "today's word" is refused (`fair_play_check`, in the dispatcher) -- unless the person asked for answers in so many words; `start_game(fair=false)` when they allowed it. Looking up the rules is always fine |
| Reading the board | `read_screen_elements`: every labelled element of the window from its structure, ROW BY ROW, with roles and disabled state ("y=481: 1st letter, C, absent \| 2nd letter, R, present in another position ..."). Prompt: read the state with it before look_at_screen |
| Prompt | PLAYING A GAME: built-in players for their games; otherwise learn -> start -> each move read / decide one move / make it as the card says (incl. what confirms it) / check / note -> end |

## 46. Stabilisation phase begins; vision model -> Nemotron 3.5 Super VL (2026-10-17)

Decision (Adarsha, 2026-10-17): stabilise the existing experience before any new features or
skills (plan: "Eva -- Plan to v1.0 Release", Phase 1 and Gate A). Feature freeze until then.

| Change | Detail |
|---|---|
| Vision model | `VISION_MODEL_ID = nvidia/nemotron-3.5-super-vl-120b-a12b` (released 2026-10-15; free endpoint on build.nvidia.com, labelled early access; text + image + video in, tool calling, reasoning switchable with `chat_template_kwargs.enable_thinking`). Fallbacks in order: the previous eye `nemotron-3-nano-omni-30b-a3b-reasoning`, then `gemma-4-31b-it`. Its coordinate format is measured on first use (vision.calibrate) and shown in tray > Status |
| Main model unchanged | `MODEL_ID` stays `nemotron-3-super-120b-a12b`: one change at a time; decide after measuring |
| Thinking-on budget | If a model refuses "thinking off", its reasoning counts against max_tokens, so vision requests then get at least 4,096 tokens instead of 300 (would return empty) |
| `tools/model_probe.py` | Run once on your key: per model, reply latency, whether a tool call comes out right (3 tries), and how far off a located target is (the calibration picture). Writes tools/model_probe_result.txt |

## 47. First run on the person's PC by Claude (2026-10-17)

The session was linked to the person's PC (folder access + computer use), so checks ran on Windows for the first time.

- **Voice was mute (fixed).** Every spoken line failed with `403 Invalid response status`: Microsoft retired the
  handshake edge-tts 7.2.8 used (~2026-10-12). edge-tts 7.3.1 (2026-10-13) works; verified on the PC ("voice OK online").
  `voice.synthesize` now: online voice -> on 401/403 the other host (`api.msedgeservices.com`, same token) -> the
  Windows SAPI voice (`TTS_LOCAL_VOICE`, default Zira), resting the online voice `ONLINE_RETRY_SECONDS` (300) after a
  failure. `requirements.txt` pins `edge-tts>=7.3.1`; `eva_doctor` uses `voice.synthesize`. Check: `she_is_never_mute`.
- **run_checks on Windows: 3 failures, all fixed** (test or resource bugs, not behaviour):
  `/etc/eva_test` is just `D:\etc` on Windows (the check now uses `%SystemRoot%`; the old run may have left
  `D:\etc\eva_test` -- safe to delete); Windows' monotonic clock ticks ~16 ms so `REASONING_MAX_SECONDS = 0` never
  tripped (now -1); `FileIndex` kept its SQLite file open (new `FileIndex.close()`, called by the check and on shutdown).
- **Model probe (the person's key):** main `nemotron-3-super` 1.3 s, tools 3/3, no vision. **Nemotron 3.5 Super VL
  0.4 s, tools 3/3, vision 6 px (norm1000)**. Nano Omni 0.5 s, tools 2/3, 2 px, hit its 16-request limit. Gemma 4 31B
  timed out on every request -- not a usable fallback today. The probe now has a 60 s per-call and 180 s per-model cap.
- **Scorecard** was started (tools/scorecard.py); the PC link dropped mid-run -- read `eva-scorecard.txt` next time.
- Screenshots from the computer-use bridge do not show the avatar (transparent window); only the status pill shows.

## 48. Safety: Eva restarted the PC (2026-10-17 11:21) -- fixed at the gate

**What happened.** The live Eva (mic on, during the scorecard run) heard "update install" -- probably speech
from the room, not addressed to her -- opened Windows Update, then on a folded-in "almost" invoked
`SystemSettings_MusUpdate_UpdateActionButton3_SplitButton`, whose label is **Restart now**. Nothing asked: the
permission gate judged a click per CONTROL only for money (finance_guard); every other click was INTERACT.
The PC restarted.

**Root fix.** `system_guard.py` (pure, like finance_guard) recognises controls whose press cannot be taken back:
restart / shut down / update and restart / sign out / uninstall / remove / delete / erase / format / reset /
empty Recycle Bin / clear data / discard / don't save / overwrite / end task / force quit / close account.
Harmless look-alikes are excluded (Restart later, Schedule the restart, Pause updates, Cancel, Don't restart,
Learn more, Close). `permissions._destructive_reason` applies it on EVERY route to a press, always from the
control's own label, never the model's words:
- `click_element` / `act_on_element` (invoke/auto/click/toggle/select) -> `describe_element`
- `ui_navigate` -> its target label (its vision rung clicks wherever that label is) and the element if given
- `click_coords` / `double_click_coords` -> new `desktop_control.name_at_point` (UIA ElementFromPoint, in
  `uia_worker` as op `name_at_point`; unreadable -> not judged, as with money)
- `send_hotkey` Enter/Space -> the focused control's name
- `browser_act` clicks -> `browser_agent._destructive_check`
Such presses are DESTROY, asked EVERY time (never task-scoped). Check: `restart_now_asks_first` (replays the
exact 11:21 call). Verified on the PC: `name_at_point` read the real Start button as 'Start (Button)'.

**Still open (not a safety issue now, but worth a look):** acting on a two-word fragment nobody addressed to her.

**Windows status (first time):** `tools\windows_checks.bat` (pip sync, voice test, live gate test, run_checks;
`windows_checks.bat probe` adds the model probe) -> **all checks pass on Windows**. Scorecard after the fix,
Eva not running: **12/12**, 34 model calls, 256 s (slow: time 35 s, settings 62 s, longform 64 s).

## 49. Live testing through her chat box (2026-10-17 12:04-12:31)

Tested on the person's PC by typing requests into her chat box (computer use). Results and fixes:

| Test | Result | Root cause / fix |
|---|---|---|
| "open notepad and type: ..." | **typed the line twice**, then "I got stuck" | `read_window_text` chose the LONGEST value in the window (Win 11 Notepad's title bar, 46 chars) over the 19-char document, then swapped short text for menu labels. Now: Document/Edit control preferred, short document text kept ahead of the labels, every value returned in `values`; `_verify_typed` and `type_into_element` search all of them. Live re-test: typed once, correct reply. Check `a_short_line_typed_once` |
| Retries typed into **her own chat box** | fixed | `type_into_element`'s fallback clicked the control's centre, which her chat box covered. New `desktop_control._clear_point`: before any coordinate click (click/double/right/drag) and that fallback, `WindowFromPoint` is checked; if it is Eva's own window, `MAKE_WAY` (main.py, GUI thread) moves the chat box/bubble/body off the point; still covered -> nothing is clicked, plain error. The fallback focuses through UIA instead of clicking when covered. Check `her_own_windows_never_take_the_click` (not reproduced live: the chat box could not be dragged over the target) |
| "close that notepad without saving" | **asked first** ("Don't save" = DESTROY), approved by the person | system_guard working as designed |
| Ctrl+A asked for approval | removed from DESTRUCTIVE_HOTKEYS | selecting loses nothing; typing over a selection never asked |
| "stop" during a long task | was **19 s**, now **16 ms** | the abort was checked only per streamed chunk; a long tool call streams nothing. `llm._call_model` now runs a watcher (every 0.2 s) that closes the stream. Check in `no_doom_loops` |
| Reply "(handle 67068, title ...)" | internals removed | `agent.clean_reply` strips bracketed handle/element-id/pid asides and "handle 12345". Check `no_internals_in_replies` |
| free space on D: | correct (133.0 of 1000.2 GB, matched `df`) | -- |
| open Recycle Bin icon | worked (found by on-screen text in 1.4 s) | clicked once then double-clicked; the success claim was re-asked though list_windows showed the window (minor, open) |
| weather in Bardhaman | correct, quick | -- |

**Deployment lesson:** `device_commit_files` re-used an earlier upload when the same staged path was committed
twice, so a fix silently did not reach the PC and a live test ran stale code. Always stage each round under a
new folder and confirm with `md5sum` on the PC.

**Also:** the `web\assets` folder was missing after the person re-extracted the code-only zip (it excludes the
assets); restored from eva-avatar-assets.zip (md5 match). Re-extract over the existing folder, never into an
emptied one.

Windows: `tools\windows_checks.bat` -> all checks pass (12:31). Diagnostic `tools\read_probe.*` can be deleted.

## 50. Phase 1: persistent UI Automation worker; model provider layer (2026-10-17)

**Persistent UI Automation worker** (plan: "a new Python process per read: 0.5-1.4 s each").
`uia_worker.py --serve` keeps ONE worker alive (JSON lines on stdin/stdout); `desktop_control._UiaServer`
serialises requests, and a reply that does not come in time (hang) or a dead worker (native UIA crash) costs that
one read and a restart -- isolation exactly as before. If the server cannot start, one-shot workers
(`_isolated_once`) take over. Stopped on shutdown. Check `one_long_lived_uia_worker` (real protocol against a fake
worker: answer, stale reply, hang, crash, recovery, unstartable). Measured on the PC (`tools\uia_bench.bat`,
File Explorer window, medians):

| Read | one-shot | long-lived |
|---|---|---|
| name_at_point (every blind click, system_guard) | 1,705 ms | **15 ms** |
| structure_snapshot | 1,987 ms | **661 ms** |
| ui_tree | 3,364 ms | **2,025 ms** |

Start-up 1.7 s once per session; results identical. Still open for the plan's "< 200 ms": the tree walk itself
(ui_tree uses pywinauto `descendants()`; moving it to a cached `FindAll` like structure.py is the next step).

**Model provider layer** (`providers.py`; plan blocker 2 "one free provider").
Any model setting may be `provider:model` (presets: nvidia -- the default for a bare id -- openrouter, openai,
groq, together, ollama, lmstudio; `config.PROVIDERS` adds/overrides, e.g. Azure). Keys: encrypted store or env var
(`set_key.bat OPENROUTER_API_KEY`); local providers need none; a provider without its key is skipped, never tried.
NVIDIA extras (`reasoning_effort`, `chat_template_kwargs`, `reasoning_content` in history) go only to NIM
endpoints -- strict providers 400 on unknown fields. `core/llm.call` walks `MODEL_ID` + `MODEL_FALLBACKS`:
busy -> 2 tries then the next; gone/refused (401/402/403/404/410, "not found", "deprecated"...) -> next at once;
the failed one rests 5 min so later turns start with a working one; a 400 bad request surfaces (no hiding);
single model = old behaviour + explain_error. `llm.last_model()`; browser_agent and vision use the same layer;
`tools/model_probe.py` takes `provider:model`. Default config unchanged (`MODEL_FALLBACKS = ()`).
Check `any_provider_and_a_fallback_chain`. Live on the PC (`tools\provider_smoke.bat`): a non-existent model
first -> NVIDIA 404 -> skipped at once, answered by nemotron-3-super ("ready", 5.5 s).

Windows checks: all pass (13:03).

## 51. Phase 1: main.py split into eva_app/ (2026-10-17, done by a parallel session; recorded here)

`main.py` (1,100+ lines) is now an 88-line composition root; each feature's wiring is one module in `eva_app/`
with `install(ctx)` on a shared `Ctx` (eva_app/context.py), installed in order: status, brain (gate, agent,
voice, history, runtime), helpers, activities, browser, mic, housekeeping, reach, lifecycle, tray, own_windows
(own-window rects + MAKE_WAY), schedule, life. `startup.py` keeps CORE_MODULES, the self test and the relaunch.
Also fixed there: `app.setQuitOnLastWindowClosed(False)` -- closing Settings or Activity used to end Eva (live
test 13:20). The pre-split file is kept on the PC as `main.py.before-split` (safe to delete). Verified: every
file identical on the PC, Eva running it since 13:21, all checks pass.

## 52. Phase 1: one cached UI Automation read for ui_tree and element lookup (2026-10-17)

`_ui_tree_inproc` now reads the whole window in ONE `FindAllBuildCache(Descendants, TrueCondition)` with the
properties it needs cached (automation id, name, class, type, native handle, rect, Invoke/Value/Text pattern
availability) instead of ~6 cross-process calls per element. Same condition as pywinauto's `descendants()`,
so the same order and the same ids (`_cached_id` mirrors `_element_id_for`: automation id -> GWL_ID ->
window_text (Text pattern when it has a class name, else name) -> `#index`). `_find_control` (every
click_element / act_on_element / type_into_element) uses the same read (`_find_cached`); not found -> the old
walk decides, so a mismatch can never cause a miss. Old walk kept as `_ui_tree_slow` (fallback + reference).

Parity on the PC (`tools\uia_tree_parity.bat`, read-only): 4 windows, every element identical, 48/48 sampled
ids resolve to the same control (runtime id). Timings (walk -> cached): File Explorer 500 el. 2,802 -> 805 ms;
236 el. 1,223 -> 386 ms; Notepad 286 -> 128 ms / 207 -> 97 ms. Profile (`tools\uia_profile.bat`): of 845 ms,
845 is Explorer building its tree for the one request; Python ~20 ms. "< 200 ms" is therefore reached for
ordinary windows but not for a large Explorer view without changing the id scheme (a filtered view = different
`#index` ids) -- next step, if needed: reuse the read while the window is unchanged (Phase 2).

**Windows CI.** `.github/workflows/windows-checks.yml`: on windows-latest, install requirements, `run_checks.py`,
`tools/soak_test.py --turns 2000` (both exit non-zero on failure). Inert until the code is in a GitHub repo
(none exists yet); locally the same gate is `tools\windows_checks.bat`.

**Gate A status (updated 2026-10-17 23:45):** Windows checks green (173/173 at 23:41); scorecard 12/12 (11:45);
synthetic soak PASS. **8-hour live soak: NOT achieved** -- the run begun at 13:58 was restarted 13 times for
deployments (14:47 ... 23:30; Eva was closed 16:13-21:31), so the longest uninterrupted run is ~49 min
(13:58-14:47). Within the runs: vitals flat (13:59 rss 392 MB / 98 threads / 2,683 handles; 23:41 371 MB / 98 /
3,747); no crash. ERROR lines: 5, all tool failures during the MakeMyTrip tests (browser_open "interrupted by
another navigation" x4 -- fixed in section 57; browser_act x1); CRITICAL: "Renderer crashed 3 times" from 23:34,
caused by a screen-capture tool hiding her window, not a crash (fixed in section 57, needs a restart). Gate A
needs one uninterrupted 8-hour run with no deployments.

Live after restart (13:59, cached reads): "open the play_lab folder" in File Explorer -> done (foreground became
"play_lab ... - File Explorer"), but slowly (~75 s): ui_tree of a 500+ element Explorer window was truncated to
8,000 chars (core/results.py) and play_lab fell in the cut part, so she located it by vision (4.3 s) and
double-clicked; then 37 s of re-checking. **Next (Phase 2 candidates):** ui_tree should return the visible,
named, actionable elements first (or accept a `contains` filter) instead of being cut blindly; a
navigation that changes the window title should count as verified.

**Soak:** Eva restarted on the final Phase 1 code at 13:58; the 8-hour live soak runs from then (check vitals in
eva.log around 22:00: rss, threads, handles flat; no ERROR/CRITICAL).

## 53. Phase 2: she feels alive; boredom; patrol; one OCR engine; ui_tree ranking (2026-10-17)

Asked for: "she just stands there ... need that human connection"; "move her hands or feet if she is getting
bored, maybe a yawn, maybe she runs around like a small child, or starts to look around the pc to manage
inefficiencies".

**Presence layer** (`web/avatar_3d.html`, `window.EVA_PRESENCE`; setting PRESENCE). Eyes follow the cursor and
meet the person's gaze when the pointer hovers her (`vrm.lookAt` yaw/pitch, `setAttention`, fed every 120 ms by
`eva_app/life.attention_tick`, which replaced the old glance timer); a small nod + smile (`acknowledge`) when she
hears speech (`eva_app/brain.py`); slow weight shifts; fidgets chosen by boredom 0-3 (`FIDGETS`: finger_tap,
foot_tap, heel_bounce, rock, hands_behind, look_hands, swing_arms). `presenceStep` runs before `writePose`,
`presenceFace` after the expression crossfade. Lab hooks: `__eva.presence()`, `presence.force`.

**Boredom** (`life.py`, `eva_app/life.boredom_level`): from `runtime.last_person_at` -- 0 under 3 min, 1 under 8,
2 under 20, else 3. Bored acts every `BORED_GAP` 70-160 s: yawn, stretch, look around, scratch head, check watch,
hum, impatient; at 3 (and no person activity for `RUN_QUIET` s, at most every `RUN_GAP` 20 min) she runs about
the bottom of the screen (`avatar_ui.run_around`, `RUN_SPEED` 16, running gait). Live: eva.log 14:58 "Idle life
(bored 2): scratch_head".

**Patrol** (`patrol.py`, setting PATROL). At boredom 2+, at most every 30 min, never while busy / an offer is
pending / full-screen / the person typed in the last 15 s: local checks only (disk >90%, Downloads 150+ old files
or 3 GB, memory >=88%, the same CPU hog two patrols running, Recycle Bin >=2 GB, >15 windows, battery <20%
unplugged = info). One finding per patrol, offered through `runtime.offer` (`source: "patrol"`); yes runs the
finding's task as an ordinary request (every destructive step still asks at the gate; the tasks say "move or
delete nothing until I agree"); no = quiet 7 days; "don't ask again" = never; unanswered = not for a day. State
`~/.eva/patrol.json`.

**OCR**: one Windows OCR engine kept for the session (`screen_text._win_engine`, warmed on the `eva-ocr-warm`
thread); winocr, then Tesseract, as fallbacks. `tools\ocr_bench.bat`: identical text.

**ui_tree ranking** (fixes the 13:59 finding of section 52): visible, named, actionable elements first
(`_rank_tree`, `UI_TREE_SHOW` 160, names cut at 160 chars) and a `contains` filter; the note tells the model to
use `contains` when more exists.

Checks: `she_feels_alive_and_looks_after_the_pc`, `finding_a_control_in_a_big_window`,
`one_ocr_engine_for_the_session`. Windows: 168/168 PASS (15:03).

## 54. Phase 2: the person's own browser through an extension (2026-10-17)

Eva can now act in the browser the person already uses (signed-in sessions, open tabs), not only in her own
Playwright browser.

    extension (MV3)  <-- native messaging -->  native_host.py  <-- 127.0.0.1 TCP + token -->  Eva (browser_link)

- `browser_bridge/extension/`: MV3, permissions nativeMessaging/tabs/scripting, `<all_urls>`; the manifest `key`
  fixes the ID `dcnonlenmnakmjokkofhknaodcapnfnd`. Commands: ping, tabs, activate, go, elements, describe,
  click, type, read, scroll. In-page: `pageElements` tags `data-eva-id`; `pageType` refuses password fields and
  uses the native value setter + input/change events (React-safe); submit via `requestSubmit`.
- `browser_bridge/native_host.py` (+ `.bat` using the repo venv): 4-byte LE length + JSON framing; relays to the
  port/token in `~/.eva/bridge.json`; retries every 3 s while Eva is down; exits when the browser closes the port.
- `browser_bridge/install.py` / `install_browser_bridge.bat`: host manifest + HKCU NativeMessagingHosts keys for
  Chrome (also used by Opera, Vivaldi), Edge, Brave, Chromium; `--uninstall` removes them. Only the Eva extension
  ID is allowed.
- `browser_link.py`: `BridgeServer` on 127.0.0.1, random port, random token (hmac compare), file removed on exit;
  tools `page_tabs`, `page_activate`, `page_go`, `page_elements`, `page_read`, `page_scroll`, `page_click`,
  `page_type`. A click is judged by the element's own label (`describe`): money -> `finance_guard`, can't be
  undone -> `system_guard`, both ask via `gate.request`. Passwords are never typed; payment fields / card
  numbers ask. The agent is told when the person's browser is connected (`_context_now`).

Verified: in the container end to end in Chromium (connect, tabs, labelled elements, type + submit verified,
read, password refused, "Delete account" and "Place order" asked and declined with the page unchanged,
`contains`, link click, new-tab `go`, disconnect detected); on Windows the check
`the_persons_own_browser_through_the_extension` passes after one fix: `BridgeServer.stop()` deleted the bridge
file while it was still open, which Windows refuses (now closed first). Eva restarted 15:04: "Browser bridge
listening on 127.0.0.1:...".

**Pending, by the person** (persistent configuration): run `install_browser_bridge.bat`; load the unpacked
extension from `browser_bridge\extension` (Opera: `opera://extensions`, Developer mode, Load unpacked) and confirm
the ID. Then live test: "what tabs do I have open", "read this page", a form fill.

**Still open:** code-signing OV vs EV (money); avatar asset licence; distribution; vendoring three.js/three-vrm
with pinned versions (today a CDN with a floating `@3`); her lower body is not clickable while she sits. The
8-hour live soak restarts from 15:04 (restarts reset it). Deletable diagnostics: `main.py.before-split`,
`tools\read_probe.*`, `uia_profile.*`, `gate_live_test.*`, `D:\etc\eva_test`.

## 55. Phase 2: task cards in the chat window (2026-10-17)

Plan to v1.0, Phase 2: "each multi-step task shows its steps, result, Undo and Replay" so a task is reviewable
without eva.log.

- `core/task_card.py` (pure): `TaskCard` -- created per turn (not for observations or helper notes); `step()` per
  tool call (label from `capabilities.describe`, else the tool name plus a safe naming argument -- never typed
  text, window handles or `#n` ids); `acted` once an INTERACT/MODIFY/DESTROY/EXTERNAL/FINANCIAL/SENSITIVE step runs
  (a turn that only looks gets no card); `finish()` -> done | not_confirmed (`unsupported_claim`) | failed (last
  acting step failed, or "you declined a step") | stopped; `changes` = checkpoint ids this task made; `undo()`
  puts them back newest first (partial failure reported, card not marked undone).
- `core/agent.py`: `on_task_card(card_dict, final)`; emitted from `_trace_step` (every path, including refusals),
  at finish (`_finish_card`), and again when the failed task's replay is kept (`card.replay`). Checkpoint ids are
  taken from `pending_undo["id"]` after `checkpoints.commit`. `_finish_trace` now judges the task from
  `turn.results` (last ACTING step), not the trace's last step.
- `chat_ui.TaskCardWidget` (status mark + colour bar, summary "N steps · time · file changes", title, why, last 4
  steps live while running, "Show N steps" when done; buttons Stop / Undo / Run again / Steps page), height from
  `heightForWidth` (sizing by sizeHint clipped the buttons of a card that grew -- seen on the PC 15:27).
  `ChatPanel.put_card` adds or updates in place; `card_action(name, id)`.
- `chat_store.put_card` / `card`: finished cards kept in the conversation (role "card"), replaced in place on undo
  or a later replay; a card saved mid-task loads as "stopped".
- `eva_app/brain.py`: Stop -> `runtime.stop()`; Run again -> `runtime.submit(task, "chat")`; Steps page ->
  `os.startfile(<replay>/index.html)`; Undo -> `task_card.undo` on a worker thread, card and history updated.

Live on the PC (15:26-15:32): a card grew step by step during "create a text file D:\etc\eva_test\card_test.txt"
(she asked to rename the blocking file; declined) and ended "Didn't finish" with its Steps page; after a restart
the card came back from history; "create a new text file D:\etc\card_test.txt" -> card "Done · 1 file change";
Undo removed the file (journal `undone: true`, eva.log "Task card: undo ... Undone: 1 change put back").

**Found and fixed on the way:** every `tools\windows_checks.bat` run wrote a fake "move of C:\Users\adars\a" into
the person's real undo journal (~/.eva/checkpoints/journal.json) -- 12 such entries; `undo_last_change` would have
picked one before the person's own last change. run_checks now keeps its own journal (temp dir; a check with its
own temporary home keeps its own). The 12 test entries were removed from the PC's journal (backup:
`journal.before-cleanup-2026-10-17.json` beside it). Leftover test file `D:\etc\eva_test` (a 1-byte file named
like a folder) is still there -- deletable.

Check: `task_cards_in_the_chat_window`.

## 56. Phase 2: the first-run welcome; her own windows masked in what leaves the PC (2026-10-17)

Plan to v1.0, Phase 2 ("First-run onboarding"): the first run used to be a tray API-key prompt, then silence.

- `onboarding.py` (pure): `needed()` / `mark("finished"|"skipped")` in ~/.eva/welcome.json (versioned: a new
  `VERSION` shows it again); `steps(key_missing)` -- the key page only without a key; `looks_like_key`,
  `check_key(key, ask)` (one 2-token request; 401/403 explained, 429 = works), `save_key` (encrypted store and
  live: `config.NVIDIA_API_KEY`, `providers.reset_clients()`, no restart); `privacy(cfg)` -- what leaves the PC,
  to whom, how to keep it local, read off the RUNNING configuration (model host, vision host, Google STT or
  phonon, Edge voice, what stays local); `FIRST_TASKS` (what's on my screen / find a file / Downloads plan only --
  none moves or deletes).
- `welcome_ui.py`: 5-6 pages (Hello -- what she does and that she always asks before what cannot be undone or
  costs money; Key; Voice and size -- live voice preview, size needs a restart, offered at the end; Microphone --
  Test shows what she heard (one-shot tap, cancelled on time-out/close), or keep it off; Privacy; Try me -- runs
  the task through `runtime.submit(..., "chat")` with the chat window open so its task card shows). Light theme
  with an explicit palette (dark-mode safe, as settings_ui); radio indicators styled (Windows drew none).
- `eva_app/welcome.py` (installed last): opens 2.5 s after start when `needed()`; tray > Welcome tour;
  `eva_app/mic.py` `hear_once(fn) -> cancel` (what is heard still goes to her); greeting "Let me show you around".

Live on the PC (15:46-15:47): the tour opened after the restart (5 steps: a key is set); microphone test timed
out correctly (nobody spoke); "What's on my screen?" ran with the chat window open; tour marked finished; after
the next restart it did not reappear.

**Found by the live run and fixed:** the privacy page says her own windows are masked from screenshots; they
were masked only in the screen fingerprint (vision cache), NOT in the picture sent to the vision model -- she
described her own chat window. `vision._grab_screen` now paints every own window (SELF_RECTS) grey in a copy of
the picture before it is encoded (`VISION_MASK_OWN_WINDOWS = True`); OCR and the cache keep the real pixels.
Verified with a real image here; on the PC the follow-up question was answered from window titles without a
screenshot, so the masked path has not yet been seen live.

Check: `a_welcome_on_the_first_run`. Windows: 170/170.

## 57. She operates the computer like a person; she hears the whole sentence (2026-10-17 evening)

The person (21:13, with eva.log 12:00-16:13): "EVA still can't handle any complex task -- reuse your computer
use skill into EVA; improve speech to text, she cuts off my mic at the beginning and end sometimes".
Evidence in the log: MakeMyTrip 16:10-16:13 -- `browser_act` pressed the same element six times without
looking, then returned ok; "OTP" heard as "TP", "make the browser full screen" as "American browser...",
"plan a trip" split in two.

**core/operator.py -- `operate_computer(goal)`** (a first version was written by a session at ~16:20 that ended
before testing; finished, checked and live-tested here). The loop Claude uses: LOOK (screenshot with her own
windows painted out + every control of the window in front as a NUMBERED box -- set-of-mark from the UIA tree,
or OCR phrases when a window publishes none) -> DECIDE (vision model, one JSON action) -> ACT through
`agent.dispatch` (the permission gate, trace, task card) -> CHECK (next look; "did NOT change" told to the
model). Actions: click/double/right, type (clicks the box, types, optional Enter), key, scroll, wait, zoom
(next look = that area only, enlarged, for calendars and small labels), open_url (her browser, brought to
the front), open_app, done, ask_person (waits via `wait_for_person`), stuck. Guards: an OTP/PIN the person
never gave is not typed; a press outside the window being worked in is not made; the window being worked in
is brought back if another came in front (21:47: a click meant for the browser landed on Explorer); the same
press three times in six steps -> told once to change approach, then it ends; three unchanged screens end it;
"stop" ends it at the next step; one retry after a vision-model 500. `wait_for_person(say)`: says it, waits
for their words (OTP, login, choice). Prompt: whole multi-step jobs go to operate_computer in one goal.

Also: `browser_act` now tells its model what it already did and whether the page changed, stops after the
same action on an unchanged page, and returns ok:false (not ok:true) when it ran out of steps;
`browser_open` treats a site's self-redirect ("interrupted by another navigation") as loading.
`read_window_text` (typing check) uses one cached UIA read -- typing "Kolkata" into a browser took 28 s;
`tools\read_text_parity.bat` on the PC: 8/8 windows identical, Explorer 1,450 -> 470 ms. Task card: "Didn't
finish" when her own reply says she could not ("I can't see the From field" was shown under "Done").

**Live on the PC (21:40-22:15, "search one-way flights Kolkata -> Ho Chi Minh City, 25 Dec, 2 adults, do not
book"):** run 1 found no browser -> fixed (open_url). Later runs: opened makemytrip.com, set From = Kolkata
(CCU) and To = Ho Chi Minh City (SGN) from the suggestion lists, opened the traveller and date pickers and
moved to December -- then mis-picked calendar days (2, 4, 5 Dec) and overshot adults (3), which led to the
zoom action (not yet seen live). Every stop was honest ("I keep doing the same thing ...", "not finished after
30 steps"); nothing was booked; no login. **Not yet achieved end to end on this site.**

**voice.Endpointer** (STT_OWN_ENDPOINTING): the microphone is read without a break and Eva decides where a
phrase starts and ends -- the 1 s run-up before the voice crosses the gate is always kept (speech_recognition's
`listen(timeout=1)` loop threw it away every second nobody spoke: the clipped first sounds), a short opening
("I think", "open") waits 2 s for the rest, the gate follows the room's quietest moment of the last 3 s
(never below calibration, never above the ceiling), a phrase at the 20 s limit continues unclipped. Capture
and recognition stay on separate threads (_ContinuousListener). On the PC: "Microphone: continuous capture,
44100 Hz, gate 93, run-up 1.0 s". **Not yet confirmed by the person's own voice.**

**Found while testing:** a screen-recording tool that hides windows it may not show (Claude's own computer
use, when Eva's python was not on its list) hid her avatar; the page stopped drawing and the heartbeat watchdog
took it for three renderer crashes and gave up -- she stayed invisible. `_reshow_if_hidden`: a hidden or
minimised window of hers is shown again (no focus taken) instead of reloaded. Eva must be restarted once to
load it (she is invisible on the PC since 23:32).

Checks: `she_operates_the_computer_like_a_person`, `she_hears_the_whole_sentence`. Windows: 173/173 (23:41).
Left open on the PC by these tests: Microsoft Edge and Chrome windows on makemytrip.com (opened by Eva), and
VS Code with tools\uia_tree_parity.py (a mis-click of mine) -- all safe to close.

## 58. Multi-step reasoning: a planner with a ledger; word games played with discipline (2026-10-18 00:00)

The person: "I want her to be able to play games like Wordle like a human would -- take this as general
guidance -- currently she is very poor with multi-step reasoning and tasks; they need to be improved
significantly". What the live runs of section 57 showed: one vision model saw, planned AND pointed, with the
last ten steps as its only memory -- it forgot what was done (Kolkata typed twice), never said what finished
means, could not tell a step that worked from one that only moved the page, and claimed "done" from a guess.

**core/operator.py: planner + eyes.** Each step: the PLANNER (the main language model, no tools) gets the goal,
its own LEDGER (the plan as checked-off sub-goals, the facts learned, what the last step was meant to show),
the steps so far with what each changed, what the eyes last reported, the window's text ROW BY ROW
(`text_rows` from the structure snapshot: a game board reads as its rows) and the numbered controls; it returns
the updated ledger, a status and ONE concrete next step with what will show it worked. The EYES (vision model)
carry out only that step on the marked screenshot (`grounder_prompt`), or say why they cannot -- which goes back
to the planner. "done" is never taken on the planner's word: the eyes must SEE it on the screen, else it is
replanned. After a step that failed or changed nothing, the planner thinks harder (`llm.thinking("medium",
90)`; normal steps stay "low", 45 s). If the planner does not answer, the eyes plan that step alone (the old
behaviour). Settings: `OPERATOR_PLANNER`, `OPERATOR_PLANNER_EFFORT`. The result carries the plan and facts.

**core/wordplay.py: letter-feedback games (Wordle and its kind).** Reads the board's own tile labels ("1st
letter, C, absent"), states what is KNOWN for the planner (pattern of letters in place, letters in the word
with the places they are not, letters out, letters seen twice), and checks a planned guess against all of it:
a guess the board rules out is not typed -- the planner is told why and thinks again (up to twice). It never
suggests a word and looks nothing up (fair play unchanged): choosing the word stays her reasoning. Prompt: a
game played move after move goes to operate_computer in one goal after game_rules / start_game.

Checks (in `she_operates_the_computer_like_a_person`): the ledger reaches the planner; the eyes get exactly the
planned step; a done the screen does not show is replanned harder; the planner failing falls back; the board is
parsed and summarised; BEARD after CRANE/START is refused and DIARY goes through. 

**Not yet run live:** at 00:07 the person was working in the copy D:\eva-avatar\eva-avatar-sarvam (its eva.log
23:53, game_play.py 00:02), so Eva was not started from this copy (two Evas would share the microphone). The
code is deployed to D:\eva-avatar\eva-avatar (md5 verified). Mic note from 23:36-23:44: twenty phrases cut at
the 20 s limit while a video or talk was playing nearby -- expected with continuous sound; only one was
recognised.

## 59. See, then do; hearing measured, not guessed (2026-10-18 00:11)

The person: "we need to do something about speech getting cut off at the start of a sentence, and EVA in most
cases decides beforehand what it needs to do instead of seeing and doing -- that is where the challenge lies in
multi-step reasoning". Section 58's planner was itself a decide-beforehand design (a text model planning from a
report of the screen), so it was replaced the same night:

**core/operator.py -- see, then do.** One look per step, and the step starts from it: the vision model gets the
screenshot with numbered controls, the window's text row by row, the steps so far with what each changed, what
its last step was MEANT to show, and its own notes (facts) from earlier steps; it must first say what is on the
screen now and whether the last step worked, correct its notes from what it sees, and only then choose ONE
action for this screen, with what that action should show (`expect`). Notes and the expected result carry to
the next look. The main language model is no longer asked to plan the job: only after a step that did not work
it is asked, with that same fresh look, why and what to try instead -- advice the next look checks against the
screen (`_think`, medium effort). Word games: what the board has established is in every look; a typed guess
the board rules out is refused before it is typed (`wordplay_check`) and she looks again.

**Hearing, measured.** `voice.Endpointer` now records for every phrase how long the run-up before the voice
was (`lead_ms`) and whether the voice was already there at the run-up's first moment (`clipped_start`, logged as
"the first sound of this phrase may be cut"). `keep_phrase`: the last `STT_KEEP_PHRASES` (20) phrases are kept on
this PC as WAV files with what was recognised (~/.eva/hearing/heard.jsonl) -- so a cut start can be told apart:
missing from the AUDIO (capture) or only from the TEXT (recognition). Setting: Privacy > "Keep the last phrases
she heard"; the welcome's privacy page lists it.

Deployed to D:\eva-avatar\eva-avatar (md5 verified); all checks pass here. Not run live: at 00:18 the person was
working in OpenCode on the PC, so Claude did not take the mouse. Next: start Eva, ask "Open Wordle and play
today's game", and read ~/.eva/hearing after a few spoken requests.

## 60. Eva 2.0, first immediate gains; the main model replaced (2026-10-28 00:30)

Design document: "Eva 2.0 -- Core Rebuild Design" (Claude Docs). Decisions taken by the person: D:\eva-avatar\eva-avatar
is the product (not the sarvam copy); free cloud models for now (the laptop cannot run a local model well).

**Tool groups (core/toolbox.py, config.TOOL_GROUPS).** Every call carried all 136 tool schemas (59,483 characters).
Now 40 core tools go with every call; the rest sit in 12 named groups (windows, screen_detail, web_pages, files_more,
system, schedule, games, body, helpers, recordings, office, self) listed in the prompt with their tool names. She
opens a group with `use_tools` from her own reasoning -- no keyword rules. A tool called from a closed group still
runs (it is named in the index) and opens its group; groups close 3 turns after last use; a game in progress opens
"games" and the person's linked browser opens "web_pages" (live state). Unlisted/new/connected tools count as core.
Measured: about 21,100 characters per call instead of 59,483 (-64%).

**Check before "done" (core/agent.needs_final_look, config.CHECK_BEFORE_DONE).** A success reply after on-screen
steps (surface screen/window/browser) that nothing has looked at since the last one gets ONE nudge: look now and
compare with what was asked (the request is quoted). Not asked when the single step verified itself, when she
looked after it, for operate_computer (it ends on a look) or for file work (reports for itself).

**One request budget per provider (providers.pace / hold / retry_after, config.PROVIDER_RPM {"nvidia": 36}).**
Text, vision and the browser agent all ask pace() first; at the limit it waits (max 30 s) for the oldest request
of the last minute to age out. A 429's Retry-After holds that provider for every caller.

**Benchmark (tools/scorecard.py).** New: false "done" (she said it worked, the check says no), requests (text +
vision + browser), characters per call, `--set=NAME=VALUE` for A/B runs, four multi-step tasks (tidy, largest,
notepad-edit, calculator). History: ~/.eva/scorecard-history-2.csv. Launchers in the app folder: run_gains.bat
(Windows checks + benchmark), run_bench.bat (benchmark only), models_now.bat.

**The main model was gone.** At 00:18 every request to nvidia/nemotron-3-super-120b-a12b returned 404 "Function ...
Not found for account": Eva could not think at all. tools/models_now.py lists what the key is served and tests each
candidate with one tool call (tools/models_now.txt). Working with tools on 2026-10-28: nemotron-3.5-super-vl-120b
(0.7 s), minimax-m3.1-flash (1.4 s), nemotron-3-ultra-550b (11.6 s), nemotron-3.5-lightning-30b, nemotron-3-nano-omni-30b,
nemotron-3.5-bolt-e4b. 404 for this key: kimi-k2.6, mistral-large, llama-3.1-nemotron-*, gemma-3, nemotron-4.
Timed out: deepseek-v4.1-flash, gemma-4-31b. Not tested (past the 30 cap): gpt-oss-20b, glm-5.3, glm-5.3-flash.
MODEL_ID is now nvidia/nemotron-3.5-super-vl-120b-a12b (the vision model, same size; one model sees and acts);
MODEL_FALLBACKS = (minimax-m3.1-flash, nemotron-3-ultra-550b).

**Results on the PC.** Windows checks: all pass (174 here). Benchmark with the new model: 16/16, 0 false "done",
36 model calls, 40 requests, 158 s. The 12 earlier tasks: 98 s and 20 calls (best before: 112 s, 24 calls; the
model changed too, so this is not an A/B -- run `scorecard.bat --set=TOOL_GROUPS=False` for one).

**Hearing, from ~/.eva/hearing (14 phrases, 18 Oct).** 13 of 14 had a full run-up before the voice (185-859 ms);
one clipped start. The mis-heard phrases ("Centre button" for "enter button", "you cannot become that sell") had
complete audio: the remaining start/word errors are RECOGNITION (free Google STT), not capture. Next: a better
free recogniser (to be researched and tested on these kept phrases), not more endpointing work.

Next: A/B run of tool groups; Wordle via operate_computer with the new model; recogniser trial on the kept WAVs;
model trial (glm-5.3, gpt-oss-20b, ultra) through the benchmark.

## 61. Next phase: measured A/B, model trial, a better ear, live Wordle (2026-10-28 00:35-08:05)

Runner: OpenCode desktop on the PC (project D:\eva-avatar\eva-avatar, session "Command runner setup") starts
`tools\job.bat NAME command...` detached; output in tools\jobs\NAME.log ending "=== DONE". Claude reads the
logs through the device link. job.bat/trials.bat now end with `exit` (earlier runs left idle cmd windows).

**A/B, tool groups (same 16 tasks, same model, one run each).** On: 16/16, 0 false "done", 36 calls, 38.5k
characters per call, 158 s. Off: 15/16, 1 false "done" (longform saved a file and said so), 46 calls, 76.9k, 182 s.

**Model trial (tools/models_now.py, then the benchmark with fallbacks off).** 3.5 Super VL 16/16 (158 s);
3 Ultra 13/16 (408 s; the 3 failures were the console-echo crash below); MiniMax M3.1 Flash 10/16 (all 6
failures 429). glm-5.3, glm-5.3-flash, gpt-oss-20b timed out at 40 s. Kept: MODEL_ID 3.5 Super VL;
MODEL_FALLBACKS (Ultra, MiniMax). No model ever reported a false "done".

**Rate limits.** The benchmark alone reached ~35 requests a minute; PROVIDER_RPM now 30. NVIDIA's 429 has no
Retry-After: llm.call now holds the provider 15/30/45 s (every caller), instead of retrying after 1.5 s.

**Console echo crash (core/llm._echo).** The live echo of her words raised UnicodeEncodeError on a cp1252
console (U+202F, U+2011) and killed the turn. The echo can no longer break a turn.

**Hearing: recognition was the cause (tools/asr_trial.py, tools/asr_trial.txt).** The 14 kept phrases through
Google and NVIDIA's free recognisers (Riva gRPC, same key): Google dropped sentence openings the audio held
("go to the top of the browser and ...", "Anyways, ...") and misheard ("Centre button", "become that sell",
"place value", "you sorted"); canary-1b got all of those right and stayed silent on silence (whisper invents
"you"; parakeet "1st", "recommend that cell"). New stt_nvidia.py; STT_BACKEND = "nvidia", STT_NVIDIA_MODEL =
"canary-1b" (Settings > Speech); any failure falls back to Google for a minute. Live path verified
(tools/stt_live_check.py): 0.25 s a phrase (Google 0.88 s). requirements: nvidia-riva-client. Privacy page
names it. Restart Eva to hear with it.

**Live Wordle (tools/live_task.py: the real agent on the real screen; approvals: on-screen yes, anything
destructive/money/secret no).** Seven runs; each failure found a defect, fixed and deployed:
1. She gave the operator only the setup, then played tile by tile and ran out of rounds -> game_play's
   prompt block said "each step ... ONE move" (contradicting the prompt); now "PLAYED BY operate_computer";
   operate() reports "game still in progress" after a setup-only goal.
2. Still drove it by hand -> `_handover_check` (config.ONSCREEN_HANDOFF = 6): after six screen steps of her
   own in one task she is told once to hand the WHOLE task, quoted, to operate_computer. Worked in runs 3-7.
3. "Accept all" cookies -> prompt: decline what is optional, never "accept all" in a goal. Run 2 on: "Reject all".
4. A click on a keyboard key put Q before CRANE ("QCRAN", rejected) -> wordplay.pending reads typed-but-
   unscored tiles ("1st letter, Q" vs "1st letter, C, absent"); the operator refuses to type on top and is
   told to Backspace N times; key actions take "times".
5. Letter-by-letter clicks on the on-screen keyboard -> rule: one type action per word, no box for a board.
6. A click on a blank tile was gated as "can't be undone" -> system_guard matched "empty" in "4th letter,
   empty"; now only "Empty" as the whole label or "Empty (the) recycle bin/trash".
7. A closed-group tool called with guessed arguments -> the error now carries its parameters.
8. Drifted to another site -> rule: stay on the site the goal names. Boards with no tile labels (wordlegame.org)
   -> the eyes report "board" [["CRANE","bbbbb"],...]; guesses are checked against it (SLATE after an all-grey
   CRANE is refused).
Result: run 6 played a full game end to end (6 guesses, 10 actions, 113 s) and honestly reported it unsolved;
every run's card and reply were honest (no false "done"). Run 7 (wordlegame.org) failed on SEEING: the free
vision model described the same board three ways within a minute, and keys went to a blank tab of the test
browser. Open: screen-reading accuracy of the free vision model on game boards; the operator opening new
about:blank tabs. Not solved yet: a Wordle win.

Checks: 175, all pass here and on the PC (tools\jobs\checks.log, 08:05).

## 62. Seeing fixed: the operator's eyes now think; first Wordle win (2026-10-28 08:10-08:45)

**Vision trial (tools/vision_trial.py; six word-game boards with known answers: three real replay screenshots,
three rendered in Wordle's style in tools/vision_boards/).** Nemotron 3.5 Super VL with thinking ON: 6/6 exact,
100% of tiles. The SAME model with thinking OFF -- how the operator called it -- 0/6, 63% (grey rows read green,
typed letters missed). Others: nano-omni (fast) 1/6; llama-3.2-11b-vision 0/6; llama-3.2-90b-vision and gemma-4-31b
time out; gemma-3 and phi-3-vision not served to this key. The model was never the problem; the setting was.
Fix: config.OPERATOR_VISION_THINKING = True (decide_with_vision -> vision._ask(think=True), 75 s deadline).
Cost: 10-90 s a step on the free tier (timeouts and an occasional empty reply under load).

**Operator fixes from the live runs.** Clicks within 40/1000 of each other count as the same click (it clicked an
empty row "to focus it" a few pixels apart each time); rule: a word game takes the keys directly, never click a
tile; the typed-row guard warns once per string, then trusts the eyes (tile labels lag the flip animation).

**Result.** NYT Wordle, today's game: CRANE, GROSS, GRIST -- "You solved with only greens!" (seen on screen).
7 min 10 s, 11 model calls; the card said done and the screen agreed. She stopped at the account sign-up offer.
Note: in two runs she moved from her own browser to the person's Brave on her own reasoning (her automation
browser showed a blank NYT page / an ad page). Legitimate for an assistant, but a test should be told which
browser it may use.

## 63. Her voice from NVIDIA Magpie; duplex voice models reviewed (2026-10-28 08:50-09:00)

**Duplex models (reviewed, not adopted).** Dessa (Block, announced 23 Oct 2026): research preview, no weights, no
API, waitlist. NVIDIA PersonaPlex-7B (on Moshi): weights under NVIDIA's Open Model License, English only, needs an
NVIDIA GPU with 16 GB+, no hosted free API. Kyutai Moshi: CC-BY 4.0, same hardware. None can call tools, and the
laptop cannot run them. Chosen instead: a near-duplex cascade from free parts -- canary-1b ears (section 61),
Magpie voice (below), and her existing stop-when-talked-over.

**Voice trial (tools/tts_trial.py, tools/tts_trial.txt, samples in tools\tts_samples\).** Seconds to the first audio
over four lines (one word to 167 characters): Edge (current) 1.43; Magpie Aria/Mia/Jason ~0.5; Magpie streaming 0.21.
Voices include moods (Magpie-Multilingual.EN-US.Mia.Calm/.Sad/.Angry/...).

**Wired in (tts_nvidia.py).** config.TTS_BACKEND = "nvidia", TTS_NVIDIA_VOICE = "Magpie-Multilingual.EN-US.Aria"
(Settings > Speech). voice.synthesize: Magpie -> Edge -> Windows; a Magpie failure rests it 120 s. 44.1 kHz WAV
through the existing playback helper. Privacy page names NVIDIA. Live on the PC (tools/voice_live_check.py): first
line 1.34 s (connection opening), then 0.47 s; talked over, silent 15 ms after stop_speaking().
Not yet: streaming playback (would bring the first audio to ~0.2 s); moods following her expression.
Checks: 175, all pass here and on the PC (tools\jobs\checks2.log, 09:00). she_is_never_mute now pins TTS_BACKEND="edge".

## 64. Smooth motion and feelings: "more alive, excited to work ... laggy when she moves" (2026-10-28 09:10-09:25)

**Walk measured (tools/walk_bench.py: the real AvatarWidget walks right-left-right; every window step timed).**
Old: a fixed 16 px step every 33 ms -- 29 steps/s, 16 px jumps, interval p95 39.6 ms under load, scene 29 fps,
body and window on unsynchronised timers. New (config.SMOOTH_MOTION): her position is a function of the CLOCK
(`_walk_plan`/`_walk_position`: 0.22 s speed-up, steady pace, slow-down), a PreciseTimer at 16 ms, the stage moved
before the hit box (`_move_together`), the scene drawn at 60 fps while she moves (`setRenderFps`), and the stride
rate matched to her ground speed (`setLocomotion(..., speedPx, heightPx)`: no foot sliding). Measured: 59 steps/s,
7 px steps, p95 23.1 ms under load, scene 58 fps. A late tick lands where she should be, never a jump-then-pause.
Glides (sit/stand) use the same path. The two ~200 ms gaps in each bench run are the pauses between its walks.

**Feelings (core/feeling.py, eva_app/feeling.py, page: setAffect/emote/affectStep).** A continuous feeling --
energy 0..1, mood -1..1, eased, held then easing back to a baseline -- shapes her whole body: upright and a light
bounce on her toes when bright and energetic, a slump and head down when low, a bigger smile with good mood,
livelier glances with energy. Generated reactions -- eager (lean in, hands meet, double nod), delight (hop, arms
lift, head tilt), oops (head shake, look down), curious (deep tilt), proud (chest up, hands to hips) -- each varied
in size, timing and side every time. Sources: spoken to -> eager; working -> keen; task card done -> delight
(bigger for more steps), failed -> oops, not confirmed -> curious; and HER OWN CHOICE: a reply may start with one
feeling in brackets ("[proud] All three are renamed."), taken out before it is shown or spoken (feeling.take) and
sent to her body (agent.on_feeling); any plain word works, known ones are tuned (LEXICON). config.FEELINGS.
Seen on the PC (tools/feel_demo.py photographs each reaction: tools\feel_shots\): all six read clearly at her real
size after the amplitudes were doubled; no page errors; presence layer stayed on. tools/motion_test.js: every
reaction stays under 0.2 rad per frame, ends, and two instances differ.
Checks: 176, all pass here and on the PC (tools\jobs\checks3.log, 09:24); motion checks pass here.
Restart Eva for all of this (voice, ears, motion, feelings).

## 65. Start-up: honest "Listening", hearing that cannot stall, no stand-in figure, no pip each start (2026-10-28 09:40-09:55)

**"Says Listening but actually it is not."** Two causes, both fixed:
1. Recognition ran one phrase at a time and the NVIDIA (Riva gRPC) call had no time limit. In the 09:37 and 09:41
   sessions not one phrase was ever recorded -- not even the capture's switch-on burst, which always is -- so the
   first request had stalled and she was deaf for the rest of the session while showing "Listening". Now
   `voice._with_deadline`: 8 s, then Google answers that phrase and NVIDIA rests 60 s.
2. The gate: speech_recognition's calibration averaged in the microphone's switch-on click (level 64-270 against a
   room of 4), giving a gate of ~95 that "never below the calibration" kept all session. Now `voice.calibrate`
   skips the first 0.4 s and takes the room's median: gate = room x STT_GATE_FACTOR (3), floor STT_MIN_GATE (12),
   ceiling 1200. The capture also skips its own first 0.5 s (another switch-on burst, ~100, that became a phrase).
   Every captured phrase is now logged ("Heard a phrase of 1.5 s (gate 84)") so silence is diagnosable.
The status says "Starting" until the microphone is really read (`voice.on_capture_started` -> speaker_status);
the pill starts as "Starting". tools/mic_probe.py prints the input devices and levels (laptop: Realtek array is
the default; an OPPO Enco headset is also present -- Eva listens to the Windows default input).

**Boot.** Measured: run.bat's two pip runs 3.1 s (every start); Eva itself ~6 s to the microphone. run.bat now runs
pip only when requirements*.txt changed (tools/deps_sync.py; fingerprint in venv\.eva_deps). Live 09:48: start-up
to mic open 5.7 s. **Stand-in figure** (the procedural robot) is hidden while her model loads and shown only if the
model fails to load (`bot.visible`).

**The real cause of "responded 1 time in 15-20" (09:52, tools/mic_compare.py, both inputs at once while he spoke):**
OPPO Enco earbuds ~9000, laptop Realtek array (Windows' default input, which Eva used) 65-114. He speaks into the
earbuds. Now `voice._pick_input_device`: config.STT_INPUT_DEVICE "auto" = a connected headset/earbuds first
(name contains headset, hands-free, buds, airpods, ...), else the Windows default; or part of a device name
(Settings > Speech > Microphone: auto / Headset / Microphone Array). Live 09:55: "Mic calibrated: room 0, gate 12
(device: Headset (OPPO Enco Buds3 Pro+))"; heard "Good to see you, Eva how are you?" and "Can you move to the
right part of the screen?" -> she moved and said "I'm over on the right side now."
Watch: the earbuds' room level is 0, so the gate sits at the floor (12); a 9.1 s phrase was seen -- if phrases run
long or her own voice is picked up through the earbuds, raise STT_MIN_GATE.

## 66. Read aloud when he cannot see; word-less replies dropped; she talks with her body (2026-10-28 10:05-10:15)
From the 09:56-10:01 log ("some issues with its responses ... not much improvement in the avatar feeling more energetic"):
- Long answers (cyclone updates) were only shown, with "Here you go." spoken, although he said he was away. Now
  she may begin a reply with [read aloud] (also [read it aloud], [read out], [aloud], [say all]) when he asks her to
  narrate/read/tell, or cannot see the screen; the whole reply is then spoken (core/longform.take_aloud,
  shape_ex(read_aloud=True), TurnState.read_aloud, runtime). Prompt bullet in core/prompt.py; core/feeling.py
  NOT_FEELINGS keeps the marker from being taken as a feeling.
- A reply of "...?" made Magpie, Edge and Windows all fail and left her on the Windows voice for 5 min:
  agent.clean_reply now returns None for text with no word characters.
- Energy: resting feeling raised (AFFECT_BASE e 0.62, v 0.35). While she speaks, talkStep in web/avatar_3d.html
  generates hand beats (one hand or both, open palms sometimes), nods, head tilts and a sway, fresh each beat
  (0.35-0.6 s, gaps scaled by energy), eased so nothing snaps. Feeling reactions now logged ("Feeling: eager (0.68)").
- Checks: run_checks "fixes from the 10:01 log ..."; node tools/motion_test.js "she talks with her body ..."
  (passed 10/10 runs). All checks passed. Deployed (md5 verified), Eva restarted 10:09:41; live log shows
  "Feeling: eager" on each request and replies working.
Watch: some phrases come back empty from canary (e.g. 6.3 s heard as ''); possibly background speech or her own
voice through the earbuds. Phrases still sometimes cut short ("Perfect. Can you").
