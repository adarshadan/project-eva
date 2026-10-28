"""Eva -- repeat an action until told to stop, or until it is done.

"Keep scrolling until I say stop", "scroll to the bottom", "keep pressing
the right arrow", "click Next until the last page", "hold W for two
seconds every three seconds". One action, a pace, and when to end:

  action   scroll (up/down/left/right, notches) | keys ("space", "ctrl+right")
           | click (x, y) | hold (keys, seconds) | type (text)
           | sequence [steps of the above]
  every    seconds between repeats (0.05 .. 60); adjustable while running
  until    stopped (default) | count N | seconds N | end (the window stops
           changing: the end of a page or a list) | text "..." (words appear
           on screen, read locally) | gone "..." (words disappear)

Keys that close, quit or delete are refused in a loop.
"""
import re
import time

import activities
import perception
from logger import get_logger

log = get_logger(__name__)

# A loop repeats blindly: nothing that closes, quits, deletes or logs out.
REFUSED_KEYS = re.compile(r"(^|\+)(delete|del|backspace)$|alt\+f4|ctrl\+w$|ctrl\+shift\+w|ctrl\+q$|"
                          r"ctrl\+shift\+(delete|del|esc)|win\+l$|ctrl\+alt\+(delete|del)|shift\+delete", re.I)
SCROLL_NOTCH = {"down": -1, "up": 1, "left": -1, "right": 1}


def _refused(action):
    steps = action.get("steps") if action.get("kind") == "sequence" else [action]
    for st in steps or []:
        k = str(st.get("keys", "")).lower().replace(" ", "")
        if k and REFUSED_KEYS.search(k):
            return f"'{st.get('keys')}' is not something I repeat in a loop (it can close or delete things)"
        if st.get("kind") not in ("scroll", "keys", "click", "hold", "type", "wait"):
            return f"unknown step {st.get('kind')!r}"
    return None


class LoopActivity(activities.Activity):
    kind = "loop"

    def __init__(self, action, every=0.5, until="stopped", until_value=None, window=None, max_seconds=600,
                 title=None, hands=None, grab=None, rect_of=None, read_text=None):
        super().__init__(title or _describe(action, until, until_value), max_seconds, window=window)
        self.action = dict(action)
        self.every = max(0.05, min(60.0, float(every or 0.5)))
        self.until = str(until or "stopped").lower()
        self.until_value = until_value
        self.count = 0
        self._hands = hands
        self._grab = grab or perception.grab_once
        self._rect_of = rect_of or perception.window_client_rect
        self._read = read_text or perception.read_text
        self._static_runs = 0

    def adjust(self, faster=None, slower=None, every=None, direction=None, amount=None, **_):
        if every:
            self.every = max(0.05, min(60.0, float(every)))
        if faster:
            self.every = max(0.05, self.every / 1.6)
        if slower:
            self.every = min(60.0, self.every * 1.6)
        if direction and self.action.get("kind") == "scroll":
            self.action["direction"] = direction
        if amount and self.action.get("kind") == "scroll":
            self.action["amount"] = max(1, min(50, int(amount)))
        return {"ok": True, "every": round(self.every, 2), "action": self.action}

    # ---- one repeat ------------------------------------------------------------------------
    def _do(self, step, rect):
        h = self._hands or perception.hands()
        k = step.get("kind")
        if k == "scroll":
            d = str(step.get("direction", "down")).lower()
            n = max(1, min(50, int(step.get("amount") or 3))) * SCROLL_NOTCH.get(d, -1)
            cx = cy = None
            if rect:
                cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            h.wheel(n, cx, cy, horizontal=d in ("left", "right"))
        elif k == "keys":
            h.tap(step["keys"])
        elif k == "hold":
            h.hold(step["keys"], min(30.0, float(step.get("seconds") or 0.5)))
        elif k == "click":
            h.click(step["x"], step["y"], step.get("button", "left"))
        elif k == "type":
            h.type(step.get("text", ""))
        elif k == "wait":
            time.sleep(min(10.0, float(step.get("seconds") or 0.5)))

    def run(self, ctx):
        why = _refused(self.action)
        if why:
            self.outcome, self.result = "failed", why
            return
        rect = self._rect_of(self.window) if self.window else None
        watch = rect
        if watch:                       # the middle of the window: past headers, sidebars and her own body
            w, hgt = watch[2] - watch[0], watch[3] - watch[1]
            watch = (watch[0] + w // 8, watch[1] + hgt // 6, watch[2] - w // 8, watch[3] - hgt // 8)
        before = self._grab(watch) if (watch and self.until in ("end", "text", "gone")) else None
        last_text_check = 0.0
        while ctx.alive():
            steps = self.action["steps"] if self.action.get("kind") == "sequence" else [self.action]
            for st in steps:
                self._do(st, rect)
            self.count += 1
            if self.count % 5 == 0 or self.every >= 1:
                ctx.progress(f"{self.count} times")
            if self.until == "count" and self.count >= int(self.until_value or 1):
                self.outcome, self.result = "done", f"done {self.count} times"
                return
            if self.until == "seconds" and ctx.elapsed() >= float(self.until_value or 1):
                self.outcome, self.result = "done", f"kept going for {int(ctx.elapsed())} seconds"
                return
            if not ctx.alive(self.every):
                break
            if watch and self.until == "end":
                now = self._grab(watch)
                if perception.changed(before, now) < 0.004:
                    self._static_runs += 1
                    if self._static_runs >= 3:   # three repeats without the page moving: the end
                        self.outcome, self.result = "done", f"reached the end after {self.count} times"
                        return
                else:
                    self._static_runs = 0
                before = now
            if watch and self.until in ("text", "gone") and time.monotonic() - last_text_check > 1.0:
                last_text_check = time.monotonic()
                seen = self._read(self._grab(rect)).lower()
                want = str(self.until_value or "").lower()
                if want and ((want in seen) == (self.until == "text")):
                    self.outcome = "done"
                    self.result = (f"'{self.until_value}' is on screen now" if self.until == "text"
                                   else f"'{self.until_value}' is gone now") + f" (after {self.count} times)"
                    return
        if not self.result:
            self.result = f"stopped after {self.count} times"


def _describe(action, until, value):
    k = action.get("kind")
    what = {"scroll": f"Scrolling {action.get('direction', 'down')}", "keys": f"Pressing {action.get('keys')}",
            "hold": f"Holding {action.get('keys')}", "click": "Clicking", "type": "Typing",
            "sequence": "Repeating"}.get(k, "Repeating")
    tail = {"end": " to the end", "count": f" {value} times", "seconds": f" for {value} s",
            "text": f" until '{value}' shows", "gone": f" until '{value}' is gone"}.get(str(until), "")
    return what + tail
