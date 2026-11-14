# Phase 1 -- Senses and attention (implementation plan)

Ref: docs/life/LIFE_DESIGN.md Phase 1. Only these files change. Reuses `presenceStep`, `affectStep`,
`life`, `config.py` and the avatar_ui bridge.
## 1. Python: sampling the world
### idle_behavior.py (typing + idle, existing listeners)
Keep the pynput listeners and `_last_input_ts`. Add `_key_ts = collections.deque(maxlen=60)`;
`def _on_key(*_)` calls `_touch_input()` then appends `time.time()`; `start_input_listeners()` uses
`keyboard.Listener(on_press=_on_key)` (mouse unchanged). Add
`def get_typing_rate(now=None, window=3.0) -> float` -> keys in the last `window` s scaled to keys/min
(`n * 60 / window`), 0.0 if none. O(60). `get_idle_seconds()` is reused unchanged.
### world_sensor.py (new; pure stdlib + config, no Qt, no pygetwindow at import)
- `KINDS = ("editor","browser","video","game","call","notification","other")`
- `def classify_title(title: str) -> str`: case-insensitive keywords, checked in order call
  (zoom/teams/meet/webex/skype/discord) -> video (youtube/netflix/vlc/mpv) -> game -> browser
  (chrome/edge/firefox/opera) -> editor (notepad/vim/code/word/excel/...) -> "other".
- `class WorldSensor:` `def __init__(self, probes, clock=time.monotonic)`; `def sample(self) -> dict`.
  `probes` is read-only and duck-typed, so checks pass fakes: `cursor() -> (x,y)`,
  `foreground() -> {"title": str, "rect": (l,t,r,b)|None}`, `screen() -> (w,h)`,
  `eva_rect() -> (l,t,r,b)`, `idle_seconds() -> float`, `typing_rate() -> float`,
  `windows() -> [(hwnd, title, (l,t,r,b))]` (person's titled windows only).
- `sample()` keeps `_last_t`, `_last_cursor`, `_seen` handles, `_new` (cleared after 3 s). Cursor speed
  `hypot(dx,dy)/dt` clamped 0..5000 px/s; `full` when the foreground rect covers >=95 % of a monitor
  (same rule as `idle_behavior.is_fullscreen_app_active`); window scan only every 0.5 s (2 Hz) --
  an unseen handle becomes `new = {"rect":[...],"at":now,"kind":"notification" if w<600 and h<300
  else "window"}`; `focus = typing_rate > config.EVA_FOCUS_TYPING_KEYS_PER_MIN or full or kind=="call"`.
### eva_app/life.py (wiring; GUI-thread timers already live here)
Add `class WorldProbes` beside `LifeProbes`, reads only: `QCursor.pos()`,
`QApplication.primaryScreen().availableGeometry()`, `avatar.geometry()`, idle/typing from idle_behavior;
`foreground()` = `dc.get_foreground_app()` + Win32 `GetWindowRect` via ctypes (exactly as avatar_ui.py:1961);
NOT `dc.get_window_rect` (scans every window). `windows()` = one `gw.getAllWindows()` pass, skipping
empty titles, calling `dc.is_own_window` only for unseen handles.
In `install(ctx)` after `attention_tick`:
```
sensor = world_sensor.WorldSensor(WorldProbes())
def world_tick():                      # GUI thread
    if not config.EVA_WORLD: return
    try: avatar.set_world(sensor.sample())
    except Exception: log.exception("world sample failed")
world_timer = QTimer(); world_timer.timeout.connect(world_tick)
world_timer.start(int(1000 / max(1, config.EVA_WORLD_HZ))); app._eva_world_timer = world_timer
```
**QTimer, not a thread**: `QCursor.pos`, widget geometry and `runJavaScript` must run on the GUI thread.
Cost: GetWindowText/GetWindowRect ~tens of us, one 60-entry deque scan; the only heavy read is the
2 Hz `getAllWindows` (~1-3 ms for ~40 windows), i.e. <3 ms/s on average. If profiling disagrees, scan
at 1 Hz first.
### avatar_ui.py (bridge, same pattern as set_affect)
`def set_world(self, world: dict)` -> `self.view.page().runJavaScript("if (window.setWorld) setWorld(" +
json.dumps(world) + ");")`. Extend `_push_gesture_settings()` with
`window.EVA_ATTENTION = <config.EVA_ATTENTION>` and `window.EVA_FOCUS_MODE = <config.EVA_FOCUS_MODE>`.
Titles are trimmed to 80 chars; payload ~350 bytes at 8 Hz (2.8 KB/s).
## 2. World JSON (exact shape; pixels = screen px, ints)
```
{"t": 1234.5,                                  // monotonic s, debug only
 "cursor": [x, y], "speed": 312.0,             // px/s
 "window": {"rect": [l,t,r,b], "kind": "editor", "title": "notes.txt - Notepad"},
 "keys": 84.0,                                 // keys/min, last 3 s
 "idle": 2.4,                                  // s since any mouse/key input
 "full": false,
 "new": {"rect": [l,t,r,b], "at": 1234.5, "kind": "window"|"notification"} | null,
 "screen": [1920, 1080],
 "eva": [l,t,r,b],                             // her hit-box rect
 "focus": false}
```
The page decays from **receipt time**, so Python/page clocks never need to agree.
## 3. Page: web/avatar_3d.html
All new code goes between `function T(` and `function playClip(`, inside the presence block, so
motion_test's slice loads it. No DOM at load time; guarded entry points.
- `let world = null;` `window.setWorld = function (w)`: validate `w && Array.isArray(w.cursor) &&
  Array.isArray(w.eva)`, copy, set `world.receivedAt = performance.now()/1000`, and remember
  `world.newSeenAt` when `w.new.at` changes; malformed input returns silently.
- `function aimPoint(P, tx, ty)` -> `{yaw, pitch}`: `headX/headY` from `world.eva` (centre x,
  top+15 % y, the `attend` convention); `dx=(tx-headX)/(screen[0]/2)`, `dy=(ty-headY)/(screen[1]/2)`;
  `yaw=clamp(dx*32,±40)`, `pitch=clamp(-dy*20,±25)` degrees.
- `function attentionStep(P, now, dt)`: no-op if `!world` or `window.EVA_ATTENTION === false`.
  Candidates: `cursor` (speed > 40 and seen < 0.4 s; salience `min(1,.45+speed/1200)`), `new` (1.0),
  `caret` (`keys >= 20`; target = foreground rect centre at 2/3 height; `.55+min(.2,keys/600)`),
  `person` (`now-P.spokenAt < 3` from `acknowledge()`, or `idle > 20`; .5), `curious` (only when
  `P.boredom >= 1` and nothing else > .2; `.28+.07*boredom`, random point reseeded 2-4 s).
  Per-second decay: cursor 1/1.2, new 1/0.8, caret 1.0, person 2.0, curious 3.0.
  Hysteresis: switch only if `best.salience > P.aim.salience*1.15+0.05`, or current < 0.15, and
  `now-P.aim.since > 0.25`; `P.aim = {kind, yaw, pitch, salience, since}`.
  Focus (`P.focus = world.focus && window.EVA_FOCUS_MODE !== false`): drop cursor/caret/new/curious;
  keep only `person` at .35 and allow a small glance (`|yaw|<=8, |pitch|<=4`) only when
  `now > P.nextFocusGlance`, then `P.nextFocusGlance = now+10+rand()*8`, `P.glanceCount++`; force
  `P.shiftTarget=0`, no fidgets.
- `presenceStep`: call `attentionStep(P, now, dt)` right after `affectStep`, before "what the eyes
  want". Existing cursor `presence.attn` wins only while hovering or higher-salience/newer than
  `P.aim`; otherwise `wantYaw/wantPitch` come from `P.aim` (`headShare` .35 cursor, .5 window/person,
  .25 curious). Cascade: eyes `dt*18`, head `dt*3.5`, torso `dt*1.1`; add `P.torsoYaw/torsoPitch`
  applied as `o.spineY += 0.25*torsoYaw*deg`, `o.spineZ += 0.15*torsoPitch*deg`. Focus multiplies
  head share .12 and torso share .03 and gates weight shifts/fidgets with `if (idleLike && !P.focus)`.
  Existing clamps keep a 300 ms acquisition under `MAX_PER_FRAME`.
- `window.acknowledge()` also sets `P.spokenAt = performance.now()/1000`.
## 4. config.py (defaults True, switchable)
After `PRESENCE = True`: `EVA_WORLD = True`, `EVA_ATTENTION = True`, `EVA_FOCUS_MODE = True`,
`EVA_WORLD_HZ = 8`, `EVA_FOCUS_TYPING_KEYS_PER_MIN = 120`.
## 5. Tests
### tools/motion_test.js -- extend `world()`'s API then add checks
Add to `this.api`: `setWorld: window.setWorld, presence, acknowledge: window.acknowledge`. Each check
advances `performance.now` like the feeling checks and calls
`w.api.presenceStep(f, t, 1/30, true)` -> `writePose`.
1. `check('a new window is looked at within 300 ms, and the eyes never snap', ...)`: settle, send a
   world with a `new` window at screen-left, step 9 frames; assert `presence.aim.kind === 'new'`
   within 2 and `Math.abs(presence.eyeYaw) > 0.7*Math.abs(presence.aim.yaw)` by frame 9; every
   `maxStep(prev, snapshot) <= MAX_PER_FRAME`.
2. `check('she looks at the person when spoken to, and a stale target decays away', ...)`:
   `acknowledge()`, 10 frames -> `aim.kind === 'person'` and `|eyeYaw| < 6`; then select a new
   window, run 4 s with no new event -> `aim.salience < 0.15` and `aim.kind !== 'new'`.
3. `check('focus mode keeps her still: at most one glance in ten seconds', ...)`: world
   `{focus:true, keys:300}`, 300 frames; head/torso channel range < 0.05 rad (non-focus control
   moves more) and `presence.glanceCount <= 1`; a non-focus world with `boredom:3` still fidgets.
### run_checks.py (one new check)
Add `"world_sensor"` to `MODULES`. `def the_world_sensor_sees_without_touching():`
- FakeProbes (cursor sequence, `{"title":"notes.txt - Notepad","rect":...}`, screen, eva, idle,
  typing_rate, windows). Assert JSON shape/types/ranges exactly as section 2 and `kind=="editor"`.
- Focus: `"Zoom Meeting"` -> kind call and `focus is True`; rect == screen -> `full` and focus;
  fake typing_rate 130 -> focus, 60 -> not focus.
- New window/notification: first sample has no `new`; add a 400x200 corner window ->
  `new.kind=="notification"`, rect equal, `at == clock`; a big window -> `kind=="window"`.
- Typing: monkeypatch `idle_behavior._key_ts` with 6 timestamps inside 3 s ->
  `get_typing_rate(now=...)` in 110..130; empty -> 0.
- No touching: patch `desktop_control.focus_window`, `dc.type_text`, `dc.click_coords`,
  `pyautogui.click` to raise, then `sample()`; scan `world_sensor.py` for
  `"SetForegroundWindow"`, `"type_text"`, `"click_coords"`, `"pyautogui"` (none may appear). < 5 s.
Register in the final list:
`("the world sensor sees without touching windows", the_world_sensor_sees_without_touching)`.
## 6. Hard rules and how the plan keeps each
- **Visual only, never acts on windows**: sensor calls read probes only; the check forbids and scans
  for window/input calls. The page only rotates bones.
- **Clear of cursor/caret/focused window**: Phase 1 moves neither the widget nor her body; attention
  changes gaze only, and focus mode reduces even that.
- **Focus mode**: Python flags keys > 120/min, fullscreen >= 95 %, or a call; the page drops other
  targets, allows <= 1 glance/10 s, head/torso shares .12/.03.
- **Cheap**: 8 Hz microsecond reads, 2 Hz enumeration, ~350-byte write; the page scores five
  candidates with easing already bounded by motion_test.
- **Flags**: `EVA_WORLD`, `EVA_ATTENTION`, `EVA_FOCUS_MODE` default True; `setWorld`/`attentionStep`
  no-op without a world.
- **Existing checks pass**: new code sits in the motion_test slice with guards; no renames;
  `presenceStep` keeps its signature and cursor behaviour before any world arrives.
## 7. Risks
- `getAllWindows` cost/stale handles on a busy desktop -> 2 Hz cap, per-window try/except, 1 Hz fallback.
- Title-only kinds mislabel some apps -> call/video/game checked first; kind is cosmetic, never a trigger.
- Small-window "notification" heuristic can fire on tooltips -> short-lived gaze only, no sound/speech.
- Attention vs `presence.attn` -> one hysteresis winner; cursor keeps precedence while hovering.
- Clock skew -> decay from receipt time; `at` is identity only.
- Focus false negatives (typing inside a browser) -> keys/min rule is title-independent.
- Multi-monitor/negative coords -> all rects are screen px; clamp yaw/pitch and never assume (0,0).
