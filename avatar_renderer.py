"""Eva -- the body behind embodiment.py.

Translates the performer's semantic cues (reach, gaze, highlight, status,
gesture) into calls on the real avatar. Every method is safe to call from
the worker thread: GUI work is handed to the GUI thread with fire_on_gui,
and the only calls that wait (reach, gesture) wait on an Event with a
timeout, so a stalled GUI can delay a cue but never wedge an action.
"""
import threading

from embodiment import Renderer
from logger import get_logger

log = get_logger(__name__)


class AvatarRenderer(Renderer):
    def __init__(self, avatar, fire_on_gui):
        self.avatar = avatar
        self._fire = fire_on_gui

    def reach(self, x, y, gesture, timeout=15.0) -> bool:
        """Walk beside (x, y) and reach toward it; returns once she is there.
        This is the pre-existing walk-and-gesture behaviour, unchanged."""
        done = threading.Event()
        self._fire(lambda: self.avatar.perform_gesture_at(x, y, gesture=gesture, on_ready=lambda: done.set()))
        return done.wait(timeout)

    def gaze(self, x, y, ttl_ms=2500):
        self._fire(lambda: self.avatar.look_toward(x, y, ttl_ms))

    def highlight(self, rect, ttl_ms=1500):
        self._fire(lambda: self.avatar.overlay.highlight_rect(rect, ttl_ms))

    def status(self, text, animated=True):
        self._fire(lambda: self.avatar.set_status(text, animated))

    def clear_status(self):
        # Back to what the status line says while a turn is running -- tools
        # only ever run inside one, and main.py owns the idle wording.
        self._fire(lambda: self.avatar.set_status("Thinking", animated=True))

    def set_activity(self, name):
        self._fire(lambda: self.avatar.set_activity(name))

    def gesture(self, name) -> bool:
        """Play a gesture and report whether the rig actually could. The
        answer comes from the page itself (playClip's own return value), so
        a pose that is disabled, unknown, or blocked by a running clip is
        reported as False and the performer tries its next candidate."""
        answer, done = {"ok": False}, threading.Event()

        def on_result(value):
            answer["ok"] = bool(value)
            done.set()

        self._fire(lambda: self.avatar.play(name, loop=False, on_result=on_result))
        return answer["ok"] if done.wait(1.0) else False
