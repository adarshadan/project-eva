"""Eva -- what she does with herself when nobody needs her.

A person waiting does not stand frozen. When you leave her be, Eva now finds
somewhere to sit -- the top edge of one of your windows, or the taskbar -- and
lives a little: swings her legs, looks around, stretches, yawns late at
night, and dozes off if you are away long enough. When you come back after a
while she wakes and waves. She glances at your cursor when it comes near her.
She never sits on the window you are using (her body would be in the way of
its title bar), and she gets off a window that moves, closes or is maximised.

This module only DECIDES; the body is driven through a small facade (main.py
wires it to AvatarWidget), so the decisions are tested without a screen:

    body.sit_on(edge_y, x, hwnd) / sit_here() / stand_up() / get_down()
    body.stroll() / body.gesture(name) / body.set_activity(name) / body.wave(delay_ms=0)
    body.watch(rect) / body.stop_watching()        a video in front: sit beside it and watch
    probes.video_window() -> (hwnd, rect) or None   (optional)
    body.state() -> {"sitting", "airborne", "perched_on", "walking"}
    body.look_at(x, y, ms) / body.head() -> (x, y) / body.size() -> (w, h)
    probes.idle_seconds() / probes.busy() / probes.cursor() / probes.hour()
    probes.perch_spots() -> [(hwnd, edge_y, left, right)] / probes.foreground() -> hwnd
    probes.ground() -> taskbar top y
"""
import math
import random
import time

from logger import get_logger

log = get_logger(__name__)

START_AFTER = 90          # seconds of no input before she starts living on her own
NAP_AFTER = 600           # ... and dozes off
WELCOME_AFTER = 300       # away at least this long: a wave when you come back
BEHAVIOUR_GAP = (40, 95)  # seconds between idle behaviours
GLANCE_RADIUS = 260       # px from her head: she notices the cursor
WATCH_AFTER = 8           # a video in front and no input for this long: she watches it with you
# Bored while the person is around (2026-10-17: "move her hands or feet if she is getting bored,
# maybe a yawn, maybe she runs around like a small child, or starts to look around the pc").
# probes.boredom() is 0..3 by how long nobody has needed her (eva_app/life.py); the body's small
# fidgets come from the page itself (presence layer); these are the bigger moments.
BORED_GAP = (70, 160)     # seconds between bored moments at boredom 2 (halved at 3)
RUN_GAP = 20 * 60         # at most one run-around every 20 minutes
RUN_QUIET = 10            # ... and only when the person has not touched anything for 10 s
# A window title that means a video is playing in front (media.py, shared with the performer).
from media import VIDEO_TITLE  # noqa: E402


class Life:
    def __init__(self, body, probes, rng=None, clock=time.monotonic):
        self.body, self.probes = body, probes
        self.rng = rng or random.Random()
        self.clock = clock
        self.next_at = 0.0
        self.napping = False
        self.was_idle = 0.0
        self.last_cursor = None
        self.last_glance = 0.0
        self.behaviours_done = 0
        self.watching = None                 # hwnd of the video she is watching with you
        self.next_bored_at = 0.0
        self.last_run = -RUN_GAP

    # ---- once a second ------------------------------------------------------------------
    def tick(self):
        idle = self.probes.idle_seconds()
        now = self.clock()
        state = self.body.state()
        returned = idle < 2 and self.was_idle >= START_AFTER
        long_away = self.was_idle >= WELCOME_AFTER
        self.was_idle = idle

        if self.probes.busy():
            if self.napping:
                self._wake(wave=False)
            return "busy"

        if returned:
            if self.napping:
                self._wake(wave=True)
                return "woke"
            # Off the window you are using: she would be in the way of its title bar.
            if state.get("perched_on") and state["perched_on"] == self.probes.foreground() \
                    and not state.get("placed"):
                self.body.get_down()
            if long_away:
                self.body.wave()
                return "welcome"
            return "returned"

        # A video in front of you: she sits beside it (never over it) and
        # watches along -- and does not doze off halfway through.
        video = self._video()
        if video and idle >= WATCH_AFTER and not self.napping:
            if self.watching != video[0]:
                self.watching = video[0]
                self.body.watch(video[1])
                log.info("Idle life: watching the video with you")
                return "watch"
            if now >= self.next_at:
                self.next_at = now + self.rng.uniform(*BEHAVIOUR_GAP)
                self.body.gesture(self.rng.choice(["p_nod", "p_look_around", "p_hum"]) if self.rng.random() < 0.3
                                  else "p_nod")
            return "watching"
        if self.watching and not video:
            self.watching = None
            self.body.stop_watching()
            return "stopped_watching"

        level = self._boredom()
        if level >= 2 and idle < START_AFTER and not self.napping and not state.get("walking") \
                and now >= self.next_bored_at:
            if self.next_bored_at == 0.0:            # the first time: wait a gap, do not start at once
                self.next_bored_at = now + self.rng.uniform(*BORED_GAP) / (level - 1)
                return "quiet"
            self.next_bored_at = now + self.rng.uniform(*BORED_GAP) / (level - 1)
            return self._bored(state, level, idle, now)

        if idle >= NAP_AFTER and not self.napping:
            if state.get("placed") and state.get("airborne") and not state.get("sitting"):
                return "quiet"                      # where you put her; no dozing in mid-air
            return self._nap(state)
        if self.napping:
            return "napping"

        # eva.log 2026-10-11 14:11: asked onto a window, she went back down to
        # the taskbar seconds later. Where the person put her, she stays.
        if idle >= 8 and state.get("airborne") and not state.get("sitting") and not state.get("walking") \
                and not state.get("placed"):
            self.body.get_down()                    # nothing to hover for: back to the ground
            return "descend"

        if idle >= START_AFTER and now >= self.next_at and not state.get("walking"):
            self.next_at = now + self.rng.uniform(*BEHAVIOUR_GAP)
            return self._behaviour(state)
        return "quiet"

    def _boredom(self):
        probe = getattr(self.probes, "boredom", None)
        try:
            return int(probe()) if probe else 0
        except Exception:
            return 0

    def _bored(self, state, level, idle, now):
        """One bigger moment of boredom while the person is around."""
        options = [("yawn", 2.0), ("stretch", 1.2), ("look_around", 1.5), ("scratch_head", 0.8),
                   ("check_watch", 0.6), ("hum", 0.8), ("impatient", 0.7 if level >= 3 else 0.2)]
        patrol_due = getattr(self.probes, "patrol_due", None)
        try:
            if patrol_due and patrol_due():
                options.append(("patrol", 3.0))
        except Exception:
            pass
        if (level >= 3 and idle >= RUN_QUIET and now - self.last_run >= RUN_GAP and not state.get("sitting")
                and not state.get("placed") and not state.get("airborne")):
            options.append(("run_around", 1.6))
        name = self._pick(options)
        if name == "patrol":
            self.body.patrol()
        elif name == "run_around":
            if self.body.run_around():
                self.last_run = now
        elif name == "stretch":
            self.body.gesture("p_stretch_up" if not state.get("sitting") else "p_look_around")
        else:
            self.body.gesture({"yawn": "p_yawn", "look_around": "p_look_around", "scratch_head": "p_scratch_head",
                               "check_watch": "p_check_watch", "hum": "p_hum", "impatient": "p_impatient"}[name])
        log.info(f"Idle life (bored {level}): {name}")
        return "bored:" + name

    def _video(self):
        probe = getattr(self.probes, "video_window", None)
        try:
            return probe() if probe else None
        except Exception:
            return None

    # ---- the cursor: a glance when it comes near -------------------------------------
    def cursor_tick(self):
        if self.napping or self.probes.busy():
            return False
        cur = self.probes.cursor()
        if cur is None:
            return False
        moved = self.last_cursor is not None and math.hypot(cur[0] - self.last_cursor[0], cur[1] - self.last_cursor[1]) > 6
        self.last_cursor = cur
        hx, hy = self.body.head()
        near = math.hypot(cur[0] - hx, cur[1] - hy) <= GLANCE_RADIUS
        if near and moved and self.clock() - self.last_glance > 0.5:
            self.body.look_at(cur[0], cur[1], 900)
            self.last_glance = self.clock()
            return True
        return False

    # ---- choices ------------------------------------------------------------------------
    def _late(self):
        h = self.probes.hour()
        return h >= 23 or h < 5

    def _behaviour(self, state):
        sitting = state.get("sitting")
        if state.get("placed"):
            # Put somewhere by the person: she lives a little, but stays there.
            name = self._pick([("look_around", 1.6), ("stretch", 0.6), ("yawn", 2.2 if self._late() else 0.25)])
            self.body.gesture({"look_around": "p_look_around", "stretch": "p_look_around" if sitting
                               else "p_stretch_up", "yawn": "p_yawn"}[name])
            log.info(f"Idle life (staying where you put her): {name}")
            return name
        options = []
        spot = self._perch_spot() if not sitting else None
        if spot:
            options.append(("sit_window", 3.0))
        if not sitting:
            options += [("sit_taskbar", 2.0), ("stroll", 1.5)]
        else:
            options += [("stand_and_stroll", 0.6 + 0.4 * min(3, self.behaviours_done))]
        options += [("look_around", 1.6), ("stretch", 0.8), ("yawn", 2.2 if self._late() else 0.25),
                    ("hum", 0.9), ("check_watch", 0.4), ("sneeze", 0.12)]
        if not sitting:
            options.append(("play", 0.6))
        name = self._pick(options)
        self.behaviours_done += 1
        if name == "sit_window":
            hwnd, edge, left, right = spot
            w, _h = self.body.size()
            x = self.rng.uniform(left + w, right - w) if right - left > 3 * w else (left + right) / 2
            self.body.sit_on(edge, int(x), hwnd)
            self.behaviours_done = 0
        elif name == "sit_taskbar":
            if state.get("airborne"):
                self.body.get_down()
            else:
                self.body.sit_here()
            self.behaviours_done = 0
        elif name in ("stroll", "stand_and_stroll"):
            self.body.stroll()
        elif name == "look_around":
            self.body.gesture("p_look_around")
        elif name == "stretch":
            self.body.gesture("p_stretch_up" if not sitting else "p_look_around")
        elif name == "yawn":
            self.body.gesture("p_yawn")
        elif name in ("hum", "check_watch", "sneeze"):
            self.body.gesture("p_" + name)
        elif name == "play":
            self.body.gesture(self.rng.choice(["twirl", "peace", "model_pose", "p_wave", "p_tada", "p_salute"]))
        log.info(f"Idle life: {name}")
        return name

    def _perch_spot(self):
        """A window edge to sit on: not the one in use, wide enough, with room above."""
        fg = self.probes.foreground()
        w, h = self.body.size()
        ground = self.probes.ground()
        spots = [s for s in self.probes.perch_spots()
                 if s[0] != fg and s[3] - s[2] >= 3 * w and s[1] >= h * 0.9 and s[1] < ground - h // 2]
        return self.rng.choice(spots) if spots else None

    def _pick(self, options):
        total = sum(w for _, w in options)
        r = self.rng.uniform(0, total)
        for name, w in options:
            r -= w
            if r <= 0:
                return name
        return options[-1][0]

    def _nap(self, state):
        if state.get("airborne") and not state.get("sitting"):
            self.body.get_down()
            return "descend_to_nap"
        if not state.get("sitting"):
            self.body.sit_here()
        self.body.set_activity("nap")
        self.napping = True
        log.info("Idle life: dozing off")
        return "nap"

    def _wake(self, wave):
        self.napping = False
        self.body.set_activity("idle")
        self.body.gesture("p_stretch_up")
        if wave:
            self.body.wave(delay_ms=2800)          # after the stretch
        log.info("Idle life: woke up")
