"""
Eva -- centralized logging. Bubble text is deliberately transient (it's a
speech bubble, not a log) and vanishes in a few seconds, so it's useless
for debugging after the fact. Everything worth knowing -- errors, tool
calls, mood changes, startup/shutdown, web-console messages from the 3D
scene -- is also written here, to a real file with timestamps, that stays
around after the bubble is long gone.

Import this once, early (main.py does it first, before anything else),
then anywhere else just do:
    from logger import get_logger
    log = get_logger(__name__)
    log.info("...")
"""
import logging
from logging.handlers import RotatingFileHandler
import sys
import threading
from pathlib import Path

# EVA_LOG_PATH lets test runs log elsewhere: run_checks.py once wrote its test
# noise into the shipped eva.log, which then appeared at the top of a real
# session's log.
import os as _os
LOG_PATH = Path(_os.environ.get("EVA_LOG_PATH") or (Path(__file__).parent / "eva.log"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s %(name)s: %(message)s",
    handlers=[
        # Rotating, not plain: a tool-heavy session appends to eva.log without
        # bound, and eva.log is the first thing asked for when diagnosing a
        # fault -- it needs to stay openable.
        RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3,
                            encoding="utf-8"),
        logging.StreamHandler(sys.stdout),  # keep everything visible in the terminal too
    ],
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# Catch-all net: anything that would otherwise die with just a bare
# traceback (or nothing at all, on some setups) gets a permanent record
# here instead. This is the same category of fix already applied
# piecemeal elsewhere (the mic listener callback, the chat/voice worker
# threads) -- this is the backstop for everything else, present and future.
def _log_uncaught(exc_type, exc_value, exc_tb):
    logging.getLogger("uncaught").critical(
        "Uncaught exception on main thread", exc_info=(exc_type, exc_value, exc_tb)
    )


def _log_thread_exception(args):
    logging.getLogger("uncaught").critical(
        f"Uncaught exception in thread '{args.thread.name}'",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


sys.excepthook = _log_uncaught
threading.excepthook = _log_thread_exception


# ---- Hard-crash diagnostics -------------------------------------------------
# eva.log 2026-09-30 ended mid-action (a click_coords call with no result, no
# traceback, no "Shutting down"). A crash inside native code -- Qt, the GPU
# process, COM/UI Automation, an audio driver -- kills the interpreter before
# any except block or excepthook can run, so it leaves nothing at all. Two
# things make the next one diagnosable:
#   1. faulthandler writes the Python stack of every thread to crash.log at
#      the moment of a fatal native error (access violation, abort).
#   2. a "running" marker that only a clean exit removes: if it is still
#      there at the next start, the previous run died, and that is logged.
import faulthandler
import os
import atexit
import time as _time

EVA_DIR = Path.home() / ".eva"
CRASH_PATH = EVA_DIR / "crash.log"
RUNNING_MARK = EVA_DIR / "running.marker"
_crash_fh = None


def install_crash_diagnostics():
    global _crash_fh
    try:
        EVA_DIR.mkdir(parents=True, exist_ok=True)
        _crash_fh = open(CRASH_PATH, "a", buffering=1, encoding="utf-8")
        _crash_fh.write(f"\n--- session start {_time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} ---\n")
        faulthandler.enable(file=_crash_fh, all_threads=True)
    except Exception as e:  # diagnostics must never stop startup
        logging.getLogger("logger").warning(f"Crash diagnostics unavailable: {e}")


def dump_all_threads(note: str):
    """Writes every thread's Python stack to crash.log NOW, at C level (no
    logging, no formatting). The GUI-stall detector calls it the moment the
    GUI thread freezes: on 2026-10-11 the process died within milliseconds of
    such a freeze, before the logged stack report could be written."""
    if _crash_fh is None:
        return False
    try:
        _crash_fh.write(f"\n=== {note} at {_time.strftime('%H:%M:%S')} ===\n")
        _crash_fh.flush()
        faulthandler.dump_traceback(file=_crash_fh, all_threads=True)
        _crash_fh.flush()
        return True
    except Exception:
        return False


def previous_run_report():
    """Call once at startup, BEFORE mark_running(). Returns a description of
    the previous run's unclean end, or None."""
    try:
        if not RUNNING_MARK.exists():
            return None
        info = RUNNING_MARK.read_text(encoding="utf-8", errors="replace").strip()
        tail = ""
        if CRASH_PATH.exists():
            lines = CRASH_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            # everything after the last "session start" header of the dead run
            starts = [i for i, l in enumerate(lines) if l.startswith("--- session start")]
            if len(starts) >= 1:
                body = lines[starts[-1] + 1:] if len(starts) == 1 else lines[starts[-2] + 1:starts[-1]]
                # The HEAD matters most: "Windows fatal exception: access
                # violation" and the stack of the thread that crashed come
                # first; the other threads follow. Only the tail used to be
                # kept, which hid exactly that (eva.log 2026-10-11 13:13).
                if len(body) > 220:
                    body = body[:150] + [f"... ({len(body) - 220} lines of other threads omitted) ..."] + body[-70:]
                tail = "\n".join(body)
        return f"previous run ({info}) did not shut down cleanly." + (
            f" Native crash trace recorded:\n{tail}" if tail.strip() else
            " No native crash trace was recorded (killed from outside, console closed, or power loss).")
    except Exception:
        return None


def mark_running():
    try:
        RUNNING_MARK.write_text(f"pid={os.getpid()} started={_time.strftime('%Y-%m-%d %H:%M:%S')}",
                                encoding="utf-8")
    except Exception:
        pass


def mark_clean_exit():
    try:
        RUNNING_MARK.unlink()
    except Exception:
        pass


atexit.register(mark_clean_exit)
