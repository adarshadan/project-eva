"""Eva -- fast eyes and hands for continuous activity.

Everything that has to keep up with the screen in real time lives here, so
the loops (loops.py), games (games.py) and reflexes (reflex.py) share one
implementation:

  Grabber(region)   frames of a screen region as numpy BGR arrays. dxcam
                    (Desktop Duplication, 100+ fps) on Windows, mss elsewhere
                    or as the fallback. ~2-6 ms a frame for a game-sized region.
  changed(a, b)     how different two frames are (0..1), for "has the page
                    moved", "did the opponent play".
  read_text(img)    local OCR: Windows' own engine (winocr) when present, else
                    Tesseract if installed, else "".
  window_client_rect(hwnd)   the drawable area of a window, in screen pixels.
  Hands             key taps / holds / clicks / wheel with no built-in delays
                    (pyautogui adds 100 ms per call by default), scan codes
                    via pydirectinput where available (DirectX games ignore
                    virtual-key events), and the cursor put back after a
                    wheel so the person's pointer does not wander.

Qt-free; nothing here touches Eva's widgets.
"""
import sys
import threading
import time

from logger import get_logger

log = get_logger(__name__)

WINDOWS = sys.platform == "win32"


def _np():
    import numpy as np
    return np


# ---- seeing -------------------------------------------------------------------------------
class Grabber:
    """Frames of one screen region. region = (left, top, right, bottom)."""

    def __init__(self, region, prefer_dxcam=True):
        self.region = clip_to_screen(tuple(int(v) for v in region))
        self._cam = None
        self._sct = None
        self.backend = "mss"
        if prefer_dxcam and WINDOWS:
            try:
                import dxcam
                self._cam = dxcam.create(output_color="BGR")
                self.backend = "dxcam"
            except Exception as e:
                log.info(f"dxcam unavailable ({e}) -- using mss")
                self._cam = None
        self._last = None

    def grab(self):
        np = _np()
        left, top, right, bottom = self.region
        if self._cam is not None:
            try:
                frame = self._cam.grab(region=(left, top, right, bottom))
                if frame is not None:
                    self._last = frame
                    return frame
                if self._last is not None:          # no new frame since the last one: nothing moved
                    return self._last
            except Exception as e:
                log.info(f"dxcam grab failed ({e}) -- switching to mss")
                self._cam = None
                self.backend = "mss"
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        shot = self._sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        frame = np.asarray(shot)[:, :, :3]          # BGRA -> BGR
        self._last = frame
        return frame

    def close(self):
        try:
            if self._cam is not None:
                self._cam.release()
        except Exception:
            pass
        try:
            if self._sct is not None:
                self._sct.close()
        except Exception:
            pass


def screen_bounds():
    """(left, top, right, bottom) of the whole desktop (all monitors)."""
    try:
        import mss
        with mss.mss() as s:
            m = s.monitors[0]
            return m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]
    except Exception:
        return None


def clip_to_screen(region):
    """A region partly off the desktop (a window hanging over an edge) would
    make the capture fail outright; keep the part that is on screen."""
    b = screen_bounds()
    if not b:
        return region
    l, t, r, btm = region
    l, t = max(l, b[0]), max(t, b[1])
    r, btm = min(r, b[2]), min(btm, b[3])
    if r - l < 8 or btm - t < 8:
        raise ValueError(f"that area is not on the screen: {region}")
    return l, t, r, btm


def grab_once(region):
    g = Grabber(region, prefer_dxcam=False)
    try:
        return g.grab()
    finally:
        g.close()


def changed(a, b, step=4) -> float:
    """0 = identical, 1 = completely different (mean absolute grey difference,
    sampled every `step` pixels: fast enough to run every frame)."""
    np = _np()
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    x = a[::step, ::step].astype(np.int16).mean(axis=2)
    y = b[::step, ::step].astype(np.int16).mean(axis=2)
    return float(np.abs(x - y).mean() / 255.0)


def playfield(img, max_trim=0.2):
    """(x0, y0, x1, y1) of the game inside a captured area: edge rows and
    columns that are a flat colour other than the game's own background
    (a page margin, a window border, a letterbox) are trimmed. The game's
    background is the most common colour; a row of plain sky is kept."""
    np = _np()
    h, w = img.shape[:2]
    small = (img[::4, ::4] // 24).reshape(-1, 3)
    vals, counts = np.unique(small, axis=0, return_counts=True)
    bg = vals[counts.argmax()].astype(np.int16) * 24 + 12

    def border(line):
        line = line.astype(np.int16)
        near_bg = (np.abs(line - bg).max(axis=1) < 40).mean()
        return near_bg < 0.3 and float(line.std(axis=0).max()) < 14
    y0, y1, x0, x1 = 0, h, 0, w
    while y0 < h * max_trim and border(img[y0]):
        y0 += 1
    while y1 > h * (1 - max_trim / 4) and border(img[y1 - 1]):
        y1 -= 1
    while x0 < w * max_trim and border(img[y0:y1, x0]):
        x0 += 1
    while x1 > w * (1 - max_trim) and border(img[y0:y1, x1 - 1]):
        x1 -= 1
    return x0, y0, x1, y1


def read_text(img) -> str:
    """Words in an image, read locally. '' when no OCR engine is available."""
    try:
        import winocr
        from PIL import Image
        pil = Image.fromarray(img[:, :, ::-1])
        res = winocr.recognize_pil_sync(pil, "en")
        return (res.get("text") if isinstance(res, dict) else getattr(res, "text", "")) or ""
    except Exception:
        pass
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.fromarray(img[:, :, ::-1]))
    except Exception:
        return ""


def window_client_rect(hwnd):
    """(left, top, right, bottom) of a window's drawable area on screen, or None."""
    if not hwnd:
        return None
    if WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            if not ctypes.windll.user32.GetClientRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(r)):
                return None
            pt = wintypes.POINT(0, 0)
            ctypes.windll.user32.ClientToScreen(ctypes.c_void_p(int(hwnd)), ctypes.byref(pt))
            return pt.x, pt.y, pt.x + r.right, pt.y + r.bottom
        except Exception:
            return None
    try:
        import desktop_control as dc
        r = dc.get_window_rect(hwnd)
        return (r["left"], r["top"], r["right"], r["bottom"]) if r and r.get("ok", True) else None
    except Exception:
        return None


def foreground_window():
    try:
        import desktop_control as dc
        return dc.get_foreground_app().get("handle")
    except Exception:
        return None


# ---- acting -----------------------------------------------------------------------------
class Hands:
    """Input without pyautogui's built-in pauses, safe to call at 60 Hz."""

    def __init__(self):
        import pyautogui
        self._pg = pyautogui
        self._pg.PAUSE = 0
        self._pg.FAILSAFE = False                   # a game may legitimately put the cursor in a corner
        self._di = None
        if WINDOWS:
            try:
                import pydirectinput
                pydirectinput.PAUSE = 0
                self._di = pydirectinput
            except Exception:
                self._di = None
        self._lock = threading.Lock()

    def _keys(self, keys):
        return [k.strip().lower() for k in str(keys).replace(" ", "").split("+") if k.strip()]

    def tap(self, keys, hold=0.03):
        ks = self._keys(keys)
        with self._lock:
            mod = self._di if (self._di and len(ks) == 1) else self._pg
            for k in ks:
                mod.keyDown(k)
            time.sleep(hold)
            for k in reversed(ks):
                mod.keyUp(k)

    def hold(self, keys, seconds):
        ks = self._keys(keys)
        with self._lock:
            mod = self._di if (self._di and len(ks) == 1) else self._pg
            for k in ks:
                mod.keyDown(k)
        time.sleep(max(0.0, float(seconds)))
        with self._lock:
            for k in reversed(ks):
                mod.keyUp(k)

    def click(self, x, y, button="left", restore=False):
        with self._lock:
            back = self._pg.position() if restore else None
            self._pg.click(int(x), int(y), button=button)
            if back is not None:
                self._pg.moveTo(back[0], back[1])

    def wheel(self, clicks, x=None, y=None, horizontal=False):
        """Scroll at (x, y) -- the wheel goes to the window under the pointer --
        then put the pointer back where the person left it."""
        with self._lock:
            back = self._pg.position()
            if x is not None and y is not None:
                self._pg.moveTo(int(x), int(y))
            if horizontal and hasattr(self._pg, "hscroll"):
                self._pg.hscroll(int(clicks))
            else:
                self._pg.scroll(int(clicks))
            if x is not None and y is not None:
                self._pg.moveTo(back[0], back[1])

    def type(self, text):
        with self._lock:
            self._pg.write(str(text), interval=0.01)


_HANDS = {"h": None}


def hands():
    if _HANDS["h"] is None:
        _HANDS["h"] = Hands()
    return _HANDS["h"]
