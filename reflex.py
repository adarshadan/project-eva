"""Eva -- real-time play: decisions many times a second, no model in the loop.

The language model is far too slow to play Flappy Bird (2-5 s a step); a
controller here watches the game area at 30-60 frames a second and presses
the key itself. The model only starts it and hears how it went.

  FlappyActivity   finds the bird and the next gap in the pipes from the
                   picture (no assumptions about colours: pipes are whatever
                   hangs from the top edge, the bird is the colourful blob
                   that stays in its column), predicts where the bird will be
                   and flaps to keep it in the gap.
  RunnerActivity   endless runners (Chrome's dinosaur and clones): watches the
                   strip ahead of the runner; jumps over what comes along the
                   ground, ducks under what flies; looks further ahead as the
                   game speeds up.
  ReactActivity    the general reflex: when something in an area changes, a
                   colour appears, or words appear -> press a key / click.
                   Covers reaction tests, "press space when it turns green",
                   rhythm prompts, "click whenever the button appears".

Every controller ends when stopped, at its time limit, or when the game is
over (the picture freezes or the player is gone), and reports a score.
"""
import time

import activities
import perception
from logger import get_logger

log = get_logger(__name__)


def _np():
    import numpy as np
    return np


def _cv():
    import cv2
    return cv2


class _FrameLoop(activities.Activity):
    fps = 45

    def __init__(self, title, region, window=None, max_seconds=600, grabber=None, hands=None, key="space"):
        super().__init__(title, max_seconds, window=window, region=region)
        self._grabber = grabber
        self._hands = hands
        self.key = key
        self.score = 0

    def hands(self):
        return self._hands or perception.hands()

    def press(self, keys=None, hold=0.03):
        k = keys or self.key
        if k == "click":
            l, t, r, b = self.region
            self.hands().click((l + r) // 2, (t + b) // 2)
        else:
            self.hands().tap(k, hold)

    def _finish(self, ctx, how_far):
        """How the game ended, in words: crashed, time limit, or stopped."""
        if self.outcome == "stopped" and self.result:       # e.g. the person switched windows
            return
        if ctx.stop_event.is_set():
            self.result = f"stopped -- {how_far}"
        elif self.outcome == "timeout":
            self.result = f"time's up and still going -- {how_far}"
        else:
            self.outcome = self.outcome or "done"
            self.result = f"game over -- {how_far}"

    def frames(self, ctx):
        """Frames of the playing field only (borders and margins trimmed,
        re-measured every 2 s), at self.fps."""
        g = self._grabber or perception.Grabber(self.region)
        period = 1.0 / self.fps
        box, measured = None, 0.0
        try:
            while ctx.alive():
                t0 = time.monotonic()
                frame = g.grab()
                if box is None or t0 - measured > 2.0:
                    box, measured = perception.playfield(frame), t0
                x0, y0, x1, y1 = box
                yield t0, frame[y0:y1, x0:x1]
                left = period - (time.monotonic() - t0)
                if left > 0:
                    time.sleep(left)
        finally:
            if self._grabber is None:
                g.close()


# ---------------------------------------------------------------------------------------
# Flappy Bird and clones
# ---------------------------------------------------------------------------------------
def find_pipes(img):
    """Pipes hang from the top edge: [(x0, x1, gap_top, gap_bottom)], left to right.
    The sky is the most common colour along the top edge; a run of columns
    whose top pixel is not sky is a pipe; down its centre column, the gap
    starts where the pipe's colour ends and ends where it comes back.
    Vectorised: ~1 ms for a 200 x 300 frame."""
    np = _np()
    h, w = img.shape[:2]
    top = img[min(2, h - 1)].astype(np.int16)
    q = top // 24
    keys, counts = np.unique(q, axis=0, return_counts=True)
    sky_key = keys[counts.argmax()]
    sky = top[(q == sky_key).all(axis=1)].mean(axis=0)
    is_pipe = (np.abs(top - sky).max(axis=1) > 40).astype(np.int8)
    edges = np.diff(np.concatenate([[0], is_pipe, [0]]))
    starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0] - 1
    pipes = []
    min_w = max(3, w // 60)
    for x0, x1 in zip(starts, ends):
        if x1 - x0 < min_w:
            continue
        gaps = []
        for cx in (x0 + (x1 - x0) // 4, (x0 + x1) // 2, x1 - (x1 - x0) // 4):
            col = img[:, cx].astype(np.int16)
            pipe_col = col[2:max(3, h // 20)].mean(axis=0)
            like_pipe = np.abs(col - pipe_col).max(axis=1) <= 45
            # the gap is the LONGEST stretch that is not pipe (the score's
            # digits painted on a pipe are a short one: eva.log lab 2026-10-11)
            e = np.diff(np.concatenate([[1], like_pipe.astype(np.int8), [1]]))
            run_s, run_e = np.where(e == -1)[0], np.where(e == 1)[0]
            if not len(run_s):
                continue
            k = int(np.argmax(run_e - run_s))
            gaps.append((int(run_s[k]), int(run_e[k])))
        if not gaps:
            continue
        gap_top = sorted(gp[0] for gp in gaps)[len(gaps) // 2]
        gap_bottom = sorted(gp[1] for gp in gaps)[len(gaps) // 2]
        if h * 0.08 < gap_bottom - gap_top < h * 0.7 and gap_top > 2:
            pipes.append((int(x0), int(x1), gap_top, gap_bottom))
    return pipes


def find_bird(img, pipes, prev=None):
    """(cx, cy, w, h) of the player: a compact, colourful blob that is not a
    pipe, in the left 65 % of the area; nearest the last position if known."""
    np, cv2 = _np(), _cv()
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # colourful, and NOT the background: a saturated sky (classic Flappy's is)
    # would otherwise swallow the bird into one huge blob
    small = (img[::4, ::4] // 24).reshape(-1, 3)
    vals, counts = np.unique(small, axis=0, return_counts=True)
    bg = vals[counts.argmax()].astype(np.int16) * 24 + 12
    far_from_bg = np.abs(img.astype(np.int16) - bg).max(axis=2) > 50
    mask = ((hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 110) & far_from_bg).astype(np.uint8)
    mask[:, int(w * 0.65):] = 0
    mask[int(h * 0.92):, :] = 0
    # not the pipes' colour (blanking whole pipe columns lost the bird
    # exactly when it matters: flying through a gap)
    for x0, x1, _, _ in pipes:
        pc = img[min(2, h - 1), (x0 + x1) // 2].astype(np.int16)
        mask &= (np.abs(img.astype(np.int16) - pc).max(axis=2) > 50).astype(np.uint8)
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask)
    best, best_score = None, None
    area_lo, area_hi = w * h * 0.0004, w * h * 0.03
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        if not (area_lo <= area <= area_hi) or bw > w * 0.2 or bh > h * 0.2:
            continue
        cx, cy = cents[i]
        score = -area if prev is None else abs(cx - prev[0]) * 3 + abs(cy - prev[1])
        if best_score is None or score < best_score:
            best, best_score = (float(cx), float(cy), int(bw), int(bh)), score
    return best


def flappy_plan(y, vy, bx, bh, pipes, speed, g, vf, lag, h, since_flap, min_gap=0.09,
                horizon=0.8, dt=1 / 60, period=1 / 30):
    """Flap now or not? Both futures are simulated for `horizon` seconds with
    the measured physics (gravity g, flap speed vf, reaction lag) and the
    pipes moving left at `speed`; in each future she keeps flapping by a
    simple rule. The choice that stays inside the gaps longer (then with
    more room to spare) wins. Returns (flap, survival_if_flap, survival_if_not)."""
    floor = h * 0.95
    base = bh / 2 + 2

    def corridor(t):
        # the margin grows with how far ahead the prediction is: the measured
        # physics is a few pixels off after half a second (lab, 2026-10-11)
        half = base + 10.0 * t / horizon * (h / 300.0)
        top, bot = 0.0, floor - half
        for x0, x1, gt, gb in pipes:
            px0, px1 = x0 - speed * t, x1 - speed * t
            if px0 - bh / 2 <= bx <= px1 + bh / 2:
                top, bot = max(top, gt + half), min(bot, gb - half)
        return top, bot

    def next_gap_line(t):
        for x0, x1, gt, gb in sorted(pipes):
            if x1 - speed * t >= bx - bh / 2:
                return gt + (gb - gt) * 0.62
        return h * 0.5

    def rollout(flap_now):
        yy, vv, t, pending, last = y, vy, 0.0, (lag if flap_now else None), (0.0 if flap_now else -since_flap)
        room, next_decision = 1e9, period
        while t < horizon:
            if pending is not None:
                pending -= dt
                if pending <= 0:
                    vv, pending = vf, None
            elif t >= next_decision:
                next_decision += period
                if t - last >= min_gap and yy + vv * lag > next_gap_line(t):
                    pending, last = lag, t        # the rule: flap when she will be below the gap line
            vv += g * dt
            yy += vv * dt
            t += dt
            top, bot = corridor(t)
            room = min(room, yy - top, bot - yy)
            if yy < top or yy > bot:
                return t, room
        return horizon, room

    if since_flap < min_gap:
        return False, 0.0, 0.0
    sf, rf = rollout(True)
    sn, rn = rollout(False)
    # longer survival wins; when both last the whole horizon, flap only for clearly more room
    flap = sf > sn + 1e-6 or (abs(sf - sn) < 1e-6 and rf > rn + 2.0)
    return flap, sf, sn


class FlappyActivity(_FrameLoop):
    """Keeps the bird in the gap with a model of the game's own physics,
    measured while playing: gravity (how fast the fall speeds up) and the
    flap (the upward speed one flap gives). Knowing both, it flaps only when
    the peak of that flap stays below the top of the gap, and while it is
    passing through one gap it already lines up for the next."""
    kind = "flappy"
    fps = 60
    SCALE = 0.5                                  # detection at half size: 4x faster

    def __init__(self, region, window=None, max_seconds=600, key="space", latency=0.05, **kw):
        super().__init__("Playing Flappy Bird", region, window, max_seconds, key=key, **kw)
        self.latency = latency                   # capture + decision + key, seconds

    def run(self, ctx):
        cv2 = _cv()
        import collections
        hist = collections.deque(maxlen=8)        # (t, y) of the bird, only when it moved
        g = vf = None                              # gravity (px/s^2), flap speed (px/s, negative)
        g_samples, rises = [], []                  # rises: (falling speed when she flapped, height gained)
        flap_y, flap_v, apex = None, 0.0, None     # the current flap: where it began, speed then, highest point
        last_flap, last_seen = time.monotonic(), time.monotonic()
        still, prev_small, prev, last_t = 0.0, None, None, None
        passed = []                                # left edge of the nearest pipe ahead, last frame
        memory = {"pipes": [], "t": 0.0, "speed": 0.0}
        prev_decision, period = None, 1 / 30       # how often she really gets to decide (measured)
        self.press()                               # most clones start on the first flap
        for now, frame in self.frames(ctx):
            small = cv2.resize(frame, None, fx=self.SCALE, fy=self.SCALE, interpolation=cv2.INTER_NEAREST)
            h, w = small.shape[:2]
            if g is None:
                g, vf = 2.5 * h, -0.72 * h         # typical, until measured
            moved = prev_small is None or perception.changed(prev_small, small, step=2) >= 0.002
            still = 0.0 if moved else still + ((now - last_t) if last_t else 0)
            prev_small, last_t = small, now
            if still > 1.2:
                break                              # the game froze: game over
            if not moved:
                continue                           # the same picture again: nothing new to decide on
            pipes = find_pipes(small)
            bird = find_bird(small, pipes, prev)
            if bird is None:
                if now - last_seen > 1.5:
                    break                          # the bird is gone: game over
                continue
            last_seen = now
            prev = bird
            bx, by, bw, bh = bird
            hist.append((now, by))
            # speed from two samples back, corrected for gravity (robust to a repeated frame)
            vy = 0.0
            if len(hist) >= 3:
                (t2, y2), (t0, y0) = hist[-1], hist[-3]
                d = max(1e-3, t2 - t0)
                vy = (y2 - y0) / d + 0.5 * g * d
                if now - last_flap > 0.2 and len(hist) >= 3:
                    (ta, ya), (tb, yb), (tc, yc) = hist[-3], hist[-2], hist[-1]
                    d1, d2 = max(1e-3, tb - ta), max(1e-3, tc - tb)
                    acc = 2 * ((yc - yb) / d2 - (yb - ya) / d1) / (d1 + d2)
                    if 0.3 * h < acc < 8 * h:
                        g_samples.append(acc)
                        if len(g_samples) >= 10:
                            g = sorted(g_samples[-60:])[len(g_samples[-60:]) // 2]
            # the flap's strength from how high it actually lifted the bird
            if flap_y is not None:
                apex = by if apex is None else min(apex, by)
                if by > apex + 1.5 and now - last_flap < 0.6:
                    rise = flap_y - apex
                    if rise > 2:
                        rises.append((flap_v, rise))
                    flap_y = apex = None
            # Pipes are tracked: a frame where one is not seen (something drawn
            # over it, a flash) uses where it must be by now, at the measured speed.
            if pipes and memory["pipes"]:
                old0 = min(memory["pipes"], key=lambda p: abs(p[0] - pipes[0][0]))
                dtp = now - memory["t"]
                if 0 < dtp < 0.2 and 0 < old0[0] - pipes[0][0] < w * 0.2:
                    memory["speed"] = 0.7 * memory["speed"] + 0.3 * (old0[0] - pipes[0][0]) / dtp
            if pipes:
                memory.update(pipes=pipes, t=now)
            elif memory["pipes"] and now - memory["t"] < 0.4:
                shift = int(memory["speed"] * (now - memory["t"]))
                pipes = [(p[0] - shift, p[1] - shift, p[2], p[3]) for p in memory["pipes"] if p[1] - shift > 0]
            # the constraint now (the pipe she is in or about to enter) and the next one
            ahead = [p for p in pipes if p[1] >= bx - bw / 2]
            margin = bh * 0.5 + 3
            if ahead:
                p0 = ahead[0]
                safe_top, safe_bot = p0[2] + margin, p0[3] - margin
                target = p0[2] + (p0[3] - p0[2]) * 0.6
                if p0[0] <= bx + bw / 2 and len(ahead) > 1:   # passing through: line up for the next gap
                    p1 = ahead[1]
                    target = min(max(p1[2] + (p1[3] - p1[2]) * 0.6, safe_top + 2), safe_bot - 2)
                if passed and p0[0] > passed[-1] + w * 0.15:  # the nearest pipe jumped to the next one
                    self.score += 1
                    ctx.progress(f"{self.score} pipes")
                passed = [p0[0]]
            else:
                safe_top, safe_bot, target = h * 0.15, h * 0.85, h * 0.5
            # The reaction lag (seeing -> the key taking effect), learned from her
            # own flaps: the height a flap gains shrinks the faster she was falling,
            # by lag x speed -- so fit rise = a - lag * speed.
            lag = self.latency
            recent = rises[-12:]
            if len(recent) >= 4:
                vs = [v for v, _ in recent]
                rs = [r for _, r in recent]
                mv, mr = sum(vs) / len(vs), sum(rs) / len(rs)
                var = sum((v - mv) ** 2 for v in vs)
                if var > (0.15 * h) ** 2 * len(vs):
                    slope = sum((v - mv) * (r - mr) for v, r in zip(vs, rs)) / var
                    lag = max(0.035, min(0.15, -slope))   # capture + key: never less than ~35 ms
            # the flap's own speed, from the learned rise at zero falling speed
            a = (sum(r for _, r in recent) / len(recent) + lag * (sum(v for v, _ in recent) / len(recent))) \
                if len(recent) >= 4 else (vf * vf) / (2 * g)
            vf_eff = -((2 * g * max(1.0, a + 0.5 * g * lag * lag)) ** 0.5)
            speed = memory["speed"] or w * 0.4
            if prev_decision:
                period = 0.8 * period + 0.2 * min(0.1, now - prev_decision)
            prev_decision = now
            flap, s_f, s_n = flappy_plan(by, vy, bx, bh, pipes, speed, g, vf_eff, lag, h, now - last_flap,
                                         period=max(1 / 60, period))
            if flap:
                self.press()
                last_flap, flap_y, flap_v, apex = now, by, vy, None
            if getattr(self, "trace", None) is not None:
                self.trace.append((round(now, 3), round(by, 1), round(vy, 1), round(target, 1), round(g),
                                   round(vf_eff), flap, [tuple(int(v) for v in p) for p in pipes],
                                   round(s_f, 2), round(s_n, 2), round(lag, 3), round(speed)))
        self._finish(ctx, f"{self.score} pipes")


# ---------------------------------------------------------------------------------------
# Endless runners (Chrome dino and clones)
# ---------------------------------------------------------------------------------------
def runner_layout(img):
    """(ground_y, runner box (x0, y0, x1, y1), background colour) or None."""
    np, cv2 = _np(), _cv()
    h, w = img.shape[:2]
    small = (img[::3, ::3] // 32).reshape(-1, 3)
    vals, counts = np.unique(small, axis=0, return_counts=True)
    bg = vals[counts.argmax()].astype(np.int16) * 32 + 16
    fg = (np.abs(img.astype(np.int16) - bg).max(axis=2) > 60).astype(np.uint8)
    rows = fg.sum(axis=1)
    ground = None
    for y in range(h - 1, int(h * 0.4), -1):           # the ground: the lowest long horizontal line
        if rows[y] > w * 0.5:
            ground = y
            break
    if ground is None:
        return None
    # the runner: the biggest blob standing on the ground in the left quarter
    region = fg[:ground - 1, :int(w * 0.3)].copy()
    n, _, stats, _ = cv2.connectedComponentsWithStats(region)
    best = None
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 20 or bh < 6:
            continue
        if best is None or area > best[4]:
            best = (x, y, bw, bh, area)
    if not best:
        return None
    x, y, bw, bh, _ = best
    return ground, (x, y, x + bw, y + bh), bg, fg


class RunnerActivity(_FrameLoop):
    kind = "runner"
    fps = 60

    def __init__(self, region, window=None, max_seconds=600, jump="space", duck="down", **kw):
        super().__init__("Playing the runner game", region, window, max_seconds, key=jump, **kw)
        self.duck = duck

    def run(self, ctx):
        self.press()                                   # start
        layout, last_jump, started = None, 0.0, time.monotonic()
        prev_frame, still, last_t = None, 0.0, None
        prev_obstacle_x, speed = None, None
        for now, frame in self.frames(ctx):
            if prev_frame is not None and perception.changed(prev_frame, frame) < 0.0015:
                still += (now - last_t) if last_t else 0
            else:
                still = 0.0
            prev_frame, last_t = frame, now
            if still > 1.0 and now - started > 2:
                break                                  # it froze: she crashed
            if layout is None or int(now * 2) % 6 == 0:
                lay = runner_layout(frame)
                if lay:
                    layout = lay
            if layout is None:
                continue
            np = _np()
            ground, (rx0, ry0, rx1, ry1), bg, _ = layout
            rh = max(10, ry1 - ry0)
            fg = (np.abs(frame.astype(np.int16) - bg).max(axis=2) > 60)
            look = int(max(rx1 - rx0, 20) * 1.6 + (speed or 0) * 0.20)
            x_from, x_to = rx1 + 3, min(frame.shape[1] - 1, rx1 + 3 + look)
            low = fg[max(0, ground - int(rh * 0.95)):ground - 2, x_from:x_to]
            high = fg[max(0, ground - int(rh * 1.9)):max(1, ground - int(rh * 1.05)), x_from:x_to]
            obstacle_x = None
            cols = np.where(fg[max(0, ground - int(rh * 1.9)):ground - 2, x_from:frame.shape[1]].any(axis=0))[0]
            if len(cols):
                obstacle_x = x_from + int(cols[0])
                if prev_obstacle_x is not None and obstacle_x < prev_obstacle_x:
                    v = (prev_obstacle_x - obstacle_x) / max(1e-3, now - (getattr(self, "_t", now - 0.02)))
                    speed = v if speed is None else 0.8 * speed + 0.2 * v
            prev_obstacle_x, self._t = obstacle_x, now
            if low.any() and now - last_jump > 0.25:
                self.press()
                last_jump = now
            elif high.any() and not low.any() and now - last_jump > 0.25:
                self.hands().hold(self.duck, 0.35)
            self.score = int(now - started)
            if self.score and self.score % 5 == 0:
                ctx.progress(f"{self.score} s")
        self._finish(ctx, f"I lasted {int(time.monotonic() - started)} seconds")


# ---------------------------------------------------------------------------------------
# The general reflex
# ---------------------------------------------------------------------------------------
class ReactActivity(_FrameLoop):
    """When `trigger` happens in the area, do `action`. trigger: change (the
    picture changes by more than `threshold`), colour (`colour` [r,g,b] covers
    more than `threshold` of the area), text (words appear, read locally).
    action: a key ("space"), "click" (the middle of the area) or {"x","y"}."""
    kind = "react"
    fps = 60

    def __init__(self, region, trigger="change", action="space", colour=None, text=None, threshold=None,
                 cooldown=0.3, times=0, window=None, max_seconds=600, **kw):
        super().__init__("Watching and reacting", region, window, max_seconds, key=action, **kw)
        self.trigger = trigger
        self.colour = colour
        self.text = (text or "").lower()
        self.threshold = threshold if threshold is not None else {"change": 0.03, "colour": 0.3}.get(trigger, 0)
        self.cooldown = cooldown
        self.times = int(times or 0)

    def fired(self, frame, baseline):
        np = _np()
        if self.trigger == "change":
            return perception.changed(baseline, frame) > self.threshold
        if self.trigger == "colour" and self.colour:
            b, g, r = int(self.colour[2]), int(self.colour[1]), int(self.colour[0])
            hit = (np.abs(frame.astype(np.int16) - np.array([b, g, r], np.int16)).max(axis=2) < 40)
            return hit.mean() > self.threshold
        if self.trigger == "text" and self.text:
            return self.text in perception.read_text(frame).lower()
        return False

    def run(self, ctx):
        baseline, last, n, last_ocr = None, 0.0, 0, 0.0
        for now, frame in self.frames(ctx):
            if baseline is None:
                baseline = frame
                continue
            if self.trigger == "text" and now - last_ocr < 0.3:
                continue
            last_ocr = now
            if now - last >= self.cooldown and self.fired(frame, baseline):
                act = self.key
                if isinstance(act, dict):
                    self.hands().click(act["x"], act["y"])
                else:
                    self.press(act)
                n, last = n + 1, now
                self.score = n
                ctx.progress(f"reacted {n} times")
                if self.times and n >= self.times:
                    self.outcome, self.result = "done", f"reacted {n} times"
                    return
                time.sleep(self.cooldown)
                baseline = None                        # the new picture is the new normal
        self.result = self.result or f"reacted {n} times"
