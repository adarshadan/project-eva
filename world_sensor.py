"""A compact, read-only picture of what the person is doing (Phase 1).

The sensor samples the person's world a few times a second: where the cursor is
and how fast it moves, the foreground window's rect and a coarse kind, typing
rate, idle seconds, full-screen, and windows that have just appeared. It never
focuses, clicks, types into or moves a window -- every probe is a read, and the
page only uses the result to decide where to look.

Pure stdlib + config: no Qt and no pygetwindow at import time, so the checks
and any headless process can use it with fake probes.
"""
import math
import re
import time

import config

KINDS = ("editor", "browser", "video", "game", "call", "notification", "other")

# Checked in this order: call and video titles often also mention a browser,
# and an editor title can contain a word that looks like a game.
_CALL = ("zoom", "teams", "meet", "webex", "skype", "discord", "whereby", "goto meeting")
_VIDEO = ("youtube", "netflix", "vlc", "mpv", "prime video", "disney+", "hulu", "twitch")
_GAME = ("steam", "epic games", "minecraft", "roblox", "valorant", "fortnite", "league of legends",
         "dota", "counter-strike", "cs2", "overwatch", "world of warcraft", "starcraft")
_BROWSER = ("chrome", "edge", "firefox", "opera", "brave", "vivaldi", "safari", "arc")
_EDITOR = ("notepad", "vim", "nvim", "emacs", "code", "word", "wordpad", "excel", "powerpoint",
           "outlook", "sublime", "pycharm", "intellij", "android studio", "text editor", "writer",
           "calc", "libreoffice", "obsidian", "typora", "postman")


def _mentions(title: str, words) -> bool:
    """Whole-word, case-insensitive keyword match ("Edge" yes, "knowledge" no)."""
    for word in words:
        if re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", title):
            return True
    return False


def classify_title(title: str) -> str:
    """A coarse kind from a window title. Cosmetic only; checked in order."""
    t = (title or "").lower()
    if not t:
        return "other"
    if _mentions(t, _CALL):
        return "call"
    if _mentions(t, _VIDEO):
        return "video"
    if _mentions(t, _GAME):
        return "game"
    if _mentions(t, _BROWSER):
        return "browser"
    if _mentions(t, _EDITOR):
        return "editor"
    return "other"


class WorldSensor:
    """`probes` is read-only and duck-typed (see e.g. eva_app/life.py):
    cursor() -> (x, y), foreground() -> {"title", "rect"}, screen() -> (w, h),
    eva_rect() -> (l, t, r, b), idle_seconds() -> float, typing_rate() -> float,
    windows() -> [(hwnd, title, (l, t, r, b))]; optional hidden(hwnd) -> bool (minimised / cloaked)."""

    SCAN_INTERVAL = 0.5        # window enumeration is the only heavy read: 2 Hz
    NEW_TTL = 3.0              # a new-window event stays reportable this long
    MAX_WINDOWS = 8            # Phase 5: windows offered to the page as affordances
    KEEP_WINDOWS = 12          # ... chosen from this many topmost valid ones
    MIN_W, MIN_H = 200, 120

    def __init__(self, probes, clock=time.monotonic):
        self._probes = probes
        self._clock = clock
        self._last_t = None
        self._last_cursor = None
        self._last_scan = None
        self._first_scan = True
        self._seen = set()
        self._new = None
        self._new_until = 0.0
        self._aff = []             # compact window list from the last scan (Phase 5)

    # -- defensive readers -------------------------------------------------------------------
    @staticmethod
    def _tuple4(value):
        try:
            l, t, r, b = (int(v) for v in value[:4])
            return (l, t, r, b)
        except Exception:
            return None

    def _scan_windows(self, now: float):
        """One pass over the person's titled windows. The first pass only seeds
        `_seen`, so opening Eva does not announce every existing window."""
        try:
            windows = self._probes.windows() or []
        except Exception:
            windows = []
        found = None
        self._aff = self._affordances(windows)
        for item in windows:
            try:
                hwnd, _title, rect = item
                hwnd = int(hwnd)
            except Exception:
                continue
            if hwnd in self._seen:
                continue
            self._seen.add(hwnd)
            if self._first_scan:
                continue
            r = self._tuple4(rect)
            if r is None or r[2] - r[0] <= 0 or r[3] - r[1] <= 0:
                continue
            if found is None:
                kind = "notification" if (r[2] - r[0]) < 600 and (r[3] - r[1]) < 300 else "window"
                found = {"rect": list(r), "at": float(now), "kind": kind}
        if self._first_scan:
            self._first_scan = False
        return found

    def _affordances(self, windows):
        """Phase 5: the person's visible top-level windows (the topmost KEEP_WINDOWS valid ones), as rects and a
        coarse kind only -- never a title. Tiny, minimised, cloaked and off-screen windows are left out. sample()
        turns this into the compact list the page gets."""
        hidden = getattr(self._probes, "hidden", None)
        out = []
        for z, item in enumerate(windows):
            try:
                hwnd, title, rect = item
                hwnd = int(hwnd)
            except Exception:
                continue
            r = self._tuple4(rect)
            if r is None:
                continue
            w, h = r[2] - r[0], r[3] - r[1]
            if w < self.MIN_W or h < self.MIN_H or r[0] < -5000 or r[1] < -5000:
                continue
            try:
                if hidden is not None and hidden(hwnd):
                    continue
            except Exception:
                pass
            out.append({"id": hwnd, "rect": list(r), "kind": classify_title(str(title or "")), "z": z})
            if len(out) >= self.KEEP_WINDOWS:
                break
        return out

    def sample(self) -> dict:
        now = float(self._clock())

        try:
            cx, cy = (int(v) for v in self._probes.cursor()[:2])
        except Exception:
            cx, cy = 0, 0
        speed = 0.0
        if self._last_cursor is not None and self._last_t is not None:
            dt = now - self._last_t
            if dt > 0:
                speed = math.hypot(cx - self._last_cursor[0], cy - self._last_cursor[1]) / dt
        speed = max(0.0, min(5000.0, speed))
        self._last_t, self._last_cursor = now, (cx, cy)

        try:
            sw, sh = (int(v) for v in self._probes.screen()[:2])
        except Exception:
            sw, sh = 0, 0
        eva = self._tuple4(self._probes.eva_rect()) or (0, 0, 0, 0)

        try:
            idle = max(0.0, float(self._probes.idle_seconds()))
        except Exception:
            idle = 0.0
        try:
            keys = max(0.0, float(self._probes.typing_rate()))
        except Exception:
            keys = 0.0

        try:
            fg = self._probes.foreground() or {}
        except Exception:
            fg = {}
        title = str(fg.get("title") or "")[:80]
        rect = self._tuple4(fg.get("rect")) if fg.get("rect") else None
        kind = classify_title(title)
        # full screen = the whole monitor, taskbar included. A maximised window stops at the taskbar
        # (about 95 % of the height), so it must NOT count -- or she would sit in focus mode all day.
        full = bool(rect and sw > 0 and sh > 0
                    and rect[2] - rect[0] >= sw * 0.99 and rect[3] - rect[1] >= sh * 0.99)

        # The window scan runs at 2 Hz, independently of the 8 Hz sample rate.
        if self._last_scan is None or now - self._last_scan >= self.SCAN_INTERVAL:
            self._last_scan = now
            event = self._scan_windows(now)
            if event is not None:
                self._new = event
                self._new_until = now + self.NEW_TTL
        if self._new is not None and now > self._new_until:
            self._new = None

        focus_keys = getattr(config, "EVA_FOCUS_TYPING_KEYS_PER_MIN", 120)
        focus = bool(keys > focus_keys or full or kind == "call")

        fg_id = fg.get("hwnd")
        # The compact list: the active window is always in it (so a maximised one is still known to the guard);
        # a non-active window that nearly fills the screen is no scenery. At most MAX_WINDOWS, largest first.
        wins = []
        for e in self._aff:
            same = rect is not None and all(abs(a - b) <= 2 for a, b in zip(e["rect"], rect))
            active = bool(same or (fg_id is not None and e["id"] == fg_id))
            area = (e["rect"][2] - e["rect"][0]) * (e["rect"][3] - e["rect"][1])
            if not active and sw > 0 and sh > 0 and area >= 0.95 * sw * sh:
                continue
            wins.append({"id": e["id"], "rect": list(e["rect"]), "kind": e["kind"], "z": e["z"],
                         "active": active, "_area": area})
        wins.sort(key=lambda e: (not e["active"], -e["_area"]))
        wins = wins[:self.MAX_WINDOWS]
        for e in wins:
            del e["_area"]
        try:
            ground = int(self._probes.ground())
        except Exception:
            ground = None

        return {
            "t": now,
            "cursor": [cx, cy],
            "speed": float(speed),
            "window": {"rect": list(rect) if rect else None, "kind": kind, "title": title},
            "keys": float(keys),
            "idle": float(idle),
            "full": bool(full),
            "new": dict(self._new) if self._new is not None else None,
            "screen": [sw, sh],
            "eva": list(eva),
            "focus": focus,
            "windows": wins,
            "ground": ground,
        }
