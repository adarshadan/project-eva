"""Eva -- the geometry of her ring menu (no Qt here, so it can be tested).

The menu is a ring of segments around Eva herself, like the weapon/sign
wheels in The Witcher 3 or Call of Duty. Two rules make it always usable:

  1. Centred on her whenever the whole ring fits on the screen.
  2. At a screen edge the segments are laid out only over the part of the ring
     that IS on screen (a half-ring at a side, a quarter-ring in a corner), as
     long as each segment stays wide enough to hit and read. If even that is
     too cramped, the ring slides just far enough onto the screen to fit whole.

Angles are degrees, counter-clockwise from 3 o'clock (Qt's convention), with
screen y pointing down: a point at angle a is (cx + r cos a, cy - r sin a).
"""
import math
from dataclasses import dataclass

MIN_SEGMENT_DEG = 24.0       # narrower than this is hard to hit and the label will not fit (24 deg = ~60 px)
GAP_DEG = 2.0                # visual gap between segments


@dataclass(frozen=True)
class Segment:
    index: int
    center: float            # degrees
    span: float              # degrees, including the gap

    def contains(self, angle: float) -> bool:
        d = (angle - self.center + 180.0) % 360.0 - 180.0
        return abs(d) <= self.span / 2.0


@dataclass(frozen=True)
class Layout:
    cx: float
    cy: float
    inner: float
    outer: float
    segments: tuple
    full: bool               # the whole ring is used
    shifted: bool            # the ring had to slide onto the screen

    def hit(self, x: float, y: float):
        """Index of the segment under (x, y), or None (hole, outside, gap)."""
        dx, dy = x - self.cx, y - self.cy
        r = math.hypot(dx, dy)
        if r < self.inner or r > self.outer + 8:
            return None
        a = math.degrees(math.atan2(-dy, dx)) % 360.0
        for s in self.segments:
            if s.contains(a):
                return s.index
        return None


def radii(avatar_w: int, avatar_h: int):
    """Ring just outside her body: inner edge clears the hit box's corners."""
    inner = math.hypot(avatar_w / 2.0, avatar_h / 2.0) + 6
    inner = max(inner, 70.0)
    thickness = max(54.0, min(70.0, inner * 0.5))
    return inner, inner + thickness


def _visible(cx, cy, r, a, screen, margin):
    left, top, right, bottom = screen
    x = cx + r * math.cos(math.radians(a))
    y = cy - r * math.sin(math.radians(a))
    return left + margin <= x <= right - margin and top + margin <= y <= bottom - margin


def _largest_arc(cx, cy, r, screen, margin, step=2.0):
    """(start, span) in degrees of the largest contiguous visible arc."""
    n = int(360 / step)
    vis = [_visible(cx, cy, r, i * step, screen, margin) for i in range(n)]
    if all(vis):
        return 0.0, 360.0
    if not any(vis):
        return 0.0, 0.0
    best, best_start = 0, 0
    # start scanning just after an invisible sample so the run does not wrap mid-way
    first_off = vis.index(False)
    run, run_start = 0, None
    for k in range(1, n + 1):
        i = (first_off + k) % n
        if vis[i]:
            if run == 0:
                run_start = i
            run += 1
            if run > best:
                best, best_start = run, run_start
        else:
            run = 0
    return best_start * step, best * step


def _place(cx, cy, n, start, span, full):
    if full:
        width = 360.0 / n
        first = 90.0                                  # first item at 12 o'clock, then clockwise
        segs = [Segment(i, (first - i * width) % 360.0, width) for i in range(n)]
    else:
        width = span / n
        # The visible arc runs counter-clockwise from `start` to `start + span`.
        # Items start at whichever end is higher on screen (on a tie, the left
        # one), so the order reads top-down or left-to-right at every edge.
        a1, a2 = start, start + span
        s1, s2 = math.sin(math.radians(a1)), math.sin(math.radians(a2))
        if abs(s1 - s2) < 0.3:                       # about level: start at the LEFT end
            first_is_a2 = math.cos(math.radians(a2)) <= math.cos(math.radians(a1))
        else:                                        # otherwise at the higher end
            first_is_a2 = s2 > s1
        if first_is_a2:
            segs = [Segment(i, (a2 - width * (i + 0.5)) % 360.0, width) for i in range(n)]   # clockwise from a2
        else:
            segs = [Segment(i, (a1 + width * (i + 0.5)) % 360.0, width) for i in range(n)]   # counter-clockwise from a1
    return tuple(segs)


def layout(center, avatar_size, screen, n):
    """center: (x, y) of Eva; avatar_size: (w, h); screen: (left, top, right,
    bottom) of the usable screen area; n: number of items."""
    cx, cy = center
    inner, outer = radii(*avatar_size)
    margin = (outer - inner) / 2.0 + 4               # a segment's half-thickness, plus a little
    mid = (inner + outer) / 2.0
    start, span = _largest_arc(cx, cy, mid, screen, margin)
    if span >= 360.0:
        return Layout(cx, cy, inner, outer, _place(cx, cy, n, 0, 360, True), True, False)
    if span / max(1, n) >= MIN_SEGMENT_DEG:
        return Layout(cx, cy, inner, outer, _place(cx, cy, n, start, span, False), False, False)
    # too cramped: slide the ring onto the screen so it fits whole
    left, top, right, bottom = screen
    pad = outer + 6
    ncx = min(max(cx, left + pad), right - pad)
    ncy = min(max(cy, top + pad), bottom - pad)
    return Layout(ncx, ncy, inner, outer, _place(ncx, ncy, n, 0, 360, True), True, True)
