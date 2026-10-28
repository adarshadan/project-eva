"""Eva -- reaching a control by whatever means works, most reliable first.

A control can resist any single approach: it may not implement the UI
Automation pattern she tried, it may report an empty or off-screen
rectangle so a click has nowhere to land, it may not be in the accessibility
tree at all (canvas, custom-drawn and game UIs), or it may simply be
reachable only by keyboard. Rather than giving up at the first failure she
works down a ladder:

    1 pattern   act on the control's own UI Automation pattern
    2 by_name   find it by its visible label and act on that
    3 keyboard  Tab through the window until focus lands on it, then press it
    4 vision    look at the screen, find it, and click where it is

THE RULE THAT MAKES A LADDER SAFE. Moving down a rung is only allowed when
the previous one demonstrably did NOT act, or demonstrably did not work.
If a rung acted and we cannot tell whether it worked, we stop and report
that, and do NOT try the next method: clicking "Send" and then pressing
Enter on it and then clicking it again is how a message goes out three
times. "Unsure" ends the climb; only "definitely not" continues it.
"""
import difflib
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from logger import get_logger

log = get_logger(__name__)

RUNGS = ("pattern", "by_name", "keyboard", "vision")
ACTIONS = ("click", "toggle", "select", "expand", "collapse", "set_value", "focus", "type")
# What each requested action means to the pattern layer.
PATTERN_ACTION = {"click": "auto", "toggle": "toggle", "select": "select", "expand": "expand",
                  "collapse": "collapse", "set_value": "set_value", "type": "set_value",
                  "focus": "focus"}


@dataclass
class Attempt:
    rung: str = ""
    acted: bool = False                 # did anything reach the UI?
    effective: Optional[bool] = None    # True / False if we could tell; None if we couldn't
    detail: str = ""
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    name: str
    role: str
    element_id: str
    rect: Optional[tuple]
    enabled: bool = True
    visible: bool = True


# ---------------------------------------------------------------------------
# Pure logic (tested without a desktop)
# ---------------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", " ".join((text or "").lower().replace("&", "").split()))


def rank_candidates(target: str, candidates, action: str = "click", threshold: float = 0.72):
    """Best matches for a label, best first. Exact beats prefix beats
    containment beats fuzzy; controls that cannot be acted on are dropped
    rather than ranked low, and the role has to make sense for the action."""
    want = _norm(target)
    if not want:
        return []
    editable = {"edit", "document", "combobox", "spinner", "slider"}
    scored = []
    for c in candidates:
        if not c.enabled or not c.visible:
            continue
        have = _norm(c.name)
        if not have:
            continue
        if have == want:
            score = 1.0
        elif have.startswith(want) or want.startswith(have):
            score = 0.9
        elif want in have or have in want:
            score = 0.8
        else:
            score = difflib.SequenceMatcher(None, want, have).ratio()
        if score < threshold:
            continue
        role = (c.role or "").lower()
        if action in ("set_value", "type") and role in editable:
            score += 0.05
        elif action in ("set_value", "type") and role not in editable:
            score -= 0.15
        if c.rect and (c.rect[2] - c.rect[0] < 2 or c.rect[3] - c.rect[1] < 2):
            score -= 0.1                       # findable, but hard to click
        scored.append((score, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _s, c in scored]


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def ahash(pixels) -> int:
    """Average hash of an 8x8 greyscale grid (64 numbers): a cheap
    fingerprint that changes when the screen's layout does."""
    flat = [p for row in pixels for p in row] if pixels and isinstance(pixels[0], (list, tuple)) else list(pixels)
    avg = sum(flat) / len(flat)
    bits = 0
    for p in flat:
        bits = (bits << 1) | (1 if p >= avg else 0)
    return bits


def screen_changed(before: int, after: int, threshold: int = 3) -> bool:
    return hamming(before, after) >= threshold


def run_ladder(rungs, ctx) -> dict:
    """rungs: [(name, fn(ctx) -> Attempt)]. Returns one honest result."""
    attempts = []
    for name, fn in rungs:
        try:
            att = fn(ctx)
        except Exception as e:                     # a rung must never take the ladder down
            log.exception(f"ui rung {name!r} raised")
            att = Attempt(acted=False, detail=f"{type(e).__name__}: {e}")
        att.rung = name
        attempts.append(att)
        if att.acted and att.effective is True:
            return _result(True, True, att, attempts)
        if att.acted and att.effective is None:
            out = _result(True, False, att, attempts)
            out["note"] = ("it was done, but nothing observable confirms it worked. NOT trying "
                           "another method, in case this one did work and a second would repeat it.")
            return out
        # not acted, or acted and demonstrably ineffective: go down a rung
    out = {"ok": False, "verified": False,
           "error": "none of the ways of reaching it worked: "
                    + "; ".join(f"{a.rung}: {a.detail or 'no effect'}" for a in attempts),
           "tried": [_summary(a) for a in attempts]}
    return out


def _summary(a: Attempt) -> dict:
    return {"method": a.rung, "acted": a.acted, "worked": a.effective, "detail": a.detail}


def _result(ok, verified, att, attempts):
    out = {"ok": ok, "verified": verified, "method": att.rung, "detail": att.detail,
           "tried": [_summary(a) for a in attempts]}
    out.update(att.data)
    return out


# ---------------------------------------------------------------------------
# The real desktop, behind an interface so the ladder can be tested
# ---------------------------------------------------------------------------
class DesktopUI:
    """Everything the ladder needs from the machine."""

    def find_candidates(self, window_handle, text):
        import desktop_control as dc
        return [Candidate(**c) for c in dc.find_elements_by_text(window_handle, text)]

    def act_pattern(self, window_handle, element_id, action, value=None):
        import desktop_control as dc
        return dc.act_on_element(window_handle, element_id, action, value, fallback=False)

    def signature(self, window_handle):
        import desktop_control as dc
        return dc.ui_signature(window_handle)

    def focus(self, window_handle):
        import desktop_control as dc
        return dc.restore_foreground(window_handle)

    def tab_to(self, window_handle, text, max_tabs=40):
        import desktop_control as dc
        return dc.tab_to_element(window_handle, text, max_tabs)

    def press(self, key):
        import desktop_control as dc
        return dc.press_key(key)

    def type_text(self, text):
        import desktop_control as dc
        return dc.type_text(text, verify=False)

    def locate_visual(self, description):
        import vision
        return vision.locate_on_screen(description)

    def click_point(self, x, y):
        import desktop_control as dc
        return dc.click_coords(x, y, "ui_navigate: vision fallback")

    def screen_hash(self):
        import desktop_control as dc
        return dc.screen_hash()


@dataclass
class Ctx:
    window_handle: int
    target: str
    action: str = "click"
    value: Optional[str] = None
    element_id: Optional[str] = None
    ui: object = None
    settle: float = 0.25


def _pause(ctx):
    if ctx.settle:
        time.sleep(ctx.settle)


def _judge(ctx, before_sig, result=None) -> Optional[bool]:
    """Did it work? A stateful result says so definitively; otherwise a
    changed window fingerprint says yes, and an unchanged one says only
    'unknown' -- never 'no' (see the module docstring)."""
    if isinstance(result, dict) and result.get("verified") is True:
        return True
    if isinstance(result, dict) and result.get("verified") is False and result.get("warning"):
        return False                     # the control reported its state did not change
    _pause(ctx)
    try:
        after = ctx.ui.signature(ctx.window_handle)
    except Exception:
        return None
    return True if (before_sig is not None and after != before_sig) else None


def rung_pattern(ctx) -> Attempt:
    if not ctx.element_id:
        return Attempt(detail="no element id given, so nothing to act on directly")
    before = ctx.ui.signature(ctx.window_handle)
    r = ctx.ui.act_pattern(ctx.window_handle, ctx.element_id, PATTERN_ACTION.get(ctx.action, "auto"), ctx.value)
    if not r.get("ok"):
        return Attempt(detail=str(r.get("error", "the element would not accept it"))[:120])
    return Attempt(acted=True, effective=_judge(ctx, before, r), detail="through its control pattern",
                   data={"state": r.get("state")})


def rung_by_name(ctx) -> Attempt:
    cands = rank_candidates(ctx.target, ctx.ui.find_candidates(ctx.window_handle, ctx.target), ctx.action)
    if not cands:
        return Attempt(detail=f"nothing labelled like “{ctx.target}” in the window's accessibility tree")
    best = cands[0]
    before = ctx.ui.signature(ctx.window_handle)
    r = ctx.ui.act_pattern(ctx.window_handle, best.element_id, PATTERN_ACTION.get(ctx.action, "auto"), ctx.value)
    if not r.get("ok"):
        return Attempt(detail=f"found “{best.name}” but: {str(r.get('error', ''))[:100]}")
    return Attempt(acted=True, effective=_judge(ctx, before, r),
                   detail=f"found “{best.name}” ({best.role}) by its label",
                   data={"element": best.name, "state": r.get("state")})


def rung_keyboard(ctx) -> Attempt:
    focus = ctx.ui.focus(ctx.window_handle)
    if isinstance(focus, dict) and not focus.get("ok", True):
        return Attempt(detail=f"could not bring the window forward: {focus.get('error', '')}"[:120])
    found = ctx.ui.tab_to(ctx.window_handle, ctx.target)
    if not found.get("found"):
        return Attempt(detail=f"tabbed {found.get('steps', 0)} times and focus never landed on “{ctx.target}”")
    before = ctx.ui.signature(ctx.window_handle)
    if ctx.action in ("set_value", "type"):
        ctx.ui.press("ctrl+a")
        ctx.ui.type_text(str(ctx.value or ""))
    elif ctx.action in ("toggle", "select"):
        ctx.ui.press("space")
    elif ctx.action != "focus":
        ctx.ui.press("enter")
    return Attempt(acted=True, effective=_judge(ctx, before),
                   detail=f"tabbed to “{found.get('focused', ctx.target)}” in {found.get('steps')} steps",
                   data={"tab_steps": found.get("steps")})


def rung_vision(ctx) -> Attempt:
    loc = ctx.ui.locate_visual(f"the “{ctx.target}” control")
    if not isinstance(loc, dict) or not loc.get("found"):
        return Attempt(detail="could not see it on the screen: "
                              f"{(loc or {}).get('reason') or (loc or {}).get('error') or 'not visible'}"[:140])
    before = ctx.ui.screen_hash()
    ctx.ui.click_point(loc["center_x"], loc["center_y"])
    if ctx.action in ("set_value", "type"):
        ctx.ui.press("ctrl+a")
        ctx.ui.type_text(str(ctx.value or ""))
    _pause(ctx)
    after = ctx.ui.screen_hash()
    changed = before is not None and after is not None and screen_changed(before, after)
    note = "" if loc.get("refined") else " (single coarse pass; less certain)"
    return Attempt(acted=True, effective=True if changed else None,
                   detail=f"clicked where it looked to be, at ({loc['center_x']}, {loc['center_y']}){note}",
                   data={"at": [loc["center_x"], loc["center_y"]]})


_RUNG_FUNCS = {"pattern": rung_pattern, "by_name": rung_by_name,
               "keyboard": rung_keyboard, "vision": rung_vision}


def navigate(window_handle, target, action="click", value=None, element_id=None,
             rungs=RUNGS, ui=None) -> dict:
    """Reach a control by the best available means. `rungs` lets a caller
    that has already tried the first ones start further down."""
    if action not in ACTIONS:
        return {"ok": False, "error": f"unknown action {action!r}", "actions": list(ACTIONS)}
    if action in ("set_value", "type") and value is None:
        return {"ok": False, "error": f"{action} needs a value"}
    if not (target or element_id):
        return {"ok": False, "error": "say what to reach: a label (target) or an element_id"}
    ctx = Ctx(int(window_handle), str(target or ""), action, value, element_id, ui or DesktopUI())
    return run_ladder([(n, _RUNG_FUNCS[n]) for n in rungs if n in _RUNG_FUNCS], ctx)
