# Phase 3 -- A body that moves like a person (implementation plan)

Ref: docs/life/LIFE_DESIGN.md Phase 3. I own how the body MOVES; behaviour selection (Phase 2) is untouched.
The VRM (VRoid AvatarSample_B) has VRM0 spring bones: 11 groups (Hair x8, Skirt, CoatSkirt/TopsUpperArm, Bust),
so three-vrm's `vrm.update(dt)` (already called every frame in `tick`) already simulates hair and skirt.
What was missing: Eva never moves through the page's world (the Qt window moves, not the avatar), so walking
gave the hair no inertia, and nothing blew. So the cloth work is: drive the existing spring bones, do not replace them.

## Flags (config.py, default on), pushed by `body_life.page_flags_js()` from avatar_ui `_push_gesture_settings`
`EVA_BODY_LIFE`, `EVA_CLOTH_PHYSICS`, plus tuning `EVA_CLOTH_STIFFNESS` (x model value), `EVA_CLOTH_DRAG`,
`EVA_CLOTH_BREEZE`. Page: `window.EVA_BODY_LIFE`, `window.EVA_CLOTH_PHYSICS`, `window.EVA_CLOTH_TUNE`.
With `EVA_BODY_LIFE === false` the pose code is byte-for-byte the old arithmetic (checked against golden numbers).

## Page (web/avatar_3d.html, all inside the region motion_test.js slices)
1. `bodyLifeApply(frame, now)` runs first in `writePose`, on the final channels (after controller + presence):
   - Followers: head/chest/spine channels pass through slightly underdamped second-order followers
     (head fastest, chest, spine slowest): overshoot-and-settle after every sizeable move, and head-leads /
     torso-follows lag. A causal ANTICIPATION kick (small counter-move) fires when a channel starts a sizeable
     move from calm; walk start leans back first, walk stop leans forward and settles.
   - Echo: transient (fast minus slow low-pass of the head) added to spine/chest = follow-through.
   - Breathing: phase-integrated, inhale quicker than exhale, rate and depth rise with `affect.e`; chest + shoulders.
   - Noise: stateless 1-D Perlin (hash of the lattice index; nothing stored, never repeats) on spine/chest/head/arms;
     scaled down in focus mode and while walking.
   - Weight shift when standing: hips sideways + pelvis roll + spine counter-roll + thighs counter-rotated so the
     feet stay planted; the unloaded knee softens. Underdamped spring, new target every 4-9 s.
   - Walk: lateral hip sway and pelvic yaw on the stride phase; stride waveform made trapezoid-like (rounded
     triangle) so the stance foot sweeps back at constant speed = better foot planting (a true IK plant would need
     the ground speed per frame; `walkRate` already matches stride to ground speed).
   - `faceStep` replaces the facing exponential with a lightly underdamped spring + tiny counter-turn.
2. `clothStep(dt, t)` before `vrm.update`: each spring joint gets gravity = its own gravity + an external force:
   inertia (-acceleration of her walking, from `setLocomotion`), drag (-velocity), and a breeze (smooth noise gusts);
   skirts/coat take less, bust none. Stiffness/drag scaled by the tuning. Originals saved, restored when off.
   No spring bones (another model) -> fallback spring-dampers on bones named hair/skirt/ribbon/tail/cape/coat.

## Tests
- tools/motion_test.js: idle moves slightly but bounded; no snaps after anticipation/settle (gestures, walk start/stop,
  activity switches, turns); noise does not repeat over 60 s; breathing scales with energy; weight shift keeps feet
  planted; walk sway; cloth force from acceleration/breeze, restored when off, bust untouched, fallback; flag off
  == golden numbers recorded from the pre-Phase-3 code; a 60 fps run for the cost bound.
- run_checks.py: `body_life.page_flags_js` honours the config flags, the page reads exactly those names,
  config defaults are on. Registered in the last `for label, fn in [...]` list.
