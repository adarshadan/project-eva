"""Eva -- a deadline for tools that talk to OTHER processes.

eva.log ended, three runs in a row, on the line that starts a tool call and
never got its result: click_coords, ui_tree on the Settings window, and
minimize_window on the console. All three make a call that waits on another
program's UI thread (SendInput, UI Automation, ShowWindow). If that program
is busy or hung, the call never returns, the single worker thread that
handles every request stays stuck inside it, and Eva stops answering while
the avatar keeps standing there -- which looks like a crash.

run_with_deadline() gives such a call a time limit. If it does not finish:
  * the model receives an ordinary failure ("did not finish"), so the turn
    carries on and Eva can say so and try something else;
  * the stack of the stuck thread is written to eva.log, so the next log
    shows WHERE it hung instead of just that it did;
  * the stuck thread is abandoned (a thread cannot be killed; it is a daemon
    and ends with the process).
A stack is also logged after SLOW_SECONDS while still waiting, so the
evidence exists even if the person closes Eva before the deadline.
"""
import sys
import threading
import time
import traceback

from logger import get_logger

log = get_logger(__name__)

SLOW_SECONDS = 8.0
DEFAULT_SECONDS = 45.0

# Which tools get a deadline, and how long, is declared with each tool in
# core/toolset/ (the `deadline=` field) -- no separate table to keep in step.


def _stack_of(thread) -> str:
    frame = sys._current_frames().get(thread.ident)
    return "".join(traceback.format_stack(frame)) if frame is not None else "(no frame)"


def run_with_deadline(name: str, fn, seconds: float, slow_after: float = SLOW_SECONDS):
    """Runs fn() and returns its result, re-raising anything it raises. On
    timeout returns a failure dict instead."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:          # re-raised on the caller's thread
            box["error"] = exc

    t = threading.Thread(target=target, name=f"tool-{name}", daemon=True)
    started = time.monotonic()
    t.start()
    t.join(min(slow_after, seconds))
    if t.is_alive() and seconds > slow_after:
        log.warning(f"Tool '{name}' still running after {slow_after:.0f}s. Where it is:\n{_stack_of(t)}")
        t.join(seconds - slow_after)
    if t.is_alive():
        waited = time.monotonic() - started
        log.error(f"Tool '{name}' did not finish within {seconds:.0f}s (waited {waited:.0f}s) -- "
                  f"abandoned. Where it is stuck:\n{_stack_of(t)}")
        return {"ok": False, "timed_out": True,
                "error": (f"{name} did not finish within {seconds:.0f}s. The program it was acting on "
                          "is probably busy or not responding, and nothing was confirmed. Do not repeat "
                          "it blindly: tell the person, or look at the screen and try another approach.")}
    if "error" in box:
        raise box["error"]
    return box.get("value")
