"""
Eva -- avatar widget. Real 3D (Three.js jointed humanoid, lit and rendered
with perspective) inside a transparent overlay window, plus a floating
chat-input popup so typing to Eva doesn't depend on finding a terminal.
"""
import os
import math
import sys
import time
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QDialog, QPushButton, QHBoxLayout,
    QApplication
)
from PySide6.QtCore import Qt, QTimer, QUrl, QPoint, Signal, QEvent
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QPainter, QColor, QCursor
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage

from overlay_effects import InteractionOverlay
from logger import get_logger

log = get_logger(__name__)

from embodiment import HELD_STATES, reach_variant   # Qt-free, so the checks can test them
HELD_STATE_MS = 6000
WALK_TICK_MS = 33
RUN_SPEED = 16          # run_around: twice her walking pace

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
def _avatar_url():
    """The page, with the render options from config.py (see flicker_bench)."""
    import config
    url = QUrl.fromLocalFile(os.path.join(WEB_DIR, "avatar_3d.html"))
    pdb = "1" if getattr(config, "AVATAR_PRESERVE_BUFFER", True) else "0"
    url.setQuery(f"pdb={pdb}&fps={int(getattr(config, 'AVATAR_FPS', 30))}")
    return url


AVATAR_HTML = _avatar_url()
# Size of the CHARACTER (its hit box), not of any visible frame -- the
# character now renders onto a larger transparent stage around it.
# Her size on screen. config.AVATAR_SCALE scales the original 200 x 380 hit box
# (and with it the 3-D stage, which is a fixed multiple of it).
def _avatar_size():
    import config
    k = float(getattr(config, "AVATAR_SCALE", 0.5) or 0.5)
    k = max(0.3, min(2.0, k))
    return round(200 * k), round(380 * k)


AVATAR_W, AVATAR_H = _avatar_size()
MIN_AVATAR_W, MIN_AVATAR_H = 60, 110  # floor for user resizing
# The stage is this many times the character's size, centred on it, so
# gestures (arms out, spins, hair) are never clipped. MUST match
# STAGE_PAD_X / STAGE_PAD_Y in web/avatar_3d.html.
# A fullscreen fixed stage with a panning camera was tried at one point and
# made the avatar vanish entirely on the actual target machine -- most
# plausibly a fullscreen transparent WebGL surface was more than that GPU
# could allocate at all. This moving-stage design (stage tracks the
# character, sized relative to it) is the one that has rendered correctly
# across every real session since, so it stays.
STAGE_PAD_X, STAGE_PAD_Y = 1.6, 1.15
# (Backed off from a more aggressive 130x177 after repeated reports of
# cropping/sizing problems at that size that multiple fix attempts didn't
# resolve -- a smaller shrink is lower-risk while those are still being
# tracked down. See the startup geometry log line in main.py for the
# actual measured sizes next time this needs debugging.)


class ConsoleLoggingPage(QWebEnginePage):
    """Without this, JS errors inside the 3D scene (a failed GLTF load, a
    Three.js exception) are completely invisible -- they vanish into the
    embedded browser with no trace anywhere. This logs them to the same
    persistent file everything else goes to."""
    def __init__(self, parent=None, on_render_process_terminated=None, on_context_lost=None):
        super().__init__(parent)
        self._on_render_process_terminated = on_render_process_terminated
        self._on_context_lost = on_context_lost
        self.last_heartbeat = time.monotonic()
        self.beaten = False              # no heartbeat yet since the page last started loading
        self.loadStarted.connect(self._on_load_started)

    def _on_load_started(self):
        self.last_heartbeat, self.beaten = time.monotonic(), False

    def javaScriptConsoleMessage(self, level, message, line, source):
        # Heartbeats are frequent (every 2s) and not interesting on their
        # own -- recorded silently, not logged, to avoid drowning out
        # everything else in eva.log.
        if message == "EVA_HEARTBEAT":
            self.last_heartbeat, self.beaten = time.monotonic(), True
            return
        log.info(f"[avatar web console] {message} (line {line})")
        # A lost WebGL context leaves the character invisible with the page
        # otherwise alive -- confirmed in a real session, and Chromium did
        # not restore it by itself. Both Chromium's own message and ours
        # contain CONTEXT_LOST.
        if "CONTEXT_LOST" in message and self._on_context_lost:
            self._on_context_lost()
        # No WebGL at all: she cannot be drawn (eva.log 2026-10-11 16:59).
        # main.py decides what to do (software drawing, once).
        if "Error creating WebGL context" in message and not getattr(self, "_webgl_reported", False):
            self._webgl_reported = True
            cb = WEBGL_FAILED.get("cb")
            if cb:
                QTimer.singleShot(0, _gui_owned(cb))

    def renderProcessTerminated(self, status, exit_code):
        # The embedded Chromium renderer -- which draws the entire 3D
        # scene -- can crash or be killed independently of the outer Qt
        # process. Confirmed as a real failure mode, not a hypothetical
        # one: a session ended with no Python traceback anywhere in the
        # log at all, which is exactly what a native renderer crash looks
        # like (our own exception handling only ever sees Python
        # exceptions -- this is invisible to it entirely). Previously
        # there was no log line for this whatsoever, and nothing
        # attempted to recover -- the avatar would just go blank/black
        # forever, which is very likely the "suddenly became invisible"
        # symptom reported, followed eventually by the whole process
        # dying. This at least gives the next debugging pass concrete
        # evidence instead of a mysteriously empty log tail.
        log.critical(f"WebEngine render process terminated: status={status}, exit_code={exit_code}")
        if self._on_render_process_terminated:
            self._on_render_process_terminated()


def _gui_owned(fn):
    """A callable Qt may hold. ROOT CAUSE of every native crash on 2026-10-11
    (01:31, 12:33, 15:34, 17:10 -- Windows: Qt6Core.dll+0xea959 =
    QMetaObject::Connection::~Connection): perform_gesture_at did
    a 450 ms QTimer.singleShot with on_ready = done.set, a BOUND METHOD
    of a threading.Event owned by the task thread. PySide keeps a bound
    method's object by weak reference and owns a Qt connection for it; when
    the task thread dropped the Event (the reach returning), PySide tore that
    connection down ON THE TASK THREAD. Qt objects may only be destroyed on
    their own thread. A plain function made here holds a strong reference
    instead, and Qt releases it on the GUI thread. Every callback that comes
    from outside goes through this before Qt sees it."""
    if fn is None:
        return None
    return lambda *a: fn(*a)


# Set by main.py: called (on the GUI thread) when the page cannot create WebGL.
WEBGL_FAILED = {"cb": None}


class ClickForwarder:
    """Detects clicks and drags on the avatar via a global OS-level mouse
    hook (pynput), completely independent of Qt/QWebEngineView's own event
    handling. Two prior attempts at intercepting this through Qt's own
    event-filter mechanism -- first per-widget on the view, then
    application-level -- both proved unreliable: QWebEngineView routes
    input through Chromium's own internal pipeline in ways that don't
    consistently surface through Qt's normal event dispatch, regardless of
    where the filter is installed. pynput's global hook is a genuinely
    different mechanism (the same one idle_behavior.py already uses
    successfully for activity detection), so this sidesteps that whole
    category of problem instead of patching around it a third time.

    Callbacks run on pynput's own listener thread, never the GUI thread --
    every actual widget mutation (move, interrupt_movement) is dispatched
    through fire_on_gui. Reading simple cached geometry (pos/geometry) is
    done directly for practicality; it's a lightweight read, not a mutation.
    """
    DRAG_THRESHOLD = 6  # pixels of movement below which a press+release counts as a click, not a drag
    RESIZE_MARGIN = 20  # pixels from the bottom-right corner that count as the resize handle

    def __init__(self, avatar_widget, fire_on_gui, on_click):
        from pynput import mouse as pynput_mouse
        self._pynput_mouse = pynput_mouse
        self._avatar = avatar_widget
        self._fire_on_gui = fire_on_gui
        self._on_click = on_click
        self._pressed_inside = False
        self._dragging = False
        self._resizing = False
        self._press_screen_pos = None
        self._press_widget_pos = None
        self._press_widget_size = None
        self._listener = pynput_mouse.Listener(on_click=self._handle_click, on_move=self._handle_move)
        self._listener.start()

    def stop(self):
        """pynput's global hook kept running as a background OS-level
        listener even after the Qt app quit -- a click landing after
        shutdown reached into an AvatarWidget whose underlying C++ object
        Qt had already deleted, crashing with a libshiboken RuntimeError.
        Must be called during app shutdown, before widgets are torn down."""
        self._listener.stop()

    def _contains(self, x, y) -> bool:
        geo = self._avatar.geometry()
        return geo.left() <= x <= geo.right() and geo.top() <= y <= geo.bottom()

    def _in_resize_handle(self, x, y) -> bool:
        geo = self._avatar.geometry()
        return (geo.right() - self.RESIZE_MARGIN <= x <= geo.right()
                and geo.bottom() - self.RESIZE_MARGIN <= y <= geo.bottom())

    def _handle_click(self, x, y, button, pressed):
        if button != self._pynput_mouse.Button.left:
            return
        if avatar_clicks_suppressed():
            # An Eva dialog is on screen, probably directly over the
            # avatar. The click belongs to the dialog, not the hit box.
            self._pressed_inside = False
            self._dragging = self._resizing = False
            return
        if pressed:
            if self._contains(x, y):
                self._pressed_inside = True
                self._dragging = False
                self._resizing = self._in_resize_handle(x, y)
                self._press_screen_pos = (x, y)
                self._press_widget_pos = self._avatar.pos()
                self._press_widget_size = self._avatar.size()
        else:
            if self._pressed_inside and not self._dragging and not self._resizing:
                self._fire_on_gui(self._on_click)
            elif self._dragging:
                # Wherever the person just put her IS home now. "Go home"
                # used to walk back to the launch position regardless, so
                # moving her to a second monitor and asking her to go home
                # sent her back to the first one.
                self._fire_on_gui(self._avatar.remember_home)
                self._fire_on_gui(self._avatar.on_put_down)      # she lands, or sits on a window edge
            self._pressed_inside = False
            self._dragging = False
            self._resizing = False

    def _handle_move(self, x, y):
        if not self._pressed_inside or self._press_screen_pos is None:
            return
        dx = x - self._press_screen_pos[0]
        dy = y - self._press_screen_pos[1]
        if self._resizing:
            # A resize drag is a completely different gesture from a move
            # drag -- it's decided once, at press time (see _in_resize_handle
            # above), never re-evaluated mid-drag, so the two can never
            # flip into each other partway through one gesture.
            aspect = self._press_widget_size.width() / max(1, self._press_widget_size.height())
            new_h = max(MIN_AVATAR_H, self._press_widget_size.height() + dy)
            new_w = max(MIN_AVATAR_W, int(new_h * aspect))
            self._fire_on_gui(lambda: self._avatar.resize(new_w, new_h))
            return
        if not self._dragging and (abs(dx) > self.DRAG_THRESHOLD or abs(dy) > self.DRAG_THRESHOLD):
            self._dragging = True
            # a manual drag always wins over any in-progress walk; she reacts to being picked up
            self._fire_on_gui(self._avatar.on_picked_up)
        if self._dragging:
            target = QPoint(self._press_widget_pos.x() + dx, self._press_widget_pos.y() + dy)
            self._fire_on_gui(lambda: self._avatar.move(target))


# Her message bubble, the chat window and the status pill (iMessage style,
# painted -- 2026-10-11 legibility report) live in chat_ui.py.
from chat_ui import ChatPanel, FloatingBubble, StatusPill  # noqa: E402


# One palette for every Eva surface (bubble, status, chat, menu, dialogs), so
# nothing she shows looks like a different program. Same values as the bubble.
THEME_BG = "rgba(30, 30, 30, 235)"
THEME_FG = "white"
THEME_ACCENT = "#5aaafa"          # the ripple/highlight blue
THEME_DANGER = "#e5534b"
THEME_MONEY = "#e3a72f"
BUTTON_STYLE = """
    QPushButton { background-color: rgba(60, 60, 64, 240); color: white; border: 1px solid #555;
                  border-radius: 8px; padding: 6px 14px; font-size: 13px; }
    QPushButton:hover { background-color: rgba(80, 80, 86, 250); border-color: #5aaafa; }
    QPushButton:pressed { background-color: rgba(50, 50, 54, 250); }
"""


# ClickForwarder is a GLOBAL mouse hook that decides "was the avatar
# clicked?" purely from screen geometry. It has no idea what window is
# actually on top at that point -- so when Eva's own confirmation dialog
# opens over the avatar, clicking "Go ahead" is ALSO counted as a click on
# the avatar and opens the radial menu on top of the work being confirmed.
# Confirmed in eva.log 2026-09-26: every single confirmation in the session
# was immediately followed by "Avatar clicked -- radial menu opened".
# Incremented while any Eva-owned modal is on screen; the hook ignores
# clicks entirely while it is non-zero.
_click_suppression = {"depth": 0}


def suppress_avatar_clicks(on: bool):
    _click_suppression["depth"] += 1 if on else -1
    _click_suppression["depth"] = max(0, _click_suppression["depth"])


def avatar_clicks_suppressed() -> bool:
    return _click_suppression["depth"] > 0


class ApprovalDialog(QDialog):
    """The question Eva asks before a destructive or money-spending action.

    Every word of it comes from the framework (permissions.py), built from
    the real arguments and target; the model never sees this dialog or
    writes any of it. Deny is the default button, so a stray Enter refuses
    rather than allows."""

    def __init__(self, req, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Eva needs your approval")
        # Add to existing flags, don't replace them -- setWindowFlags(X)
        # alone strips QDialog's own default Qt.Dialog type flag, which
        # affects how the window manager handles z-order/focus/modality,
        # especially with several other always-on-top frameless windows
        # (avatar, bubble, chat popup, radial menu, overlay) live at once.
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(420)
        self.setStyleSheet(f"QDialog {{ background-color: {THEME_BG}; }} QLabel {{ color: {THEME_FG}; }}"
                           + BUTTON_STYLE)
        self._answer = "deny"
        accent = THEME_MONEY if req.effect == "FINANCIAL" else THEME_DANGER
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        heading = QLabel(req.title)
        heading.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {accent};")
        layout.addWidget(heading)

        detail = QLabel(req.detail)
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 13px; "
                             "background-color: rgba(0,0,0,110); border-radius: 6px; padding: 10px;")
        layout.addWidget(detail)

        if req.reason:
            why = QLabel(req.reason)
            why.setWordWrap(True)
            why.setStyleSheet("color: #aaa; font-size: 12px;")
            layout.addWidget(why)

        row = QHBoxLayout()
        allow = QPushButton("Allow once")
        allow.clicked.connect(lambda: self._finish("once"))
        row.addWidget(allow)
        if req.task_scope:
            task = QPushButton("Allow for this task")
            task.setToolTip("Allow this same kind of action, for this request only.")
            task.clicked.connect(lambda: self._finish("task"))
            row.addWidget(task)
        deny = QPushButton("Deny")
        deny.setDefault(True)
        deny.setStyleSheet(f"QPushButton {{ border-color: {accent}; }}")
        deny.clicked.connect(lambda: self._finish("deny"))
        row.addWidget(deny)
        layout.addLayout(row)
        deny.setFocus()

    def showEvent(self, event):
        suppress_avatar_clicks(True)
        super().showEvent(event)

    def hideEvent(self, event):
        suppress_avatar_clicks(False)
        super().hideEvent(event)

    def _finish(self, answer: str):
        self._answer = answer
        self.accept()

    def reject(self):                      # Esc or the window's close button
        self._answer = "deny"
        super().reject()

    def ask(self) -> str:
        self.exec()
        return self._answer


def ask_approval(req, parent=None) -> str:
    """Show the approval dialog; returns "once", "task" or "deny".

    The dialog necessarily takes the foreground, and Windows does not give
    it back on its own -- so the very action being approved then typed
    into the dialog's ghost instead of the document. Confirmed in eva.log
    2026-09-26: get_foreground_app right after a confirmation reported
    'python', not the Notepad window being worked on, and the model had to
    spend extra round trips calling focus_window to recover. Whatever was
    in front BEFORE the dialog is restored after it."""
    import desktop_control as _dc

    previous, previous_title = _dc._foreground_handle()
    try:
        return ApprovalDialog(req, parent=parent).ask()
    finally:
        if previous:
            restored = _dc.restore_foreground(previous)
            log.info(f"Focus returned to {previous_title!r} after the approval dialog: "
                     f"{'ok' if restored.get('ok') else restored.get('error')}")


class DesktopStage(QWidget):
    """Transparent, click-through render surface the 3D character is drawn
    on. It is never interacted with directly: Qt.WindowTransparentForInput
    makes the OS pass every click straight through to whatever is behind
    it, so the empty space around the character never blocks the desktop.
    AvatarWidget (the character's own hit box) keeps it positioned."""

    def __init__(self, on_render_process_terminated, on_context_lost=None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setPage(ConsoleLoggingPage(self.view, on_render_process_terminated=on_render_process_terminated,
                                             on_context_lost=on_context_lost))
        self.view.setAttribute(Qt.WA_TranslucentBackground)
        self.view.page().setBackgroundColor(Qt.transparent)
        self.view.setMinimumSize(0, 0)
        self.view.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        # Required for avatar_3d.html to load its local assets by relative path.
        self.view.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        self.view.load(AVATAR_HTML)
        layout.addWidget(self.view)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        # Second guard, independent of _sync_stage's move()/setGeometry
        # split: Qt can deliver a resizeEvent whose dimensions match what
        # the scene already has, and re-running resizeScene() then costs a
        # full renderer.setSize() and drawing-buffer reallocation for no
        # visual change at all.
        if (w, h) == getattr(self, "_last_scene_size", None):
            return
        self._last_scene_size = (w, h)
        self.view.page().runJavaScript(f"if (window.resizeScene) resizeScene({w}, {h});")


class AvatarWidget(QWidget):
    """The 3D character. play()/walk_to()/perform_gesture_at() are the real
    API brain.py and main.py drive."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # resize(), not setFixedSize() -- the window is now user-resizable
        # by dragging the bottom-right corner (see ClickForwarder's
        # RESIZE_MARGIN handling), so nothing should lock it to one fixed
        # size anymore. setMinimumSize keeps it from being dragged down to
        # something unusably tiny or inverted.
        # This window IS the character's hit box: exactly the character's
        # size, painted with alpha 1/255 (invisible, but Windows still routes
        # clicks to it), so clicks on the character are captured while
        # everything around it goes straight to the desktop. The visible
        # character is drawn on self.stage, which follows this window.
        self.setMinimumSize(MIN_AVATAR_W, MIN_AVATAR_H)
        self.setCursor(Qt.OpenHandCursor)
        self._last_ctx_recovery = 0.0
        self._reload_history = []
        self.stage = DesktopStage(on_render_process_terminated=self._recover_from_render_crash,
                                  on_context_lost=self._recover_from_context_loss)
        self.view = self.stage.view  # existing play()/expression code drives this page
        # Re-centre the stage on the character once the page has fully
        # loaded (the hit box may have moved while the page was still
        # loading) -- also re-fires correctly after the WebGL-crash
        # recovery reload, for the same reason.
        self.view.loadFinished.connect(lambda *_: self._sync_stage(force=True))
        self.view.loadFinished.connect(lambda *_: self._push_gesture_settings())
        self.resize(AVATAR_W, AVATAR_H)
        self.move(200, 200)
        # Where Eva started. Asked to "move back to your original
        # position" she had no record of it at all and guessed the centre
        # of the screen instead -- this gives go_home something real to
        # aim at. Feet-anchored, matching walk_to_screen's convention.
        # Updated whenever the person drags her somewhere -- see
        # ClickForwarder's release branch and remember_home below.
        self.home_position = (200 + AVATAR_W // 2, 200 + AVATAR_H)

        # ClickForwarder (global mouse hook, not a Qt event filter) is
        # constructed from main.py instead of here, since it needs
        # fire_on_gui and the on-click callback, both defined there.
        self.click_forwarder = None

        self.bubble = FloatingBubble()
        self.chat_popup = ChatPanel()
        self.bubble.on_show_all = self.open_chat
        self.overlay = InteractionOverlay()
        self.status = StatusPill()

        # Independent fallback for the exact gap seen in real testing: a
        # WebGL context/GPU-process crash whose signal never reached either
        # the JS 'webglcontextlost' listener or the CONTEXT_LOST console
        # check above. If the page hasn't logged a heartbeat in 8s (it logs
        # one every 2s -- see avatar_3d.html's tick()), something has
        # frozen the render loop entirely regardless of which specific
        # browser event did or didn't fire, and reloading is the same safe
        # recovery already used for the cases that DO get caught.
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self._check_heartbeat)
        self._watchdog.start(3000)
        self.status.on_render = self._reposition_status
        self._reposition_status()

        self._walk_timer = QTimer(self)
        self._walk_timer.setTimerType(Qt.PreciseTimer)      # a coarse timer drifts 10-15 ms: visible as hitches
        self._walk_timer.timeout.connect(self._walk_tick)
        self._walk_plan = None                               # time-based walk (SMOOTH_MOTION): start, end, timing
        self._walk_target = None
        self._walk_on_arrive = None
        self._walk_speed = 5
        self._revert_timer = None  # tracks the pending one-shot revert-to-idle from play()
        # Where her body is (see "her body in the world" below).
        self._body = {"held": False, "airborne": False, "sitting": False}
        self._perch = None
        self._perch_timer = QTimer(self)
        self._perch_timer.timeout.connect(self._perch_tick)
        self._glide = None
        self._glide_timer = QTimer(self)
        self._glide_timer.setTimerType(Qt.PreciseTimer)
        self._glide_timer.timeout.connect(self._glide_tick)

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, 1))

    def _sync_stage(self, force: bool = False):
        """Keep the stage centred on the character, STAGE_PAD_X/Y times its
        size. Called on every move and resize of the character -- during a
        walk that's every 16ms (_walk_timer), which was calling
        setGeometry() on the SEPARATE stage window (hosting the actual
        QWebEngineView/WebGL canvas) 60+ times a second. Confirmed directly:
        this is what was crashing the GPU context mid-walk ("GL_CONTEXT_
        LOST_KHR") in real testing -- that never happened before the stage
        was split into its own window, when a walk only ever moved one
        widget with nothing else to keep in sync. Throttled to roughly the
        30fps render cap below, which is still visually seamless (the gap
        between the character's hit box and the stage is invisible anyway)
        but cuts the GPU/compositor load during a walk by half or more.
        force=True bypasses the throttle -- used when a walk actually
        arrives and for the initial show, so the stage can't end up
        stuck slightly behind the character's true final resting spot if
        the very last move happened to land inside a throttle window.
        """
        if not hasattr(self, "stage"):
            return
        now = time.monotonic()
        if not force and now - getattr(self, "_last_stage_sync", 0) < 0.033:
            return
        self._last_stage_sync = now
        w, h = self.width(), self.height()
        sw, sh = int(w * STAGE_PAD_X), int(h * STAGE_PAD_Y)
        x, y = self.x() - (sw - w) // 2, self.y() - (sh - h) // 2
        # move() when only the POSITION changed, setGeometry() only when the
        # size genuinely differs. setGeometry re-runs the stage's resize
        # path -- renderer.setSize(), a new drawing buffer, a recomputed
        # projection matrix -- every time it is called, even for a pure
        # move. That fired on every moveEvent, i.e. continuously while the
        # person drags the character, and reallocating the WebGL drawing
        # buffer under the compositor mid-drag is a strong candidate for
        # the flicker that persists now that the context losses are gone.
        if self.stage.width() == sw and self.stage.height() == sh:
            self.stage.move(x, y)
        else:
            self.stage.setGeometry(x, y, sw, sh)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_stage(force=True)
        self.stage.show()
        self.raise_()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stage.hide()

    def closeEvent(self, event):
        self.stage.close()
        super().closeEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        if getattr(self, "_walk_target", None) is None:
            # During an active walk, _walk_tick calls _walk_stage_follow()
            # itself (a cheap move-only sync) -- calling the full
            # _sync_stage() here too on every single tick would be
            # redundant AND reintroduce the expensive setGeometry() ->
            # resizeEvent -> JS resizeScene() chain this split was meant
            # to avoid during a walk in the first place.
            self._sync_stage()
        # Catches every way the avatar can move -- manual drag, autonomous
        # walk, programmatic move() -- in one place, so the status label
        # never has to be repositioned separately at each call site.
        if hasattr(self, "status"):  # not yet constructed during __init__'s own initial move
            self._reposition_status()
        if hasattr(self, "bubble"):
            self.bubble.follow(self._bubble_anchor())     # her words travel with her

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The QVBoxLayout (0 margins) should already stretch self.view to
        # fill the widget on its own, but this is called explicitly too
        # since that chain can't be visually verified without actually
        # running it. More importantly, this is also where the JS-side
        # renderer/camera actually learn about the new size at all --
        # nothing else would tell them a resize happened.
        self._sync_stage()  # the stage's own resizeEvent re-fits the 3D camera
        if hasattr(self, "status"):
            self._reposition_status()

    def _reposition_status(self):
        # Below the avatar, opposite side from the bubble (which anchors
        # above) so they never overlap when both are showing at once.
        center_x = self.x() + self.width() // 2
        y = self.y() + self.height() + 4
        self.status.move(center_x - self.status.width() // 2, y)

    # The line under her shows only these; anything else (what she is doing) goes in her bubble.
    PILL_STATES = ("Listening", "Muted", "Paused", "Speaking", "Not listening", "Starting")

    def set_status(self, text: str, animated: bool = False):
        """Work and thinking go in the bubble above her; the line below shows the microphone
        (2026-10-11: the bubble's dots and "Thinking..." below said the same thing twice)."""
        text = str(text or "")
        if text and not any(text.startswith(s) for s in self.PILL_STATES):
            self.bubble.show_status(text, anchor=self._bubble_anchor())
            return
        if text != "Speaking":
            self.bubble.hide_status()
        self.status.set_status(text, animated)  # _reposition_status runs via on_render, every frame

    # Reload attempts in the last RENDER_CRASH_WINDOW seconds. Without a
    # limiter, a renderer that crashes on every load (driver bug, missing
    # GPU capability, corrupted cache) turns this into a two-reloads-per-
    # second hot loop. The JS-side limiter only covers webglcontextlost,
    # not a dead renderer process, so the ceiling has to exist here too.
    RENDER_CRASH_WINDOW = 300
    MAX_RENDER_CRASH_RELOADS = 3

    def _recover_from_render_crash(self):
        # Once the renderer process itself is gone, a plain reload isn't
        # enough -- the page needs a fresh load() to spawn an entirely new
        # renderer process, not just re-trigger the now-dead one. A short
        # delay gives Qt/Chromium a moment to actually finish tearing down
        # the crashed process first.
        now = time.monotonic()
        self._reload_history = [t for t in self._reload_history
                                if now - t < self.RENDER_CRASH_WINDOW]
        if len(self._reload_history) >= self.MAX_RENDER_CRASH_RELOADS:
            log.critical(
                f"Renderer crashed {self.MAX_RENDER_CRASH_RELOADS} times in "
                f"{self.RENDER_CRASH_WINDOW // 60} minutes -- not auto-reloading again. "
                "Restart Eva; if it persists this is likely a GPU driver problem.")
            return
        self._reload_history.append(now)

        # A crash mid-walk leaves _walk_target set and the walk timer
        # ticking, and moveEvent deliberately skips _sync_stage during a
        # walk -- so the reloaded stage would come back mis-positioned
        # relative to the hit box. Stop the walk before reloading.
        self.interrupt_movement()
        log.info(f"Recovering the avatar view after a render process crash "
                 f"(attempt {len(self._reload_history)}/{self.MAX_RENDER_CRASH_RELOADS}).")
        QTimer.singleShot(500, lambda: self.view.load(AVATAR_HTML))

    def _check_heartbeat(self):
        page = self.stage.view.page()
        now = time.monotonic()
        last_tick, self._last_watchdog_tick = getattr(self, "_last_watchdog_tick", now), now
        # The page's heartbeats reach Python through THIS thread. If this timer
        # itself was held up (a 3 s timer that last ran 6+ s ago) or the stall
        # detector saw the GUI thread blocked, the silence is Eva's own stall,
        # not a dead renderer: reloading then only adds risk (eva.log
        # 2026-10-11 01:40 -- the reload ran right after such a stall and the
        # process died). Restart the count instead.
        import gui_watchdog
        if now - last_tick > 6 or gui_watchdog.stalled_within(10):
            page.last_heartbeat = now
            return
        # The first heartbeat comes only once the 3-D model has loaded, which on
        # a slow start took longer than 8 s (eva.log 2026-10-11 14:07:54: a
        # needless "recovery" 6 s after start). Until then, allow 45 s.
        if now - page.last_heartbeat > (8 if getattr(page, "beaten", True) else 45):
            # Routed to the render-crash path, not the context-loss one. A
            # silent page can mean either, but only _recover_from_render_crash
            # delays for teardown and is rate-limited -- and if the renderer
            # process is actually gone, the context-loss path's plain reload
            # is not guaranteed to spawn a new one.
            # Debounced. Routing the watchdog here was right, but
            # _recover_from_render_crash had no debounce of its own (the
            # context-loss path did), so a page that was merely SLOW -- a
            # busy GUI thread during a long action -- could be reloaded on
            # every watchdog tick, each reload also cancelling any walk in
            # progress. That reads as exactly the stuttering it is meant
            # to cure.
            if self._reshow_if_hidden():
                page.last_heartbeat = time.monotonic()
                return
            now = time.monotonic()
            if now - self._last_ctx_recovery < 15:
                return
            self._last_ctx_recovery = now
            log.warning("Avatar page has not sent a heartbeat for 8s -- recovering.")
            self._recover_from_render_crash()

    def _reshow_if_hidden(self) -> bool:
        """A page that is not drawn sends no heartbeat. 2026-10-17 21:31 and 23:31 on the PC: another
        program hid her windows (a screen-recording tool), the page stopped drawing, and three
        "renderer crash" reloads later she gave up and stayed invisible -- nothing had crashed. A
        hidden or minimised window of hers is shown again (without taking focus) instead."""
        if sys.platform != "win32":
            return False
        shown = False
        try:
            import ctypes
            u = ctypes.windll.user32
            for w in (self, self.stage):
                try:
                    hwnd = int(w.winId())
                except Exception:
                    continue
                if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                    u.ShowWindow(hwnd, 4)                    # SW_SHOWNOACTIVATE
                    shown = True
        except Exception:
            log.exception("Could not check whether her windows are hidden")
            return False
        if shown:
            log.warning("Her window had been hidden or minimised by another program -- shown again.")
        return shown

    def _recover_from_context_loss(self):
        # Debounced: Chromium reports one loss with two console messages.
        now = time.monotonic()
        if now - self._last_ctx_recovery < 15:
            return
        self._last_ctx_recovery = now
        log.warning("WebGL context lost -- reloading the avatar view to recover.")
        QTimer.singleShot(1500, lambda: self.view.load(AVATAR_HTML))

    def look_toward(self, x: int, y: int, ttl_ms: int = 2500):
        """Turn her head toward a screen point for a moment.

        Sends a direction, not a position: (-1..1, -1..1) relative to where
        her head is, scaled by the screen, which is all a head turn needs.
        The rig applies it on top of its idle look-around and lets it fade."""
        screen = QApplication.primaryScreen().availableGeometry()
        head_x = self.x() + self.width() // 2
        head_y = self.y() + int(self.height() * 0.2)
        gx = max(-1.0, min(1.0, (x - head_x) / max(1.0, screen.width() / 2)))
        gy = max(-1.0, min(1.0, (y - head_y) / max(1.0, screen.height() / 2)))
        self.view.page().runJavaScript(
            f"if (window.setGaze) setGaze({gx:.3f}, {gy:.3f}, {int(ttl_ms)});")

    # ---- presence: the human connection (web/avatar_3d.html, presence layer) ----------------
    def attend(self, x: int, y: int, moving: bool, hover: bool):
        """Where the cursor is, relative to her head: her eyes follow it while it moves near her,
        her head half-follows, and a cursor resting on her gets a smile. Sends only changes."""
        head_x = self.x() + self.width() // 2
        head_y = self.y() + int(self.height() * 0.15)
        dx = max(-1.0, min(1.0, (x - head_x) / 420.0))
        dy = max(-1.0, min(1.0, (y - head_y) / 420.0))
        key = (round(dx, 2), round(dy, 2), bool(moving), bool(hover))
        if key == getattr(self, "_attn_sent", None):
            return
        self._attn_sent = key
        self.view.page().runJavaScript(
            f"if (window.setAttention) setAttention({dx:.2f}, {dy:.2f}, {str(bool(moving)).lower()}, "
            f"{str(bool(hover)).lower()});")

    def set_boredom(self, level: int):
        """0 content .. 3 very bored (nobody has needed her for a while): fidgets grow with it."""
        level = max(0, min(3, int(level)))
        if getattr(self, "_boredom_sent", None) == level:
            return
        self._boredom_sent = level
        self.view.page().runJavaScript(f"if (window.setBoredom) setBoredom({level});")

    def acknowledge(self):
        """The person spoke to her: a small nod and a smile."""
        self.view.page().runJavaScript("if (window.acknowledge) acknowledge();")

    def run_around(self):
        """A burst of play like a small child: run along the taskbar, a hop, run back part-way."""
        if self._body["sitting"] or self._body["held"] or self._walk_target is not None:
            return False
        screen = QApplication.primaryScreen().availableGeometry()
        if abs(self.y() + self.height() - screen.bottom()) > GROUND_TOLERANCE:
            return False                                   # only on the taskbar, never mid-air
        cx = self.x() + self.width() // 2
        span = int(screen.width() * 0.22)
        far = cx + span if (screen.right() - cx) > (cx - screen.left()) else cx - span
        back = cx + (far - cx) // 3
        self.view.page().runJavaScript("if (window.setRunning) setRunning(true);")

        def done():
            self.view.page().runJavaScript("if (window.setRunning) setRunning(false);")
            self.play("p_tada", loop=False)

        def turn():
            self.play("p_hop", loop=False)
            QTimer.singleShot(650, lambda: self.walk_to(back, screen.bottom(), on_arrive=done, speed=RUN_SPEED))
        self.walk_to(far, screen.bottom(), on_arrive=turn, speed=RUN_SPEED)
        return True

    def apply_settings(self):
        """Settings changed while running: re-send what the page reads (GUI thread)."""
        self._push_gesture_settings()

    def _push_gesture_settings(self):
        import config
        flag = "true" if getattr(config, "PROCEDURAL_GESTURES", True) else "false"
        self.view.page().runJavaScript(f"window.EVA_PROCEDURAL = {flag};")
        import json
        life = "true" if getattr(config, "AVATAR_LIFE", True) else "false"
        tune = json.dumps({k: v for k, v in (getattr(config, "POSE_TUNING", None) or {}).items()
                           if isinstance(v, (int, float))})
        speed = float(getattr(config, "ANIMATION_SPEED", 1.0) or 1.0)
        self.view.page().runJavaScript(f"window.EVA_LIFE = {life}; window.EVA_TUNE = {tune}; "
                                       f"window.EVA_SPEED = {max(0.5, min(1.6, speed)):.3f};")
        self._speaking_sent = None            # a reloaded page has forgotten it
        self._activity_sent = None
        if hasattr(self, "_body"):
            import json as _json
            self.view.page().runJavaScript(f"if (window.setBodyState) setBodyState({_json.dumps(self._body)});")

    def set_speaking(self, speaking: bool):
        """Tells the page whether she is talking, so the mouth and head move.
        Only sends a change; safe to call every tick."""
        if getattr(self, "_speaking_sent", None) == speaking:
            return
        self._speaking_sent = speaking
        self.view.page().runJavaScript(
            f"if (window.setSpeaking) setSpeaking({'true' if speaking else 'false'});")

    def set_helpers(self, count: int):
        """How many helpers are working: shown as small lights beside her."""
        n = max(0, min(5, int(count or 0)))
        self.view.page().runJavaScript(f"if (window.setHelpers) setHelpers({n});")

    def set_activity(self, name: str):
        """Tells the page which activity pose to hold (idle/reading/writing/...).
        Only sends a change; safe to call often. The page eases into it."""
        if getattr(self, "_activity_sent", None) == name:
            return
        self._activity_sent = name
        safe = str(name or "idle").replace("'", "")
        self.view.page().runJavaScript(f"if (window.setActivity) setActivity('{safe}');")

    def remember_home(self):
        """Records where the person just placed her as her home spot."""
        rect = self.geometry()
        self.home_position = (rect.center().x(), rect.bottom())
        log.info(f"Home position updated to {self.home_position} after a manual move.")

    def interrupt_movement(self):
        """Stops any in-progress autonomous walk. Called the moment a
        manual drag starts, so a user-initiated move always wins instead
        of fighting with the walk timer's own position updates."""
        self._walk_timer.stop()
        self._walk_target = None
        self._walk_on_arrive = None
        self._set_locomotion(False, 0)

    # ---------------- speech ----------------
    def _bubble_anchor(self):
        return self.geometry().topLeft() + QPoint(self.width() // 2, int(self.height() * 0.06))

    def show_text(self, text: str, duration_ms: int = 5000):
        self.bubble.show_text(text, duration_ms, anchor=self._bubble_anchor())

    def show_typing(self, on: bool = True):
        """iMessage's typing dots above her while she thinks."""
        if on:
            self.bubble.show_status("Thinking", anchor=self._bubble_anchor())
        else:
            self.bubble.hide_status()

    def open_chat(self):
        pos = self.pos() + QPoint(self.width() + 10, 20)
        self.chat_popup.open_at(pos, beside=self.geometry())

    # ---------------- animation ----------------
    def play(self, name: str, loop: bool = True, on_result=None):
        """on_result(bool), if given, is called with whether a real clip
        was actually found and started -- NOT a guess, the JS side's own
        playClip() now returns this. Previously this call was pure
        fire-and-forget with no callback at all, so play_animation's tool
        result claimed {"ok": True} completely unconditionally, regardless
        of whether anything actually happened -- confirmed directly in
        practice: "walk_taskbar" (which isn't a real animation clip at
        all, see play_animation's own handling of it) silently did
        nothing while still reporting success. runJavaScript's second
        argument is exactly this: a callback invoked with the page's own
        return value once the script actually finishes running there."""
        # A previous play(..., loop=False) call's pending revert-to-idle
        # timer was never cancelled here -- if a second animation started
        # before that timer fired, the OLD timer would still fire on
        # schedule and force idle, cutting off the NEW animation partway
        # through (e.g. play("wave", loop=False) then quickly
        # play("dance", loop=True): the dance would get cut off ~1.2s in).
        if self._revert_timer is not None:
            self._revert_timer.stop()
            self._revert_timer = None
        # window.playClip only exists once the module script's imports
        # (three.module.js + GLTFLoader.js, fetched over the network) have
        # fully resolved -- that can take noticeably longer than any fixed
        # startup delay. Guarding here means an early call is a harmless
        # no-op instead of a ReferenceError.
        script = f"(window.playClip ? playClip('{name}') : false)"
        if on_result:
            self.view.page().runJavaScript(script, _gui_owned(on_result))
        else:
            self.view.page().runJavaScript(script)
        if not loop and name in HELD_STATES:
            # Held states (nap, hover) are driven by the page until told
            # otherwise, so a one-off one is ended here. Everything else ends
            # ITSELF in the page: a clip when it finishes, a procedural gesture
            # when its envelope returns to zero. The old blanket 3.5 s revert
            # cut 7-12 s clips off mid-motion and, fired during a walk, stopped
            # her legs while the window kept sliding -- both seen as glitches.
            self._revert_timer = QTimer(self)
            self._revert_timer.setSingleShot(True)
            self._revert_timer.timeout.connect(self.stop)
            self._revert_timer.start(HELD_STATE_MS)

    def stop(self):
        self.view.page().runJavaScript("if (window.playClip) playClip('idle');")

    # ---------------- facial expression (mood) ----------------
    def set_affect(self, energy: float, mood: float, hold_s: float = 10):
        """Her feeling (core/feeling.py): energy 0..1, mood -1..1, held for hold_s, then easing back."""
        self.view.page().runJavaScript(
            f"if (window.setAffect) setAffect({float(energy):.2f}, {float(mood):.2f}, {float(hold_s):.0f});")

    def emote(self, name: str, size: float = 0.6):
        """A generated reaction: eager, delight, oops, curious, proud (varies every time)."""
        if name in ("eager", "delight", "oops", "curious", "proud"):
            self.view.page().runJavaScript(f"if (window.emote) emote('{name}', {float(size):.2f});")

    def set_expression(self, name: str, weight: float = 1.0):
        """name is one of the model's real morph targets: Angry, Surprised,
        Sad. There's no 'Happy' target -- positive feedback is conveyed via
        body animation (see play('wave')/play('point')) instead."""
        self.view.page().runJavaScript(f"if (window.setExpression) setExpression('{name}', {weight});")

    def clear_expression(self):
        self.view.page().runJavaScript("if (window.setExpression) setExpression('', 0);")

    # ---------------- movement: real on-screen walking ----------------
    def _set_locomotion(self, moving: bool, direction: int, speed_px_s: float = 0):
        self.view.page().runJavaScript(
            f"if (window.setLocomotion) setLocomotion({'true' if moving else 'false'}, {direction}, "
            f"{float(speed_px_s):.0f}, {self.height()});")

    def _set_render_fps(self, fps):
        """60 fps while she moves (window and body in step), back to the configured rate when still."""
        if not _smooth():
            return
        try:
            self.view.page().runJavaScript(f"if (window.setRenderFps) setRenderFps({int(fps) if fps else 0});")
        except Exception:
            pass

    def walk_to(self, x: int, y: int, on_arrive=None, speed: int = 8, anchor: str = "feet"):
        """Walk so that (x, y) is under the character's feet (anchor="feet")
        or beside its reaching hand (anchor="hand", used before clicking a
        real screen element). The destination is clamped into the USABLE
        screen area (availableGeometry, i.e. excluding the taskbar) -- the
        model can ask for off-screen or negative coordinates via
        walk_to_screen, and clamping against the full screen geometry could
        strand her behind the taskbar with no way back except a manual drag.
        """
        if self._body["sitting"]:
            self.stand_up(animate=False)          # she gets up before going anywhere
        w, h = self.width(), self.height()
        if anchor == "hand":
            tx, ty = x - int(w * 0.8), y - int(h * 0.35)
        else:
            tx, ty = x - w // 2, y - h  # the fitted camera puts the feet at the hit box bottom
        screen = QApplication.primaryScreen().availableGeometry()
        tx = max(screen.left(), min(tx, screen.right() - w))
        ty = max(screen.top(), min(ty, screen.bottom() - h))
        self._walk_target = (tx, ty)
        self._walk_on_arrive = on_arrive
        self._walk_speed = speed
        direction = 0 if abs(tx - self.x()) < 4 else (1 if tx > self.x() else -1)
        # She WALKS along the taskbar; anywhere else (up to a window's button,
        # onto a title bar, down from one) she flies, legs trailing.
        ground = screen.bottom()
        on_ground_now = abs(self.y() + h - ground) <= GROUND_TOLERANCE
        on_ground_after = abs(ty + h - ground) <= GROUND_TOLERANCE
        flying = not (on_ground_now and on_ground_after)
        self._walking_kind = "fly" if flying else "walk"
        self.play(self._walking_kind, loop=True)
        self.set_body(airborne=flying)
        self._set_locomotion(True, direction)
        # 100ms (10fps), not the original 16ms (60+fps): every tick moves
        # THREE layered always-on-top windows in lockstep (the hit box, the
        # transparent WebGL render stage, the status label) -- at 60+
        # ticks/second that's a lot of compositor churn, and it's the most
        # likely real driver of both the reported flickering and the
        # confirmed WebGL context losses during a walk (throttling just the
        # stage's OWN sync, tried previously, helped less than expected
        # because setGeometry() -- even at a reduced rate -- still triggers
        # a full resizeEvent -> JS resizeScene() chain every time, which is
        # the actually expensive part, not just the move itself). Per-tick
        # step distance is scaled by 6x below to keep the same overall
        # traversal speed at 1/6th the tick rate.
        # 33 ms (30 fps). It was 100 ms (10 fps) with 6x steps: ~45 px jumps
        # ten times a second, which is the stutter seen when she walks across
        # other windows. Per-tick work is a plain move() of the windows
        # (_walk_stage_follow, no setGeometry/resize), cheap enough at 30 fps;
        # the resize chain that once caused the WebGL losses is not on this path.
        if _smooth():
            # 2026-10-28 "her motion seems a bit laggy": a fixed step per tick made every late tick a pause
            # and then a jump. Now her position is a function of the CLOCK (start, end, a gentle speed-up
            # and slow-down), so a late tick only lands where she should be by then; 60 Hz while moving,
            # and the scene is drawn at 60 fps for the walk (setRenderFps) so body and window move together.
            self._walk_plan = _walk_plan((self.x(), self.y()), (tx, ty), self._walk_speed / 16.0)
            self._set_render_fps(60)
            self._set_locomotion(True, direction, self._walk_speed / 16.0 * 1000)
            self._walk_timer.start(SMOOTH_TICK_MS)
        else:
            self._walk_plan = None
            self._walk_timer.start(WALK_TICK_MS)
        self._sync_stage(force=True)  # initial placement, not just the next tick

    def walk_along_taskbar(self):
        """Stroll along the top of the taskbar toward the side with more room."""
        screen = QApplication.primaryScreen().availableGeometry()
        cx = self.x() + self.width() // 2
        span = int(screen.width() * 0.4)
        target_x = cx + span if (screen.right() - cx) > (cx - screen.left()) else cx - span
        self.walk_to(target_x, screen.bottom())
        return {"ok": True}

    def go_to(self, where: str = "center", x=None, y=None):
        """Walk somewhere on the desktop: explicit x/y (feet position), or a
        named spot. Named spots stand on top of the taskbar."""
        screen = QApplication.primaryScreen().availableGeometry()
        if x is not None and y is not None:
            target = (int(x), int(y))
        elif where == "cursor":
            pos = QCursor.pos()
            target = (pos.x() + self.width(), pos.y() + self.height() // 2)
        else:
            spot_x = {"left": screen.left() + self.width(), "right": screen.right() - self.width(),
                      "center": screen.center().x(), "taskbar": screen.center().x()}.get(where)
            if spot_x is None:
                return {"ok": False, "error": f"unknown place {where!r}"}
            target = (spot_x, screen.bottom())
        self.walk_to(*target)
        return {"ok": True, "walking_to": list(target)}

    def _walk_stage_follow(self):
        """Move-only stage sync for use during a walk's per-tick updates --
        deliberately NOT calling setGeometry() (used by the full _sync_stage
        below), because setGeometry() triggers the stage's resizeEvent ->
        JS resizeScene() chain (recomputing the camera's projection matrix,
        calling renderer.setSize()) EVERY time, even when the size hasn't
        actually changed -- only the position has, during a walk. That
        chain is the expensive part, not the move itself, and was a real,
        confirmed contributor to both the flickering and the WebGL context
        losses. move() alone repositions the window without touching size
        or triggering that chain at all."""
        if not hasattr(self, "stage"):
            return
        w, h = self.width(), self.height()
        sw, sh = int(w * STAGE_PAD_X), int(h * STAGE_PAD_Y)
        self.stage.move(self.x() - (sw - w) // 2, self.y() - (sh - h) // 2)

    def _arrive(self):
        tx, ty = self._walk_target
        self._move_together(tx, ty)
        self._sync_stage(force=True)
        self._walk_timer.stop()
        self._walk_target = None
        self._walk_plan = None
        self._set_render_fps(None)
        self._set_locomotion(False, 0)
        self.play("idle")
        self.update_footing()
        if self._walk_on_arrive:
            cb, self._walk_on_arrive = self._walk_on_arrive, None
            cb()

    def _move_together(self, x, y):
        """The visible stage first, then the hit box: moved the other way round, the picture trails the
        box by a frame on every step."""
        if hasattr(self, "stage"):
            w, h = self.width(), self.height()
            sw, sh = int(w * STAGE_PAD_X), int(h * STAGE_PAD_Y)
            self.stage.move(x - (sw - w) // 2, y - (sh - h) // 2)
        self.move(x, y)

    def _walk_tick(self):
        if self._walk_target is None:
            self._walk_timer.stop()
            return
        plan = self._walk_plan
        if plan is not None:
            x, y, done = _walk_position(plan, time.monotonic())
            if done:
                self._arrive()
                return
            self._move_together(x, y)
            return
        tx, ty = self._walk_target
        cx, cy = self.x(), self.y()
        dx, dy = tx - cx, ty - cy
        dist = math.hypot(dx, dy)
        # 6x per-tick distance to preserve the same overall traversal speed
        # at 1/6th the tick rate (100ms vs. the original 16ms) -- see the
        # comment on _walk_timer.start(100) in walk_to.
        step = self._walk_speed * WALK_TICK_MS / 16.0      # same speed as the original 16 ms ticks
        if dist <= step:
            self.move(tx, ty)
            self._sync_stage(force=True)  # correctness, not just throttling -- see _sync_stage
            self._walk_timer.stop()
            self._walk_target = None
            self._set_locomotion(False, 0)
            self.play("idle")
            self.update_footing()
            if self._walk_on_arrive:
                cb, self._walk_on_arrive = self._walk_on_arrive, None
                cb()
            return
        # round(), not int(): int truncates toward zero, so an interrupted
        # walk reported a position up to a pixel behind the real one.
        self.move(round(cx + dx * step / dist), round(cy + dy * step / dist))
        self._walk_stage_follow()

    # ---------------- physical interaction with real window elements ----------------
    def perform_gesture_at(self, x: int, y: int, gesture: str = "click", on_ready=None):
        """Walk NEAR (x, y) -- not onto it -- then gesture toward it, then
        signal on_ready so the real OS action fires just after the reach
        begins. Two real fixes here over a naive walk-to-the-exact-point:

        1. Stands BESIDE the target (offset left, or right if there's no
           room), feet clamped near the bottom of the screen -- standing
           exactly on the target made her float over page content and
           cover the very element she was about to act on with her own
           body.
        2. If already close (consecutive steps of one multi-tool task
           hitting the same window -- focus, then click, then type...),
           gestures in place instead of re-walking every time. Re-walking
           for every single step of a multi-step task made her pace back
           and forth constantly, which read as glitchy restlessness rather
           than a real presence.
        """
        feet_x, feet_y = reach_spot(x, y, self.width(), self.height(), _screen_rect())

        def arrived():
            self.play(reach_variant(gesture, x, self.x() + self.width() // 2), loop=False)
            self.overlay.pulse_at(x, y)
            if on_ready:
                QTimer.singleShot(450, _gui_owned(on_ready))

        cur_feet_x = self.x() + self.width() // 2
        cur_feet_y = self.y() + self.height()
        if math.hypot(feet_x - cur_feet_x, feet_y - cur_feet_y) < max(24, self.height() // 6):
            arrived()  # already at this spot -- reach out, don't re-walk
            return
        self.walk_to(feet_x, feet_y, on_arrive=arrived, speed=7)


    # ---------------- her body in the world: footing, sitting, being carried ----------------
    def set_body(self, **state):
        """held / airborne / sitting -> the page eases her legs and arms (setBodyState)."""
        changed = {k: bool(v) for k, v in state.items() if k in self._body and self._body[k] != bool(v)}
        if not changed:
            return
        self._body.update(changed)
        import json
        self.view.page().runJavaScript(f"if (window.setBodyState) setBodyState({json.dumps(changed)});")

    def body(self) -> dict:
        return dict(self._body, perched_on=(self._perch or {}).get("hwnd"), placed=self.placed_recently())

    def on_ground(self) -> bool:
        return abs(self.y() + self.height() - _screen_rect()[3]) <= GROUND_TOLERANCE

    def update_footing(self):
        """In the air unless she stands on the taskbar or sits on something."""
        self.set_body(airborne=not (self._body["sitting"] or self.on_ground()))

    def sit_on(self, edge_y: int, x: int = None, hwnd=None, on_done=None):
        """Go to (x, edge_y) -- the top edge of a window, or the taskbar -- and
        sit down on it, legs over the edge. hwnd: the window, so she moves with it."""
        x = self.x() + self.width() // 2 if x is None else int(x)
        if self._body["sitting"]:
            self.stand_up(animate=False)        # off the old seat first

        def arrived():
            self.play("p_hop", loop=False)
            QTimer.singleShot(260, lambda: self.sit_here(edge_y, hwnd, on_done))
        if abs(self.x() + self.width() // 2 - x) < 6 and abs(self.y() + self.height() - edge_y) < 6:
            arrived()
        else:
            self.walk_to(x, edge_y, on_arrive=arrived, speed=7)

    def sit_here(self, edge_y=None, hwnd=None, on_done=None):
        """Sits down where she stands (her feet on edge_y)."""
        if self._body["sitting"]:
            return
        edge_y = self.y() + self.height() if edge_y is None else int(edge_y)
        drop = int(self.height() * SIT_DROP)
        self.set_body(sitting=True, airborne=False)
        self._glide_to(self.x(), edge_y - self.height() + drop, 420)
        self._perch = {"hwnd": hwnd, "edge": edge_y, "dx": None}
        if hwnd:
            rect = _window_rect(hwnd)
            if rect:
                self._perch["dx"] = self.x() - rect[0]
                self._perch["edge_offset"] = edge_y - rect[1]
            self._perch_timer.start(300)
        if on_done:
            on_done()

    def is_sitting(self) -> bool:
        return self._body["sitting"]

    def stand_up(self, animate=True):
        if not self._body["sitting"]:
            return
        drop = int(self.height() * SIT_DROP)
        self._perch = None
        self._perch_timer.stop()
        self.set_body(sitting=False)
        if animate:
            self._glide_to(self.x(), self.y() - drop, 320, then=self.update_footing)
        else:
            self._glide = None
            self._glide_timer.stop()
            self.move(self.x(), self.y() - drop)
            self._sync_stage(force=True)
            self.update_footing()

    def _perch_tick(self):
        """She rides along when the window she sits on moves, and hops down
        when it is closed, minimised, maximised or slides away from under her."""
        p = self._perch
        if not p or not p.get("hwnd") or self._walk_target is not None or self._body["held"]:
            return
        rect = _window_rect(p["hwnd"])
        if rect is None or _window_iconic(p["hwnd"]) or rect[1] <= _screen_rect()[1] + 2:
            self.get_down()
            return
        left, top, right, _bottom = rect
        new_x = left + p["dx"] if p.get("dx") is not None else self.x()
        new_edge = top + p.get("edge_offset", 0)
        if not (left - 10 <= new_x + self.width() // 2 <= right + 10):
            self.get_down()
            return
        if new_edge != p["edge"] or new_x != self.x():
            drop = int(self.height() * SIT_DROP)
            p["edge"] = new_edge
            self.move(new_x, new_edge - self.height() + drop)
            self._sync_stage()

    # ---- where the person put her ------------------------------------------------------
    PLACED_HOLD_S = 600

    def mark_placed(self):
        """The person sent her here (walk_to_screen, sit_on_window): her idle
        life leaves her where she is for a while instead of flying her back
        down to the taskbar 8 s later (eva.log 2026-10-11 14:11)."""
        self._placed_at = time.monotonic()

    def placed_recently(self) -> bool:
        return time.monotonic() - getattr(self, "_placed_at", -1e9) < self.PLACED_HOLD_S

    def jump(self, times: int = 1, on_done=None):
        """A real jump: crouch, up into the air (the window itself rises),
        down, land. Sitting, she stands up first."""
        if self._body["sitting"]:
            self.stand_up(animate=False)
        times = max(1, min(5, int(times or 1)))
        x0, y0 = self.x(), self.y()
        height = int(self.height() * 0.32)
        top = _screen_rect()[1]

        def one(n):
            if n <= 0:
                self.play("p_land", loop=False)
                self.update_footing()
                if on_done:
                    QTimer.singleShot(500, _gui_owned(on_done))
                return
            self.play("p_hop", loop=False)

            def up():
                self._glide_to(x0, max(top, y0 - height), 230,
                               then=lambda: self._glide_to(x0, y0, 260, then=lambda: QTimer.singleShot(120, lambda: one(n - 1))))
            QTimer.singleShot(200, up)
        self.interrupt_movement()
        one(times)

    def watch_video(self, rect):
        """Watch the video in `rect` with the person: never over it -- she
        moves beside it on the taskbar (2026-10-11: she crashed twice while
        over a playing video) -- sits, and looks at it."""
        left, top, right, bottom = rect
        sl, st, sr, sb = _screen_rect()
        w = self.width()
        cx, cy = (left + right) // 2, (top + bottom) // 2

        def settle():
            if not self._body["sitting"]:
                self.sit_here()
            self.set_activity("watching")
            self.look_toward(cx, cy, 6000)
        full = left <= sl + 4 and right >= sr - 4 and top <= st + 4
        mine = (self.x(), self.y(), self.x() + w, self.y() + self.height())
        over = mine[0] < right and mine[2] > left and mine[1] < bottom and mine[3] > top
        if full or self.placed_recently() or not over:
            self.set_activity("watching")
            self.look_toward(cx, cy, 6000)
            if not full and not self.placed_recently() and not self._body["sitting"] and self.on_ground():
                self.sit_here()
            return
        room_left, room_right = left - sl, sr - right
        tx = left - w // 2 - 12 if room_left >= room_right else right + w // 2 + 12
        tx = max(sl + w // 2, min(tx, sr - w // 2))
        if self._body["sitting"]:
            self.stand_up(animate=False)
        self.walk_to(tx, sb, on_arrive=settle, speed=7)

    def keep_clear_of(self, rect):
        """Out of the way of an area she is watching or playing in: her own
        body would be in the screenshots and under the clicks. Beside it on
        the taskbar, on the side with more room."""
        left, top, right, bottom = rect
        sl, st, sr, sb = _screen_rect()
        w = self.width()
        mine = (self.x(), self.y(), self.x() + w, self.y() + self.height())
        if not (mine[0] < right and mine[2] > left and mine[1] < bottom and mine[3] > top):
            return
        room_left, room_right = left - sl, sr - right
        if max(room_left, room_right) < w:          # no room beside it: the corner furthest away
            tx = sl + w // 2 if (left + right) // 2 > (sl + sr) // 2 else sr - w // 2
        else:
            tx = left - w // 2 - 12 if room_left >= room_right else right + w // 2 + 12
        tx = max(sl + w // 2, min(tx, sr - w // 2))
        if self._body["sitting"]:
            self.stand_up(animate=False)
        self.mark_placed()
        self.walk_to(tx, sb, speed=9)

    def stop_watching(self):
        self.set_activity("idle")

    def get_down(self):
        """Off whatever she is on, down to the taskbar below her."""
        self.stand_up(animate=False)
        self.walk_to(self.x() + self.width() // 2, _screen_rect()[3], speed=8)

    def on_picked_up(self):
        """The person started dragging her."""
        self.interrupt_movement()
        if self._body["sitting"]:
            self._perch = None
            self._perch_timer.stop()
        self.set_body(held=True, sitting=False, airborne=False)
        self.set_expression("Surprised", 0.45)

    def on_put_down(self):
        """The person let go. Dropped on a window's top edge, she sits on it;
        on the taskbar she lands; anywhere else she hovers."""
        self.set_body(held=False)
        self.mark_placed()                 # you put her there: she stays
        self.clear_expression()
        feet_x, feet_y = self.x() + self.width() // 2, self.y() + self.height()
        edge = _window_edge_near(feet_x, feet_y, tolerance=max(14, self.height() // 10))
        if edge:
            hwnd, edge_y = edge
            self.move(self.x(), edge_y - self.height())
            self.sit_here(edge_y, hwnd)
            return
        if abs(feet_y - _screen_rect()[3]) <= max(GROUND_TOLERANCE, self.height() // 8):
            self.move(self.x(), _screen_rect()[3] - self.height())
        self.play("p_land", loop=False)
        self.update_footing()

    def _glide_to(self, x, y, ms, then=None):
        """A short eased move (sitting down, standing up)."""
        self._glide = {"from": (self.x(), self.y()), "to": (int(x), int(y)), "start": time.monotonic(),
                       "ms": max(60, ms), "then": then}
        self._set_render_fps(60)
        self._glide_timer.start(SMOOTH_TICK_MS)

    def _glide_tick(self):
        g = self._glide
        if not g:
            self._glide_timer.stop()
            return
        k = min(1.0, (time.monotonic() - g["start"]) * 1000 / g["ms"])
        e = k * k * (3 - 2 * k)
        (x0, y0), (x1, y1) = g["from"], g["to"]
        self._move_together(round(x0 + (x1 - x0) * e), round(y0 + (y1 - y0) * e))
        if k >= 1.0:
            self._glide = None
            self._glide_timer.stop()
            self._set_render_fps(None)
            self._sync_stage(force=True)
            if g["then"]:
                g["then"]()


SMOOTH_TICK_MS = 16
ACCEL_S = 0.22            # seconds to reach walking speed, and to slow down at the end


def _smooth():
    return bool(_cfg("SMOOTH_MOTION", True))


def _walk_plan(start, end, v_px_ms, now=None):
    """A walk as a function of time: a gentle speed-up, a steady pace, a gentle slow-down."""
    (x0, y0), (x1, y1) = start, end
    d = math.hypot(x1 - x0, y1 - y0)
    v = max(0.05, float(v_px_ms)) * 1000.0                    # px per second
    ta = min(ACCEL_S, d / v) if d else 0.0                     # short walks never reach full speed
    cruise = max(0.0, (d - v * ta) / v)                         # accel+decel cover v*ta in total
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "d": d, "v": v, "ta": ta, "cruise": cruise,
            "t0": time.monotonic() if now is None else now, "total": 2 * ta + cruise}


def _walk_position(plan, now):
    """(x, y, done) for the clock time `now`."""
    t = now - plan["t0"]
    d, v, ta, cruise = plan["d"], plan["v"], plan["ta"], plan["cruise"]
    if d <= 0 or t >= plan["total"]:
        return plan["x1"], plan["y1"], True
    a = v / ta if ta else 0.0
    if t < ta:
        s = 0.5 * a * t * t
    elif t < ta + cruise:
        s = 0.5 * v * ta + v * (t - ta)
    else:
        r = plan["total"] - t
        s = d - 0.5 * a * r * r
    k = max(0.0, min(1.0, s / d))
    return (round(plan["x0"] + (plan["x1"] - plan["x0"]) * k),
            round(plan["y0"] + (plan["y1"] - plan["y0"]) * k), False)


# How far she sinks when she sits: her hips go to the edge, so the hit box
# drops by the hip height (about half her height). Tunable: config.SIT_DROP.
def _cfg(name, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


SIT_DROP = float(_cfg("SIT_DROP", 0.46))
GROUND_TOLERANCE = 10


def _screen_rect():
    a = QApplication.primaryScreen().availableGeometry()
    return a.left(), a.top(), a.right(), a.bottom()


def reach_spot(x, y, w, h, screen):
    """Where her FEET go to reach (x, y) with a hand.

    Low on the screen (near the taskbar) she stands on the taskbar beside it.
    Higher up she flies to it: hovering beside the target with her shoulder
    at its height, on whichever side has room, so the reaching hand -- the
    one nearer the target -- lands on it (eva.log 2026-10-11 request: reach
    out to the close button and press it, like a person)."""
    left, top, right, bottom = screen
    reach = max(30, int(h * 0.30))                    # shoulder-to-fingertip, sideways
    if y >= bottom - int(h * 0.55):
        fx = x - reach if x - reach - w // 2 >= left else x + reach
        return max(left + w // 2, min(fx, right - w // 2)), bottom
    fx = x + reach if x + reach + w // 2 <= right else x - reach
    fy = y + int(h * 0.74)                            # shoulder level with the target
    return max(left + w // 2, min(fx, right - w // 2)), max(top + h, min(fy, bottom))


def _window_rect(hwnd):
    try:
        import desktop_control as dc
        r = dc.get_window_rect(hwnd)
        return (r["left"], r["top"], r["right"], r["bottom"]) if r else None
    except Exception:
        return None


def _window_iconic(hwnd) -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsIconic(ctypes.c_void_p(hwnd)))
    except Exception:
        return False


def _window_edge_near(x, y, tolerance=14):
    """(hwnd, top) of a visible window whose top edge is at (x, y) and is not
    covered there by another window, or None."""
    try:
        import ctypes
        import desktop_control as dc
        for w in dc.list_windows().get("windows", []):
            r = _window_rect(w["handle"])
            if not r or _window_iconic(w["handle"]):
                continue
            left, top, right, _b = r
            if left + 20 <= x <= right - 20 and abs(y - top) <= tolerance and top > _screen_rect()[1] + 40:
                import ctypes.wintypes
                pt = ctypes.wintypes.POINT(int(x), int(top) + 6)
                ctypes.windll.user32.WindowFromPoint.restype = ctypes.c_void_p
                ctypes.windll.user32.GetAncestor.restype = ctypes.c_void_p
                hit = ctypes.windll.user32.WindowFromPoint(pt)
                root = ctypes.windll.user32.GetAncestor(ctypes.c_void_p(hit), 2) if hit else None   # GA_ROOT
                if root and int(root) != int(w["handle"]) and not dc.is_own_window(int(root)):
                    continue
                return w["handle"], top
    except Exception:
        return None
    return None


class RegionPicker(QWidget):
    """Drag a box around part of the screen (the region shortcut). The screen
    dims a little, the box stays clear with a blue edge and its size shown;
    Esc or a click without dragging cancels. on_done((left, top, right, bottom))."""

    def __init__(self, on_done):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self._on_done = on_done
        self._a = self._b = None

    def start(self):
        geo = QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(geo)
        self._a = self._b = None
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _rect(self):
        from PySide6.QtCore import QRect
        if not (self._a and self._b):
            return None
        x, y = min(self._a.x(), self._b.x()), min(self._a.y(), self._b.y())
        return QRect(x, y, abs(self._a.x() - self._b.x()), abs(self._a.y() - self._b.y()))

    def mousePressEvent(self, e):
        self._a = self._b = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        if self._a is not None and e.buttons():
            self._b = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        r = self._rect()
        self.hide()
        if r is not None and r.width() >= 12 and r.height() >= 12:
            g = r.translated(self.geometry().topLeft())
            self._on_done((g.x(), g.y(), g.x() + g.width(), g.y() + g.height()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()

    def paintEvent(self, e):
        from PySide6.QtGui import QPen, QFont
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dim = QColor(8, 12, 18, 70)
        r = self._rect()
        if r is None:
            p.fillRect(self.rect(), dim)
            p.setPen(QColor(235, 242, 250))
            p.setFont(QFont("Segoe UI", 13))
            p.drawText(self.rect().adjusted(0, 60, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                       "Drag a box around what you want to ask Eva about  (Esc to cancel)")
            return
        # dim everything AROUND the box; the box itself only gets a faint tint
        # (fully clear pixels would not receive the mouse on Windows)
        W, H = self.width(), self.height()
        for x, y, w, h in ((0, 0, W, r.top()), (0, r.bottom() + 1, W, H - r.bottom() - 1),
                           (0, r.top(), r.left(), r.height()), (r.right() + 1, r.top(), W - r.right() - 1, r.height())):
            if w > 0 and h > 0:
                p.fillRect(x, y, w, h, dim)
        p.fillRect(r, QColor(90, 170, 250, 14))
        p.setPen(QPen(QColor(THEME_ACCENT), 2))
        p.drawRoundedRect(r, 4, 4)
        label = f"{r.width()} x {r.height()}"
        p.setFont(QFont("Segoe UI", 9))
        tw = p.fontMetrics().horizontalAdvance(label) + 12
        ty = r.top() - 22 if r.top() > 26 else r.top() + 4
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(16, 20, 28, 220))
        p.drawRoundedRect(r.left(), ty, tw, 18, 6, 6)
        p.setPen(QColor(235, 242, 250))
        p.drawText(r.left() + 6, ty + 13, label)


class RadialMenu(QWidget):
    """Eva's menu: a ring of options around Eva herself, like the selection
    wheels in The Witcher 3 or Call of Duty.

    The old menu was a column of buttons beside her: over a busy window it was
    hard to read, and at a screen edge it opened off-screen. The ring is drawn
    on a dark, shadowed, nearly opaque surface so it reads over anything; it is
    centred on her, and at an edge it uses only the part of the ring that is on
    screen (ring_layout.py), sliding onto the screen only when even that is too
    cramped. Mouse: hover lights a segment, click chooses it. Keyboard: arrows
    move, Enter chooses, 1-7 choose directly, Esc closes. Clicking Eva again,
    clicking elsewhere or waiting closes it. "Quit" asks for a second click.
    """
    chat_requested = Signal()
    mic_toggle_requested = Signal()
    action_requested = Signal()
    quit_requested = Signal()
    restart_requested = Signal()
    stop_requested = Signal()
    pause_requested = Signal()

    PAD = 16
    AUTO_HIDE_MS = 12000
    QUIT_CONFIRM_S = 2.5
    # (key, label, Segoe glyph, fallback glyph)
    # Order round the ring, clockwise from 12 o'clock. Quit sits at the bottom,
    # away from Chat, and still needs a second click.
    ITEMS = [("chat", "Chat", "\uE8BD", "\u2709"),
             ("mic", "Mute", "\uE720", "\u266A"),
             ("stop", "Stop", "\uE71A", "\u25A0"),
             ("restart", "Restart", "\uE72C", "\u27F3"),
             ("quit", "Quit", "\uE7E8", "\u23FB"),
             ("look", "Look", "\uE7B3", "\u25C9"),
             ("pause", "Pause", "\uE769", "\u2016")]

    def __init__(self, avatar: AvatarWidget):
        super().__init__()
        from PySide6.QtCore import QVariantAnimation, QEasingCurve
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._avatar = avatar
        self._layout = None
        self._hover = None
        self._muted = False
        self._paused = False
        self._quit_until = 0.0
        self._shown_at = 0.0
        self._p = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._autohide = QTimer(self)
        self._autohide.setSingleShot(True)
        self._autohide.timeout.connect(self.hide)
        self._icon_family = self._find_icon_font()

    # ---- state shown on the ring ----------------------------------------------------
    def set_mic_label(self, muted: bool):
        self._muted = bool(muted)
        self.update()

    def set_paused(self, paused: bool):
        self._paused = bool(paused)
        self.update()

    def label(self, key):
        return {"mic": "Unmute" if self._muted else "Mute",
                "pause": "Resume" if self._paused else "Pause",
                "quit": "Click again" if time.monotonic() < self._quit_until else "Quit"}.get(
            key, next(lbl for k, lbl, _g, _f in self.ITEMS if k == key))

    # ---- opening ----------------------------------------------------------------------
    def toggle_around(self, avatar=None):
        if self.isVisible():
            self.hide()
        else:
            self.open_around(avatar or self._avatar)

    def open_around(self, avatar=None):
        import dataclasses
        import ring_layout
        avatar = avatar or self._avatar
        g = avatar.geometry()
        center = (g.x() + g.width() / 2.0, g.y() + g.height() / 2.0)
        screen = QApplication.screenAt(QPoint(int(center[0]), int(center[1]))) or QApplication.primaryScreen()
        a = screen.availableGeometry()
        lay = ring_layout.layout(center, (g.width(), g.height()),
                                 (a.left(), a.top(), a.right(), a.bottom()), len(self.ITEMS))
        size = int(2 * (lay.outer + self.PAD))
        self.setGeometry(int(lay.cx - size / 2), int(lay.cy - size / 2), size, size)
        self._layout = dataclasses.replace(lay, cx=size / 2.0, cy=size / 2.0)   # in window coordinates
        self._hover = None
        self._quit_until = 0.0
        self._shown_at = time.monotonic()
        if callable(getattr(self, "paused_state", None)):     # main.py: lambda: runtime.paused
            self._paused = bool(self.paused_state())
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._anim.stop()
        self._anim.start()
        self._autohide.start(self.AUTO_HIDE_MS)

    def _on_anim(self, v):
        self._p = float(v)
        self.update()

    # ---- choosing ---------------------------------------------------------------------
    def choose(self, index):
        if index is None or not (0 <= index < len(self.ITEMS)):
            return
        key = self.ITEMS[index][0]
        if key == "quit":
            if time.monotonic() < self._quit_until:
                self.hide()
                self.quit_requested.emit()
            else:
                self._quit_until = time.monotonic() + self.QUIT_CONFIRM_S
                QTimer.singleShot(int(self.QUIT_CONFIRM_S * 1000) + 50, self.update)
                self.update()
            return
        self.hide()
        {"chat": self.chat_requested, "mic": self.mic_toggle_requested, "stop": self.stop_requested,
         "pause": self.pause_requested, "look": self.action_requested,
         "restart": self.restart_requested}[key].emit()

    def mouseMoveEvent(self, event):
        if self._layout is None:
            return
        p = event.position()
        hover = self._layout.hit(p.x(), p.y())
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover is not None else Qt.ArrowCursor)
            self.update()
        self._autohide.start(self.AUTO_HIDE_MS)

    def mousePressEvent(self, event):
        if self._layout is None:
            return
        p = event.position()
        index = self._layout.hit(p.x(), p.y())
        if index is None:
            self.hide()                      # the hole (Eva) or the empty corners
        else:
            self.choose(index)

    def keyPressEvent(self, event):
        k, n = event.key(), len(self.ITEMS)
        if k == Qt.Key_Escape:
            self.hide()
        elif k in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Tab):
            self._hover = 0 if self._hover is None else (self._hover + 1) % n
            self.update()
        elif k in (Qt.Key_Left, Qt.Key_Up, Qt.Key_Backtab):
            self._hover = n - 1 if self._hover is None else (self._hover - 1) % n
            self.update()
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.choose(self._hover)
        elif Qt.Key_1 <= k <= Qt.Key_9 and k - Qt.Key_1 < n:
            self.choose(k - Qt.Key_1)
        else:
            super().keyPressEvent(event)

    def changeEvent(self, event):
        # Clicking another program takes focus away: close, like any menu.
        # (A short grace period: Windows can deactivate a window mid-show.)
        if event.type() == QEvent.ActivationChange and self.isVisible() and not self.isActiveWindow() \
                and time.monotonic() - self._shown_at > 0.3:
            self.hide()
        super().changeEvent(event)

    def hideEvent(self, event):
        self._autohide.stop()
        self._hover = None
        super().hideEvent(event)

    # ---- drawing ----------------------------------------------------------------------
    @staticmethod
    def _find_icon_font():
        from PySide6.QtGui import QFontDatabase
        families = set(QFontDatabase.families())
        for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
            if f in families:
                return f
        return None

    def _wedge(self, cx, cy, r0, r1, center, span):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainterPath
        gap = 2.0
        start, sweep = center - span / 2 + gap / 2, span - gap
        outer = QRectF(cx - r1, cy - r1, 2 * r1, 2 * r1)
        inner = QRectF(cx - r0, cy - r0, 2 * r0, 2 * r0)
        path = QPainterPath()
        path.arcMoveTo(outer, start)
        path.arcTo(outer, start, sweep)
        path.arcTo(inner, start + sweep, -sweep)
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        if self._layout is None:
            return
        import math
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QFont, QPen, QRadialGradient
        L, p = self._layout, self._p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(p)
        scale = 0.86 + 0.14 * p
        painter.translate(L.cx, L.cy)
        painter.scale(scale, scale)
        painter.translate(-L.cx, -L.cy)
        accent, danger = QColor(THEME_ACCENT), QColor(THEME_DANGER)
        mid = (L.inner + L.outer) / 2.0
        quit_armed = time.monotonic() < self._quit_until

        # shadow first, so the ring reads over any window behind it
        for grow, alpha in ((7, 30), (4, 45), (2, 60)):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            for seg in L.segments:
                painter.drawPath(self._wedge(L.cx, L.cy + 2, L.inner - grow, L.outer + grow, seg.center, seg.span))

        icon_font = QFont(self._icon_family, 15) if self._icon_family else QFont("Segoe UI Symbol", 14)
        label_font = QFont("Segoe UI", 8)
        label_font.setBold(True)
        for seg in L.segments:
            key, _label, glyph, fallback = self.ITEMS[seg.index]
            hovered = seg.index == self._hover
            pop = 5 if hovered else 0
            path = self._wedge(L.cx, L.cy, L.inner, L.outer + pop, seg.center, seg.span)
            if hovered:
                c = danger if key == "quit" else accent
                grad = QRadialGradient(QPointF(L.cx, L.cy), L.outer + pop)
                grad.setColorAt(L.inner / (L.outer + pop), c.darker(150))
                grad.setColorAt(1.0, c)
                painter.setBrush(grad)
                painter.setPen(QPen(QColor(255, 255, 255, 170), 1.2))
            else:
                armed = key == "quit" and quit_armed
                painter.setBrush(QColor(110, 30, 30, 240) if armed else QColor(16, 20, 28, 236))
                painter.setPen(QPen(QColor(255, 255, 255, 46), 1.0))
            painter.drawPath(path)

            a = math.radians(seg.center)
            x, y = L.cx + mid * math.cos(a), L.cy - mid * math.sin(a)
            if key == "mic" and self._muted:
                fg = QColor("#ff7b72")
            else:
                fg = QColor(255, 255, 255) if hovered else QColor(222, 230, 240)
            painter.setPen(fg)
            painter.setFont(icon_font)
            g = glyph if self._icon_family else fallback
            if key == "pause" and self._paused:
                g = "\uE768" if self._icon_family else "\u25B6"
            painter.drawText(QRectF(x - 20, y - 22, 40, 24), Qt.AlignCenter, g)
            painter.setFont(label_font)
            painter.drawText(QRectF(x - 44, y + 1, 88, 16), Qt.AlignCenter, self.label(key))

        # a thin bright rim along the used arc
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 120), 1.6))
        r = L.outer + 3
        rect = QRectF(L.cx - r, L.cy - r, 2 * r, 2 * r)
        if L.full:
            painter.drawEllipse(rect)
        else:
            for seg in L.segments:
                painter.drawArc(rect, int((seg.center - seg.span / 2) * 16), int(seg.span * 16))
        painter.end()


# ---------------------------------------------------------------------------
# Staying on top. eva.log 2026-10-10: "I can't see you" -- after Eva opened or
# focused another app (Settings, Chrome), she was behind it. Qt sets
# WS_EX_TOPMOST once, at show(); Windows can drop it, and another topmost
# window can sit above hers. keep_on_top() re-asserts HWND_TOPMOST for her
# visible always-on-top windows, without activating them (no focus is stolen):
# the input-transparent stage first, then the hit box, then bubbles/menus/
# dialogs, so each ends above the one before. main.py calls it whenever the
# foreground window changes, and whenever her topmost flag has gone.
# ---------------------------------------------------------------------------
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x0001, 0x0002, 0x0010, 0x0200
GW_HWNDPREV = 3
GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x0008


def _own_on_top_windows():
    """Her windows that must stay in front, TOP first: an open menu or chat box,
    the speech bubble, the status line, the hit box, then the 3-D stage.
    The full-screen highlight overlay is deliberately NOT included: raising a
    full-screen translucent window above the WebGL stage on every check made
    the compositor redraw the whole desktop over her -- a flicker source."""
    from PySide6.QtWidgets import QApplication
    rank = {RadialMenu: 0, ChatPanel: 0, FloatingBubble: 1, StatusPill: 2, AvatarWidget: 3, DesktopStage: 4}
    wins = [w for w in QApplication.topLevelWidgets()
            if w.isVisible() and type(w) in rank and (w.windowFlags() & Qt.WindowStaysOnTopHint)]
    return sorted(wins, key=lambda w: rank[type(w)])


def _hwnd(w):
    return int(w.winId())


def topmost_lost() -> bool:
    """True if any of her always-on-top windows has lost WS_EX_TOPMOST."""
    if os.name != "nt":
        return False
    import ctypes
    u = ctypes.windll.user32
    for w in _own_on_top_windows():
        if not (u.GetWindowLongW(ctypes.c_void_p(_hwnd(w)), GWL_EXSTYLE) & WS_EX_TOPMOST):
            return True
    return False


def covered() -> bool:
    return covering_window() is not None


def covering_window():
    """The handle of a window that is not hers sitting ABOVE her stage and
    overlapping it, or None.
    Only then is anything re-ordered: re-asserting z-order when nothing covers
    her made the compositor redraw her for nothing (flicker)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    wins = _own_on_top_windows()
    stage = next((w for w in wins if isinstance(w, DesktopStage)), None)
    if stage is None:
        return None
    ours = {_hwnd(w) for w in wins}
    u.GetWindow.restype = ctypes.c_void_p
    sr = wintypes.RECT()
    u.GetWindowRect(ctypes.c_void_p(_hwnd(stage)), ctypes.byref(sr))
    h = u.GetWindow(ctypes.c_void_p(_hwnd(stage)), GW_HWNDPREV)
    for _ in range(400):                        # windows above the stage, nearest first
        if not h:
            return None
        if int(h) not in ours and u.IsWindowVisible(ctypes.c_void_p(h)) and not _cloaked(h):
            r = wintypes.RECT()
            if u.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(r)) and \
                    r.left < sr.right and r.right > sr.left and r.top < sr.bottom and r.bottom > sr.top:
                return int(h)
        h = u.GetWindow(ctypes.c_void_p(h), GW_HWNDPREV)
    return None


def _cloaked(h) -> bool:
    """Windows 10/11 keep invisible 'cloaked' windows (suspended UWP apps,
    other virtual desktops) in the z-order; they cover nothing."""
    import ctypes
    try:
        val = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(h), 14, ctypes.byref(val), 4)  # DWMWA_CLOAKED
        return val.value != 0
    except Exception:
        return False


def keep_on_top() -> int:
    """Puts her windows back at the top of the topmost band in ONE atomic
    batch (DeferWindowPos), top window first and each next one directly below
    it, never taking focus. Returns how many were placed."""
    if os.name != "nt":
        return 0
    import ctypes
    u = ctypes.windll.user32
    wins = _own_on_top_windows()
    if not wins:
        return 0
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
    u.BeginDeferWindowPos.restype = ctypes.c_void_p
    u.DeferWindowPos.restype = ctypes.c_void_p
    batch = u.BeginDeferWindowPos(len(wins))
    after = HWND_TOPMOST
    for w in wins:
        if batch:
            batch = u.DeferWindowPos(ctypes.c_void_p(batch), ctypes.c_void_p(_hwnd(w)), ctypes.c_void_p(after),
                                     0, 0, 0, 0, flags)
        after = _hwnd(w)
    if batch and u.EndDeferWindowPos(ctypes.c_void_p(batch)):
        return len(wins)
    # the batch failed: one by one, same order
    n, after = 0, HWND_TOPMOST
    for w in wins:
        if u.SetWindowPos(ctypes.c_void_p(_hwnd(w)), ctypes.c_void_p(after), 0, 0, 0, 0, flags):
            n += 1
        after = _hwnd(w)
    return n


def foreground_hwnd():
    if os.name != "nt":
        return None
    import ctypes
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = ctypes.c_void_p
    return u.GetForegroundWindow()
