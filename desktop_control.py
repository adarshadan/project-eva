"""
Eva -- native Windows desktop control. This is the layer that actually
matters: real UI Automation element resolution and invocation (not just
coordinate guessing), native SendInput for keystrokes, and post-action
verification. click_coords/pyautogui exist only as the documented fallback
for the rare app with no accessibility tree.
"""
import re
import threading
import time
import tempfile
import os
import sys
import ctypes
from datetime import datetime
import pyautogui
import pygetwindow as gw
import mss

from logger import get_logger

log = get_logger(__name__)
# pywinauto is imported lazily inside _connect() -- NOT here at module
# level. Importing it eagerly initializes Windows COM (UI Automation
# needs MTA) as a side effect, on whatever thread runs this import. If
# that happens to be the main/GUI thread before QApplication sets up its
# own COM/OLE state (STA), the two collide with exactly the
# "COM error 0x80010106: Cannot change thread mode after it is set"
# crash. Deferring the import means COM only gets initialized inside
# whatever background thread actually calls a UIA function -- a fresh
# thread with no prior COM state, so there's nothing to collide with.

# PyAutoGUI's fail-safe raises FailSafeException on EVERY call -- keyboard
# calls included, which is the surprising part -- once the cursor sits in a
# screen corner. It is meant as "slam the mouse into a corner to abort a
# runaway script", but for Eva it is a latch with no key: recovering means
# moving the cursor, moving the cursor means calling pyautogui, and that
# call raises too.
#
# eva.log 2026-09-26 is the whole story. One click on a hidden control put
# the cursor at (0,0), and from that instant every send_hotkey, type_text,
# click_coords and move_mouse failed identically for two solid minutes.
# Vision located the Save button correctly at (803, 634) three separate
# times and the click could never be delivered. Eva tried move_mouse to
# escape; that raised as well.
#
# The corner check therefore moves into our own code, where it is a
# refusal rather than a latch (_rect_is_usable and _safe_point below), and
# recovery uses the Win32 call directly, bypassing pyautogui entirely. The
# person's means of stopping Eva -- the spoken "stop", the tray icon, the
# radial menu -- all work regardless of where the cursor is, so nothing
# the fail-safe was protecting is lost.
pyautogui.FAILSAFE = False


def _norm_ws(value: str) -> str:
    """Whitespace-normalised text, for comparing what was sent against what
    an editor actually stored. Notepad turns "\n\n" into "\r\r", Word
    reflows, and some controls collapse runs of spaces -- none of which
    means the text failed to arrive."""
    return " ".join((value or "").split())


def _safe_point(x: int, y: int):
    """Clamps a target inside the screen and away from the corners."""
    w, h = pyautogui.size()
    return (max(2, min(int(x), w - 2)), max(2, min(int(y), h - 2)))


# Set by main.py: make_way(x, y) moves Eva's own windows (chat box, bubbles, body) off a screen
# point. Called from task threads; it hops to the GUI thread itself.
MAKE_WAY = None


def _own_window_at(x, y) -> bool:
    """Whether the window that would receive a click at (x, y) is one of Eva's own."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        root = user32.GetAncestor(hwnd, 2) if hwnd else None            # GA_ROOT
        return bool(root) and is_own_window(int(root))
    except Exception:
        return False


def _clear_point(x, y):
    """None when a click at (x, y) reaches what is really there; otherwise an error result.

    eva.log 2026-10-17 12:19: typing into Notepad clicked the middle of its document -- where
    her own chat box was -- and the text went into her chat box. Her own windows are asked to
    step aside first; if they still cover the spot, nothing is clicked."""
    if not _own_window_at(x, y):
        return None
    hook = MAKE_WAY
    if hook is not None:
        try:
            hook(int(x), int(y))
        except Exception as e:
            log.warning(f"Could not move Eva's windows out of the way: {e}")
        for _ in range(15):
            time.sleep(0.1)
            if not _own_window_at(x, y):
                return None
    return {"ok": False, "delivered": False,
            "error": "one of your own windows (your chat box or your body) covers that spot, so the "
                     "click would land on you, not the target -- nothing was clicked. Use the element "
                     "tools (ui_tree / act_on_element / type_into_element) or ask the person to move it."}


def _rect_is_usable(rect, screen=None):
    """Whether a control's rect is a real, clickable, on-screen area.

    A zero rect (0, 0, 0, 0) is what Windows reports for hidden and
    virtualised controls -- Notepad's Save dialog is full of them. Clicking
    such a control's "centre" drives the cursor to the top-left corner,
    which is how one bad click disabled all input for the session."""
    try:
        width, height = rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        return False
    if width <= 1 or height <= 1:
        return False
    cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    if cx <= 1 or cy <= 1:
        return False
    if screen and not (0 <= cx < screen[0] and 0 <= cy < screen[1]):
        return False
    return True


def recover_input():
    """Moves the cursor back to the centre of the screen using the Win32
    API directly, so it works even in states where pyautogui would refuse.
    Safe to call at any time."""
    w, h = pyautogui.size()
    try:
        ctypes.windll.user32.SetCursorPos(w // 2, h // 2)
        return {"ok": True, "moved_to": (w // 2, h // 2)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _window_pid(hwnd: int):
    """The process id owning a window, or None if it cannot be read."""
    try:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd),
                                                      ctypes.byref(pid))
        return pid.value or None
    except Exception:
        return None


def is_own_window(hwnd: int) -> bool:
    """Whether a window belongs to Eva's own process.

    eva.log 2026-09-26 23:29: asked to close the open applications, Eva
    called list_windows -- which listed her own four "python" windows
    alongside the person's -- focused one, and sent Alt+F4 three times in
    five seconds. The person's next words were "if I can't see you now".
    She closed herself, and the heartbeat watchdog then fired three times
    against a page whose window no longer existed.

    Nothing stopped her, because no layer knew which windows were hers."""
    return _window_pid(hwnd) == os.getpid()


def list_windows(include_own: bool = False):
    """The person's windows. Eva's own are excluded by default -- they are
    not something she was ever asked to act on, and listing them is what
    let her close herself."""
    out, own = [], 0
    for w in gw.getAllWindows():
        if not w.title.strip():
            continue
        if is_own_window(w._hWnd):
            own += 1
            if not include_own:
                continue
            out.append({"handle": w._hWnd, "title": w.title, "is_eva_herself": True})
            continue
        out.append({"handle": w._hWnd, "title": w.title})
    result = {"windows": out}
    if own and not include_own:
        result["note"] = f"{own} of Eva's own windows are not listed and must not be closed"
    return result


def get_foreground_app():
    """The window in front. Read straight from Win32: pygetwindow's
    getActiveWindow() raises "1400 - Invalid window handle" when that window
    is closing (the Run dialog after Enter, eva.log 2026-10-11 13:50), which
    the z-order timer then logged twice a second."""
    try:
        import ctypes
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        h = u.GetForegroundWindow()
        if not h or not u.IsWindow(ctypes.c_void_p(h)):
            return {"title": None, "handle": None}
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(ctypes.c_void_p(h), buf, 512)
        return {"title": buf.value, "handle": int(h)}
    except Exception:
        try:
            w = gw.getActiveWindow()
            return {"title": w.title if w else None, "handle": w._hWnd if w else None}
        except Exception:
            return {"title": None, "handle": None}


def _force_foreground(hwnd: int) -> bool:
    """Brings a window to the front despite Windows' foreground lock, the rule
    that lets a background process ask but not insist. pygetwindow's
    activate() raised "Error code from Windows: 0 - The operation completed
    successfully" whenever the lock refused it (eva.log 2026-10-10, Settings,
    three times). The standard sequence, verified after each step:
      1. restore it if minimised, then SetForegroundWindow;
      2. attach to the input of the thread that owns the foreground and of the
         target's thread, then BringWindowToTop + SetForegroundWindow;
      3. last resort: a synthetic Alt tap (input the lock accepts), then retry.
    Returns whether it is now the foreground window."""
    if sys.platform != "win32":
        return False
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetForegroundWindow.restype = ctypes.c_void_p
    h = ctypes.c_void_p(int(hwnd))

    def front():
        return (u.GetForegroundWindow() or 0) == int(hwnd)
    if u.IsIconic(h):
        u.ShowWindowAsync(h, 9)                          # SW_RESTORE
        time.sleep(0.12)
    u.SetForegroundWindow(h)
    if front():
        return True
    fg = ctypes.c_void_p(u.GetForegroundWindow() or 0)
    me = k.GetCurrentThreadId()
    attached = []
    for tid in {u.GetWindowThreadProcessId(fg, None), u.GetWindowThreadProcessId(h, None)}:
        if tid and tid != me and u.AttachThreadInput(me, tid, True):
            attached.append(tid)
    try:
        u.BringWindowToTop(h)
        u.SetForegroundWindow(h)
    finally:
        for tid in attached:
            u.AttachThreadInput(me, tid, False)
    time.sleep(0.06)
    if front():
        return True
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    u.keybd_event(VK_MENU, 0, 0, 0)
    u.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    u.SetForegroundWindow(h)
    time.sleep(0.06)
    return front()


def focus_window(window_handle: int, verify: bool = True, allow_own: bool = False):
    """Brings a window to the front and CHECKS that it got there.

    pygetwindow's activate() is silently ignored by Windows when the
    calling process is not itself in the foreground -- the state Eva is in
    right after her own dialog closes. It used to return {"ok": True}
    regardless, so the model believed focus had moved and typed into
    whatever actually had it. Same class as the other unconditional
    successes: report what happened."""
    # allow_own is for internal restoration only (confirm_blocking putting
    # focus back where it was, which may legitimately be Eva's own window).
    # Model-driven calls leave it False.
    if not allow_own and is_own_window(window_handle):
        return {"ok": False, "error": "that window belongs to Eva herself -- refusing to focus it."}
    for w in gw.getAllWindows():
        if w._hWnd == window_handle:
            if sys.platform == "win32":
                _force_foreground(window_handle)
            else:
                try:
                    w.activate()
                except Exception as e:
                    return {"ok": False, "error": f"activate failed: {e}"}
            if not verify:
                return {"ok": True, "verified": False}
            time.sleep(0.15)
            actual, title = _foreground_handle()
            if actual == window_handle:
                return {"ok": True, "verified": True, "title": title}
            return {"ok": False, "verified": False,
                    "error": f"the window did not come to the front -- {title!r} still has "
                             "focus. Try restore_foreground, or click the window first.",
                    "foreground_title": title}
    return {"ok": False, "error": STALE_HANDLE_MSG}


def get_window_rect(window_handle: int):
    """Screen rect of a window by handle, so main.py can walk the avatar
    to the window's centre before acting on it (the "physical presence"
    layer). Coordinates are screen pixels; None if the handle is gone."""
    for w in gw.getAllWindows():
        if w._hWnd == window_handle:
            return {"left": w.left, "top": w.top,
                    "right": w.left + w.width, "bottom": w.top + w.height}
    return None


def _verify_window_state(window_handle: int, check, label: str, expected: str):
    """Re-reads a window after a state change and reports what happened.

    pygetwindow's moveTo/resizeTo/minimize/maximize/restore all return None
    and swallow the outcome. A maximized window ignores moveTo, a window
    with a fixed size ignores resizeTo, and a window owned by an elevated
    process ignores all of them -- each silently, each previously reported
    as {"ok": True}. Same defect that hid four other failures."""
    # ShowWindowAsync only POSTS the request, so these polls are how the
    # result is learned; give the window up to ~1.5s to act on it.
    for _ in range(15):
        time.sleep(0.1)
        try:
            w0 = next((x for x in gw.getAllWindows() if x._hWnd == window_handle), None)
            if w0 is not None and check(w0):
                break
        except Exception:
            break
    for w in gw.getAllWindows():
        if w._hWnd == window_handle:
            try:
                if check(w):
                    return {"ok": True, "verified": True}
                return {"ok": False, "verified": False,
                        "error": f"the window did not {label} -- it is still at {expected}. "
                                 "It may be maximized, fixed-size, or owned by an elevated "
                                 "process this cannot control."}
            except Exception as e:
                return {"ok": True, "verified": False, "note": f"could not verify: {e}"}
    return {"ok": False, "error": "the window disappeared while being changed"}


def move_window(window_handle: int, x: int, y: int):
    if is_own_window(window_handle):
        return {"ok": False, "error": "that window belongs to Eva herself -- refusing to move it."}
    for w in gw.getAllWindows():
        if w._hWnd == window_handle:
            w.moveTo(x, y)
            return _verify_window_state(
                window_handle,
                lambda win: abs(win.left - x) <= 8 and abs(win.top - y) <= 8,
                "move", f"({w.left}, {w.top})")
    return {"ok": False, "error": STALE_HANDLE_MSG}


def resize_window(window_handle: int, w: int, h: int):
    if is_own_window(window_handle):
        return {"ok": False, "error": "that window belongs to Eva herself -- refusing to resize it."}
    for win in gw.getAllWindows():
        if win._hWnd == window_handle:
            win.resizeTo(w, h)
            return _verify_window_state(
                window_handle,
                lambda x: abs(x.width - w) <= 16 and abs(x.height - h) <= 16,
                "resize", f"{win.width}x{win.height}")
    return {"ok": False, "error": STALE_HANDLE_MSG}


SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9


def _show_async(hwnd: int, cmd: int) -> bool:
    """ShowWindowAsync, not ShowWindow / pygetwindow's minimize(). The
    synchronous call sends messages to the target's UI thread and waits; if
    that program is busy or hung it never returns, and Eva's only worker
    thread hangs with it (eva.log 2026-09-30 20:29 ended on minimize_window
    of the console window). The async form posts the request and returns."""
    try:
        return bool(ctypes.windll.user32.ShowWindowAsync(ctypes.c_void_p(int(hwnd)), cmd))
    except Exception as e:
        log.warning(f"ShowWindowAsync failed: {e}")
        return False


def minimize_window(window_handle: int):
    if is_own_window(window_handle):
        return {"ok": False, "error": "that window belongs to Eva herself -- refusing to minimize it."}
    for w in gw.getAllWindows():
        if w._hWnd == window_handle:
            _show_async(window_handle, SW_MINIMIZE)
            return _verify_window_state(window_handle, lambda x: x.isMinimized,
                                        "minimize", "not minimized")
    return {"ok": False, "error": STALE_HANDLE_MSG}


def maximize_window(window_handle: int):
    if is_own_window(window_handle):
        return {"ok": False, "error": "that window belongs to Eva herself -- refusing to maximize it."}
    for w in gw.getAllWindows():
        if w._hWnd == window_handle:
            _show_async(window_handle, SW_MAXIMIZE)
            return _verify_window_state(window_handle, lambda x: x.isMaximized,
                                        "maximize", "not maximized")
    return {"ok": False, "error": STALE_HANDLE_MSG}


def restore_window(window_handle: int):
    """Un-minimizes/un-maximizes back to normal size and position."""
    if is_own_window(window_handle):
        return {"ok": False, "error": "that window belongs to Eva herself -- refusing to restore it."}
    for w in gw.getAllWindows():
        if w._hWnd == window_handle:
            _show_async(window_handle, SW_RESTORE)
            return _verify_window_state(window_handle,
                                        lambda x: not (x.isMinimized or x.isMaximized),
                                        "restore", "still minimized or maximized")
    return {"ok": False, "error": STALE_HANDLE_MSG}


def _connect(window_handle: int):
    from pywinauto.application import Application  # see note above -- deliberately lazy
    return Application(backend="uia").connect(handle=window_handle)


def _find_control(win, element_id: str):
    # Fast path: child_window(auto_id=...) leverages UIA's own tree cache
    # instead of a full linear descendants() scan -- meaningfully faster
    # for complex apps with hundreds of elements. Only usable when
    # element_id actually IS a real automation_id, though (this function
    # also matches on control_id/window_text as fallbacks -- see below),
    # and child_window's own search isn't guaranteed to find every
    # genuinely nested control the full scan would, so this is a
    # best-effort shortcut with the original full scan kept as the
    # fallback, never a replacement for it.
    try:
        ctrl = win.child_window(auto_id=element_id)
        if ctrl.exists(timeout=0, retry_interval=0):  # immediate check, no waiting/retrying
            return ctrl
    except Exception:
        pass
    found = _find_cached(win, element_id)
    if found:
        return found                      # not found by the fast read: the old walk decides
    for idx, ctrl in enumerate(win.descendants()):
        try:
            if _element_id_for(ctrl, idx) == element_id:
                return ctrl
        except Exception:
            continue
    return None


def _find_cached(win, element_id):
    """The control with this id via one cached read (see _ui_tree_inproc), or None."""
    try:
        try:
            handle = win.wrapper_object().handle
        except Exception:
            handle = win.handle
        for idx, e in enumerate(_cached_elements(handle)):
            if _cached_id(e, idx) == element_id:
                from pywinauto.controls.uiawrapper import UIAWrapper
                from pywinauto.uia_element_info import UIAElementInfo
                return UIAWrapper(UIAElementInfo(e))
        return None
    except Exception:
        return None


def _element_id_for(ctrl, index: int) -> str:
    """A unique, resolvable id for one control.

    The expression this replaces was:
        ctrl.automation_id() or str(ctrl.control_id()) or ctrl.window_text()
    and str(None) is the TRUTHY string "None". Every control lacking both an
    automation_id and a control_id was therefore published to the model as
    element_id "None" -- dozens of identical ids per window. In eva.log
    2026-09-26 the model quite reasonably called click_element("None") in a
    Save dialog, _find_control returned whichever "None" it met first, and
    that one was a hidden control at the screen corner.

    Positional ids as the fallback: unique, and _find_control resolves them
    by the same enumeration order that produced them."""
    try:
        auto = ctrl.automation_id()
        if auto:
            return str(auto)
    except Exception:
        pass
    try:
        cid = ctrl.control_id()
        if cid:                      # 0 and None are both "no id"
            return str(cid)
    except Exception:
        pass
    try:
        text = (ctrl.window_text() or "").strip()
        if text:
            return text
    except Exception:
        pass
    return f"#{index}"               # always unique, never the string "None"


STALE_HANDLE_MSG = ("that window no longer exists -- it was already closed or its handle changed. "
                    "Call list_windows for the current handles.")
UIA_WORKER_TIMEOUT = 25.0

ELEMENT_GONE_MSG = ("element {eid!r} is not in that window any more -- the window changed since ui_tree "
                    "(a dialog opened or closed, the page moved on). Call ui_tree again and use the new "
                    "element_id; do not guess coordinates.")


def _window_alive(window_handle) -> bool:
    """Whether the handle still names a window. Module function so the checks can replace it."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsWindow(ctypes.c_void_p(int(window_handle))))
    except Exception:
        return True


def uia_error(e, element_id=None) -> dict:
    """A UI Automation failure in words she can act on, instead of a raw
    COM / pywinauto message ('(-2147220991, 'An event was unable to invoke
    any of the subscribers'...)' told her nothing)."""
    m = f"{type(e).__name__}: {e}"
    low = m.lower()
    what = f"element {element_id!r}" if element_id else "that control"
    if "notenabled" in low or "not enabled" in low:
        hint = f"{what} is greyed out (disabled) -- something must be done first (fill a required field, " \
               "accept a choice); read the window to see what"
    elif "notvisible" in low or "not visible" in low or "offscreen" in low:
        hint = f"{what} is hidden or scrolled out of view -- act_on_element with action=scroll_into_view, or " \
               "scroll the window, then try again"
    elif "timed out" in low or "timeout" in low:
        hint = "the program did not answer in time (it is busy or not responding) -- wait a moment and try " \
               "once more, or use look_at_screen to see its state"
    elif "access is denied" in low or "e_accessdenied" in low or "-2147024891" in low:
        hint = "Windows refused access to that window (it may run as administrator) -- tell the person"
    elif "-2147" in low or "comerror" in low or "elementnotavailable" in low:
        hint = f"{what} vanished while acting on it (the window changed) -- call ui_tree again"
    else:
        return {"ok": False, "error": str(e) or type(e).__name__}
    return {"ok": False, "error": hint, "detail": m[:200]}


def _element(window_handle, element_id):
    """(control, None) or (None, error result): the one place that checks it is
    not Eva's own window, the window still exists, connects, and finds the
    element -- click_element, type_into_element, act_on_element and
    resolve_element_center all start here."""
    try:
        h = int(window_handle)
    except (TypeError, ValueError):
        return None, {"ok": False, "error": "window_handle must be a number from list_windows"}
    if is_own_window(h):
        return None, {"ok": False, "error": "that window belongs to Eva herself -- refusing."}
    if not _window_alive(h):
        return None, {"ok": False, "error": STALE_HANDLE_MSG}
    try:
        win = _connect(h).window(handle=h)
        ctrl = _find_control(win, str(element_id))
    except Exception as e:
        return None, uia_error(e, element_id)
    if not ctrl:
        return None, {"ok": False, "error": ELEMENT_GONE_MSG.format(eid=element_id)}
    return ctrl, None


class _UiaServer:
    """ONE long-lived uia_worker (--serve). Requests are serialised; a reply that does not come
    within the timeout means the worker hung -- it is killed and the next request starts a new
    one. A crash is the same. So isolation is exactly as before (UIA faults never reach Eva),
    without a new Python process per read (0.5-1.4 s each, plan Phase 1)."""

    START_TIMEOUT = 20.0

    def __init__(self, cmd=None):
        here = os.path.dirname(os.path.abspath(__file__))
        self.cmd = cmd or [sys.executable, os.path.join(here, "uia_worker.py"), "--serve"]
        self._lock = threading.Lock()
        self._proc = None
        self._replies = None
        self._next = 0
        self.restarts = 0
        self.disabled = False             # the server cannot start here: one-shot workers instead

    def _start(self):
        import json
        import queue
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, cwd=here,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        replies = queue.Queue()

        def pump():
            for line in proc.stdout:
                replies.put(line)
            replies.put(None)                 # the worker ended
        threading.Thread(target=pump, daemon=True, name="eva-uia-reader").start()
        try:
            first = replies.get(timeout=self.START_TIMEOUT)
        except queue.Empty:
            first = None
        if not first or not json.loads(first).get("ready"):
            self._kill(proc)
            raise RuntimeError("the UI Automation worker did not start")
        self._proc, self._replies = proc, replies

    @staticmethod
    def _kill(proc):
        try:
            proc.kill()
        except Exception:
            pass

    def stop(self):
        with self._lock:
            if self._proc is not None:
                self._kill(self._proc)
            self._proc = None

    def call(self, name, args, timeout):
        """(payload, None) | (None, error text) | (None, None) when the server is unusable."""
        import json
        import queue
        with self._lock:
            if self.disabled:
                return None, None
            if self._proc is None or self._proc.poll() is not None:
                try:
                    self._start()
                except Exception as e:
                    log.warning(f"Persistent UI Automation worker unavailable ({e}) -- using one-shot workers.")
                    self.disabled = True
                    return None, None
            self._next += 1
            rid = self._next
            try:
                self._proc.stdin.write(json.dumps({"id": rid, "fn": name, "args": args}) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError):
                self._kill(self._proc)
                self._proc = None
                self.restarts += 1
                return None, "the UI Automation reader stopped unexpectedly; Eva is unaffected -- try again"
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                try:
                    line = self._replies.get(timeout=max(0.01, left))
                except queue.Empty:
                    log.warning(f"UI Automation worker '{name}' timed out after {timeout:.0f}s -- restarted")
                    self._kill(self._proc)
                    self._proc = None
                    self.restarts += 1
                    return None, (f"reading that window took longer than {timeout:.0f}s and was stopped "
                                  "(a very large or unresponsive window). Use look_at_screen or "
                                  "locate_on_screen instead.")
                if line is None:              # crashed mid-request (a native UIA fault)
                    code = self._proc.poll()
                    log.error(f"UI Automation worker died on '{name}' (exit {code}) -- restarted")
                    self._proc = None
                    self.restarts += 1
                    return None, (f"the UI Automation reader crashed on that window (exit code {code}); "
                                  "Eva is unaffected. Use look_at_screen or locate_on_screen for this window.")
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue                  # not a reply line
                if reply.get("id") != rid:
                    continue                  # a late reply to a request that already timed out
                if "error" in reply:
                    return None, f"the UI Automation reader failed: {reply['error']}"[:300]
                return reply.get("result"), None


UIA_SERVER = _UiaServer()


def _isolated(name: str, args: dict, timeout: float = UIA_WORKER_TIMEOUT):
    """Runs a read-only UI Automation enumeration in a child process (see
    uia_worker.py). A native crash or hang there is reported as a normal
    failure instead of killing Eva. Returns (payload, error_text).
    The long-lived worker is used when it can start; otherwise one process per call."""
    payload, err = UIA_SERVER.call(name, args, timeout)
    if payload is not None or err is not None:
        return payload, err
    return _isolated_once(name, args, timeout)


def _isolated_once(name: str, args: dict, timeout: float = UIA_WORKER_TIMEOUT):
    """Runs a read-only UI Automation enumeration in a child process (see
    uia_worker.py). A native crash or hang there is reported as a normal
    failure instead of killing Eva. Returns (payload, error_text)."""
    import json
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        cp = subprocess.run(
            [sys.executable, os.path.join(here, "uia_worker.py"), name, json.dumps(args)],
            capture_output=True, text=True, timeout=timeout, cwd=here,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        log.warning(f"UI Automation worker '{name}' timed out after {timeout:.0f}s -- killed")
        return None, (f"reading that window took longer than {timeout:.0f}s and was stopped "
                      "(a very large or unresponsive window). Use look_at_screen or locate_on_screen instead.")
    except Exception as e:
        return None, f"could not start the UI Automation reader: {e}"
    if cp.returncode != 0:
        log.error(f"UI Automation worker '{name}' died (exit {cp.returncode}): {(cp.stderr or '')[-400:]}")
        return None, (f"the UI Automation reader crashed on that window (exit code {cp.returncode}); "
                      "Eva is unaffected. Use look_at_screen or locate_on_screen for this window.")
    try:
        return json.loads(cp.stdout.strip().splitlines()[-1]), None
    except Exception:
        return None, "the UI Automation reader returned unreadable output"


UI_TREE_SHOW = 160            # elements listed per call; the rest are counted, and `contains` narrows


def ui_tree(window_handle: int, contains: str = None):
    """Real UIA element tree: id, name, role, whether it's invokable/editable,
    and its rect -- the model uses this to decide click vs type vs invoke.
    Runs isolated: eva.log 2026-09-30 20:18 ended inside this call.

    What she sees first is what she can use: visible, named, actionable controls, then other named
    ones; off-screen and nameless parts are left out (counted). eva.log 2026-10-17 13:59: a File
    Explorer window's 540 elements were cut at 8,000 characters in tree order, "play_lab" fell in
    the cut, and she located it by vision instead. `contains` lists only controls whose name holds
    those words. Ids are unchanged (they come from the full enumeration), so every id resolves."""
    if is_own_window(int(window_handle)):
        return {"elements": [], "error": "that window belongs to Eva herself"}
    payload, err = _isolated("ui_tree", {"window_handle": int(window_handle)})
    if err:
        return {"ok": False, "elements": [], "error": err}
    return _rank_tree(payload, contains)


def _rank_tree(payload, contains=None):
    els = list((payload or {}).get("elements") or [])
    total = len(els)
    named = lambda e: bool(str(e.get("name") or "").strip())
    useful = [e for e in els if not e.get("offscreen") and (named(e) or e.get("patterns"))]
    if contains:
        words = [w for w in str(contains).lower().split() if w]
        useful = [e for e in useful if all(w in str(e.get("name") or "").lower() for w in words)]
    ranked = sorted(useful, key=lambda e: 0 if (named(e) and e.get("patterns")) else 1 if named(e) else 2)
    shown = []
    for e in ranked[:UI_TREE_SHOW]:
        e = {k: v for k, v in e.items() if k != "offscreen"}
        if len(str(e.get("name") or "")) > 160:               # a document's whole text is not a label
            e["name"] = str(e["name"])[:160] + "..."
        shown.append(e)
    out = {"elements": shown, "total": total, "shown": len(shown)}
    hidden = total - len(shown)
    if hidden:
        out["note"] = (f"{hidden} more elements (off-screen, nameless, or beyond the first {UI_TREE_SHOW}) are not "
                       "listed; call ui_tree again with contains='words' to find a specific control by its name.")
    if "error" in (payload or {}):
        out["error"] = payload["error"]
    return out


# ---- one cross-process read instead of six per element (Phase 1: ui_tree < 200 ms) -------------
# pywinauto's descendants() is FindAll(TreeScope_Descendants, TrueCondition) followed by separate
# calls for every property of every element. FindAllBuildCache with the SAME condition returns the
# same elements in the same order -- so the same "#index" ids -- with every property fetched in
# one round trip. Ids are built exactly as _element_id_for builds them: automation id, else the
# native control id, else the element's text (pywinauto's window_text: Text pattern when the
# element has a class name and the pattern, else its name), else "#index".
_UIA_P = {"aid": 30011, "name": 30005, "cls": 30012, "type": 30003, "hwnd": 30020, "rect": 30001,
          "invoke": 30031, "value": 30043, "text": 30040, "offscreen": 30022}


def _cached_elements(window_handle):
    from pywinauto.uia_defines import IUIA
    iu = IUIA()
    root = iu.iuia.ElementFromHandle(int(window_handle))
    cr = iu.iuia.CreateCacheRequest()
    for pid in _UIA_P.values():
        cr.AddProperty(pid)
    arr = root.FindAllBuildCache(4, iu.true_condition, cr)              # TreeScope_Descendants
    return [arr.GetElement(i) for i in range(arr.Length if arr is not None else 0)]


def _cached_text(e) -> str:
    """pywinauto's window_text() for a cached element."""
    name = e.CachedName or ""
    if not (e.CachedClassName or ""):
        return name
    try:
        if e.GetCachedPropertyValue(_UIA_P["text"]):
            from pywinauto.uia_defines import get_elem_interface
            return get_elem_interface(e, "Text").DocumentRange.GetText(-1)
    except Exception:
        pass
    return name


def _cached_id(e, index) -> str:
    aid = e.CachedAutomationId
    if aid:
        return str(aid)
    hwnd = e.CachedNativeWindowHandle
    if hwnd:
        try:
            import ctypes
            cid = ctypes.windll.user32.GetWindowLongW(int(hwnd), -12)       # GWL_ID, as pywinauto
            if cid:
                return str(cid)
        except Exception:
            pass
    text = (_cached_text(e) or "").strip()
    return text if text else f"#{index}"


def _friendly(control_type_id) -> str:
    from pywinauto.uia_defines import IUIA
    from pywinauto.controls.uiawrapper import _friendly_classes
    ct = IUIA().known_control_type_ids.get(int(control_type_id or 0), "InvalidControlType")
    return _friendly_classes.get(ct) or ct


def _ui_tree_inproc(window_handle: int, max_elements: int = 3000, budget_s: float = 8.0):
    try:
        started = time.time()
        elements = []
        for idx, e in enumerate(_cached_elements(window_handle)):
            if len(elements) >= max_elements or time.time() - started > budget_s:
                break
            try:
                r = e.CachedBoundingRectangle
                patterns = (["invoke"] if e.GetCachedPropertyValue(_UIA_P["invoke"]) else []) + \
                           (["value"] if e.GetCachedPropertyValue(_UIA_P["value"]) else [])
                elements.append({
                    "element_id": _cached_id(e, idx),
                    "name": _cached_text(e),
                    "role": _friendly(e.CachedControlType),
                    "rect": f"(L{int(r.left)}, T{int(r.top)}, R{int(r.right)}, B{int(r.bottom)})",
                    "patterns": patterns,
                    "offscreen": bool(e.GetCachedPropertyValue(_UIA_P["offscreen"])),
                })
            except Exception:
                continue
        return {"elements": elements}
    except Exception as e:
        log.info(f"Cached UI read failed ({e}) -- using the element-by-element walk")
        return _ui_tree_slow(window_handle, max_elements, budget_s)


def _ui_tree_slow(window_handle: int, max_elements: int = 500, budget_s: float = 8.0):
    """The original walk: ~6 cross-process calls per element (2 s on a File Explorer window).
    Kept as the fallback and as the reference the fast read is checked against."""
    try:
        app = _connect(window_handle)
        win = app.window(handle=window_handle)
        elements = []
        started = time.time()
        for idx, ctrl in enumerate(win.descendants()):
            if len(elements) >= max_elements or time.time() - started > budget_s:
                break
            try:
                eid = _element_id_for(ctrl, idx)
                patterns = []
                try:
                    if ctrl.iface_invoke:
                        patterns.append("invoke")
                except Exception:
                    pass
                try:
                    if ctrl.iface_value:
                        patterns.append("value")
                except Exception:
                    pass
                elements.append({
                    "element_id": eid,
                    "name": ctrl.window_text(),
                    "role": ctrl.friendly_class_name(),
                    "rect": str(ctrl.rectangle()),
                    "patterns": patterns,
                })
            except Exception:
                continue
        return {"elements": elements}
    except Exception as e:
        return {"elements": [], "error": str(e)}


def resolve_element_center(window_handle: int, element_id: str):
    """Real on-screen center point of a native element, for the avatar to
    walk to and gesture at before the actual action fires."""
    try:
        ctrl, err = _element(window_handle, element_id)
        if err:
            return None
        rect = ctrl.rectangle()
        return {"x": (rect.left + rect.right) // 2, "y": (rect.top + rect.bottom) // 2}
    except Exception:
        return None


def click_element(window_handle: int, element_id: str):
    """Native invocation: uses the UIA Invoke pattern when the control
    supports it (most buttons/menu items), falls back to a real synthetic
    click on the control's own screen rect otherwise -- both are native
    UIA operations, not blind coordinate guesses."""
    ctrl, err = _element(window_handle, element_id)
    if err:
        return err
    try:
        try:
            ctrl.invoke()  # native UIA Invoke pattern -- most reliable path
            return {"ok": True, "method": "invoke"}
        except Exception:
            # invoke() is unavailable, so this falls back to driving the
            # real cursor at the control's rect. Check the rect first: a
            # hidden or virtualised control reports (0, 0, 0, 0), and
            # clicking its "centre" parks the cursor in the screen corner.
            try:
                rect = ctrl.rectangle()
            except Exception:
                rect = None
            if rect is None or not _rect_is_usable(rect, pyautogui.size()):
                return {"ok": False,
                        "error": f"element {element_id!r} is hidden or has no on-screen "
                                 f"area ({rect}), so it cannot be clicked. Pick a visible "
                                 "element from ui_tree, or use locate_on_screen and "
                                 "click_coords."}
            ctrl.click_input()  # native synthetic click at the control's real rect
            return {"ok": True, "method": "click_input"}
    except Exception as e:
        return uia_error(e, element_id)


def type_into_element(window_handle: int, element_id: str, text: str,
                      mode: str = "replace"):
    """Prefer setting an edit control's value directly via the native UIA
    Value pattern (fast, no keystroke race conditions); fall back to
    focusing the control and using native SendInput-based typing.

    mode: "replace" (default) clears the control first; "append" adds to
    the end; "at_cursor" types wherever the caret happens to be.

    WHY THE CARET IS POSITIONED EXPLICITLY. The fallback path used to
    click_input() and type immediately. click_input() lands in the MIDDLE
    of the control, which puts the caret in the middle of the existing
    text -- so the new text was spliced into the middle of a word.
    Confirmed in eva.log 2026-09-26, where appending to an essay produced
    "...essential for a sustainable futBeyond electricity generation..."
    and left the orphaned "ure." at the very end. The result still said
    {"ok": True} and nobody noticed until the person read the screen.

    AND WHY IT IS VERIFIED. Same reason as type_text: nothing here
    reported whether the text actually arrived."""
    if mode not in ("replace", "append", "at_cursor"):
        return {"ok": False, "error": f"unknown mode {mode!r}"}
    ctrl, err = _element(window_handle, element_id)
    if err:
        return err
    try:
        before = read_window_text(window_handle).get("text", "")

        method = None
        if mode == "replace":
            try:
                ctrl.set_edit_text(text)  # native UIA Value pattern
                method = "set_edit_text"
            except Exception:
                method = None
        if method is None:
            r = ctrl.rectangle()
            covered = _clear_point((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            if covered:
                # Focus the control through UI Automation instead of clicking whatever is on top
                # of it, and only type if its window really has the keyboard.
                ctrl.set_focus()
                time.sleep(0.15)
                if _foreground_handle()[0] != int(window_handle):
                    return covered
            else:
                ctrl.click_input()
            time.sleep(0.15)
            if mode == "replace":
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("delete")
            elif mode == "append":
                pyautogui.hotkey("ctrl", "end")
            time.sleep(0.1)
            type_text(text, verify=False)
            method = f"click_and_type ({mode})"

        time.sleep(0.3)
        after = read_window_text(window_handle)
        content = after.get("text", "")
        # Compare on normalised whitespace. Editors rewrite line endings:
        # text sent as "\n\n" comes back from Notepad as "\r\r", so a raw
        # substring check failed on any text containing a blank line --
        # eva.log 2026-09-26 23:38, where 1300+ characters HAD landed and
        # the tool reported "the text is NOT in the target window".
        probe = _norm_ws(text)[:60]
        if probe and not any(probe in _norm_ws(t) for t in [content] + list(after.get("values") or [])):
            return {"ok": False, "typed": False, "method": method, "verified": False,
                    "error": "the text is NOT in the target window afterwards. Do not "
                             "report this as done.",
                    "chars_in_window": after.get("length")}
        # Splicing into the middle passes a simple "is my text present"
        # check AND a length check -- the real corruption in eva.log was
        # exactly the right length, because the characters were all there,
        # just in the wrong order ("...a sustainable futBeyond electricity
        # generation..." with the orphaned "ure." at the end). The only
        # check that catches it is POSITION: after an append the original
        # must still be an intact prefix, and after a replace the content
        # must be the new text and nothing else.
        misplaced = None
        if mode == "append" and before.strip():
            if not _norm_ws(content).startswith(_norm_ws(before)[:200]):
                misplaced = ("the existing text is no longer at the start of the document, "
                             "so the new text was spliced into the middle of it instead of "
                             "being appended")
        elif mode == "replace":
            if not _norm_ws(content).startswith(_norm_ws(text)[:200]):
                misplaced = ("the document does not begin with the text that was written, "
                             "so old content was left in front of it")
        if misplaced:
            return {"ok": False, "method": method, "verified": False,
                    "error": f"the document is now WRONG: {misplaced}. Read it back with "
                             "read_window_text, tell the person it went wrong, and fix it. "
                             "Do not report this as done.",
                    "chars_in_window": after.get("length")}
        return {"ok": True, "method": method, "verified": True,
                "chars_in_window": after.get("length")}
    except Exception as e:
        return uia_error(e, element_id)


def _click_result(before_hash):
    """Result for a raw coordinate click. The click itself can only be
    reported as delivered, but the screen can be compared before/after so the
    model has evidence instead of a bare "verified: False" -- the exact
    situation in eva.log 2026-09-30 (Wordle's Play button), where she spoke
    about not being able to confirm rather than checking. screen_changed is
    evidence, not proof: True means something on screen changed; False means
    nothing visibly did, so the click probably missed or needs another approach
    (look_at_screen to confirm, then ui_navigate or a different target)."""
    time.sleep(0.7)
    before = before_hash if isinstance(before_hash, dict) else {"hash": before_hash, "small": None}
    after = screen_snapshot() if before.get("small") is not None else {"hash": screen_hash(), "small": None}
    res = {"ok": True, "delivered": True, "verified": False}
    changed = None
    if before.get("hash") is not None and after.get("hash") is not None:
        import ui_navigate
        changed = ui_navigate.screen_changed(before["hash"], after["hash"])
    if not changed and before.get("small") and after.get("small"):
        changed = screen_moved(before["small"], after["small"]) or bool(changed)
    if changed is not None:
        res["screen_changed"] = bool(changed)
        if not res["screen_changed"]:
            res["note"] = ("nothing on screen visibly changed after the click -- it may have missed. "
                           "Check with look_at_screen before repeating or reporting success.")
    return res


def click_coords(x: int, y: int, reason_no_element: str):
    """Fallback only -- used when ui_tree returned no usable element
    (custom-rendered canvas, game, remote-desktop window)."""
    x, y = _safe_point(x, y)
    blocked = _clear_point(x, y)
    if blocked:
        return blocked
    before = screen_snapshot()
    pyautogui.moveTo(x, y, duration=0.25)  # visible glide -- she moves like a person, not a teleport
    pyautogui.click()
    return _click_result(before)

# NOTE ON "verified": False BELOW. These primitives deliver a keystroke or
# a cursor action; what the focused application then DOES with it is not
# knowable from here. They report delivery honestly rather than implying
# the intended effect happened -- the system prompt tells Eva that
# verified:false means the outcome is unknown and must be checked, which
# is exactly right for a raw click.

# ---- the rest of the cursor: everything a human can do with a mouse ----
# Same coordinate-based fallback philosophy as click_coords: usable on ANY
# window, including apps with no accessibility tree (Opera, games, custom
# canvas apps). All of them glide the cursor visibly first.

def right_click_coords(x: int, y: int, reason_no_element: str):
    """Right-click (context menu) at a screen position."""
    x, y = _safe_point(x, y)
    blocked = _clear_point(x, y)
    if blocked:
        return blocked
    pyautogui.moveTo(x, y, duration=0.25)
    pyautogui.rightClick()
    return {"ok": True, "delivered": True, "verified": False}

def double_click_coords(x: int, y: int, reason_no_element: str):
    """Double-click at a screen position (open files/folders, select words)."""
    x, y = _safe_point(x, y)
    blocked = _clear_point(x, y)
    if blocked:
        return blocked
    before = screen_snapshot()
    pyautogui.moveTo(x, y, duration=0.25)
    pyautogui.doubleClick()
    return _click_result(before)

def drag_and_drop(from_x: int, from_y: int, to_x: int, to_y: int,
                  reason_no_element: str):
    """Press at (from_x, from_y), drag to (to_x, to_y), release. Works on
    anything that accepts drags: files into folders, window handles,
    canvas shapes, sliders."""
    from_x, from_y = _safe_point(from_x, from_y)
    to_x, to_y = _safe_point(to_x, to_y)
    blocked = _clear_point(from_x, from_y)
    if blocked:
        return blocked
    pyautogui.moveTo(from_x, from_y, duration=0.25)
    pyautogui.mouseDown()
    # A real drag is a continuous glide, not a jump -- some apps (file
    # explorers, DnD-aware web pages) reject or mis-handle a teleport.
    pyautogui.moveTo(to_x, to_y, duration=0.6)
    pyautogui.mouseUp()
    return {"ok": True, "delivered": True, "verified": False}

def scroll(amount: int, x: int = None, y: int = None):
    """Scroll the mouse wheel. Positive scrolls up, negative scrolls down.
    Optional x/y scrolls at that position (first moves the cursor there)."""
    if x is not None and y is not None:
        pyautogui.moveTo(x, y, duration=0.2)
    pyautogui.scroll(amount)
    return {"ok": True, "delivered": True, "verified": False}

def move_mouse(x: int, y: int):
    """Just move the cursor somewhere -- hover tooltips/menus, or pointing
    something out to the person. No click."""
    x, y = _safe_point(x, y)
    pyautogui.moveTo(x, y, duration=0.3)
    return {"ok": True, "delivered": True, "verified": False}

def launch_app(name_or_path: str, argument: str = ""):
    """Start a program the way a person would from the Run box / Start
    menu: "notepad", "calc", "explorer", or a full path. Optional argument
    is usually a file to open with it. Confirmed as a real gap: asked to
    open a file in Notepad, the model had no way to launch anything at
    all and resorted to opening it in the browser instead.

    os.startfile with a bare document path uses the file's own default
    program; passing a program plus an argument goes through the shell.
    """
    import app_resolver
    try:
        # Not just PATH: installed apps (Brave, Spotify, Word...) are found
        # through App Paths, the Start Menu, install folders and the Store
        # list -- see app_resolver. eva.log 2026-09-30: launch_app('brave')
        # failed on PATH-only lookup.
        resolved = app_resolver.resolve(name_or_path)
        if not resolved.get("kind"):
            cands = resolved.get("candidates") or []
            return {"ok": False,
                    "error": f"no installed program matches {name_or_path!r}",
                    "did_you_mean": cands,
                    "note": "It may not be installed. Search the web for it or ask the person."}
        # Snapshot BEFORE starting: taking it after the sleep meant a window
        # that appeared quickly was already "before" and was never reported.
        before = {w._hWnd for w in gw.getAllWindows()}
        app_resolver.start(resolved, argument)
        time.sleep(1.0)  # let the window actually appear before anything looks for it
        # "Started" is not "running". eva.log 2026-09-26 shows five
        # launch attempts for apps that were not installed; those raised,
        # which is fine. The case this catches is the opposite one -- a
        # process that launches and exits immediately (wrong arguments, a
        # stub launcher, an installer shim), which used to report success
        # and leave the model hunting for a window that never appears.
        time.sleep(0.6)
        new_windows = [w for w in gw.getAllWindows()
                       if w._hWnd not in before and w.title.strip()]
        if new_windows:
            w = new_windows[0]
            return {"ok": True, "verified": True, "found_via": resolved.get("via"),
                    "window": {"handle": w._hWnd, "title": w.title}}
        return {"ok": True, "verified": False, "found_via": resolved.get("via"),
                "note": "the process was started but no new window appeared within ~1.5s. It "
                        "may still be loading, or it may have exited immediately. Check with "
                        "list_windows before assuming it is open."}
    except Exception as e:
        if getattr(e, "winerror", None) == 740 or "740" in str(e)[:20]:
            # eva.log 2026-10-11 13:50: regedit. Opening it another way (Win+R)
            # shows the UAC prompt, and the window then runs as administrator,
            # where Windows lets Eva do nothing in it (core/elevation.py).
            return {"ok": False, "needs_admin": True,
                    "error": (f"{name_or_path} runs only as administrator. Opening it shows a Windows (UAC) "
                              "prompt the person must approve, and once open, Windows will not let Eva click, "
                              "type into, read or close it. Ask the person whether they want it opened anyway "
                              "for them to use themselves.")}
        return {"ok": False, "error": str(e)}


def navigate_browser(window_handle: int, url: str, new_tab: bool = True):
    """Natively drive ANY browser the person already has open -- Opera,
    Chrome, Edge, Firefox, anything with an address bar: focus its window,
    open a NEW TAB (Ctrl+T) unless told to reuse the current one, select the
    address bar (Ctrl+L), type the URL, press Enter. The person's open page
    is never overwritten by default (eva.log 2026-10-11 13:16: "you should
    not have overwritten the browser URL"). Verified by the window title
    changing, since browsers title their window after the active tab."""
    res = focus_window(window_handle)
    if not res.get("ok"):
        return res
    before = _window_title(window_handle)
    time.sleep(0.3)
    if new_tab:
        pyautogui.hotkey("ctrl", "t")
        time.sleep(0.45)
    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.2)
    type_text(url)
    time.sleep(0.15)
    pyautogui.press("enter")
    after = before
    for _ in range(16):                      # up to ~4 s for the new page to title the window
        time.sleep(0.25)
        after = _window_title(window_handle)
        if after and after != before:
            break
    changed = bool(after) and after != before
    return {"ok": True, "delivered": True, "verified": changed, "new_tab": bool(new_tab),
            "window_title": after,
            **({} if changed else {"note": "the window title did not change yet -- the page may still be loading"})}


def _window_title(window_handle):
    try:
        for w in gw.getAllWindows():
            if w._hWnd == window_handle:
                return w.title or ""
    except Exception:
        pass
    return ""


def _browser_tabs(window_handle):
    """The browser's tabs IN STRIP ORDER, from UI Automation: [(name, element)].
    Chromium browsers (Chrome, Edge, Opera, Brave) and Firefox expose each
    tab as a TabItem named after its page."""
    try:
        app = _connect(window_handle)
        win = app.window(handle=window_handle)
        tabs = []
        for t in win.descendants(control_type="TabItem"):
            try:
                name = (t.element_info.name or "").strip()
            except Exception:
                continue
            if name:
                tabs.append((name, t))
        return tabs
    except Exception:
        return []


def _select_tab(element):
    """Selects one tab element: the SelectionItem pattern, then Invoke, then
    a click on its own rectangle (exact, from UI Automation)."""
    for how in ("select", "invoke", "click"):
        try:
            if how == "select":
                element.iface_selection_item.Select()
            elif how == "invoke":
                element.invoke()
            else:
                r = element.rectangle()
                pyautogui.click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            return how
        except Exception:
            continue
    return None


def switch_browser_tab(window_handle: int, title_contains: str = "", index: int = 0):
    """Switches to a tab of a browser the person has open, by words in its
    title, or by position (1 = first, -1 = last, -2 = second to last).

    eva.log 2026-10-11 17:29-17:32: "the last two tabs are the YouTube ones"
    -- she could never reach them. Ctrl+Tab in Opera cycles RECENTLY USED
    tabs, so it only ever flipped between two; Ctrl+9 / Ctrl+8 did nothing
    there. Now the tab strip is read from UI Automation (every tab, in strip
    order, by name) and the one wanted is selected directly. Without UI
    Automation it falls back to Ctrl+PageDown, which moves through the strip
    in order in every browser."""
    res = focus_window(window_handle)
    if not res.get("ok"):
        return res
    time.sleep(0.25)
    want = str(title_contains or "").strip().lower()
    n = int(index or 0)
    if not want and not n:
        return {"ok": False, "error": "give words from the tab's title, or its position (index; -1 = last)"}
    tabs = _browser_tabs(window_handle)
    if tabs:
        names = [t[0] for t in tabs]
        if want:
            pick = next((i for i, nm in enumerate(names) if want in nm.lower()), None)
            if pick is None:
                words = [w for w in re.findall(r"\w+", want) if len(w) > 2]
                scored = sorted(((sum(w in nm.lower() for w in words), i) for i, nm in enumerate(names)), reverse=True)
                pick = scored[0][1] if scored and scored[0][0] > 0 else None
        else:
            pick = (n - 1) if n > 0 else (len(names) + n)
            if not 0 <= pick < len(names):
                pick = None
        if pick is None:
            return {"ok": False, "error": f"no tab matches {title_contains or index!r}", "tab_titles": names[:40]}
        how = _select_tab(tabs[pick][1])
        time.sleep(0.35)
        title = _window_title(window_handle)
        ok = names[pick].lower()[:25] in title.lower() or bool(how)
        return {"ok": bool(how), "verified": names[pick].lower()[:25] in title.lower(), "window_title": title,
                "tab": names[pick], "position": pick + 1, "of": len(names), "method": how or "none",
                **({} if ok else {"error": "could not select that tab"})}
    # No UI Automation tab strip: walk it in order with Ctrl+PageDown.
    start = _window_title(window_handle)
    seen = [start]
    for _ in range(60):
        if want and want in seen[-1].lower():
            return {"ok": True, "verified": True, "window_title": seen[-1]}
        pyautogui.hotkey("ctrl", "pagedown")
        time.sleep(0.25)
        t = _window_title(window_handle)
        if t == start:
            break                                # all the way round
        seen.append(t)
    titles = list(dict.fromkeys(x for x in seen if x))
    if n:
        pick = (n - 1) if n > 0 else (len(titles) + n)
        if 0 <= pick < len(titles):
            for _ in range(pick):
                pyautogui.hotkey("ctrl", "pagedown")
                time.sleep(0.2)
            return {"ok": True, "verified": _window_title(window_handle) == titles[pick],
                    "window_title": _window_title(window_handle), "position": pick + 1, "of": len(titles)}
    return {"ok": False, "error": f"no open tab's title contains {title_contains!r}", "tab_titles": titles[:40]}


def media_key(action: str, times: int = 1):
    """The keyboard's media keys, which Windows sends to whatever is playing
    (a YouTube tab, a Picture-in-Picture video, Spotify, VLC) -- no play button
    to find on screen (eva.log 2026-10-11 17:32: YouTube hides its controls
    until the pointer is over the video, and vision kept missing them)."""
    keys = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack", "stop": "stop",
            "mute": "volumemute", "volume_up": "volumeup", "volume_down": "volumedown"}
    key = keys.get(str(action or "").strip().lower())
    if not key:
        return {"ok": False, "error": f"action must be one of {sorted(keys)}"}
    for _ in range(max(1, min(25, int(times or 1)))):
        pyautogui.press(key)
        time.sleep(0.05)
    return {"ok": True, "delivered": True, "action": action, "times": max(1, min(25, int(times or 1)))}


_LABEL_ROLES = ("Text", "Hyperlink", "Button", "ListItem", "Header", "HeaderItem", "TabItem", "CheckBox",
                "RadioButton", "MenuItem", "TreeItem", "DataItem", "Group", "Edit", "ComboBox")


def visible_labels(win, limit=400):
    """The words a person sees in a window, in screen order, each once."""
    seen, out = set(), []
    for ctrl in list(win.descendants())[:3000]:
        try:
            info = ctrl.element_info
            if info.control_type not in _LABEL_ROLES or getattr(info, "visible", True) is False:
                continue
            name = (info.name or "").strip()
        except Exception:
            continue
        if not name or name in seen or len(name) > 400:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= limit:
            break
    return "\n".join(out)


_LABEL_TYPES = {50020, 50005, 50000, 50007, 50034, 50035, 50019, 50002, 50013, 50011, 50024, 50029, 50026, 50004, 50003}


def _read_window_text_cached(window_handle, max_chars):
    """read_window_text in ONE cross-process read (FindAllBuildCache), same answer.

    2026-10-17 22:07 on the PC: typing "Kolkata" into a web page took 28 s, because the check read the
    browser window twice with pywinauto's descendants() -- six calls per element, 2,000+ elements."""
    from pywinauto.uia_defines import IUIA
    iu = IUIA()
    root = iu.iuia.ElementFromHandle(int(window_handle))
    cr = iu.iuia.CreateCacheRequest()
    for pid in (30003, 30005, 30022, 30043, 30045):        # type, name, offscreen, has Value, Value
        cr.AddProperty(pid)
    arr = root.FindAllBuildCache(7, iu.true_condition, cr)  # TreeScope_Subtree: the window too
    values, doc, labels, seen = [], "", [], set()
    for i in range(min(arr.Length if arr is not None else 0, 6000)):
        e = arr.GetElement(i)
        ctype = int(e.GetCachedPropertyValue(30003) or 0)
        if e.GetCachedPropertyValue(30043):
            value = str(e.GetCachedPropertyValue(30045) or "")
            if value:
                values.append(value)
                if ctype in (50030, 50004) and len(value) > len(doc):
                    doc = value
        if ctype in _LABEL_TYPES and not e.GetCachedPropertyValue(30022) and len(labels) < 400:
            name = str(e.GetCachedPropertyValue(30005) or "").strip()
            if name and name not in seen and len(name) <= 400:
                seen.add(name)
                labels.append(name)
    best = doc or max(values, key=len, default="")
    source = "document" if doc else "value"
    if len(best.strip()) < 40:
        text = "\n".join(labels)
        if len(text) > len(best):
            best, source = (best.strip() + "\n" + text if best.strip() else text), "labels"
    return {"text": best[:max_chars], "length": len(best), "source": source,
            "truncated": len(best) > max_chars, "values": [v[:max_chars] for v in values[:40]]}


def read_window_text(window_handle: int, max_chars: int = 4000):
    """The text content of a window's main editable/document control.

    Exists so an action that claims to have written something can be
    CHECKED, cheaply, without the vision model. ui_tree exposes the same
    value buried among 40 other elements, which is how the model came to
    verify against the wrong window entirely.

    The DOCUMENT is preferred over whichever control holds the longest value: in Windows 11
    Notepad a tab or toolbar value (46 chars) outranked a 19-character line just typed, so the
    typing check said "did NOT reach the window" and she typed it again (eva.log 2026-10-17
    12:05 and 12:16). Every value read is also returned in "values" for checks to search."""
    import config
    if getattr(config, "UIA_CACHED_READS", True):
        try:
            return _read_window_text_cached(window_handle, max_chars)
        except Exception as e:
            log.info(f"Cached window read failed ({e}) -- reading it the slow way")
    try:
        app = _connect(window_handle)
        win = app.window(handle=window_handle)
        values, doc = [], ""
        for ctrl in [win] + list(win.descendants()):
            try:
                if not ctrl.iface_value:
                    continue
            except Exception:
                continue
            try:
                value = ctrl.iface_value.CurrentValue or ""
            except Exception:
                value = ctrl.window_text() or ""
            if not value:
                continue
            values.append(value)
            if _control_type(ctrl) in ("Document", "Edit") and len(value) > len(doc):
                doc = value
        best = doc or max(values, key=len, default="")
        source = "document" if doc else "value"
        if len(best.strip()) < 40:
            # No document control: Settings and other modern apps show their
            # content as labels, and the only "value" was the title bar
            # ("Settings", eva.log 2026-10-11 13:47 -- read four times while
            # she reported "already up to date"). Read the visible labels --
            # keeping the short value in front of them.
            labels = visible_labels(win)
            if len(labels) > len(best):
                best, source = (best.strip() + "\n" + labels if best.strip() else labels), "labels"
        return {"text": best[:max_chars], "length": len(best), "source": source,
                "truncated": len(best) > max_chars, "values": [v[:max_chars] for v in values[:40]]}
    except Exception as e:
        return {"error": str(e)}


def restore_foreground(window_handle: int, attempts: int = 3):
    """Puts a window back in front and CONFIRMS it got there.

    focus_window uses pygetwindow's activate(), which Windows silently
    ignores when the calling process is not itself in the foreground --
    precisely the state Eva is in just after her own dialog closes. This
    retries, verifies, and says plainly if it failed, so the caller does
    not type into thin air believing focus was restored."""
    for attempt in range(attempts):
        focus_window(window_handle, verify=False, allow_own=True)
        time.sleep(0.15 + 0.1 * attempt)
        actual, title = _foreground_handle()
        if actual == window_handle:
            return {"ok": True, "title": title, "attempts": attempt + 1}
    actual, title = _foreground_handle()
    return {"ok": False, "error": f"could not restore focus; foreground is {title!r}",
            "foreground_title": title}


def _foreground_handle():
    try:
        w = gw.getActiveWindow()
        return (w._hWnd, w.title) if w else (None, None)
    except Exception:
        return (None, None)


def type_text(text: str, window_handle: int = None, verify: bool = True):
    """Native SendInput-based typing (via pyautogui's Windows backend) into
    whatever control currently has keyboard focus.

    TYPING IS VERIFIED BEFORE IT IS REPORTED AS DONE. It used to return
    {"ok": True} the moment pyautogui.typewrite returned -- and typewrite
    returns None whether every keystroke landed in the intended window,
    landed in some other window, or went nowhere. Confirmed in eva.log
    2026-09-26: a 235-character passage was "typed" into Notepad, the tool
    reported ok, and the document afterwards contained exactly one
    character. The model had no way to know, so it told the person the
    passage had been written -- truthfully reporting what the tool told
    it. Same class of defect as play_animation returning unconditional
    success, and the same fix: report what actually happened.

    pyautogui's typewrite/write only handles characters it can map to a
    basic keyboard scancode -- effectively ASCII. Non-ASCII text (₹, curly
    quotes, em dashes, accented characters -- all things ordinary model
    output produces routinely, not rare edge cases) silently failed or
    typed garbage. For non-ASCII text, set the clipboard and paste instead
    -- the clipboard holds arbitrary Unicode natively, sidestepping the
    scancode limitation entirely. Uses the same env-var-based PowerShell
    invocation pattern as voice.py's TTS fix, for the same reason: passing
    the text as an environment variable's value means no character in it
    needs escaping, since it's never embedded as literal script text."""
    # Focus first, and confirm it actually took. w.activate() can silently
    # fail on Windows when the calling process is not in the foreground,
    # which is exactly the state Eva is in after a confirmation dialog.
    if window_handle is not None:
        res = focus_window(window_handle)
        if not res.get("ok"):
            return res
        time.sleep(0.25)
        actual, actual_title = _foreground_handle()
        if actual != window_handle:
            return {"ok": False, "typed": False,
                    "error": f"could not focus the target window -- keyboard focus is on "
                             f"{actual_title!r} instead, so nothing was typed",
                    "foreground_title": actual_title}

    before = read_window_text(window_handle).get("text", "") if (verify and window_handle) else None
    _TYPE_SCREEN["before"] = screen_small() if verify else None

    if text.isascii():
        pyautogui.typewrite(text, interval=0.02)
        return _verify_typed(text, window_handle, before, "typewrite", verify)

    import subprocess
    import base64
    ps_script = "Set-Clipboard -Value $env:EVA_CLIPBOARD_TEXT"
    encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
    env = os.environ.copy()
    env["EVA_CLIPBOARD_TEXT"] = text

    # Save whatever the person had on the clipboard. Typing one accented
    # character used to destroy it permanently -- copy a paragraph, ask Eva
    # to type "café", and the next Ctrl+V pasted Eva's text instead. Best
    # effort: a clipboard manager racing us can still win, but that beats
    # unconditionally throwing the person's clipboard away.
    saved_clipboard = None
    try:
        import pyperclip
        saved_clipboard = pyperclip.paste()
    except Exception as e:
        log.warning(f"Could not save the clipboard before typing ({e}) -- it will not be restored.")

    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, timeout=15, check=False, env=env,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.decode("utf-8", errors="replace").strip()}
    pyautogui.hotkey("ctrl", "v")
    _pasted_result = _verify_typed(text, window_handle, before, "clipboard_paste", verify)
    if saved_clipboard is not None:
        # After the paste has actually been consumed by the target window.
        time.sleep(0.15)
        try:
            import pyperclip
            pyperclip.copy(saved_clipboard)
        except Exception as e:
            log.warning(f"Could not restore the clipboard after typing: {e}")
    return _pasted_result


_TYPE_SCREEN = {"before": None}


def _verify_typed(text: str, window_handle, before, method: str, verify: bool):
    """Compares the target's content against what was supposed to land."""
    if not verify or window_handle is None:
        # Nothing to check against: say so rather than implying success.
        return {"ok": True, "method": method, "verified": False,
                "note": "no window_handle given, so the text could not be verified "
                        "as having landed in the intended window"}
    time.sleep(0.3)   # let the target catch up before reading it back
    after = read_window_text(window_handle)
    if after.get("error"):
        return {"ok": True, "method": method, "verified": False,
                "note": f"could not read the window back to verify: {after['error']}"}

    content = after.get("text", "")
    # A representative slice rather than the whole string: editors
    # reflow, normalise line endings and autocorrect, so an exact
    # full-text match produces false failures. Searched in every value the window holds, not
    # only the one read_window_text chose as its main text.
    probe = _norm_ws(text)[:60]
    landed = probe and any(probe in _norm_ws(t) for t in [content] + list(after.get("values") or []))
    grew = len(content) - len(before or "")
    if landed:
        return {"ok": True, "method": method, "verified": True,
                "chars_in_window": after.get("length")}
    # A game board, a canvas or a custom editor shows typed letters without publishing them
    # as window text (eva.log 2026-10-11 23:40: Wordle; every guess was reported as "did NOT
    # reach the target window", so she kept retyping). The window was focused before typing;
    # if the screen changed when she typed, the keys arrived.
    shot_before, _TYPE_SCREEN["before"] = _TYPE_SCREEN.get("before"), None
    if grew == 0 and shot_before is not None and screen_moved(shot_before, screen_small()):
        return {"ok": True, "method": method, "verified": False, "screen_changed": True,
                "note": "the window does not publish its text (a game or custom control), but the screen "
                        "changed as the keys were typed, so they arrived. Look at the screen to see the result."}
    return {"ok": False, "typed": False, "method": method, "verified": False,
            "error": "the text did NOT reach the target window -- it gained "
                     f"{grew} characters and does not contain what was typed. "
                     "Do not report this as done; the keystrokes went elsewhere.",
            "chars_in_window": after.get("length")}


# Keystrokes that close or destroy. Sent at Eva's own window they end the
# session; sent blind they hit whatever happens to be in front.
_CLOSING_KEYS = {"alt+f4", "ctrl+w", "ctrl+q", "ctrl+shift+w"}


def send_hotkey(keys: str, window_handle: int = None):
    """Sends a key combination to the FOREGROUND window.

    window_handle is optional and means "make sure this window is in front
    first". It used to be absent entirely, so the model -- reasonably,
    since every other action tool takes one -- passed it anyway and got
    TypeError: send_hotkey() got an unexpected keyword argument
    'window_handle'. Twice in one session, both times mid-task."""
    combo = keys.lower().replace(" ", "")

    if window_handle is not None:
        try:
            window_handle = int(window_handle)     # the model sometimes sends it as a string
        except (TypeError, ValueError):
            return {"ok": False, "error": f"window_handle must be a number, got {window_handle!r}"}
        if is_own_window(window_handle):
            return {"ok": False, "error": "that window belongs to Eva herself -- refusing. "
                                          "Use list_windows to pick one of the person's windows."}
        res = restore_foreground(window_handle)
        if not res.get("ok"):
            return {"ok": False, "error": f"could not bring that window to the front first: "
                                          f"{res.get('error')}"}

    if combo in _CLOSING_KEYS:
        # Whatever is in front right now is what will receive this. If it
        # is Eva, the session ends here -- which is exactly what happened.
        front, title = _foreground_handle()
        if front is not None and is_own_window(front):
            return {"ok": False,
                    "error": f"refusing to send {keys} -- the window in front ({title!r}) is "
                             "Eva's own, and this would close her. Focus one of the person's "
                             "windows first, and never close Eva's."}

    pyautogui.hotkey(*combo.split("+"))
    return {"ok": True, "delivered": True, "verified": False}


def close_window(window_handle: int):
    """Closes ONE named window with WM_CLOSE.

    The only way to close anything used to be send_hotkey("alt+f4"), which
    goes to whatever is in front at that instant. eva.log 2026-09-26 shows
    four blind Alt+F4s in five seconds; one of them closed Eva herself.
    WM_CLOSE names its target, so there is nothing to race, and the app
    still gets to show its own "save changes?" prompt rather than being
    killed."""
    if is_own_window(window_handle):
        return {"ok": False, "error": "that window belongs to Eva herself -- refusing to close it."}
    title = None
    for w in gw.getAllWindows():
        if w._hWnd == window_handle:
            title = w.title
            break
    if title is None:
        return {"ok": False,
                "error": "that window no longer exists -- it was already closed or its handle "
                         "changed. Call list_windows for the current handles."}
    WM_CLOSE = 0x0010
    try:
        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(window_handle), WM_CLOSE, 0, 0)
    except Exception as e:
        return {"ok": False, "error": f"could not send the close message: {e}"}

    # An app with unsaved work answers WM_CLOSE with a dialog and stays
    # open. That is not a failure, but it is also not "closed", and the
    # difference matters to whoever is told about it.
    time.sleep(0.6)
    still_open = any(w._hWnd == window_handle for w in gw.getAllWindows())
    if still_open:
        return {"ok": True, "closed": False, "title": title,
                "note": "the close request was delivered but the window is still open -- it is "
                        "probably asking about unsaved changes. Look at the screen before "
                        "telling the person it closed."}
    return {"ok": True, "closed": True, "title": title}


def get_current_time():
    """The person's local date and time, plus their timezone."""
    now = datetime.now()
    return {"iso": now.isoformat(timespec="seconds"),
            "friendly": now.strftime("%A, %d %B %Y, %I:%M %p").replace(" 0", " "),
            "timezone": str(datetime.now().astimezone().tzinfo),
            "unix": int(now.timestamp())}


def get_screen_size():
    """Every monitor, plus the combined virtual desktop.

    This used to report only the primary monitor, so on a multi-monitor
    setup any coordinate past its right edge looked out of range, and the
    avatar could never be walked onto a second screen."""
    monitors = []
    try:
        with mss.mss() as sct:
            for i, mon in enumerate(sct.monitors):
                entry = {"left": mon["left"], "top": mon["top"],
                         "width": mon["width"], "height": mon["height"]}
                if i == 0:
                    virtual = entry
                else:
                    entry["index"] = i
                    entry["primary"] = (i == 1)
                    monitors.append(entry)
    except Exception as e:
        return {"error": f"could not read the monitor layout: {e}"}
    primary = next((m for m in monitors if m.get("primary")), None) or virtual
    return {"virtual_screen": virtual, "monitors": monitors,
            "primary": {"width": primary["width"], "height": primary["height"]},
            # Kept so anything reading the old shape still works.
            "screen": {"width": primary["width"], "height": primary["height"]}}


# ---------------------------------------------------------------------------
# Acting on ANY native element, by what it is rather than by pixels.
#
# click_element could only click. Windows exposes a standard set of
# CONTROL PATTERNS -- Toggle, ExpandCollapse, SelectionItem, Value,
# RangeValue, ScrollItem -- that nearly every control on the desktop
# implements, so acting through them reaches checkboxes, dropdowns, tabs,
# tree nodes, sliders and text fields uniformly, without guessing at
# coordinates. Each action reads the control's state before and after and
# reports what actually changed, so a toggle that did not toggle is
# reported as exactly that.
# ---------------------------------------------------------------------------
_AUTO_ACTION = {
    "Button": "invoke", "Hyperlink": "invoke", "MenuItem": "invoke", "SplitButton": "invoke",
    "CheckBox": "toggle", "RadioButton": "select", "TabItem": "select",
    "ListItem": "select", "TreeItem": "select", "ComboBox": "expand",
    "Edit": "set_value", "Document": "set_value", "Slider": "set_value", "Spinner": "set_value",
}
_TOGGLE_STATES = {0: "off", 1: "on", 2: "indeterminate"}
_EXPAND_STATES = {0: "collapsed", 1: "expanded", 2: "partial", 3: "leaf"}
_ELEMENT_ACTIONS = ("auto", "invoke", "toggle", "expand", "collapse", "select",
                    "set_value", "focus", "scroll_into_view", "get_state")


def _control_type(ctrl) -> str:
    try:
        return str(ctrl.element_info.control_type or "")
    except Exception:
        return ""


def _element_state(ctrl) -> dict:
    """Whatever this control will tell us about itself. Each property is
    read independently: a control that lacks a pattern raises for it, and
    that must not hide the properties it does have."""
    readers = (
        ("enabled", lambda: bool(ctrl.is_enabled())),
        ("visible", lambda: bool(ctrl.is_visible())),
        ("toggle", lambda: _TOGGLE_STATES.get(ctrl.iface_toggle.CurrentToggleState)),
        ("expanded", lambda: _EXPAND_STATES.get(ctrl.iface_expand_collapse.CurrentExpandCollapseState)),
        ("selected", lambda: bool(ctrl.iface_selection_item.CurrentIsSelected)),
        ("value", lambda: ctrl.iface_value.CurrentValue),
        ("range", lambda: ctrl.iface_range_value.CurrentValue),
    )
    state = {}
    for key, read in readers:
        try:
            value = read()
            if value is not None:
                state[key] = value
        except Exception:
            pass
    return state


def _act_on(ctrl, action: str, value=None, click_fallback=None) -> dict:
    """The logic of act_on_element, separated from finding the control so it
    can be tested against stand-in controls."""
    if action not in _ELEMENT_ACTIONS:
        return {"ok": False, "error": f"unknown action {action!r}",
                "actions": list(_ELEMENT_ACTIONS)}
    ctype = _control_type(ctrl)
    if action == "auto":
        action = _AUTO_ACTION.get(ctype, "invoke")
    before = _element_state(ctrl)
    if action == "get_state":
        return {"ok": True, "control_type": ctype, "state": before}
    if before.get("enabled") is False:
        return {"ok": False, "error": "that element is disabled", "state": before}
    if before.get("visible") is False:
        return {"ok": False, "error": "that element is not visible", "state": before}

    method, expected = "pattern", None
    try:
        if action == "toggle":
            ctrl.iface_toggle.Toggle()
        elif action == "expand":
            ctrl.iface_expand_collapse.Expand(); expected = ("expanded", "expanded")
        elif action == "collapse":
            ctrl.iface_expand_collapse.Collapse(); expected = ("expanded", "collapsed")
        elif action == "select":
            ctrl.iface_selection_item.Select(); expected = ("selected", True)
        elif action == "set_value":
            if value is None:
                return {"ok": False, "error": "set_value needs a value"}
            if "range" in before:
                ctrl.iface_range_value.SetValue(float(value)); expected = ("range", float(value))
            else:
                ctrl.iface_value.SetValue(str(value)); expected = ("value", str(value))
        elif action == "focus":
            ctrl.set_focus()
        elif action == "scroll_into_view":
            ctrl.iface_scroll_item.ScrollIntoView()
        else:                                        # invoke
            ctrl.invoke()
    except Exception as first:
        # The control does not implement the pattern. Falling back to a real
        # click is the honest second choice for the actions a click can do.
        if click_fallback and action in ("invoke", "toggle", "select", "expand"):
            method = "click"
            try:
                click_fallback()
            except Exception as e:
                return {"ok": False, "error": f"{action} failed ({first}); click fallback also "
                                              f"failed: {e}", "state": before}
        else:
            return {"ok": False, "error": f"{action} is not supported by this element: {first}",
                    "control_type": ctype, "state": before}

    time.sleep(0.15)
    after = _element_state(ctrl)
    result = {"ok": True, "action": action, "method": method,
              "control_type": ctype, "state": after}
    if expected is not None:
        key, want = expected
        got = after.get(key)
        if key == "value":
            matched = got is not None and _norm_ws(str(want)) in _norm_ws(str(got))
        elif key == "range":
            matched = got is not None and abs(float(got) - want) < 1e-6
        else:
            matched = got == want
        result["verified"] = bool(matched)
        if not matched:
            result["warning"] = (f"asked for {key}={want!r} but the element reports {got!r} "
                                 "afterwards -- it did not take effect")
    elif action == "toggle" or before != after:
        changed = before != after
        result["verified"] = changed
        if not changed:
            result["warning"] = "the element's state did not change after the action"
    else:
        result["verified"] = False       # delivered; nothing observable to compare
    return result


def act_on_element(window_handle: int, element_id: str, action: str = "auto", value=None,
                   fallback: bool = True):
    """Act on a native element through its control pattern. action: auto
    (chosen from the control type), invoke, toggle, expand, collapse,
    select, set_value, focus, scroll_into_view, get_state.

    If the pattern route fails -- unsupported, or it reports no effect --
    and fallback is on, this does not give up: it moves on to driving the
    UI the way a person would (Tab to the control and press it, then look
    for it on screen and click). See ui_navigate for why that climb stops
    the moment something has acted and cannot be judged."""
    ctrl, err = _element(window_handle, element_id)
    if err:
        return err
    try:
        def click():
            rect = ctrl.rectangle()
            if not _rect_is_usable(rect, pyautogui.size()):
                raise RuntimeError("the element has no clickable on-screen area")
            ctrl.click_input()
        result = _act_on(ctrl, action, value, click_fallback=click)
        failed = (not result.get("ok")) or (result.get("verified") is False and result.get("warning"))
        if not (fallback and failed) or action in ("get_state", "scroll_into_view"):
            return result
        label = " ".join((ctrl.window_text() or "").split())
        if not label:
            result["note"] = "no visible label to look for, so the keyboard/vision fallbacks could not run"
            return result
        import ui_navigate
        nav_action = {"auto": "click", "invoke": "click", "toggle": "toggle", "expand": "expand",
                      "collapse": "collapse", "select": "select", "set_value": "set_value",
                      "focus": "focus"}.get(action, "click")
        alt = ui_navigate.navigate(window_handle, label, nav_action, value,
                                   rungs=("keyboard", "vision"))
        alt["via_fallback"] = True
        alt["first_attempt"] = result.get("error") or result.get("warning")
        return alt
    except Exception as e:
        return uia_error(e, element_id)


def window_title(window_handle=None) -> str:
    """Title of a window (the frontmost one, if no handle), or ''."""
    try:
        if window_handle is None:
            w = gw.getActiveWindow()
            return (w.title or "") if w and not is_own_window(w._hWnd) else ""
        for w in gw.getAllWindows():
            if w._hWnd == int(window_handle):
                return w.title or ""
    except Exception:
        pass
    return ""


def describe_element(window_handle, element_id) -> str:
    """A label for a native element -- what a person would call it -- read
    straight from the control. Used to build approval text and to judge
    whether a click is a payment, so it must come from the control itself,
    never from anything the model said about it."""
    try:
        app = _connect(int(window_handle))
        ctrl = _find_control(app.window(handle=int(window_handle)), str(element_id))
        if not ctrl:
            return ""
        name = " ".join((ctrl.window_text() or "").split())
        kind = _control_type(ctrl)
        return f"{name} ({kind})" if name and kind else (name or kind)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Primitives for ui_navigate.py: finding a control by its label, noticing
# that the window changed, driving focus with the keyboard. Written without
# being able to run UI Automation here, so each one degrades to "could not"
# rather than guessing, and the ladder reports which rung failed and why.
# ---------------------------------------------------------------------------
def find_elements_by_text(window_handle, text, limit=400, budget_s=3.0):
    """Isolated wrapper -- see uia_worker.py."""
    payload, err = _isolated("find_elements_by_text",
                             {"window_handle": int(window_handle), "text": text,
                              "limit": limit, "budget_s": budget_s})
    if err:
        log.warning(f"find_elements_by_text: {err}")
        return []
    return [dict(e, rect=tuple(e["rect"])) for e in (payload or [])]


def _find_by_text_inproc(window_handle, text, limit=400, budget_s=3.0):
    """Every labelled control in a window, as plain dicts, for ranking.
    Bounded in count AND time: a big Electron app's tree can hold tens of
    thousands of nodes, and a search that hangs is worse than one that
    says it found nothing."""
    out, started = [], time.time()
    app = _connect(int(window_handle))
    win = app.window(handle=int(window_handle))
    for idx, ctrl in enumerate(win.descendants()):
        if len(out) >= limit or time.time() - started > budget_s:
            break
        try:
            name = " ".join((ctrl.window_text() or "").split())
            if not name:
                continue
            rect = ctrl.rectangle()
            out.append({"name": name, "role": _control_type(ctrl),
                        "element_id": _element_id_for(ctrl, idx),
                        "rect": (rect.left, rect.top, rect.right, rect.bottom),
                        "enabled": bool(ctrl.is_enabled()), "visible": bool(ctrl.is_visible())})
        except Exception:
            continue
    return out


def ui_signature(window_handle):
    """A fingerprint of what a window (and the desktop's window list)
    currently looks like. Two calls differ if something appeared, vanished
    or was relabelled -- the cheapest honest evidence that a click DID
    something. Equal fingerprints prove nothing (plenty of real actions
    change nothing visible), which is why callers treat 'same' as 'unknown'."""
    import hashlib
    names, started = [], time.time()
    try:
        win = _connect(int(window_handle)).window(handle=int(window_handle))
        for idx, ctrl in enumerate(win.descendants()):
            if idx > 400 or time.time() - started > 1.5:
                break
            try:
                if ctrl.is_visible():
                    label = (ctrl.window_text() or "")[:40]
                    if label:
                        names.append(label)
            except Exception:
                pass
    except Exception:
        pass
    try:
        open_windows = len([w for w in gw.getAllWindows() if w.title.strip()])
    except Exception:
        open_windows = -1
    raw = "|".join(sorted(names)) + f"##{window_title(None)}##{open_windows}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def _name_at_point_inproc(x, y):
    """The control under a screen point and, if it has no label, its parent's (a text run
    inside a button). Read-only; runs in uia_worker."""
    from pywinauto import Desktop
    el = Desktop(backend="uia").from_point(int(x), int(y))
    for _ in range(3):
        if el is None:
            break
        name = " ".join((el.window_text() or "").split())
        if name:
            return {"name": name, "role": str(getattr(el.element_info, "control_type", "") or "")}
        try:
            el = el.parent()
        except Exception:
            break
    return {"name": "", "role": ""}


def name_at_point(x, y) -> str:
    """'Restart now (Button)' -- what a blind click at x, y would press, read from the control
    itself, so the permission gate can judge coordinate clicks too. '' when unreadable."""
    try:
        payload, err = _isolated("name_at_point", {"x": int(x), "y": int(y)}, timeout=6)
    except Exception:
        return ""
    if err or not payload:
        return ""
    name, role = payload.get("name", ""), payload.get("role", "")
    return f"{name} ({role})" if name and role else name


def focused_element_name():
    """The label of whichever control currently has keyboard focus, or None
    if it cannot be read (in which case Tab navigation cannot work)."""
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        return UIAElementInfo(IUIA().get_focused_element()).name or ""
    except Exception:
        return None


def tab_to_element(window_handle, text, max_tabs=40):
    """Press Tab until keyboard focus lands on a control labelled `text`.
    Stops at the end of the focus cycle so it cannot loop forever."""
    import difflib
    want = " ".join(str(text or "").lower().split())
    if not want:
        return {"found": False, "steps": 0, "error": "no label to look for"}
    first = None
    for step in range(1, int(max_tabs) + 1):
        pyautogui.press("tab")
        time.sleep(0.08)
        name = focused_element_name()
        if name is None:
            return {"found": False, "steps": step, "error": "cannot read which control has focus"}
        have = " ".join(name.lower().split())
        if have and (want in have or have in want or
                     difflib.SequenceMatcher(None, want, have).ratio() >= 0.8):
            return {"found": True, "steps": step, "focused": name}
        if first is None:
            first = have
        elif have == first and step > 2:
            return {"found": False, "steps": step, "error": "went all the way round the window's focus cycle"}
    return {"found": False, "steps": int(max_tabs)}


def press_key(key: str):
    pyautogui.hotkey(*str(key).lower().replace(" ", "").split("+"))
    return {"ok": True, "delivered": True, "verified": False}


SMALL_W, SMALL_H = 320, 180


def screen_small():
    """The whole screen as a 320 x 180 grey picture (bytes, w, h, origin, scale), or
    None. Fine enough to see a letter land in a Wordle tile or a menu open, which the
    8 x 8 screen_hash cannot (eva.log 2026-10-11 23:42: clicks and typing that DID
    change the page were reported as "nothing changed")."""
    try:
        from PIL import Image
        with mss.mss() as sct:
            m = sct.monitors[0]
            raw = sct.grab(m)
        img = Image.frombytes("RGB", raw.size, raw.rgb).convert("L").resize((SMALL_W, SMALL_H))
        return (img.tobytes(), SMALL_W, SMALL_H, (m["left"], m["top"]), SMALL_W / float(m["width"]),
                SMALL_H / float(m["height"]))
    except Exception:
        return None


def screen_moved(a, b, min_pixels=4, step=24) -> bool:
    """Whether anything visibly changed between two screen_small() pictures, ignoring
    Eva's own windows (she is always moving)."""
    if not a or not b or a[1:3] != b[1:3]:
        return False
    pa, pb, w, h, (ox, oy), kx, ky = a[0], b[0], a[1], a[2], a[3], a[4], a[5]
    masks = []
    try:
        import vision
        for (l, t, r, bt) in list(vision.SELF_RECTS):
            masks.append((int((l - ox) * kx) - 1, int((t - oy) * ky) - 1, int((r - ox) * kx) + 1, int((bt - oy) * ky) + 1))
    except Exception:
        pass
    n = 0
    for i in range(len(pa)):
        if abs(pa[i] - pb[i]) > step:
            x, y = i % w, i // w
            if any(x0 <= x <= x1 and y0 <= y <= y1 for (x0, y0, x1, y1) in masks):
                continue
            n += 1
            if n >= min_pixels:
                return True
    return False


def screen_snapshot():
    """Before an action: both fingerprints."""
    return {"hash": screen_hash(), "small": screen_small()}


def screen_hash():
    """A perceptual fingerprint of the whole screen (8x8 average hash), or
    None if it cannot be taken."""
    try:
        from PIL import Image
        import ui_navigate
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[0])
        img = Image.frombytes("RGB", raw.size, raw.rgb).convert("L").resize((8, 8))
        return ui_navigate.ahash(list(img.getdata()))
    except Exception:
        return None


def resolve_element_rect(window_handle: int, element_id: str):
    """(left, top, right, bottom) of a native element, or None. Used to
    highlight the element she is about to act on."""
    try:
        app = _connect(window_handle)
        ctrl = _find_control(app.window(handle=window_handle), element_id)
        if not ctrl:
            return None
        rect = ctrl.rectangle()
        if not _rect_is_usable(rect, pyautogui.size()):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def get_foreground_rect():
    """The person's frontmost window as (l, t, r, b) -- never Eva's own."""
    try:
        w = gw.getActiveWindow()
        if not w or is_own_window(w._hWnd):
            return None
        return (w.left, w.top, w.right, w.bottom)
    except Exception:
        return None



def screenshot(path: str = None):
    # /tmp doesn't exist on Windows by default -- this was silently going to
    # fail every call on the only platform this app actually targets.
    path = path or os.path.join(tempfile.gettempdir(), "eva_screenshot.png")
    with mss.mss() as sct:
        # mon=0 is the VIRTUAL screen -- every monitor combined into one
        # image. sct.shot()'s default of mon=1 is the primary monitor only,
        # so on a multi-monitor setup vision saw the wrong screen and any
        # coordinates derived from it pointed at the wrong display. mss
        # returns absolute virtual-desktop coordinates for mon=0, which is
        # the same space click_coords works in, so no rescaling is needed.
        virtual = sct.monitors[0]
        sct.shot(mon=0, output=path)
    return {"path": path, "origin_x": virtual["left"], "origin_y": virtual["top"],
            "width": virtual["width"], "height": virtual["height"]}


def list_background_jobs(top_n: int = 5):
    import psutil
    # psutil's cpu_percent() ALWAYS returns 0.0 on a process's first call --
    # it needs a prior baseline measurement plus elapsed time to compute a
    # real delta. The single sleep here used to happen AFTER the one and
    # only measurement was already taken (via process_iter's snapshot), so
    # every single result was always 0.0, regardless of the sleep -- a
    # genuine two-pass measurement is required: prime on the first pass
    # (result discarded), wait, then read real values on the second pass.
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent()  # primes the internal baseline; return value discarded
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(0.3)
    results = []
    for p in procs:
        try:
            results.append({"name": p.info["name"], "cpu": p.cpu_percent()})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    results.sort(key=lambda j: j["cpu"] or 0, reverse=True)
    return {"jobs": results[:top_n]}
