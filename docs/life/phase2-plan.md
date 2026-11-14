# Phase 2 -- Inner state and choosing what to do (implementation plan)

Ref: docs/life/LIFE_DESIGN.md Phase 2. Builds on Phase 1 (`setWorld`, `attentionStep`, `presenceStep`).

## Where things live
- **Page JS (web/avatar_3d.html, inside the motion_test slice, after `attentionStep`)**: the drives, the personality
  table, the utility selector and the pose of the behaviours that need only her body. Per-frame cost: five
  multiply-adds for the drives; the selector scores ~12 behaviours once every 2-6 s.
- **Python (`drive_guard.py`, new, stdlib only)**: the only place that can move her window. The page cannot act
  on the desktop; it queues *intents* (`wander`, `sit`, `corner`) that Python polls (`takeDriveIntents`, 4 Hz,
  GUI-thread timer in eva_app/life.py), VETS against the real cursor / foreground rect / body state, and
  executes with the existing `avatar.walk_to` / `sit_here` / `stand_up`. Python reports back
  (`driveResult`) so the page ends the behaviour early when refused. Two independent checks of the respect rules.
- **Old scripted idle** (`life.py` `_behaviour`/`_bored`/welcome wave): gated by `Life.drives` (default False so the
  class and its existing check are unchanged); eva_app/life.py sets it to `config.EVA_DRIVES`. With drives on the
  old random picks are skipped; nap/wake, video-watching and patrol (functional, not idle theatre) stay.

## Drives (all clamped to [0,1])
`energy` (time of day, falls with exertion, recovers when settled), `curiosity` (new windows / motion, falls
when she looks), `boredom` (nothing happening; also floored by Python's 0..3 level), `social` (time since spoken to;
jumps when the person returns), `focusRespect` (eases toward 1 in focus mode / fast typing). One `PERSONALITY` table
holds rates and weights (bubbly, inquisitive, friendly, playful, respectful).

## Selector
Every 2-6 s, only when she is idle-like (activity idle, no clip/gesture, not walking, not held), score each eligible
behaviour = weight * drive terms * personality + novelty bonus; pick by softmax (temperature from a table) with a
recency penalty. A novelty memory excludes any name-triple already used this session when an alternative exists, so
no length-3 sequence repeats. Parameters are drawn fresh in `make()`.
Behaviours: wander, sit (settle), lean, peek, stretch, yawn, look_around, hum_sway, wave_return, corner_settle,
stay_still. Gestures reuse `PROC` (`p_stretch_up`, `p_yawn`, `p_look_around`, `p_hum`, `p_wave`); lean/peek/
settle/stay_still are eased offsets (`behaviourPose`), no new animation system. Gaze for peek/look uses a `drive`
attention candidate.

## Respect rules
`safeSpot`: her swept rect (start..target) must stay >= 150 px from the cursor and from the foreground window rect
(`EVA_CLEAR_PX`); leans lean AWAY from the cursor. Focus mode (Phase 1 `P.focus`): selector picks only
`corner_settle` (once per episode, if a corner is clear) and `stay_still`. Python re-vets every intent and aborts a
walk if the cursor closes within 150 px. Nothing here clicks, types or moves a person's window.

## Config
`EVA_DRIVES = True`, `EVA_DRIVE_CLEAR_PX = 150` (config.py), pushed to the page with `_push_gesture_settings`.

## Tests
- tools/motion_test.js: drives stay in [0,1] over 10 simulated minutes (incl. events); no name-triple repeats and
  >= 8 distinct behaviours; no pose snaps across the run; focus mode yields only corner/still behaviours and no
  intent other than `corner`; cursor/window respect (no intent whose spot is within 150 px); behaviours produce
  distinct params; flag off => nothing happens.
- run_checks.py: `drive_guard` check (vetting, focus, refusal reasons, old routines gated, flag defaults).
