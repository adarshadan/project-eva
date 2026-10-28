"""Eva core -- windows that run as administrator.

eva.log 2026-10-11 13:50-13:55: Registry Editor was opened through Win+R and
the UAC prompt, so it ran as administrator. From then on nothing Eva did
reached it: ui_tree saw only the title bar, every click "did not change the
screen", "*" (expand all) did nothing while she said "Expanded all registry
folders", close_window and Alt+F4 left it open, and she spent five minutes
guessing at coordinates and asking the vision model about save dialogs that
were never there.

That is Windows working as designed (User Interface Privilege Isolation): a
normal program cannot send input to, read the controls of, or close a window
of a program running as administrator. So the framework now checks BEFORE the
action: if the target window belongs to an elevated process and Eva is not
elevated herself, the tool is not run and the result says why, in words she
can repeat, and what the person can do.

check(tool, args) -> None (go ahead) or the refusal result. The Windows
probes are module functions so the checks can replace them.
"""
import sys

from logger import get_logger

log = get_logger(__name__)

# What UIPI blocks. Moving, sizing, minimising and focusing an elevated window
# still work, so those are not checked.
BLOCKED_BY_UIPI = {
    "close_window", "click_coords", "right_click_coords", "double_click_coords", "drag_and_drop", "scroll",
    "click_element", "type_into_element", "act_on_element", "ui_navigate", "ui_tree", "read_window_text",
    "type_text", "send_hotkey", "navigate_browser", "switch_browser_tab",
    "start_repeating", "play_game", "react_when",
}

_self = {"elevated": None}


def _is_windows():
    return sys.platform == "win32"


def self_elevated() -> bool:
    if _self["elevated"] is None:
        _self["elevated"] = bool(_process_elevated_pid(None)) if _is_windows() else False
    return _self["elevated"]


def _process_elevated_pid(pid):
    """True / False, or None when it cannot be told. pid None = this process."""
    import ctypes
    from ctypes import wintypes
    k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
    PROCESS_QUERY_LIMITED_INFORMATION, TOKEN_QUERY, TokenElevation = 0x1000, 0x0008, 20
    if pid is None:
        hproc, own = k32.GetCurrentProcess(), True
    else:
        hproc, own = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)), False
        if not hproc:
            return None
    try:
        tok = wintypes.HANDLE()
        if not adv.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(tok)):
            # The process could be opened but not its token: an administrator
            # process seen from a normal one.
            return True if not own and ctypes.GetLastError() == 5 else None
        try:
            elev, size = wintypes.DWORD(), wintypes.DWORD()
            if not adv.GetTokenInformation(tok, TokenElevation, ctypes.byref(elev), 4, ctypes.byref(size)):
                return None
            return bool(elev.value)
        finally:
            k32.CloseHandle(tok)
    finally:
        if not own:
            k32.CloseHandle(hproc)


def window_elevated(hwnd) -> bool:
    if not hwnd or not _is_windows():
        return False
    import ctypes
    from ctypes import wintypes
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    return bool(pid.value) and _process_elevated_pid(pid.value) is True


def window_at(x, y):
    if not _is_windows():
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.WindowFromPoint.restype = wintypes.HWND
    u.GetAncestor.restype = wintypes.HWND
    h = u.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    return u.GetAncestor(h, 2) if h else None          # GA_ROOT


def foreground():
    if not _is_windows():
        return None
    import ctypes
    from ctypes import wintypes
    ctypes.windll.user32.GetForegroundWindow.restype = wintypes.HWND
    return ctypes.windll.user32.GetForegroundWindow()


def title(hwnd):
    if not _is_windows() or not hwnd:
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(int(hwnd), buf, 256)
    return buf.value


def _target(tool, args):
    if args.get("window_handle"):
        return args["window_handle"]
    if tool.locus == "coords" or {"x", "y"} <= set(args):
        if "x" in args and "y" in args:
            return window_at(args["x"], args["y"])
        if "from_x" in args and "from_y" in args:
            return window_at(args["from_x"], args["from_y"])
    if tool.locus == "foreground" or tool.name in ("send_hotkey", "type_text"):
        return foreground()
    return None


def check(tool, args):
    if tool is None or tool.name not in BLOCKED_BY_UIPI:
        return None
    try:
        if self_elevated():
            return None
        hwnd = _target(tool, args or {})
        if not hwnd or not window_elevated(hwnd):
            return None
        name = title(hwnd) or "That window"
    except Exception:
        log.exception("Elevation check failed -- running the tool as usual")
        return None
    log.warning(f"{tool.name}: '{name}' runs as administrator; Windows blocks Eva from acting on it")
    return {"ok": False, "elevated": True, "window_title": name,
            "error": (f"'{name}' is running as administrator. Windows does not let a normal program like Eva "
                      "click, type into, read or close an administrator window, so this cannot work and must "
                      "not be retried by other means (coordinates, hotkeys, Alt+F4). Tell the person plainly: "
                      "they can do this step in that window themselves, or you can restart as administrator "
                      "(restart_as_administrator, or tray > Restart as administrator; Windows asks them to "
                      "approve) and then do it -- offer that, do not do it unasked.")}
