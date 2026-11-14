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

Phase 5 (windows as surroundings): `perch` (sit on a window's top edge), `lean_wall` and `peek_edge` (stand at
a window's vertical edge) name a window by its id. The target is looked up in THIS module's own window list
(probes.windows(), the world sample's compact list) and the spot is computed here -- the page's coordinates are
not trusted. Never the active window (flag or same rect), never within CLEAR_PX of the cursor or the active
window (swept path included), never in focus mode. `get_down` (hop to the taskbar) is always allowed unless she
is being carried. `check_perch()` runs on every world sample: window gone, moved, active, cursor near or focus
mode -> she gets down.

Pure stdlib, no Qt: the body and the probes are duck-typed, so the checks run without a screen.

    probes.cursor() -> (x, y)                  probes.work_rect() -> (l, t, r, b) | None  (foreground window)
    probes.eva_rect() -> (l, t, r, b)          probes.ground() -> y of the taskbar top
    probes.screen_rect() -> (l, t, r, b)       probes.focus() -> bool
    probes.busy() -> bool                      probes.napping() -> bool
    probes.windows() -> [{"id", "rect", "active"}]   (Phase 5; optional)
    body.state() -> {"sitting","airborne","walking","placed","held"}
    body.walk_to(x_centre, speed) / body.sit_here() / body.halt()
    body.perch_on(x_centre, edge_y, window_id) / body.get_down()      (Phase 5)
"""
import math
import time

CLEAR_PX = 150            # never nearer than this to the cursor or the active window
GROUND_TOL = 12           # px: how close to the ground line still counts as standing on it
WINDOW_KINDS = ("perch", "lean_wall", "peek_edge")
KINDS = ("wander", "sit", "corner", "get_down") + WINDOW_KINDS
PERCH_MARGIN = 12         # px kept between her feet-span and the ends of a title bar
SAME_RECT_PX = 3          # two rects this close are the same window


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


def _same_rect(a, b, tol=SAME_RECT_PX):
    try:
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
    except Exception:
        return False


def _windows(probes):
    fn = getattr(probes, "windows", None)
    try:
        out = list(fn() or []) if fn else []
    except Exception:
        out = []
    return [w for w in out if isinstance(w, dict) and isinstance(w.get("rect"), (list, tuple)) and len(w["rect"]) == 4]


def active_rects(probes, windows=None):
    """The rects of the person's active window: the sample's flagged one(s) and the foreground work area."""
    out = [tuple(w["rect"]) for w in (_windows(probes) if windows is None else windows) if w.get("active")]
    wr = probes.work_rect()
    if wr is not None:
        out.append(tuple(wr))
    return out


def is_active_window(w, probes, windows=None):
    return bool(w.get("active")) or any(_same_rect(w["rect"], a) for a in active_rects(probes, windows))


def vet_window_intent(intent, probes, st, clear=CLEAR_PX):
    """(ok, reason, plan) for perch / lean_wall / peek_edge. plan = {"x": centre, "y": feet y, "wid", "side"}."""
    kind = intent["kind"]
    wins = _windows(probes)
    wid = intent.get("wid")
    w = next((x for x in wins if wid is not None and x.get("id") == wid), None)
    if w is None:
        return False, "no_window", None
    if is_active_window(w, probes, wins):
        return False, "active_window", None                  # never the one the person is working in
    eva, ground, screen = probes.eva_rect(), probes.ground(), probes.screen_rect()
    try:
        rect = tuple(int(v) for v in w["rect"])
    except (TypeError, ValueError):
        return False, "no_window", None
    h = eva[3] - eva[1]
    half = (eva[2] - eva[0]) / 2.0
    side = intent.get("side")
    if kind == "perch":
        if rect[2] - rect[0] < 2 * half + 2 * PERCH_MARGIN:
            return False, "too_narrow", None
        edge_y = rect[1]
        if edge_y - h < screen[1] + 4:
            return False, "no_room", None                    # no head room above the title bar
        if edge_y > ground - 0.4 * h:
            return False, "too_low", None                    # that is the taskbar line, not a ledge
        try:
            x = float(intent["x"])
        except (KeyError, TypeError, ValueError):
            return False, "no_target", None
        x = max(rect[0] + half + PERCH_MARGIN, min(x, rect[2] - half - PERCH_MARGIN))
        target = (int(x - half), int(edge_y - h), int(x + half), int(edge_y))
        plan = {"x": x, "y": edge_y, "wid": wid, "side": None}
    else:
        if side not in ("left", "right"):
            return False, "no_target", None
        if rect[1] > ground - 0.6 * h or rect[3] < ground - 0.5 * h:
            return False, "not_a_wall", None                 # it must reach down to where she stands
        edge = rect[0] if side == "left" else rect[2]
        if kind == "lean_wall":
            x = edge - half - 2 if side == "left" else edge + half + 2
        else:
            x = float(edge)
        if x - half < screen[0] or x + half > screen[2] + 1:
            return False, "off_screen", None
        target = (int(x - half), int(ground - h), int(x + half), int(ground))
        plan = {"x": x, "y": ground, "wid": wid, "side": side}
        if abs(x - (eva[0] + eva[2]) / 2.0) < 4 and abs(eva[3] - ground) <= GROUND_TOL:
            return False, "no_move", None
    cur = probes.cursor()
    acts = active_rects(probes, wins) or [None]
    for i, a in enumerate(acts):
        if not path_is_clear(eva, target, cur if i == 0 else None, a, clear):
            return False, "too_close", None
    return True, "ok", plan


def vet_intent(intent, probes, body, clear=CLEAR_PX):
    """(ok, reason, x_centre) -- for the Phase 5 window kinds the third value is a plan dict. Everything the
    page asked for is checked again here."""
    if not isinstance(intent, dict) or intent.get("kind") not in KINDS:
        return False, "unknown_intent", None
    kind = intent["kind"]
    st = body.state()
    if kind == "get_down":
        if st.get("held"):
            return False, "held", None
        eva, ground = probes.eva_rect(), probes.ground()
        if not st.get("sitting") and abs(eva[3] - ground) <= GROUND_TOL:
            return False, "on_ground", None
        return True, "ok", None                              # coming down is never refused
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
    if kind in WINDOW_KINDS:
        return vet_window_intent(intent, probes, st, clear)
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

    def __init__(self, body, probes, clear=CLEAR_PX, clock=time.monotonic):
        self.body, self.probes, self.clear = body, probes, clear
        self.clock = clock
        self.walking = None           # {"id", "cursor_gap", "work_gap"} while a drive walk runs
        self.perch = None             # {"wid", "rect", "since"} from a vetted perch until she is down again

    def handle(self, intent):
        ok, reason, x = vet_intent(intent, self.probes, self.body, self.clear)
        res = {"id": intent.get("id") if isinstance(intent, dict) else None, "ok": ok, "reason": reason}
        if not ok:
            return res
        if intent["kind"] == "sit":
            self.body.sit_here()
            return res
        if intent["kind"] == "get_down":
            self.perch = None
            self.body.get_down()
            return res
        if intent["kind"] == "perch":
            win = next(w for w in _windows(self.probes) if w.get("id") == x["wid"])
            self.perch = {"wid": x["wid"], "rect": tuple(int(v) for v in win["rect"]), "since": self.clock()}
            self._remember_walk(res["id"])
            self.body.perch_on(x["x"], x["y"], x["wid"])
            return res
        if intent["kind"] in WINDOW_KINDS:
            self.perch = None
            x = x["x"]
        speed = int(max(5, min(12, float(intent.get("speed", 8) or 8))))
        self._remember_walk(res["id"])
        self.body.walk_to(x, speed)
        return res

    def _remember_walk(self, intent_id):
        eva, cur, work = self.probes.eva_rect(), self.probes.cursor(), self.probes.work_rect()
        self.walking = {"id": intent_id,
                        "cursor_gap": point_gap(cur, eva) if cur is not None else None,
                        "work_gap": rect_gap(eva, work) if work is not None else None}

    def _perch_problem(self, p):
        wins = _windows(self.probes)
        w = next((x for x in wins if x.get("id") == p["wid"]), None)
        if w is None:
            return "window_gone"
        if not _same_rect(w["rect"], p["rect"], 6):
            return "window_moved"
        if is_active_window(w, self.probes, wins):
            return "window_active"
        if self.probes.focus():
            return "focus_mode"
        cur = self.probes.cursor()
        if cur is not None and point_gap(cur, self.probes.eva_rect()) < self.clear:
            return "cursor_close"
        return None

    def check_perch(self):
        """Called on every world sample. While she is on (or flying to) a window's title bar: if the window closed,
        moved, became the active one, the person started to concentrate or the cursor came close, she hops down to
        the taskbar. Returns the reason, or None. Never touches the window."""
        p = self.perch
        if not p:
            return None
        st = self.body.state()
        if st.get("held") or st.get("placed"):
            self.perch = None                                # the person took her: she stays as put
            return None
        if not st.get("walking") and not st.get("sitting") and self.clock() - p["since"] > 3.0:
            self.perch = None                                # she is not up there any more
            return None
        reason = self._perch_problem(p)
        if reason is None:
            return None
        if st.get("walking"):
            self.body.halt()
        self.body.get_down()
        self.perch = None
        self.walking = None
        return reason

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
            if self.perch:                                   # halted in mid-air on the way up: down she goes
                self.perch = None
                self.body.get_down()
            return True
        return False
