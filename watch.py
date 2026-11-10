"""Eva -- show her once: she watches the PERSON do a task and writes down the steps (2026-11-10).

The older recorder (recorder.py) only notes Eva's own actions, so the person could not teach by doing.
Here the person says "watch me" and does the task once; Eva sees each click (and reads, from the control
itself, WHAT was clicked: "Save (Button)" in "Report - Excel"), what was typed and which keys were
pressed. When they say they are done she has an ordered list of steps in words, and drafts a skill from
it (save_skill) for the person to approve -- never a replay of raw coordinates.

What is kept and what is not:
  * In memory only, for this one demonstration (10 minutes / 200 steps at most). Nothing is written to
    disk; the log holds only the number of steps.
  * Typing into a password box, or in a window that looks like a sign-in, is noted as "[hidden]".
  * Clicks on Eva's own windows are not steps.

    start(task)  -> watching      stop() -> {"steps": [...], "text": "1. In ...", ...}

Input comes from pynput's global listeners (already used for idle detection); the element under a click
is read on a worker thread (desktop_control.name_at_point), never on the hook thread.
"""
import queue
import re
import threading
import time

from logger import get_logger

log = get_logger(__name__)

MAX_SECONDS, MAX_STEPS = 600, 200
_SECRET_WINDOW = re.compile(r"\b(password|passcode|sign[ -]?in|log[ -]?in|credential|otp|verification code|pin)\b", re.I)
_MODS = {"ctrl", "alt", "cmd", "win", "shift"}
_NAMED = {"enter": "Enter", "tab": "Tab", "esc": "Escape", "backspace": "Backspace", "delete": "Delete",
          "space": " ", "up": "Up", "down": "Down", "left": "Left", "right": "Right", "home": "Home", "end": "End",
          "page_up": "Page Up", "page_down": "Page Down"}


def _key_name(key):
    """('char', 'a') | ('named', 'enter') | ('mod', 'ctrl') | (None, None) from a pynput key."""
    ch = getattr(key, "char", None)
    if ch:
        return "char", ch
    name = str(getattr(key, "name", "") or str(key).replace("Key.", "")).lower()
    base = name.split("_")[0]
    if base in _MODS or name in ("ctrl_l", "ctrl_r", "alt_l", "alt_r", "alt_gr", "shift_r", "cmd_r"):
        return "mod", "win" if base == "cmd" else base
    if name.startswith("f") and name[1:].isdigit():
        return "named", name.upper()
    return ("named", name) if name else (None, None)


class Watcher:
    """Collects the person's steps. Pure: input arrives through on_click / on_press / on_release;
    `describe(x, y) -> (element, window)` and `is_own(x, y)` are supplied (real ones at start-up)."""

    def __init__(self, describe=None, is_own=None, clock=time.time):
        self.describe = describe or (lambda x, y: ("", ""))
        self.is_own = is_own or (lambda x, y: False)
        self.clock = clock
        self.active, self.task, self.started = False, "", 0.0
        self.steps, self._typing, self._mods, self._window = [], None, set(), ""
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self._worker = None

    # ---- life cycle ---------------------------------------------------------------------
    def start(self, task=""):
        with self._lock:
            self.active, self.task, self.started = True, " ".join(str(task or "").split())[:200], self.clock()
            self.steps, self._typing, self._mods, self._window = [], None, set(), ""
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._resolve_loop, name="eva-watch", daemon=True)
            self._worker.start()
        log.info("Watching the person do a task (show her once)")
        return {"ok": True, "watching": True,
                "note": "Say one short line -- that you are watching and they can say 'done' when finished -- then "
                        "wait. Do nothing on screen while they demonstrate. When they say they are done, call "
                        "stop_watching."}

    def stop(self):
        with self._lock:
            if not self.active:
                return {"ok": False, "error": "I was not watching"}
            self.active = False
        self._q.join() if self._worker is not None and self._worker.is_alive() else None
        with self._lock:
            self._flush_typing()
            steps = list(self.steps)
        text = as_text(steps)
        log.info(f"Stopped watching: {len(steps)} steps in {self.clock() - self.started:.0f} s")
        if not steps:
            return {"ok": True, "steps": 0, "note": "I saw no clicks or typing. Ask the person to show it again."}
        return {"ok": True, "task": self.task, "steps": len(steps), "demonstration": text,
                "note": "This is what the person did, in order. Now: (1) work out the GENERAL procedure -- what "
                        "stays the same each time and what would differ (a file name, a date, a recipient becomes "
                        "an input); (2) call save_skill with a short name, when to use it, and the steps in your "
                        "own words naming the controls and windows, with the inputs listed; (3) tell the person in "
                        "one or two sentences what you learnt and ask whether it is right. Never include anything "
                        "marked [hidden]."}

    def status(self):
        return {"watching": self.active, "steps": len(self.steps)}

    # ---- input (called on pynput's threads) -------------------------------------------------
    def _live(self):
        if not self.active:
            return False
        if self.clock() - self.started > MAX_SECONDS or len(self.steps) >= MAX_STEPS:
            self.active = False
            log.info("Watching ended by itself (time or step limit)")
            return False
        return True

    def on_click(self, x, y, button, pressed):
        if not pressed or not self._live():
            return
        if self.is_own(x, y):
            return
        b = str(getattr(button, "name", button)).lower()
        with self._lock:
            self._flush_typing()
            step = {"kind": "click", "button": "right" if "right" in b else ("middle" if "middle" in b else "left"),
                    "x": int(x), "y": int(y), "at": self.clock(), "element": "", "window": ""}
            # a second click on the same spot within 0.45 s is a double click
            last = self.steps[-1] if self.steps else None
            if (last and last["kind"] == "click" and last["button"] == step["button"] == "left"
                    and abs(last["x"] - step["x"]) < 6 and abs(last["y"] - step["y"]) < 6
                    and step["at"] - last["at"] < 0.45):
                last["double"] = True
                return
            self.steps.append(step)
        self._q.put(step)

    def on_press(self, key):
        if not self._live():
            return
        kind, name = _key_name(key)
        if kind is None:
            return
        with self._lock:
            if kind == "mod":
                self._mods.add(name)
                return
            chord = self._mods - {"shift"}
            if chord:                                             # Ctrl+S, Alt+F4, Win+E
                self._flush_typing()
                keyname = name.upper() if kind == "char" and len(name) == 1 else _NAMED.get(name, name.title())
                # with Ctrl held, pynput reports control characters ('\x13' for S): turn them back
                if kind == "char" and len(name) == 1 and ord(name) < 32:
                    keyname = chr(ord(name) + 64)
                order = [m for m in ("ctrl", "alt", "shift", "win") if m in self._mods]
                self.steps.append({"kind": "keys", "keys": "+".join([m.title() for m in order] + [keyname]),
                                   "at": self.clock(), "window": self._window})
                return
            if kind == "char" or name == "space":
                ch = " " if name == "space" else name
                if self._typing is None:
                    self._typing = {"kind": "type", "text": "", "at": self.clock(), "window": self._window}
                self._typing["text"] += ch
                return
            if name == "backspace" and self._typing is not None and self._typing["text"]:
                self._typing["text"] = self._typing["text"][:-1]
                return
            self._flush_typing()
            self.steps.append({"kind": "keys", "keys": _NAMED.get(name, name.replace("_", " ").title()),
                               "at": self.clock(), "window": self._window})

    def on_release(self, key):
        kind, name = _key_name(key)
        if kind == "mod":
            with self._lock:
                self._mods.discard(name)

    def _flush_typing(self):
        t, self._typing = self._typing, None
        if t and t["text"].strip():
            if _SECRET_WINDOW.search(t.get("window") or "") or t.get("secret"):
                t["text"] = "[hidden]"
            self.steps.append(t)

    # ---- reading what was clicked (worker thread) -----------------------------------------------
    def _resolve_loop(self):
        while True:
            step = self._q.get()
            try:
                element, window = self.describe(step["x"], step["y"])
                step["element"], step["window"] = str(element or "")[:80], str(window or "")[:80]
                with self._lock:
                    self._window = step["window"] or self._window
                    if _SECRET_WINDOW.search(step["element"]):       # a click into a password box
                        step["secret_next"] = True
                    if self._typing is not None:
                        self._typing.setdefault("window", self._window)
            except Exception as e:
                log.debug(f"Could not read what was clicked: {e}")
            finally:
                self._q.task_done()


def as_text(steps) -> str:
    """The demonstration in words: '1. In "Report - Excel": click "Save (Button)".'"""
    out, window, secret = [], None, False
    for s in steps:
        where = ""
        if s.get("window") and s["window"] != window:
            window = s["window"]
            where = f'In "{window}": '
        if s["kind"] == "click":
            what = f'"{s["element"]}"' if s.get("element") else f"at ({s['x']}, {s['y']}) (unlabelled)"
            verb = ("right-click" if s["button"] == "right" else "middle-click" if s["button"] == "middle"
                    else "double-click" if s.get("double") else "click")
            line = f"{where}{verb} {what}"
            secret = bool(s.get("secret_next"))
        elif s["kind"] == "type":
            text = "[hidden]" if secret else s["text"]
            line = f'{where}type "{text}"' if text != "[hidden]" else f"{where}type [hidden]"
            secret = False
        else:
            line = f"{where}press {s['keys']}"
        line = line[:1].upper() + line[1:]
        out.append(f"{len(out) + 1}. {line}.")
    return "\n".join(out)


WATCHER = Watcher()
_listeners = {"started": False}


def _real_describe(x, y):
    import desktop_control as dc
    element = dc.name_at_point(x, y)
    window = ""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.restype = wintypes.HWND
        h = u.GetAncestor(u.WindowFromPoint(wintypes.POINT(int(x), int(y))), 2)      # GA_ROOT
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, buf, 256)
        window = buf.value
    except Exception:
        pass
    return element, window


def _on_own_window(x, y):
    try:
        import vision
        return any(l <= x <= r and t <= y <= b for (l, t, r, b) in list(vision.SELF_RECTS))
    except Exception:
        return False


def install(is_own=None):
    """Start the global listeners once (they do nothing unless she is watching)."""
    if _listeners["started"]:
        return
    WATCHER.describe = _real_describe
    WATCHER.is_own = is_own or _on_own_window
    from pynput import keyboard, mouse
    mouse.Listener(on_click=WATCHER.on_click).start()
    keyboard.Listener(on_press=WATCHER.on_press, on_release=WATCHER.on_release).start()
    _listeners["started"] = True


def start(task=""):
    try:
        install()
    except Exception as e:
        return {"ok": False, "error": f"I cannot watch the mouse and keyboard on this PC: {e}"}
    return WATCHER.start(task)


def stop():
    return WATCHER.stop()
