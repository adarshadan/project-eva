"""Eva -- measures the avatar flicker on the real screen.

The 2026-10-11 screen recording showed it exactly: the character vanishes for
1-3 frames at a time, about one frame in seven, while the "Listening" label
(a separate, plainly painted window) never does -- so the 3-D stage presents
empty frames; nothing else flickers. Until now the only way to know whether a
change helped was to watch by eye.

This probe photographs the screen where she stands (mss, which captures what
the compositor actually shows, layered windows included) ~15 times a second
for a few seconds while she is idle, and counts the shots that differ sharply
from her usual image: those are the frames where she was missing. The result
goes to eva.log as one line, so render settings can be compared by number:

    Flicker probe: 14 of 90 frames (16%) showed the character missing ...

It runs once, shortly after start-up, on its own thread; it never touches the
avatar and never runs during a walk or a gesture.
"""
import threading
import time

from logger import get_logger

log = get_logger(__name__)

SAMPLES = 90
INTERVAL = 1 / 15
GRID = (16, 32)            # region is reduced to this many cells (w, h)
MISSING_DIFF = 28          # mean per-cell difference (0-255) that means "she was not there"


def missing_frames(frames, threshold=MISSING_DIFF):
    """frames: list of equal-length lists of grey values. Returns the indexes
    of frames that differ sharply from the per-cell median of all frames --
    the character is present in most frames, so the median is her image."""
    if len(frames) < 5:
        return []
    n = len(frames[0])
    median = []
    for i in range(n):
        col = sorted(f[i] for f in frames)
        median.append(col[len(col) // 2])
    out = []
    for k, f in enumerate(frames):
        diff = sum(abs(a - b) for a, b in zip(f, median)) / n
        if diff > threshold:
            out.append(k)
    return out


def _grab_grey(sct, rect):
    from PIL import Image
    left, top, w, h = rect
    shot = sct.grab({"left": left, "top": top, "width": w, "height": h})
    img = Image.frombytes("RGB", shot.size, shot.rgb).convert("L").resize(GRID)
    return list(img.tobytes())


def body_rect(x, y, w, h):
    """The middle of the hit box, where her body is (not the hair or the gaps
    between arms and torso, which show the desktop anyway)."""
    return (x + int(w * 0.30), y + int(h * 0.25), max(8, int(w * 0.40)), max(8, int(h * 0.55)))


def run(get_rect, is_busy, delay=25.0, on_result=None):
    """get_rect() -> (x, y, w, h) of the hit box; is_busy() -> True while she
    walks or gestures. Starts a daemon thread and returns it."""
    def work():
        time.sleep(delay)
        for _ in range(12):                       # wait up to a minute for a quiet moment
            if not is_busy():
                break
            time.sleep(5)
        else:
            log.info("Flicker probe skipped: she was busy the whole time.")
            return
        try:
            import mss
        except Exception as e:
            log.info(f"Flicker probe unavailable: {e}")
            return
        frames, skipped = [], 0
        try:
            rect = body_rect(*get_rect())
            with mss.mss() as sct:
                for _ in range(SAMPLES):
                    if is_busy():
                        skipped += 1
                    else:
                        frames.append(_grab_grey(sct, rect))
                    time.sleep(INTERVAL)
        except Exception:
            log.exception("Flicker probe failed")
            return
        bad = missing_frames(frames)
        pct = 100 * len(bad) / max(1, len(frames))
        import config
        mode = f"RENDER_MODE={getattr(config, 'RENDER_MODE', '?')}, QT_GRAPHICS_API={getattr(config, 'QT_GRAPHICS_API', '') or 'default'}"
        log.info(f"Flicker probe: {len(bad)} of {len(frames)} frames ({pct:.0f}%) showed the character "
                 f"missing ({mode}).")
        if on_result:
            on_result(len(bad), len(frames))

    t = threading.Thread(target=work, name="eva-flicker-probe", daemon=True)
    t.start()
    return t
