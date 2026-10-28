"""Eva -- continuous activities: things that run until they are done or told to stop.

Her ordinary way of working asks the model "what next?" after every step:
2-5 s a step, fine for "open Notepad", hopeless for "keep scrolling",
"play Flappy Bird" or a game of tic-tac-toe (2026-10-11). Here the model
only STARTS an activity and is told how it ended; a local thread does the
work at whatever pace it needs:

  loops.py   repeat an action until a condition (scroll to the end of the page)
  games.py   turn-based games: read the board, solve, move, wait for the reply
  reflex.py  real-time games and reactions: 30-60 decisions a second

One activity at a time. Every one can be stopped at once -- "stop" (the
runtime's stop hooks), the ring menu, or the hotkey -- ends after
max_seconds whatever happens, and reports how it went (spoken by main.py).
While it runs it pauses itself if the person brings another window to the
front, so keys never land in the wrong program.

The manager is Qt-free; main.py gives it callbacks for the status pill,
for speaking the result, and for her body (moving her out of the way of
the region she watches, the playing pose, a cheer or a facepalm).
"""
import threading
import time

from logger import get_logger

log = get_logger(__name__)

DEFAULT_MAX_SECONDS = 600
HARD_MAX_SECONDS = 3600


class Activity:
    """Subclass and implement run(ctx); call ctx.alive() often."""
    kind = "activity"

    def __init__(self, title, max_seconds=DEFAULT_MAX_SECONDS, window=None, region=None):
        self.title = title
        self.max_seconds = max(5, min(HARD_MAX_SECONDS, int(max_seconds or DEFAULT_MAX_SECONDS)))
        self.window = window             # the window it acts on: paused while another is in front
        self.region = region             # the screen area it watches: she keeps out of it
        self.progress = ""               # short live text: "12 pipes", "3 moves"
        self.result = None               # how it ended, in words
        self.outcome = None              # "won" | "lost" | "draw" | "done" | "stopped" | "failed" | "timeout"

    def run(self, ctx):
        raise NotImplementedError

    def adjust(self, **changes):
        return {"ok": False, "error": "this activity cannot be adjusted while it runs"}


class Context:
    def __init__(self, manager, activity):
        self._m, self._a = manager, activity
        self.stop_event = threading.Event()
        self.started = time.monotonic()
        self.paused_for = 0.0

    def elapsed(self):
        return time.monotonic() - self.started

    def alive(self, wait=0.0):
        """False once stopped or out of time. Blocks while the person has
        another window in front (keys must not land there)."""
        if wait > 0 and self.stop_event.wait(wait):
            return False
        if self.stop_event.is_set():
            return False
        if self.elapsed() - self.paused_for > self._a.max_seconds:
            self._a.outcome = self._a.outcome or "timeout"
            return False
        if self._a.window and self._m.foreground() not in (None, self._a.window):
            t0 = time.monotonic()
            self._m.status(f"{self._a.title}: paused -- that window is not in front")
            while not self.stop_event.is_set() and self._m.foreground() not in (None, self._a.window):
                if self.stop_event.wait(0.25):
                    return False
                if time.monotonic() - t0 > 120:
                    self._a.outcome, self._a.result = "stopped", "you switched to another window, so I stopped"
                    return False
            self.paused_for += time.monotonic() - t0
            self._m.status(self._a.title)
        return True

    def progress(self, text):
        self._a.progress = str(text)
        self._m.status(f"{self._a.title} -- {text}")


class Manager:
    def __init__(self, *, on_status=None, on_end=None, body=None, foreground=None, clock=time.monotonic):
        self._on_status = on_status or (lambda text: None)
        self._on_end = on_end or (lambda activity: None)
        self.body = body                                     # optional: keep_clear(region), mood(outcome), pose(on)
        self._foreground = foreground or (lambda: None)
        self._lock = threading.Lock()
        self.current = None
        self._ctx = None
        self._thread = None
        self.history = []

    # ---- probes / outputs (each fails soft) --------------------------------------------
    def foreground(self):
        try:
            return self._foreground()
        except Exception:
            return None

    def status(self, text):
        try:
            self._on_status(text)
        except Exception:
            pass

    def _body(self, name, *a):
        fn = getattr(self.body, name, None) if self.body else None
        if fn:
            try:
                fn(*a)
            except Exception:
                log.exception(f"Activity body cue {name} failed")

    # ---- control ------------------------------------------------------------------------
    def start(self, activity):
        self.stop("a new activity started")
        ctx = Context(self, activity)
        with self._lock:
            self.current, self._ctx = activity, ctx
        if activity.region:
            self._body("keep_clear", activity.region)
        self._body("pose", True)
        self.status(activity.title)
        t = threading.Thread(target=self._run, args=(activity, ctx), name=f"eva-activity-{activity.kind}",
                             daemon=True)
        self._thread = t
        t.start()
        log.info(f"Activity started: {activity.title} (max {activity.max_seconds} s)")
        return {"ok": True, "started": activity.title, "max_seconds": activity.max_seconds,
                "note": "it runs on its own now; say stop to end it. You will be told how it ended."}

    def _run(self, activity, ctx):
        try:
            activity.run(ctx)
            if not activity.outcome:
                activity.outcome = "stopped" if ctx.stop_event.is_set() else "done"
        except Exception as e:
            log.exception(f"Activity {activity.title!r} failed")
            activity.outcome, activity.result = "failed", f"it went wrong: {str(e)[:120]}"
        finally:
            took = ctx.elapsed()
            if activity.outcome == "timeout" and not activity.result:
                activity.result = f"I stopped after {int(activity.max_seconds // 60) or 1} minutes, the time limit"
            with self._lock:
                if self.current is activity:
                    self.current, self._ctx = None, None
            self.history.append({"title": activity.title, "outcome": activity.outcome, "result": activity.result,
                                 "seconds": round(took, 1)})
            self.history = self.history[-20:]
            log.info(f"Activity ended: {activity.title} -> {activity.outcome}: {activity.result} ({took:.1f} s)")
            self._body("pose", False)
            self._body("mood", activity.outcome)
            self.status(None)
            if activity.outcome != "replaced":
                try:
                    self._on_end(activity)
                except Exception:
                    log.exception("Activity end callback failed")

    def stop(self, why="stopped"):
        with self._lock:
            a, ctx, t = self.current, self._ctx, self._thread
        if not a:
            return False
        if why == "a new activity started":
            a.outcome = "replaced"
        else:
            a.outcome = a.outcome or "stopped"
        ctx.stop_event.set()
        if t and t is not threading.current_thread():
            t.join(timeout=3)
        return True

    def adjust(self, **changes):
        with self._lock:
            a = self.current
        if not a:
            return {"ok": False, "error": "nothing is running"}
        return a.adjust(**changes)

    def state(self):
        with self._lock:
            a, ctx = self.current, self._ctx
        if not a:
            last = self.history[-1] if self.history else None
            return {"ok": True, "running": False, "last": last}
        return {"ok": True, "running": True, "activity": a.title, "progress": a.progress,
                "seconds": round(ctx.elapsed(), 1), "max_seconds": a.max_seconds}


MANAGER = {"m": None}


def manager():
    if MANAGER["m"] is None:
        MANAGER["m"] = Manager()
    return MANAGER["m"]
