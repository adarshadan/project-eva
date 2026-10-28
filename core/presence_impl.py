"""Eva core -- implementations of her presence tools, bound at startup.

Qt rule: widgets are touched only on the GUI thread. run_on_gui_sync runs a
function there and waits for its result (only for functions that finish on
their own); fire_on_gui schedules one without waiting (for walks and clips
whose completion arrives later through a callback, which the caller then
waits for on its own thread).
"""
import difflib
import threading

from core import registry
from core.toolset.presence import GESTURES

# Requests people actually make, mapped to a gesture this model really has.
ALIASES = {
    "moonwalk": "dance", "spin": "dance", "celebrate": "cheer", "party": "dance", "groove": "dance",
    "boogie": "dance", "hop": "jump", "bounce": "jump", "up_and_down": "jump", "yay": "cheer", "hooray": "cheer",
    "hi": "wave", "bye": "wave", "goodbye": "wave",
    "applause": "clap", "applaud": "clap", "bravo": "clap", "oops": "facepalm", "doh": "facepalm",
    "love": "heart", "aww": "heart", "quiet": "shh", "hush": "shh", "silence": "shh", "time": "check_watch",
    "watch": "check_watch", "waiting": "impatient", "tap_foot": "impatient", "confused": "scratch_head",
    "puzzled": "scratch_head", "look_closely": "peer", "squint": "peer", "lift": "carry", "box": "carry",
    "throw": "paper_plane", "send": "paper_plane", "phew": "wipe_brow", "tired": "wipe_brow",
    "pump": "fist_pump", "ta_da": "tada", "presto": "tada", "noted": "tap_head", "achoo": "sneeze",
    "sing": "hum", "sway": "hum", "humming": "hum", "go_away": "shoo", "undo": "rewind", "list": "checklist",
    "greet": "hello", "greeting": "hello", "excited": "hello",
    "turn": "twirl", "turn_around": "twirl", "show_off": "twirl", "outfit": "twirl",
    "thank": "bow", "thanks": "bow", "curtsy": "bow", "yes": "nod", "agree": "nod",
    "hmm": "think", "ponder": "think", "dunno": "shrug", "idk": "shrug",
    "sleep": "nap", "rest": "nap", "doze": "nap",
    "fly": "fly_loop", "hover": "fly_loop", "float": "fly_loop",
    "victory": "peace", "v_sign": "peace", "photo": "pose", "model": "pose", "hands_on_hips": "pose",
    "shoot": "finger_gun", "gun": "finger_gun", "dab": "finger_gun",
    "crouch": "squat", "squats": "squat", "kneel": "squat",
    "yoga": "stretch", "exercise": "stretch", "warm_up": "stretch",
    "show": "point", "come": "beckon", "come_here": "beckon",
    "tap": "click", "press": "click", "clean": "tidy",
    "walk": "walk_taskbar", "stroll": "walk_taskbar",
    "type": "work", "typing": "work",
}


def resolve_gesture(name: str):
    """(gesture to play, note or None). Never refuses: an unknown request gets
    the closest real gesture, and the note says so honestly."""
    key = str(name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in GESTURES:
        return key, None
    for word in [key] + key.split("_"):
        if word in ALIASES:
            g = ALIASES[word]
            return g, f"there is no {name!r} animation, so I played {g!r} instead"
    close = difflib.get_close_matches(key, GESTURES + list(ALIASES), n=1, cutoff=0.6)
    g = (ALIASES.get(close[0], close[0]) if close else "dance")
    return g, f"there is no {name!r} animation, so I played {g!r} instead"


def bind_presence(avatar, run_on_gui_sync, fire_on_gui):
    def play_animation(name, loop=False, times=1):
        gesture, note = resolve_gesture(name)
        if gesture == "walk_taskbar":
            result = run_on_gui_sync(lambda: avatar.walk_along_taskbar()) or {"ok": True}
        elif gesture == "jump":
            # A real jump: she leaves the ground (eva.log 2026-10-11 14:14 -- asked
            # to jump, she danced, squatted and cheered, then offered to fake it).
            done = threading.Event()
            n = max(1, min(5, int(times or 1)))
            fire_on_gui(lambda: avatar.jump(n, on_done=lambda: done.set()))
            done.wait(timeout=3 + 1.3 * n)
            result = {"ok": True, "jumps": n}
        else:
            box, done = {}, threading.Event()

            def on_result(success):
                box["ok"] = bool(success)
                done.set()
            fire_on_gui(lambda: avatar.play(gesture, bool(loop), on_result=on_result))
            if not done.wait(timeout=3):
                return {"ok": False, "error": "the animation did not start (the avatar page did not answer)"}
            if not box["ok"]:
                return {"ok": False, "error": f"the {gesture!r} gesture is not available on this avatar model"}
            result = {"ok": True}
        result = dict(result, played=gesture)
        if note:
            result["note"] = note
        return result

    def stop_animation():
        run_on_gui_sync(avatar.stop)
        return {"ok": True}

    def walk_to_screen(x, y):
        done = threading.Event()

        def start():
            def arrived():
                avatar.play("p_wave", loop=False)     # a short real wave, not a 7 s clip
                done.set()
            if avatar.is_sitting():
                avatar.stand_up(animate=False)
            avatar.mark_placed()                      # sent here: she stays here
            avatar.walk_to(int(x), int(y), on_arrive=arrived)
        fire_on_gui(start)
        arrived = done.wait(timeout=45)
        return {"ok": True} if arrived else {"ok": False, "error": "did not arrive within 45 s"}

    def sit_on_window(window_handle, spot="middle"):
        import avatar_ui as ui
        rect = ui._window_rect(window_handle)
        if not rect:
            return {"ok": False, "error": "that window is not there any more -- list_windows for current handles"}
        if ui._window_iconic(window_handle):
            return {"ok": False, "error": "that window is minimised; restore it first"}
        left, top, right, _bottom = rect
        # Qt is asked on its own thread only (this runs on the task thread).
        screen_top, width = run_on_gui_sync(lambda: (ui._screen_rect()[1], avatar.width())) or (0, 115)
        if top <= screen_top + 2:
            return {"ok": False, "error": "that window is maximised or full screen: there is no top edge to sit on"}
        if right - left < width * 2:
            return {"ok": False, "error": "that window is too narrow to sit on"}
        margin = width
        x = {"left": left + margin, "right": right - margin}.get(str(spot or "middle"), (left + right) // 2)
        done = threading.Event()

        def start():
            avatar.mark_placed()
            avatar.sit_on(top, int(x), window_handle, on_done=lambda: done.set())
        fire_on_gui(start)
        if not done.wait(timeout=45):
            return {"ok": False, "error": "did not get there within 45 s"}
        b = run_on_gui_sync(avatar.body) or {}
        sat = bool(b.get("sitting")) and b.get("perched_on") == window_handle
        return {"ok": sat, "verified": sat, "sitting_on": window_handle if sat else None,
                **({} if sat else {"error": "she got there but is not sitting on it"})}

    def sit_down():
        def go():
            import avatar_ui as ui
            avatar.mark_placed()
            feet = avatar.y() + avatar.height()
            edge = None if avatar.on_ground() else ui._window_edge_near(avatar.x() + avatar.width() // 2, feet)
            if edge:
                hwnd, top = edge
                avatar.sit_here(top, hwnd)          # on that window: she rides along with it
            else:
                avatar.sit_here()
        run_on_gui_sync(go)
        return {"ok": True, "sitting": bool((run_on_gui_sync(avatar.body) or {}).get("sitting"))}

    def stand_up(come_down=False):
        run_on_gui_sync(avatar.get_down if come_down else avatar.stand_up)
        return {"ok": True}

    def go_home():
        x, y = avatar.home_position
        return walk_to_screen(x, y)

    registry.bind("play_animation", play_animation)
    registry.bind("stop_animation", stop_animation)
    registry.bind("walk_to_screen", walk_to_screen)
    registry.bind("go_home", go_home)
    registry.bind("sit_on_window", sit_on_window)
    registry.bind("sit_down", sit_down)
    registry.bind("stand_up", stand_up)
