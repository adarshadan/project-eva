"""Eva -- embodiment: turning actions into presence.

The capability manifest says what an action IS. This module decides how
Eva PERFORMS it, so that nothing she does is silent and motionless:

    front end   a visible target  -> she looks at it, walks beside it,
                                     reaches, the element is highlighted,
                                     the action fires, she reacts to how
                                     it actually went.
    back end    no visible target -> she shows what she is working on
                                     ("Reading agent_core.py..."), takes
                                     a working pose, and reacts the same.

THE RULES THIS MODULE HOLDS ITSELF TO (each one a lesson from this project):

  1. Embodiment never changes an outcome. The action runs exactly once, its
     result is returned untouched, and an exception from it propagates
     unchanged. A cue that fails -- or hangs -- is logged and skipped.
  2. The performance reports the truth. Reactions come from the verified
     result, never from hope: a failure gets a shrug, an unverified write
     gets a hesitant look, and a raw click that cannot be verified gets no
     celebration at all. (play_animation once returned ok:true for a clip
     that did not exist.)
  3. Feel must not cost speed. Observation is not dressed up, batches of
     rapid actions drop to a minimal mode, and slow work only earns a
     status line after a delay, so quick calls do not flicker one.
  4. Missing animations degrade. Every gesture is a semantic name with a
     fallback list; the renderer says whether it could play it, and if it
     cannot, the next candidate is tried, and finally text alone.
"""
import collections
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import capabilities
from capabilities import Effect
from logger import get_logger

log = get_logger(__name__)

# Verb -> the motion she makes on arriving beside the target. Each one MEANS
# the action (a reach-and-tap for a click, a press-down for minimising, a
# frame-and-slide for arranging) -- never an unrelated dance clip, which is
# what this used to map to ("click" was a finger-gun, "tidy" a crouch-and-jump).
# The avatar picks the hand on the target's side.
REACH_GESTURE = {"reach": "click", "open": "click", "tidy": "tidy", "arrange": "arrange",
                 "greet": "arrange", "point": "point", "scroll": "click",
                 "send": "click", "type": "click", "write": "click", "close": "tidy"}

# Verb -> candidate gestures for work with nowhere to walk to. All procedural:
# short, self-ending, blended over the activity pose she is holding.
WORK_GESTURES = {"type": ("p_type",), "write": ("p_type",), "run": ("p_type",),
                 "work": ("p_type",), "read": ("p_read",), "look": ("p_read",),
                 "search": ("p_read",), "scroll": ("p_read",), "send": ("p_point_r",),
                 "open": (), "point": ("p_point_r",)}

# How it went. A nod for a verified success -- not the 7-second jump-and-wave
# clip it used to fall back to after every file write.
OUTCOME_GESTURES = {"success": ("p_nod",), "fail": ("p_shrug",),
                    "unsure": ("p_think",), "needs_person": ("p_beckon",)}

# 2026-10-11: a motion that means THIS tool, played as it starts (only where
# she is not already walking over to reach something). Each is a procedural
# gesture in web/avatar_3d.html.
TOOL_GESTURES = {
    "locate_on_screen": ("p_peer",), "look_at_screen": ("p_peer",), "screenshot": ("p_peer",),
    "get_current_time": ("p_check_watch",), "schedule_reminder": ("p_check_watch",),
    "add_calendar_event": ("p_check_watch",), "list_scheduled": ("p_check_watch",),
    "schedule_task": ("p_salute",), "remember": ("p_tap_head",), "recall": ("p_think",),
    "move_file": ("p_carry",), "merge_pdfs": ("p_carry",), "split_pdf": ("p_carry",),
    "draft_email": ("p_paper_plane",), "notify": ("p_paper_plane",), "agent_message": ("p_paper_plane",),
    "system_info": ("p_checklist",), "disk_space": ("p_checklist",), "battery_status": ("p_checklist",),
    "startup_apps": ("p_checklist",), "list_processes": ("p_checklist",), "largest_files": ("p_checklist",),
    "duplicate_files": ("p_checklist",),
    "end_process": ("p_shoo",), "undo_last_change": ("p_rewind",), "focus_start": ("p_shh",),
}
# How it went, with some variety so she does not nod the same nod forty times
# a day: the first of each list is the classic, the rest take turns with it.
OUTCOME_VARIETY = {"success": ("p_nod", "p_fist_pump", "p_nod", "p_tada"), "fail": ("p_shrug", "p_facepalm")}
LONG_JOB_S = 15          # a success after this long: "phew"
LONG_WAIT_S = 7          # an action still running after this long: she waits like a person
WAITING_GESTURES = ("p_impatient", "p_check_watch")

# Gestures that are held states in the page rather than self-ending motions;
# the avatar ends a one-off one with a timer (avatar_ui.play).
HELD_STATES = {"nap", "fly_loop"}


def reach_variant(gesture: str, target_x: int, body_x: int) -> str:
    """A reach or point uses the hand on the target's side. She faces the
    person, so a target to the screen's RIGHT is on her LEFT hand's side."""
    side = "l" if target_x > body_x else "r"
    if gesture in ("click", "reach", "p_reach_l", "p_reach_r"):
        return "p_reach_" + side
    if gesture in ("point", "p_point_l", "p_point_r"):
        return "p_point_" + side
    return gesture


VERB_PHRASE = {"reach": "click that", "type": "type that", "write": "write that",
               "read": "read that", "run": "run that", "open": "open that",
               "tidy": "move that", "close": "close that", "arrange": "arrange that", "search": "search",
               "scroll": "scroll", "send": "send that", "work": "finish that",
               "greet": "restore that", "point": "point at that", "look": "look at that"}

Rect = tuple  # (left, top, right, bottom)


@dataclass(frozen=True)
class Locus:
    x: int
    y: int
    rect: Optional[Rect] = None


class Renderer:
    """What the performer needs from a body. Every method is a harmless
    no-op here, so a renderer only implements what it can actually do."""

    def reach(self, x, y, gesture, timeout=15.0) -> bool: return False
    def gaze(self, x, y, ttl_ms=2500): pass
    def highlight(self, rect, ttl_ms=1500): pass
    def status(self, text, animated=True): pass
    def clear_status(self): pass
    def gesture(self, name) -> bool: return False
    def set_activity(self, name): pass


class DesktopProbes:
    """Where things are on screen. Kept behind an interface so the planner
    can be tested without a desktop."""

    def element_rect(self, window_handle, element_id):
        import desktop_control as dc
        return dc.resolve_element_rect(window_handle, element_id)

    def window_rect(self, window_handle):
        import desktop_control as dc
        rect = dc.get_window_rect(window_handle)
        return (rect["left"], rect["top"], rect["right"], rect["bottom"]) if rect else None

    def foreground_rect(self):
        import desktop_control as dc
        return dc.get_foreground_rect()

    def cursor(self):
        import pyautogui
        p = pyautogui.position()
        return int(p[0]), int(p[1])


def _locus_from_rect(rect, keep_rect=True):
    if not rect:
        return None
    l, t, r, b = (int(v) for v in rect)
    if r - l < 2 or b - t < 2:
        return None
    return Locus((l + r) // 2, (t + b) // 2, (l, t, r, b) if keep_rect else None)


def resolve_locus(spec, args, probes):
    """Where on screen this action happens, or None (a back-end action, or
    a target that cannot be found -- never a reason to fail the action)."""
    kind = spec.locus
    if kind == "coords":
        for kx, ky in (("x", "y"), ("from_x", "from_y")):
            if kx in args and ky in args:
                return Locus(int(args[kx]), int(args[ky]))
        kind = "foreground"           # e.g. scroll without a position
    if kind == "element":
        return _locus_from_rect(probes.element_rect(args.get("window_handle"),
                                                    args.get("element_id")))
    if kind in ("window", "foreground"):
        handle = args.get("window_handle")
        rect = probes.window_rect(handle) if handle is not None else probes.foreground_rect()
        return _locus_from_rect(rect, keep_rect=False)
    if kind in CAPTION_BUTTONS:
        # The window's own title-bar button: she reaches for the real thing --
        # the X to close, the dash to minimise -- and the highlight rings it.
        handle = args.get("window_handle")
        rect = probes.window_rect(handle) if handle is not None else None
        if rect and _covers_screen(rect):
            # A full-screen window (a video, a game) has no title bar to reach
            # for: she looks at it instead; the close itself is the same WM_CLOSE.
            return None
        return caption_button_locus(rect, kind, _dpi_scale(handle))
    return None


# Title-bar buttons, counted from the right edge (Windows 10/11: 46 x 32 px each at 100 %).
CAPTION_BUTTONS = {"caption_close": 0, "caption_max": 1, "caption_min": 2}


def caption_button_locus(rect, kind, scale=1.0):
    if not rect:
        return None
    left, top, right, bottom = (int(v) for v in rect)
    if right - left < 150 or bottom - top < 40:
        return None
    bw, bh = int(46 * scale), int(32 * scale)
    top = max(top, 0) + (int(7 * scale) if top < 0 else 0)     # a maximised window sits a few px off-screen
    i = CAPTION_BUTTONS[kind]
    x1 = right - int(7 * scale if rect[1] < 0 else 1) - bw * i
    x0 = x1 - bw
    return Locus((x0 + x1) // 2, top + bh // 2, (x0, top, x1, top + bh))


def _covers_screen(rect) -> bool:
    try:
        import ctypes
        cx, cy = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    except Exception:
        return False
    left, top, right, bottom = rect
    return left <= 0 and top <= 0 and right >= cx and bottom >= cy


def _dpi_scale(hwnd) -> float:
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd)) if hwnd else 0
        return dpi / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def classify_result(result) -> str:
    """How an action actually went. The only input to a reaction."""
    if not isinstance(result, dict):
        return "ok"
    if result.get("aborted"):
        return "aborted"
    if result.get("needs_person"):
        return "needs_person"
    if result.get("ok") is False or ("error" in result and result.get("ok") is not True):
        return "failed"
    if result.get("warning") or result.get("closed") is False:
        return "unsure"
    if result.get("verified") is True:
        return "verified"
    return "ok"


def _short_error(result) -> str:
    text = " ".join(str((result or {}).get("error", "")).split()) if isinstance(result, dict) else ""
    for stop in (". ", " -- ", "; "):
        if stop in text:
            text = text.split(stop)[0]
    return text if len(text) <= 60 else text[:59] + "…"


class Performer:
    """Wraps every tool call. Installed by core/agent.py around every dispatch."""

    def __init__(self, renderer: Renderer, probes=None, *, mode="full",
                 status_delay=0.5, hold_seconds=2.5, burst_count=4, burst_window=3.0,
                 should_abort: Optional[Callable[[], bool]] = None,
                 pressure: Optional[Callable[[], bool]] = None,
                 clock=time.monotonic, timer_factory=threading.Timer):
        self.r = renderer
        self.probes = probes or DesktopProbes()
        self.mode = mode
        self.status_delay = status_delay
        self.hold_seconds = hold_seconds
        self.burst_count, self.burst_window = burst_count, burst_window
        self.should_abort = should_abort
        self.pressure = pressure
        self._clock = clock
        self._timer = timer_factory
        self._recent = collections.deque(maxlen=32)
        self._base_activity = "idle"
        self._gen = 0
        self._lock = threading.Lock()

    # -- safety: no cue may ever break, or hold up, an action -----------
    def _safe(self, fn, *args, default=None):
        try:
            return fn(*args)
        except Exception:
            log.exception(f"Embodiment cue {getattr(fn, '__name__', fn)!r} failed -- skipped")
            return default

    def _next_gen(self):
        with self._lock:
            self._gen += 1
            return self._gen

    def _current(self, gen):
        with self._lock:
            return gen == self._gen

    # -- pacing ----------------------------------------------------------
    def _effective_mode(self, spec):
        if spec is None or not spec.embodied or self.mode == "off":
            return "off"
        now = self._clock()
        self._recent.append(now)
        if self.mode != "full":
            return self.mode
        if self.pressure and self._safe(self.pressure, default=False):
            return "brisk"           # the person is waiting on something else
        recent = sum(1 for t in self._recent if now - t <= self.burst_window)
        return "brisk" if recent >= self.burst_count else "full"

    def set_activity(self, name):
        """The task-level pose the agent sets (per turn, per loaded skill); held
        between tool calls. perform() overlays the per-tool pose and restores
        this afterwards."""
        self._base_activity = name or "idle"
        self._safe(self.r.set_activity, self._base_activity)

    # -- the performance --------------------------------------------------
    def perform(self, name, args, execute):
        spec = capabilities.get(name)
        mode = self._effective_mode(spec)
        if mode == "off":
            return execute()
        import core.activity as activity_map
        tool_activity = self._safe(activity_map.for_tool, name)
        if tool_activity:
            self._safe(self.r.set_activity, tool_activity)

        gen = self._next_gen()
        effect = capabilities.effect_of(name, args)
        status_timer = wait_timer = None
        shown = {"status": False, "started": time.monotonic()}
        try:
            if mode == "full" and not (self.should_abort and self._safe(self.should_abort, default=False)):
                self._anticipate(spec, args, effect)
                if spec.approach != "walk" and name in TOOL_GESTURES:
                    self._play_first(TOOL_GESTURES[name])
                wait_timer = self._schedule_waiting(gen)
            status_timer = self._schedule_status(spec, args, gen, shown)
            try:
                result = execute()
            except Exception as exc:
                self._cancel(status_timer)
                self._cancel(wait_timer)
                self._outcome(spec, effect, {"ok": False, "error": str(exc)}, gen, mode, shown)
                raise
            self._cancel(status_timer)
            self._cancel(wait_timer)
            self._outcome(spec, effect, result, gen, mode, shown)
            return result
        finally:
            self._cancel(status_timer)
            self._cancel(wait_timer)
            if tool_activity:
                self._safe(self.r.set_activity, self._base_activity)

    def _anticipate(self, spec, args, effect):
        if spec.approach == "none":
            return
        locus = self._safe(resolve_locus, spec, args, self.probes)
        if locus is None:
            return
        if spec.approach == "walk":
            # A title-bar button is pressed with a fingertip, whatever the verb.
            gesture = "click" if spec.locus in CAPTION_BUTTONS else REACH_GESTURE.get(spec.verb, "click")
            self._safe(self.r.reach, locus.x, locus.y, gesture)
            if locus.rect and effect >= Effect.INTERACT:
                self._safe(self.r.highlight, locus.rect, 1200)
        else:                                    # gaze: look, do not walk
            self._safe(self.r.gaze, locus.x, locus.y, 2500)
            if spec.name not in TOOL_GESTURES:   # that one is played by perform()
                self._play_first(WORK_GESTURES.get(spec.verb, ()))

    def _play_first(self, candidates):
        """First gesture the renderer can actually play; else nothing."""
        for name in candidates:
            if self._safe(self.r.gesture, name, default=False):
                return name
        return None

    def _schedule_status(self, spec, args, gen, shown):
        if self.status_delay is None or spec.approach == "walk":
            return None
        text = capabilities.describe(spec.name, args)
        if not text:
            return None

        def show():
            if not self._current(gen):
                return
            shown["status"] = True
            self._safe(self.r.status, text, True)
            if spec.approach == "none":
                self._play_first(WORK_GESTURES.get(spec.verb, ()))

        timer = self._timer(self.status_delay, show)
        timer.daemon = True
        timer.start()
        return timer

    @staticmethod
    def _cancel(timer):
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _outcome(self, spec, effect, result, gen, mode, shown):
        kind = classify_result(result)
        if shown["status"]:
            self._safe(self.r.clear_status)
        if kind == "aborted":
            return
        if mode != "full" and kind not in ("failed", "needs_person"):
            return                                # brisk: only speak up when it matters
        if kind == "failed":
            phrase = VERB_PHRASE.get(spec.verb, "do that")
            self._flash(f"Couldn't {phrase}: {_short_error(result)}".rstrip(": "), gen)
            self._play_first((self._vary("fail"),))
        elif kind == "needs_person":
            self._flash("I need you for this bit", gen)
            self._play_first(OUTCOME_GESTURES["needs_person"])
        elif kind == "unsure" and effect >= Effect.MODIFY:
            self._play_first(OUTCOME_GESTURES["unsure"])
        elif kind == "verified" and effect >= Effect.MODIFY:
            long_job = time.monotonic() - shown.get("started", time.monotonic()) >= LONG_JOB_S
            self._play_first(("p_wipe_brow",) if long_job else (self._vary("success"),))
        # Everything else -- an observation that worked, a raw click nobody
        # can verify -- is deliberately silent. Celebrating unverified
        # actions is how she came to tell you an essay was written.

    def _vary(self, kind):
        """The next of OUTCOME_VARIETY[kind], in turn (the classic first)."""
        seq = OUTCOME_VARIETY[kind]
        n = self._variety = getattr(self, "_variety", {})
        n[kind] = n.get(kind, -1) + 1
        return seq[n[kind] % len(seq)]

    def _schedule_waiting(self, gen):
        """An action that takes a while: she waits the way a person does --
        hands on hips, a glance at her watch -- instead of standing frozen."""
        def wait():
            if self._current(gen):
                self._play_first((WAITING_GESTURES[getattr(self, "_waits", 0) % len(WAITING_GESTURES)],))
                self._waits = getattr(self, "_waits", 0) + 1
        timer = self._timer(LONG_WAIT_S, wait)
        timer.daemon = True
        timer.start()
        return timer

    def _flash(self, text, gen):
        # Cleared by its own token, not by the action generation. Gating the
        # clear on "still the current action" meant that a failure followed by
        # any action that shows no status of its own left the message on
        # screen indefinitely -- reported as the same failure text "kept
        # showing". A newer flash still wins: its token replaces this one.
        self._flash_seq = getattr(self, "_flash_seq", 0) + 1
        token = self._flash_seq
        self._safe(self.r.status, text, False)

        def clear():
            if getattr(self, "_flash_seq", 0) == token:
                self._safe(self.r.clear_status)

        timer = self._timer(self.hold_seconds, clear)
        timer.daemon = True
        timer.start()

    # -- ambient life: reacting to things that are not tool calls -----------
    def event(self, kind, **_):
        """Called for moments in the conversation rather than actions."""
        if self.mode == "off":
            return
        if kind == "heard":
            pos = self._safe(self.probes.cursor)
            if pos:
                self._safe(self.r.gaze, pos[0], pos[1], 1800)
