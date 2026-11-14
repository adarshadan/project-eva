"""Eva -- Phase 2: the desktop side of her drives (docs/life/LIFE_DESIGN.md).

The page (web/avatar_3d.html) owns her inner state and CHOOSES what to do. It cannot touch the desktop, so a
behaviour that has to move her window (wander to a spot, sit down, settle in a corner) is queued as an
INTENT. This module is the only place that turns an intent into a move, and it re-checks every rule against the
real cursor, foreground window and body state first -- the page's own check is a convenience, this is the
guarantee:

  * never move toward or stand within CLEAR_PX of the cursor or the active window's working area
    (her swept path counts, not just where she ends up);
  * in focus mode only a corner settle is allowed (and only once per intent), never a wander or a sit;
  * never while she is busy, napping, carried, or where the person put her;
  * visual only: nothing here focuses, clicks, types into or moves a person's window.

Pure stdlib, no Qt: the body and the probes are duck-typed, so the checks run without a screen.

    probes.cursor() -> (x, y)                  probes.work_rect() -> (l, t, r, b) | None  (foreground window)
    probes.eva_rect() -> (l, t, r, b)          probes.ground() -> y of the taskbar top
    probes.screen_rect() -> (l, t, r, b)       probes.focus() -> bool
    probes.busy() -> bool                      probes.napping() -> bool
    body.state() -> {"sitting","airborne","walking","placed","held"}
    body.walk_to(x_centre, speed) / body.sit_here() / body.halt()
"""
import math

CLEAR_PX = 150            # never nearer than this to the cursor or the active window
GROUND_TOL = 12           # px: how close to the ground line still counts as standing on it
KINDS = ("wander", "sit", "corner")


def rect_gap(a, b):
    """Distance between two rects (l, t, r, b); 0 when they touch or overlap."""
    dx = max(b[0] - a[2], a[0] - b[2], 0)
    dy = max(b[1] - a[3], a[1] - b[3], 0)
    return math.hypot(dx, dy)


def point_gap(p, r):
    """Distance from a point to a rect; 0 inside."""
    dx = max(r[0] - p[0], p[0] - r[2], 0)
    dy = max(r[1] - p[1], p[1] - r[3], 0)
    return math.hypot(dx, dy)


def swept(a, b):
    """The rect that covers a body going from rect a to rect b (she moves along a line, so this is generous)."""
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def moved_to(eva, x_centre, screen):
    """Her rect with its centre moved to x_centre, kept inside the screen."""
    w = eva[2] - eva[0]
    left = int(round(x_centre - w / 2.0))
    left = max(screen[0], min(left, screen[2] - w))
    return (left, eva[1], left + w, eva[3])


def _clear_of(gap, eva, target, clear):
    """gap(rect) -> distance to the thing to keep clear of. The swept path must stay `clear` away; a path that
    starts inside the zone is allowed only when it ends outside and never gets nearer than it started."""
    sweep = swept(eva, target)
    if gap(sweep) >= clear:
        return True
    start, end = gap(eva), gap(target)
    return start < clear and end >= clear and gap(sweep) >= start - 1


def path_is_clear(eva, target, cursor, work, clear=CLEAR_PX):
    """True when her walk from `eva` to `target` keeps `clear` px from the cursor and the work area."""
    if cursor is not None and not _clear_of(lambda r: point_gap(cursor, r), eva, target, clear):
        return False
    if work is not None and not _clear_of(lambda r: rect_gap(r, work), eva, target, clear):
        return False
    return True


def vet_intent(intent, probes, body, clear=CLEAR_PX):
    """(ok, reason, x_centre). Everything the page asked for is checked again here."""
    if not isinstance(intent, dict) or intent.get("kind") not in KINDS:
        return False, "unknown_intent", None
    kind = intent["kind"]
    st = body.state()
    if probes.busy():
        return False, "busy", None
    if probes.napping():
        return False, "napping", None
    if st.get("held"):
        return False, "held", None
    if st.get("placed"):
        return False, "placed", None            # where the person put her, she stays
    focus = bool(probes.focus())
    if focus and kind != "corner":
        return False, "focus_mode", None        # the person is concentrating: only a quiet corner settle
    eva, ground, screen = probes.eva_rect(), probes.ground(), probes.screen_rect()
    if abs(eva[3] - ground) > GROUND_TOL and not st.get("sitting"):
        return False, "not_on_ground", None
    if st.get("walking"):
        return False, "already_walking", None
    if kind == "sit":
        if st.get("sitting"):
            return False, "already_sitting", None
        return True, "ok", None
    try:
        x = float(intent["x"])
    except (KeyError, TypeError, ValueError):
        return False, "no_target", None
    target = moved_to(eva, x, screen)
    if abs(target[0] - eva[0]) < 4:
        return False, "no_move", None
    if not path_is_clear(eva, target, probes.cursor(), probes.work_rect(), clear):
        return False, "too_close", None
    return True, "ok", (target[0] + target[2]) / 2.0


class DriveExecutor:
    """Executes vetted intents with the body's existing walk and sit. One result per intent."""

    def __init__(self, body, probes, clear=CLEAR_PX):
        self.body, self.probes, self.clear = body, probes, clear
        self.walking = None           # {"id", "cursor_gap", "work_gap"} while a drive walk runs

    def handle(self, intent):
        ok, reason, x = vet_intent(intent, self.probes, self.body, self.clear)
        res = {"id": intent.get("id") if isinstance(intent, dict) else None, "ok": ok, "reason": reason}
        if not ok:
            return res
        if intent["kind"] == "sit":
            self.body.sit_here()
            return res
        speed = int(max(5, min(12, float(intent.get("speed", 8) or 8))))
        eva, cur, work = self.probes.eva_rect(), self.probes.cursor(), self.probes.work_rect()
        self.walking = {"id": res["id"],
                        "cursor_gap": point_gap(cur, eva) if cur is not None else None,
                        "work_gap": rect_gap(eva, work) if work is not None else None}
        self.body.walk_to(x, speed)
        return res

    def guard_walk(self):
        """Called a few times a second. If the cursor or the work area closes in on her while she walks, she stops
        where she is: she never walks into it. True when she was stopped."""
        if not self.walking:
            return False
        if not self.body.state().get("walking"):
            self.walking = None
            return False
        eva = self.probes.eva_rect()
        cur, work = self.probes.cursor(), self.probes.work_rect()
        w = self.walking
        closing = False
        if cur is not None and w["cursor_gap"] is not None:
            g = point_gap(cur, eva)
            closing = closing or (g < self.clear and g < w["cursor_gap"] - 2)
        if work is not None and w["work_gap"] is not None:
            g = rect_gap(eva, work)
            closing = closing or (g < self.clear and g < w["work_gap"] - 2)
        if closing:
            self.body.halt()
            self.walking = None
            return True
        return False
