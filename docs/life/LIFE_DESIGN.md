# Making Eva feel alive -- design (2026-11-14)

Goal: Eva is a digital being, not an NPC playing clips. Her body is driven every frame by a few systems that
run continuously and interact, so behaviour emerges and never repeats exactly. Personality: inquisitive,
bubbly, friendly, welcoming, playful but respectful. She moves around freely but never gets in the way, and
is always eager to help.

Hard rules (all phases)
- Never click, type into or move the person's windows on her own. Using windows as scenery is VISUAL only.
- Never cover what the person is working on: keep clear of the cursor, the caret and the focused window's
  working area; when they type fast, are full-screen or in a call, she goes small and still ("focus mode").
- Cheap: everything below the "mind" layer runs locally at frame rate, no network. The laptop is an i5 10th
  gen without a GPU: no per-frame Python work heavier than a few ms; the page must stay at 60 fps.
- Every feature is behind a config flag (config.py, default on) so it can be switched off.
- Existing behaviour and checks keep working: `node tools/motion_test.js` and `python run_checks.py` pass.

Where things live today
- web/avatar_3d.html: the body. `presenceStep` (eyes/head follow the cursor, micro-saccades), `affectStep`
  (energy/mood posture), `talkStep` (gestures while speaking), `life` (blink, mouth), activities
  (`setActivity`), walk (`_walk_plan`), clips. `writePose` writes bones; VRM look-at drives the eyes.
- avatar_ui.py: Python -> page bridge (runJavaScript: set_affect, emote, set_activity, setBodyState, walk_to).
- idle_behavior.py: pynput input listeners, idle time, the old scripted idle routines.
- eva_app/feeling.py + core/feeling.py: feelings from events (eager, delight, oops, curious, proud).
- tools/motion_test.js: the page's motion run headless (no snaps, bounded, varied).

## Phase 1 -- Senses and attention (biggest visible gain)
Python gathers a compact "world" ~8 times a second and sends it to the page (`setWorld(json)`):
cursor x/y and speed, the foreground window's rect and title kind (editor, browser, video, game, call),
typing rate (keys/min, last 3 s), seconds idle, full-screen flag, a new window or notification appearing
(rect + time), screen size, where Eva is. A page-side ATTENTION system scores candidate targets every frame
(the cursor when it moves, a new window, the caret while typing, the person (camera) when spoken to or idle,
a random "curious" spot when bored) with salience that decays; it picks one with hysteresis and drives eyes
-> head -> upper body (eyes fast, head slower, torso slowest). Focus mode: typing > 120 keys/min, full-screen
or call -> she glances only rarely and stays still. Acceptance: her gaze lands on a new window within 300 ms;
no snapping (motion_test); she looks at the person when spoken to; focus mode measurable in a test.

## Phase 2 -- Inner state and choosing what to do (replaces scripted idle)
Drives in [0,1] that rise and fall on their own: energy (time of day, activity), curiosity (novel things
seen), boredom (nothing happening), social (time since the person talked to her), focus-respect (the
person's focus). Personality = rates and weights (one table). A UTILITY selector every 2-6 s scores
parameterised behaviours against drives + world and picks one with softmax noise: wander to a spot, sit on
the taskbar, lean on a window edge, peek around a window, stretch, yawn, look around, hum/sway, wave when the
person returns, settle in a corner (focus). Each behaviour has its own parameters drawn fresh each time.
Acceptance: over 10 simulated minutes no behaviour sequence repeats; respect rules hold (never within N px of
the cursor/caret; still during focus).

## Phase 3 -- A body that moves like a person
Weight shift between feet, foot planting during walks, breathing scaled by energy, overlapping action (head
leads, shoulders follow), anticipation and settle on every move, Perlin micro-motion. Hair and clothes:
spring physics (the VRM's spring bones if present, otherwise spring-dampers on the cloth/hair bones) driven
by her own acceleration plus a light breeze. Acceptance: motion_test bounds; visual check with
tools/feel_demo.py screenshots.

## Phase 4 -- Listening to music and dancing
Capture what the PC plays (WASAPI loopback, no microphone), onset/beat tracking on CPU (tempo, beat phase,
loudness, low/high energy) ~20 Hz to the page. Dance is generated: bounce and step on the beat, arm and hip
patterns chosen by energy and section changes, intensity follows loudness; she only dances when music plays
and she is not in focus mode (or asked). Acceptance: beat phase error < 60 ms on a click track; no dance on
speech/silence.

## Phase 5 -- Windows as surroundings, and the mind
Window rects become affordances: title bars = ledges to sit on, edges = walls to lean on, gaps to peek from;
she walks there, never over the work area. Mind layer: every 60-120 s (or on notable events) the language
model gets a 10-line summary (what the person is doing, time, recent events, her drives) and returns
intentions + mood + an optional short line (spoken only when the person is not focused). It never moves the
body directly. Acceptance: one mind call per minute at most; respectful lines only.
