# Eva -- architecture review and overhaul plan (2026-11-15)

Reviewer: Claude (Fable). Basis: repository at 6f16a1f, eva.log 2026-11-14 12:52 .. 11-15 11:31 on the PC.
Audience: whoever implements next (Gemini). Each item has the evidence, the change, and how to prove it.

## What went wrong on 2026-11-14 (so it is not repeated)
tools/update_main.bat (written by Claude) set aside coder.py and core/reader.py on EVERY run. The first run
was right (they were untracked trial copies); the second run moved the now-tracked files away, and Eva could
not start (13:23 "No module named 'coder'"). There was no health check and no rollback. See A1.

## Why fixes do not add up (the diagnosis)
1. Behaviour lives in prose. core/prompt.py is 20 KB and grows with every failure (latest: arithmetic,
   fares, "never close_window on a browser"). 142 tools are registered. A free model cannot hold this; rules
   are followed by luck. A rule the code can enforce must be code.
2. One reactive loop does everything. core/agent.py (60 KB) asks the model "what next?" turn by turn. There
   is no plan, no step state, no check of the outcome by observation. Multi-step work fails or is reported
   done when it is not (10-28 "minimised" that was not; 11-15 closed the whole browser for "close this website").
3. Two body systems fight. The model drives canned clips (play_animation) while the life layer (drives,
   attention, body, dance) runs on its own. "Dance to the beat" played the old clip, not the generated beat
   dance. Task movements leave her off the ground and the life layer then refuses 15 moves ("not_on_ground").
4. Every part shares one free allowance with no priorities: conversation, operator, vision, reader, mind,
   skill review, speech. Result: "still busy", 429, 5-20 s per spoken turn ("a bit more faster": 10-20 s each).
   Patches read private state (providers._recent) instead of a scheduler.
5. She answers everything she hears ("Good to see you both", "No.", "Well then I'm safe"). There is no notion
   of who is being addressed or whether a turn is finished. canary-1b still writes English in Devanagari
   (10 times); the measured better recogniser (parakeet) is only the retry.
6. Engineering base: run_checks.py is one 424 KB file, 15 minutes, with many assertions on SOURCE TEXT (they
   break on refactor and prove little). The 23-task scorecard, the only real measure of multi-step ability,
   has never been run. Monoliths: avatar_3d.html 159 KB (9 *Step functions writing one pose object),
   desktop_control.py 108 KB, avatar_ui.py 100 KB. Three AI contributors commit straight to main.

## The overhaul, in order (do not reorder: each makes the next safe)

### A1. Safe releases and a real test pyramid  [first; small]
- Releases: never update in place. `releases/<commit>/` + a `current` pointer (junction). Update = clone to a
  new folder, run `python main.py --selftest` there (imports, tool bindings, page loads headless, 5 s run),
  switch the pointer, start; if she is not healthy within 60 s (heartbeat file), switch back automatically.
- Tests: split run_checks.py into tests/unit/<module>_test.py (pytest, whole suite < 60 s), tests/scenario/
  (agent with a scripted model), and the scorecard as acceptance. Delete source-text assertions as their
  module gets behavioural tests. CI (GitHub Actions, windows-latest) runs unit + scenario on every PR.
- Rule for all contributors: branch -> PR -> CI green -> scorecard not lower -> merge. No direct pushes to main.
- Prove: a release with a deliberate import error rolls back by itself; unit suite < 60 s.

### A2. A task engine instead of a chat loop  [the multi-step fix]
- core/task/: Task = goal + ordered Steps; Step = {intent, tool call(s), expected observation, status}.
  States: UNDERSTAND -> PLAN -> ACT -> VERIFY -> (REPAIR | NEXT) -> REPORT. The model fills PLAN and REPAIR;
  code owns the state machine, retries and the budget.
- VERIFY is by observation, never by the model's word: each tool declares a `verify(args, before, after)`
  probe (window list, file exists, tab list, clipboard, UIA value). "done" is only said from VERIFY results.
- Procedures: common jobs as code, not model improvisation (open site, close MY tab, scroll to read, play/
  pause, save file as, find file). The model picks a procedure and its parameters.
- Ownership ledger: everything Eva opens (tab, window, file, process) is recorded. Closing something she
  did not open is a destructive action -> approval. This replaces the prompt rule about browsers.
- Prove: scorecard (23 tasks) baseline today, then >= 20/23 with 0 false "done"; a scenario test where the
  model says "done" but the probe disagrees -> she repairs or reports honestly.

### A3. Reflexes and a request scheduler  [latency and quota]
- core/reflex.py: a local intent router BEFORE the model for closed commands (stop, faster/slower, louder,
  play/pause, sit/stand/come here, yes/no to a pending question, thanks). Pattern + slot rules first; later a
  tiny local classifier. Target: < 300 ms from end of speech to action for these.
- core/scheduler.py: ONE gate for every model/vision/speech request with priority classes: 1 conversation,
  2 active task, 3 perception for the task, 4 background (mind, skill review, reader pre-reads). Per-class
  budgets inside the provider limit; class 4 only spends what is left over and is dropped, not queued.
  Remove pacing code from providers.py / learning.py / mind.py; they all call the scheduler.
- Streaming replies: speak the first sentence while the rest is generated.
- Prove: log line per request with class and wait; no class-1 wait > 2 s in a 30-minute session; zero 429.

### A4. One body, one director  [the "alive" fix]
- body/director.py owns the body. Sources submit INTENTS with a priority: person command > task acting >
  speech gestures > autonomous life. It arbitrates, blends and hands back when a higher source ends.
- The model no longer plays clips. Its body tools become intents: go(place), sit(spot|window), dance(music|
  style), express(feeling), look(target), gesture(kind). Clips are one of the director's materials.
- Invariants in code: never left in mid-air (returns to ground or a ledge), never over the cursor/caret,
  focus mode. "not_on_ground" stops being a refusal and becomes a recovery behaviour.
- One audio-out listener: music.py and core/internal_audio.py both capture loopback -> a single audio service
  with two consumers (beat tracker, transcription).
- Page: split avatar_3d.html into ES modules (senses, attention, drives, pose layers, dance, cloth, face) with
  ONE layer stack (base pose -> locomotion -> behaviour -> gesture -> breath/noise -> physics), each layer a
  pure function of state. motion_test.js imports the modules instead of regex-extracting the page.
- Prove: "dance to this" follows the beat (phase error test); after any task she is on the ground within 3 s;
  no layer writes the pose object directly (lint).

### A5. A conversation manager  [hearing and manners]
- State machine: IDLE / ADDRESSED / IN_DIALOGUE(until 20 s quiet) / BUSY. A phrase is acted on only when
  addressed: her name, a reply inside an open dialogue, or a command-shaped phrase while she is looking at the
  person. Otherwise she may look up, never answer.
- Recogniser: parakeet as primary for English (trial 2026-11-10), canary only on request for other languages;
  partial results drive end-of-turn (semantic, not a fixed 1.3 s pause); barge-in cancels speech and the task
  step cleanly (21 "cut short" warnings today).
- Prove: replay of the kept phrases + a side-conversation recording -> zero replies to unaddressed speech.

### A6. Prompt diet and tool surface  [after A2-A5 make it possible]
- System prompt <= 6 KB: who she is, how she speaks, the task protocol. Everything else is enforced by code
  (A2 procedures, ledger, A4 invariants) or lives in the description of the one tool it concerns.
- Tools shown per context: a core set (~15) plus the group of the chosen procedure; never all 142.
- Prove: scorecard not lower with the short prompt; prompt size check in CI.

## What NOT to do
- No more rules added to the prompt for single incidents. No new feature flags without a test. No model
  change without running the scorecard before and after (MiniMax was made primary on 11-14 without one).
- Do not start A4 before A1: the body refactor touches the largest files and needs fast tests and rollback.

# Part B -- the body worth having (target architecture, not fixes)

Design question: if a mind had to live in this body, what would it need? Part A is the skeleton that stops
her breaking. Part B is the set of organs. The governing idea: THE MIND IS SLOW AND RARE (a free cloud
model, seconds per thought, ~30 thoughts a minute at best), so the body must perceive, move and react on its
own, locally, and consult the mind for MEANING, never for motor control.

## B0. Nervous system (everything else plugs into this)
- Three speeds: REFLEX (< 100 ms, local rules) -> SKILL (0.1-2 s, local procedures, "muscle memory") ->
  DELIBERATION (seconds, the model). A request falls to the slowest tier only when the faster ones cannot
  handle it. Today everything goes to deliberation.
- One event bus (in-process, typed events: heard, saw, touched, changed, felt, wants). Senses publish; the
  world model, the director, the task engine and the mind subscribe. No module calls another's internals.
- One WORLD MODEL: a live, queryable picture of the desktop and the person -- windows, their controls and
  text, what changed in the last minute, what the person is doing, what is playing, where she is. Every
  layer and every prompt reads from it. It is her working memory of the present.

## B1. Eyes -- always open, cheap, structured
- Today she is blind between tool calls and each look is a 5-10 s cloud request.
- Continuous local vision: UI Automation / accessibility tree diffs, window events (SetWinEventHook), screen
  change detection (frame differencing on a downscaled capture), local OCR only on regions that changed.
  This feeds the world model at a few Hz for almost no cost.
- The cloud vision model is the fovea: called only for what structure cannot tell (a picture, a game board,
  a canvas), on a cropped region, with the world model's text as context.
- Prove: "what just changed on screen?" answered from the world model with no model call; a new dialog is in
  the world model within 300 ms.

## B2. Ears -- continuous, and aware of who is speaking
- Streaming recognition with partial words (so she can nod, look up, and know when a turn ends).
- Source separation by channel: microphone (the person) vs loopback (what the PC plays) vs her own voice
  (subtracted, so she never hears herself or a video as the person).
- Beyond words: tone (calm, annoyed, hurried -> her manner), sound events (notification, call ringing,
  music starting/stopping), silence as a signal.
- Prove: a YouTube voice never triggers a reply; her own speech is never transcribed; an annoyed "no, STOP"
  halts within 300 ms.

## B3. Hands -- precise, fast, with a sense of touch
- Act through structure first: app APIs and accessibility (UIA invoke/value patterns, browser DOM via the
  extension), keyboard next, pixels last. Pixel clicking is the wheelchair, not the hand.
- Touch feedback on every action: what did the thing I touched do? (value changed, window appeared, focus
  moved). No feedback within the expected time = the action failed, immediately, without asking the model.
- Motor skills: parameterised procedures compiled from demonstrations (watch.py) and from successful tasks,
  stored with their preconditions and checks. Performed at the SKILL tier; the mind only chooses them.
- An undo journal: every change she makes records how to reverse it (file versions, closed tabs' URLs,
  moved windows' old places). "Undo that" always works for her own actions.

## B4. Proprioception and interoception -- knowing her own state
- Body: where she is, what she is touching, what she is in the middle of.
- Inner: energy = remaining request budget; fatigue = current latency; pain = errors and failed checks;
  health of each sense (mic level, recogniser up, vision up). All in the world model.
- She adapts and says so honestly: low budget -> shorter thinking, more reflex; a sense is down -> "I cannot
  hear you well right now" instead of guessing.

## B5. Memory and continuity of self
- Working memory = the world model + the current task. Episodic = what happened, with whom, how it went
  (searchable, summarised nightly). Semantic = facts and preferences of the person and the machine.
  Procedural = skills. Today these are scattered (memory.db, skills, chat history, HANDOFF).
- She wakes knowing yesterday: what was left unfinished, what the person was annoyed by, what worked.
- Corrections become lasting behaviour through memory and procedures, not through new prompt text.

## B6. Voice, face and presence -- one expression system
- Speech that starts in < 1 s (streamed), can be interrupted mid-word, uses back-channels ("mm", "okay") from
  the reflex tier, and carries the feeling (pace, energy) the director holds.
- Face, gaze, gesture and voice driven from ONE feeling state, so they never disagree. Lip sync from audio.

## B7. Initiative -- a being acts, a tool waits
- From the world model she notices: a download finished, an error dialog the person has stared at for a
  minute, battery low, a meeting in five minutes, the same manual steps done three times (offer to learn it).
- She offers, briefly and rarely, by the manners in A5; she never acts on the person's things unasked.
- Reach: phone notifications for approvals and news when the person is away; connectors (calendar, mail).

## B8. Growth
- Every task leaves a trace: outcome by observation, time, model calls. A nightly self-review (idle hours,
  class-4 budget) turns failures into repaired procedures and runs the scorecard as her fitness measure.

## Order of building (after A1-A3)
B0 (bus + world model) -> B1 eyes -> B3 hands -> B2 ears -> B4 -> A4/B6 body and expression -> B5 -> B7 -> B8.
B0 and B1 change the most: once she sees continuously and structurally, tasks, manners and aliveness all
get a real foundation instead of rules.

## Honest limits
A free cloud model and an i5 laptop cap how fast and how deeply she can THINK. They do not cap how well she
can sense, move and react -- that is local engineering. This plan spends the effort there for that reason.
