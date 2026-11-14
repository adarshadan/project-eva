"""
Eva — idle entertainment + proactive suggestions.
Idle animation never touches real windows/clicks/keys/network — visual only.
Suggestions are always phrased as offers, rate-limited, and suppressed during
calls, fullscreen apps, or right after real input.
"""
import collections
import random
import time
from pynput import mouse, keyboard
import pygetwindow as gw
import screeninfo

import config
import desktop_control as dc

_last_input_ts = time.time()
# When each of the last 60 keys was pressed (Phase 1 typing rate). A deque, so
# appending a key is O(1) and the rate scan is bounded by 60 entries.
_key_ts = collections.deque(maxlen=60)


def _touch_input(*_):
    global _last_input_ts
    _last_input_ts = time.time()


def _on_key(*_):
    _touch_input()
    _key_ts.append(time.time())


def get_typing_rate(now=None, window=3.0) -> float:
    """Keys per minute over the last `window` seconds (0.0 when none).
    O(60): the deque never holds more than 60 timestamps."""
    if window <= 0:
        return 0.0
    now = time.time() if now is None else now
    n = sum(1 for ts in _key_ts if now - ts <= window)
    return n * 60.0 / window


def touch_input():
    """Public entry point for OTHER modules to register user activity --
    e.g. the mic hearing the person speak. Without this, idle detection
    only ever sees mouse/keyboard via pynput, so a person actively talking
    to Eva without touching the mouse or keyboard still counts as "idle",
    and the idle-entertainment loop starts walking/dancing mid-conversation."""
    _touch_input()


def start_input_listeners():
    mouse.Listener(on_click=_touch_input, on_move=_touch_input, on_scroll=_touch_input).start()
    keyboard.Listener(on_press=_on_key).start()


def get_idle_seconds():
    return {"idle_seconds": time.time() - _last_input_ts}


def pick_idle_animation() -> str:
    idle = get_idle_seconds()["idle_seconds"]
    if idle >= config.IDLE_NAP_SECONDS:
        return "nap"
    # "fly_loop" is left out deliberately -- confirmed (see play_animation
    # in brain.py) that no clip named "fly_loop"/"flyloop" exists on the
    # current VRM model, so picking it here would just be a silent no-op
    # beat during idle. "spin"/"modelpose" are real VRMA gestures, added
    # for variety; walk_taskbar's weight is reduced from what it once was
    # since even the now-much-cheaper walk (throttled window-sync, slower
    # tick rate) is still more motion than a held pose.
    # Names are the semantic gestures of core/toolset/presence.py; the page maps
    # each to the clip that really shows it (see ANIM_MAP in avatar_3d.html).
    return random.choices(
        ["walk_taskbar", "twirl", "wave", "stretch", "pose", "peace"],
        weights=[0.35, 0.15, 0.15, 0.15, 0.1, 0.1],
    )[0]


def is_fullscreen_app_active() -> bool:
    app = dc.get_foreground_app()
    if not app.get("handle"):
        return False
    try:
        for w in gw.getAllWindows():
            if w._hWnd == app["handle"]:
                monitors = screeninfo.get_monitors()
                # Exact >= match was too strict -- window decorations/DPI
                # scaling routinely leave a genuinely fullscreen app a few
                # pixels short of the raw monitor dimensions, so this never
                # triggered for some real fullscreen apps. A 95% threshold
                # still excludes ordinary maximized-but-windowed apps.
                return any(w.width >= m.width * 0.95 and w.height >= m.height * 0.95 for m in monitors)
    except Exception:
        return False
    return False


def should_suppress_suggestion(mic_active: bool = False, camera_active: bool = False) -> bool:
    idle = get_idle_seconds()["idle_seconds"]
    if idle < config.SUGGESTION_SUPPRESS_RECENT_INPUT_SECONDS:
        return True
    if mic_active or camera_active:
        return True
    if is_fullscreen_app_active():
        return True
    return False


def check_for_suggestion():
    """Returns a suggestion string or None. Caller enforces the 5-min rate limit."""
    if should_suppress_suggestion():
        return None
    jobs = dc.list_background_jobs()["jobs"]
    heavy = [j for j in jobs if (j["cpu"] or 0) > 50]
    if heavy:
        return f"{heavy[0]['name']} is using a lot of CPU — want me to look into it?"
    return None
