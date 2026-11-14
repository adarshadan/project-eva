# Phase 5 -- Windows as surroundings, and the mind (implementation plan)

Ref: docs/life/LIFE_DESIGN.md Phase 5. Builds on Phase 1 (world sample), Phase 2 (selector, drive_guard).

## 1. Window affordances (visual only)
- world_sensor.py: the 2 Hz window scan also builds a compact list `windows` (max 8: topmost 12 valid, largest
  first): `{id, rect, kind, active, z}`. No titles (coarse kind only). Skips tiny, minimised, off-screen and
  near-full-screen windows; Eva's own are already excluded by the probe; the foreground one is flagged `active`.
- Page (avatar_3d.html): `setWorld` keeps a sanitised copy. Three new BEHAVIOURS: `perch` (sit on the top edge of a
  NON-active window: intent `perch {x, y, wid}`), `lean_wall` and `peek_edge` (stand at a window's vertical edge
  on the taskbar line, lean / peek: intents carry x and the window id). Perch ends with a `get_down` intent.
  Flag `window.EVA_WINDOW_PLAY`. Plans return null (before any random draw) in focus/quiet or without a window.
- drive_guard.py: new kinds `perch`, `lean_wall`, `peek_edge`, `get_down`. The guard looks the window up by id in
  its OWN probe list (never trusts the page's coordinates), refuses the active window (flag or equal rect), a
  target within 150 px of the cursor or the active window (swept path too), focus mode, no room above the title
  bar, windows she cannot reach. Executed with existing body moves: `perch_on` = avatar.sit_on, `walk_to`,
  `get_down` (no new avatar method needed). `check_perch()` runs on every world sample: window gone / moved /
  became active / focus mode / cursor close -> she hops down to the taskbar.

## 2. The mind (mind.py, stdlib; model call on a worker thread)
- `Mind.step(snap)` is called by a 5 s GUI timer: applies a finished result, observes events (person returned,
  long focus ended, music started), maybe starts ONE call. Hard limit: >= max(60, EVA_MIND_MIN_SECONDS) between
  calls, whatever events do; regular cadence random 60-120 s; no call while busy; 120 s rest after an error.
- Summary: ~10 lines built from a whitelist (time of day, window KIND only, focus/typing/idle, music, recent
  event phrases, her drives). No titles, clipboard or file names can reach it (tested with poisoned input).
- Reply JSON `{mood, intention, say}` parsed defensively; mood must be in core/feeling LEXICON, intention in a
  fixed list; anything else is ignored. Mood -> Feeling.felt; intention -> page `setIntention(name, ttl)` which
  multiplies selector weights for ~4 minutes (INTENT_BIAS table; cannot unlock anything focus mode forbids).
- `say` only if EVA_MIND_SPEAK, not focus, not a call, not typing, not busy, person present, >= 10 min since the
  last mind line; checked at delivery time with a fresh snapshot, otherwise dropped.
- Model: config.EVA_MIND_MODEL or READER_MODEL, EVA_MIND_TIMEOUT s, like core/reader.py. Fails silently.
- Flags: EVA_WINDOW_PLAY, EVA_MIND, EVA_MIND_SPEAK, EVA_MIND_MIN_SECONDS (+ EVA_MIND_MODEL, EVA_MIND_TIMEOUT).

## 3. Tests
run_checks.py (appended to the last list): guard vetting, sensor list, mind rate limit / parsing / speak gating /
privacy / non-blocking. tools/motion_test.js: new behaviours only with a suitable non-active window, never in
focus, intention bias shifts the mix, no snaps.
