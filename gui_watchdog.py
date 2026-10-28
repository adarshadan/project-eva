"""Eva -- finds out WHERE the GUI thread hangs.

eva.log 2026-10-11 01:40: a close_window walk never arrived, the avatar page
went silent for 8 s, the recovery reload ran, and the process died with no
traceback. A page that stops sending heartbeats looks the same whether the
renderer died or Eva's own GUI thread was blocked (Qt WebEngine delivers the
page's frames and console messages through that thread), and the two need
opposite responses: reloading a healthy page because the GUI thread was
stuck is pointless and, mid-stall, risky.

This module tells them apart and leaves evidence:
  * the GUI thread touches a timestamp every 250 ms (a QTimer);
  * a background thread checks it; when the GUI thread has not run for
    STALL_SECONDS, the full stack of every thread is written to eva.log
    ONCE per stall -- the line that is blocking is then in the log;
  * when it runs again, the length of the stall is logged;
  * `stalled_within(s)` lets the avatar watchdog skip a reload that would
    only be reacting to the stall.
"""
import sys
import threading
import time
import traceback

from logger import get_logger

log = get_logger(__name__)

STALL_SECONDS = 3.0


class GuiStallDetector:
    def __init__(self, stall_seconds: float = STALL_SECONDS, clock=time.monotonic):
        self.stall_seconds = stall_seconds
        self._clock = clock
        self.last_beat = clock()
        self.last_stall_end = None        # when the most recent stall ended
        self._in_stall = False
        self._stall_started = None
        self._main_ident = threading.main_thread().ident
        self._stop = threading.Event()
        self._thread = None

    # ---- called on the GUI thread ----------------------------------------------------
    def beat(self):
        now = self._clock()
        if self._in_stall:
            length = now - (self._stall_started or now)
            log.warning(f"GUI thread ran again after a {length + self.stall_seconds:.1f} s stall.")
            self._in_stall = False
            self.last_stall_end = now
        self.last_beat = now

    def stalled_within(self, seconds: float) -> bool:
        """True while stalled, or if a stall ended in the last `seconds`."""
        if self._in_stall:
            return True
        return self.last_stall_end is not None and self._clock() - self.last_stall_end < seconds

    # ---- background ------------------------------------------------------------------
    def check(self):
        """One check; returns the stack report if a new stall was detected."""
        if self._stop.is_set():
            return None
        now = self._clock()
        if self._in_stall or now - self.last_beat < self.stall_seconds:
            return None
        self._in_stall = True
        self._stall_started = now
        try:
            import logger
            logger.dump_all_threads(f"GUI STALL ({now - self.last_beat:.1f} s)")   # first, and fast
        except Exception:
            pass
        report = self.stacks()
        log.warning(f"GUI thread has not run for {now - self.last_beat:.1f} s -- the avatar cannot "
                    f"move or redraw. Where every thread is:\n{report}")
        return report

    def stacks(self) -> str:
        names = {t.ident: t.name for t in threading.enumerate()}
        parts = []
        frames = sys._current_frames()
        # the GUI thread first: that is the one that matters
        for ident in sorted(frames, key=lambda i: i != self._main_ident):
            name = names.get(ident, str(ident))
            stack = "".join(traceback.format_stack(frames[ident])[-12:])
            parts.append(f"--- {name}{' (GUI thread)' if ident == self._main_ident else ''} ---\n{stack}")
        return "\n".join(parts)

    def start(self, timer_factory=None):
        """timer_factory(interval_ms, fn) creates the GUI-thread timer (a QTimer
        in Eva); it is kept on self so it is not garbage-collected."""
        if timer_factory is not None:
            self._timer = timer_factory(250, self.beat)

        def run():
            while not self._stop.wait(0.5):
                try:
                    self.check()
                except Exception:
                    log.exception("GUI stall check failed")
        self._thread = threading.Thread(target=run, name="eva-gui-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()


_detector = None


def install(timer_factory):
    global _detector
    _detector = GuiStallDetector().start(timer_factory)
    return _detector


def stalled_within(seconds: float) -> bool:
    return bool(_detector and _detector.stalled_within(seconds))


def shutdown():
    """Stops reporting: Eva is closing, and the goodbye waits on purpose."""
    if _detector:
        _detector.stop()

