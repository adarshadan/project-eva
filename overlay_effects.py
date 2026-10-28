"""
Eva — interaction overlay. A small, always-on-top, click-through window
drawn only for the ~0.6s life of a visual ripple at the exact screen point
Eva is about to click/type, so the 3D character's gesture visibly connects
to the real action performed on the window underneath.

Design history (kept so the same mistake isn't remade): this used to be a
FULLSCREEN window. Two variants both caused problems -- the original
repainted the whole surface at 60Hz from app start (constant compositor
churn, flickering, likely contributor to the WebGL context losses), and an
"always visible but idle" variant showed a fullscreen translucent window
that never painted anything -- undefined content on some Windows/DWM
setups, and it was deployed in the same batch as a stage change that made
the avatar vanish entirely. The ripple only ever needs ~160x160 pixels
around the click point, so that's all the window is now: tiny, shown only
while a ripple is alive, never touching the rest of the screen.
"""
import sys
import time
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QPen

WINDOW_SIZE = 160  # px; the ripple never grows past this
RIPPLE_LIFETIME = 0.6


class InteractionOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

        self._ripples = []  # list of (x, y, start_time) in WINDOW coordinates
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        # Deliberately NOT started here -- it runs only while a ripple is
        # actually alive (see pulse_at / paintEvent).

        self._make_click_through_on_windows()

    def _make_click_through_on_windows(self):
        if sys.platform != "win32":
            return
        # Applied lazily (the window has no native handle while hidden).
        self._pending_click_through = True

    def _apply_click_through(self):
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
            )
        except Exception:
            pass  # visual overlay still works, just may intercept clicks on some setups

    def _prepare_native(self):
        # First-show only: the native window handle doesn't exist until the
        # widget is shown, so the click-through style is applied on the first
        # use rather than in __init__.
        if not self.isVisible() and getattr(self, "_pending_click_through", False):
            self._pending_click_through = False
            self.show()  # creates the native handle
            self._apply_click_through()

    def highlight_rect(self, rect, ttl_ms: int = 1200):
        """Outline the on-screen element she is about to act on.

        Uses THIS overlay, not a new window: this project's remaining
        flicker is suspected to come from the number of always-on-top
        translucent windows the compositor has to blend, so adding another
        would be the wrong trade. Large regions (a whole document pane) are
        skipped -- outlining half the screen says nothing about which thing
        she means."""
        left, top, right, bottom = (int(v) for v in rect)
        if (right - left) > 1000 or (bottom - top) > 600 or right <= left or bottom <= top:
            return
        pad = 8
        self._prepare_native()
        self.setGeometry(left - pad, top - pad, (right - left) + 2 * pad, (bottom - top) + 2 * pad)
        self._ripples = []
        self._highlight = {"box": (pad, pad, right - left, bottom - top),
                           "start": time.time(), "ttl": max(0.2, ttl_ms / 1000.0)}
        self.show()
        self._timer.start(66)

    def pulse_at(self, x: int, y: int):
        """Show a ripple centred on the screen point (x, y)."""
        self._prepare_native()
        self._highlight = None
        self.setGeometry(x - WINDOW_SIZE // 2, y - WINDOW_SIZE // 2,
                          WINDOW_SIZE, WINDOW_SIZE)
        self._ripples = [[WINDOW_SIZE // 2, WINDOW_SIZE // 2, time.time()]]
        self.show()
        self._timer.start(66)  # ~15fps for ripple — half the compositor churn

    def _paint_highlight(self, painter, now):
        h = getattr(self, "_highlight", None)
        if not h:
            return False
        age = now - h["start"]
        if age > h["ttl"]:
            self._highlight = None
            return False
        # Fade in over the first 150ms, out over the last 300ms.
        alpha = min(1.0, age / 0.15, (h["ttl"] - age) / 0.3)
        alpha = max(0.0, alpha)
        x, y, w, hgt = h["box"]
        painter.setBrush(QColor(90, 170, 250, int(28 * alpha)))
        painter.setPen(QPen(QColor(90, 170, 250, int(220 * alpha)), 2))
        painter.drawRoundedRect(x - 3, y - 3, w + 6, hgt + 6, 6, 6)
        return True

    def paintEvent(self, event):
        if not self._ripples and not getattr(self, "_highlight", None):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        now = time.time()
        highlighting = self._paint_highlight(painter, now)
        alive = []
        for rx, ry, start in self._ripples:
            age = now - start
            if age > RIPPLE_LIFETIME:
                continue
            alive.append([rx, ry, start])
            radius = 8 + age * 60
            alpha = max(0, 200 - int(age * 340))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(90, 170, 250, alpha))
            painter.drawEllipse(int(rx - radius / 2), int(ry - radius / 2),
                                int(radius), int(radius))
        self._ripples = alive
        if not alive and not highlighting:
            self._timer.stop()
            self.hide()
