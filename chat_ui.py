"""Eva -- message bubbles and the chat window, in the style of iMessage.

Why: her text on screen was a dark, half-transparent label styled through a
Qt stylesheet. On a translucent window that background is not reliably
painted, so her words could sit almost bare over whatever was behind them --
hard to read on busy windows. Everything here is PAINTED: an opaque rounded
bubble with a soft shadow and a tail pointing at whoever is speaking, real
font sizes, and enough contrast on any background.

  FloatingBubble   her latest words beside her head (light grey, dark text),
                   or iMessage's "typing" dots while she thinks. A long answer
                   shows its beginning with "Show all" (opens the chat window
                   at it) and "Notepad"; it stays up longer and while the mouse
                   is over it.
  ChatPanel        the chat window: the whole conversation as bubbles -- the
                   person's in blue on the right, Eva's in grey on the left,
                   long answers in full and selectable -- plus the box to type
                   in, with the "selected text"/"marked area" chip.
  TaskCardWidget   one task in the chat window: status, steps, Stop / Undo /
                   Run again / Steps page (core/task_card.py).
  StatusPill       the small status under her ("Listening", "Thinking"...).
"""
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

# iOS system colours
EVA_BG = QColor("#E9E9EB")          # incoming (light)
EVA_FG = QColor("#111111")
ME_BG = QColor("#0A6CDB")           # outgoing blue (a shade deeper than iOS: white text at 5:1 contrast)
ME_FG = QColor("#FFFFFF")
DARK_EVA_BG = QColor("#2C2C2E")     # incoming, in the dark chat window
DARK_EVA_FG = QColor("#F2F2F7")
PANEL_BG = QColor(20, 22, 28, 246)
FONT_FAMILY = "Segoe UI"

# Colour schemes (config.BUBBLE_COLOURS). Every text colour is at least 4.5:1
# against its bubble (checked by run_checks); the bubble's gradient only
# darkens toward the bottom, so the contrast never drops below that.
THEMES = {
    # Eva in blue, you in green (default, 2026-10-11: "can the bubbles be coloured, maybe green or blue")
    "blue": {"eva": ("#0A6CDB", "#FFFFFF"), "me": ("#1E7E34", "#FFFFFF"), "float": ("#0A6CDB", "#FFFFFF"),
             "link": "#FFFFFF", "dots": (255, 255, 255)},
    # Eva in green, you in blue
    "green": {"eva": ("#1E7E34", "#FFFFFF"), "me": ("#0A6CDB", "#FFFFFF"), "float": ("#1E7E34", "#FFFFFF"),
              "link": "#FFFFFF", "dots": (255, 255, 255)},
    # iMessage: Eva in grey, you in blue
    "grey": {"eva": ("#2C2C2E", "#F2F2F7"), "me": ("#0A6CDB", "#FFFFFF"), "float": ("#E9E9EB", "#111111"),
             "link": "#0058B8", "dots": (142, 142, 147)},
}


def theme():
    try:
        import config
        name = str(getattr(config, "BUBBLE_COLOURS", "blue") or "blue").strip().lower()
    except Exception:
        name = "blue"
    return THEMES.get(name, THEMES["blue"])


def bubble_brush(rect: QRectF, colour):
    """The bubble's fill: its colour at the top, a little deeper at the bottom."""
    from PySide6.QtGui import QLinearGradient
    base = QColor(colour)
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0.0, base)
    g.setColorAt(1.0, base.darker(112))
    return g
RADIUS = 17
TAIL = 7
FLOAT_PREVIEW_CHARS = 320


def bubble_path(rect: QRectF, tail_side: str = "left", radius: float = RADIUS) -> QPainterPath:
    """A rounded bubble with a small iMessage-style tail at the bottom corner
    of tail_side ("left", "right", or "none")."""
    p = QPainterPath()
    p.addRoundedRect(rect, radius, radius)
    if tail_side in ("left", "right"):
        t = QPainterPath()
        b = rect.bottom()
        if tail_side == "left":
            x = rect.left()
            t.moveTo(x + 10, b - 14)
            t.cubicTo(x + 8, b - 4, x + 2, b, x - TAIL + 2, b + 1)
            t.cubicTo(x + 6, b + 2, x + 13, b - 1, x + 18, b - 5)
        else:
            x = rect.right()
            t.moveTo(x - 10, b - 14)
            t.cubicTo(x - 8, b - 4, x - 2, b, x + TAIL - 2, b + 1)
            t.cubicTo(x - 6, b + 2, x - 13, b - 1, x - 18, b - 5)
        t.closeSubpath()
        p = p.united(t)
    return p


def paint_shadow(painter: QPainter, path: QPainterPath, strength=1.0):
    painter.setPen(Qt.NoPen)
    for i, a in enumerate((18, 12, 7)):
        painter.setBrush(QColor(0, 0, 0, int(a * strength)))
        painter.drawPath(path.translated(0, 1.5 + i))


def _font(pt=10.5, bold=False):
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


def fit_width(label: QLabel, text: str, max_width: int):
    """A word-wrapped QLabel picks a narrow width by itself (three words to a
    line). Size it like iMessage: as wide as the text's longest line, up to
    max_width, so a sentence wraps only when it has to."""
    fm = label.fontMetrics()
    natural = max((fm.horizontalAdvance(line) for line in str(text).split("\n")), default=0) + 6
    label.setFixedWidth(max(24, min(max_width, natural)))


# ---- one message, in the chat window ------------------------------------------------------
class MessageBubble(QWidget):
    def __init__(self, role: str, text: str, when=None, parent=None, max_width=280):
        super().__init__(parent)
        self.role = "me" if role == "me" else "eva"
        bg, fg = theme()[self.role]
        self.bg, self.fg = QColor(bg), QColor(fg)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setFont(_font(10.5))
        pal = self.label.palette()
        pal.setColor(QPalette.WindowText, self.fg)
        self.label.setPalette(pal)
        self.label.setStyleSheet("background: transparent;")
        fit_width(self.label, text, max_width)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14 + (0 if self.role == "me" else TAIL), 8, 14 + (TAIL if self.role == "me" else 0), 10)
        lay.addWidget(self.label)
        self.when = when

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(TAIL if self.role == "eva" else 0, 1, -(TAIL if self.role == "me" else 0), -3)
        path = bubble_path(r, "right" if self.role == "me" else "left")
        p.setPen(Qt.NoPen)
        p.setBrush(bubble_brush(r, self.bg))
        p.drawPath(path)


# ---- a task card, in the chat window (core/task_card.py) ------------------------------------
CARD_BG = QColor("#232327")
CARD_LINE = QColor(255, 255, 255, 30)
CARD_STATUS = {   # status -> (label colour, mark); every colour >= 4.5:1 on CARD_BG (run_checks)
    "running": ("#6AA9FF", "\u25CF"), "done": ("#7BD88F", "\u2713"), "not_confirmed": ("#F2C46D", "!"),
    "failed": ("#FF8A80", "\u2715"), "stopped": ("#C7C7CC", "\u25A0")}
STEP_MARK = {"ok": ("\u2713", "#7BD88F"), "failed": ("\u2715", "#FF8A80"), "unchecked": ("?", "#F2C46D"),
             "declined": ("\u2013", "#C7C7CC")}
LIVE_STEPS = 4


class TaskCardWidget(QWidget):
    """One task: what was asked, its status, the steps, and Stop / Undo / Run again / Steps page.
    Emits action(name, card_id) -- name in stop, undo, again, replay."""
    action = Signal(str, str)

    def __init__(self, card: dict, parent=None, width=320):
        super().__init__(parent)
        self.card = {}
        self._open = False
        self.setFixedWidth(width)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(5)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.status = QLabel()
        self.status.setFont(_font(9.5, bold=True))
        self.meta = QLabel()
        self.meta.setFont(_font(8.5))
        self.meta.setStyleSheet("color: #AEAEB2; background: transparent;")
        head.addWidget(self.status)
        head.addStretch(1)
        head.addWidget(self.meta)
        lay.addLayout(head)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setFont(_font(10.5, bold=True))
        self.title.setStyleSheet("color: #F2F2F7; background: transparent;")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.title)
        self.why = QLabel()
        self.why.setWordWrap(True)
        self.why.setFont(_font(9))
        lay.addWidget(self.why)
        self.toggle = QPushButton()
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setFlat(True)
        self.toggle.setStyleSheet("QPushButton { color: #9CC3FF; background: transparent; border: 0; padding: 0; "
                                  "text-align: left; font-size: 9pt; } QPushButton:hover { color: #CFE2FF; }")
        self.toggle.clicked.connect(self._flip)
        lay.addWidget(self.toggle)
        self.steps = QLabel()
        self.steps.setWordWrap(True)
        self.steps.setTextFormat(Qt.RichText)
        self.steps.setFont(_font(9))
        self.steps.setStyleSheet("color: #D1D1D6; background: transparent;")
        lay.addWidget(self.steps)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.buttons = {}
        for name, text in (("stop", "Stop"), ("undo", "Undo"), ("again", "Run again"), ("replay", "Steps page")):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFont(_font(9))
            b.setStyleSheet("QPushButton { color: #F2F2F7; background: rgba(255,255,255,22); border: 1px solid "
                            "rgba(255,255,255,40); border-radius: 11px; padding: 3px 10px; } QPushButton:hover "
                            "{ background: rgba(255,255,255,44); } QPushButton:disabled { color: #8E8E93; }")
            b.clicked.connect(lambda _=False, n=name: self.action.emit(n, self.card.get("id", "")))
            row.addWidget(b)
            self.buttons[name] = b
        row.addStretch(1)
        lay.addLayout(row)
        self.update_card(card)

    def _flip(self):
        self._open = not self._open
        self._render()

    def update_card(self, card: dict):
        self.card = dict(card or {})
        self._render()

    def _render(self):
        c = self.card
        st = c.get("status") or "running"
        colour, mark = CARD_STATUS.get(st, CARD_STATUS["done"])
        from core.task_card import STATUS_TEXT
        word = STATUS_TEXT.get(st, st)
        self.status.setText(f"{mark}  {word}" + ("…" if st == "running" else ""))
        self.status.setStyleSheet(f"color: {colour}; background: transparent;")
        self.meta.setText(c.get("summary") or "")
        self.title.setText(c.get("task") or "Task")
        why = c.get("why") or ""
        if c.get("undone"):
            why = (why + " " if why else "") + "Its file changes were undone."
        self.why.setText(why[:1].upper() + why[1:] if why else "")
        self.why.setStyleSheet(f"color: {colour if st != 'done' else '#AEAEB2'}; background: transparent;")
        self.why.setVisible(bool(why))
        steps = list(c.get("steps") or [])
        running = st == "running"
        shown = steps[-LIVE_STEPS:] if running and not self._open else (steps if self._open else [])
        if steps and not running:
            self.toggle.setText(("Hide steps" if self._open else f"Show {len(steps)} step"
                                 + ("s" if len(steps) != 1 else "")))
            self.toggle.show()
        elif running and len(steps) > LIVE_STEPS:
            self.toggle.setText("Hide earlier steps" if self._open else f"Show all {len(steps)} steps")
            self.toggle.show()
        else:
            self.toggle.hide()
        import html as _h
        lines = []
        for s_ in shown:
            m, col = STEP_MARK.get(s_.get("state"), ("\u2022", "#AEAEB2"))
            lines.append(f'<span style="color:{col}">{m}</span>&nbsp; {s_.get("n")}. {_h.escape(s_.get("text", ""))}')
        self.steps.setText("<br>".join(lines))
        self.steps.setVisible(bool(lines))
        self.buttons["stop"].setVisible(running)
        self.buttons["undo"].setVisible(bool(c.get("changes")) and not running)
        self.buttons["undo"].setEnabled(not c.get("undone"))
        self.buttons["undo"].setText("Undone" if c.get("undone") else "Undo")
        self.buttons["again"].setVisible(not running and bool(c.get("task")))
        self.buttons["replay"].setVisible(not running and bool(c.get("replay")))
        # Wrapped labels: the height comes from the width (heightForWidth), not from sizeHint --
        # sizing by sizeHint clipped the buttons off a card that grew (PC, 2026-10-17 15:27).
        lay = self.layout()
        lay.invalidate()
        lay.activate()
        h = lay.heightForWidth(self.width()) if lay.hasHeightForWidth() else lay.sizeHint().height()
        self.setFixedHeight(h + 4)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -2)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.setPen(CARD_LINE)
        p.setBrush(CARD_BG)
        p.drawPath(path)
        colour = QColor(CARD_STATUS.get(self.card.get("status") or "running", CARD_STATUS["done"])[0])
        p.setPen(Qt.NoPen)
        p.setBrush(colour)
        p.drawRoundedRect(QRectF(r.left() + 1, r.top() + 10, 3, r.height() - 20), 1.5, 1.5)


# ---- the chat window ---------------------------------------------------------------------------
class ChatPanel(QWidget):
    """The conversation and the box to type in. Same API the old one-line
    ChatPopup had (message_submitted, open_at, set_context, take_context,
    input, chip), plus add_message / load_history / scroll_to_last."""
    message_submitted = Signal(str)
    card_action = Signal(str, str)      # (stop | undo | again | replay, card id) from a task card
    W, H = 360, 480

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(self.W, self.H)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Eva")
        title.setFont(_font(11.5, bold=True))
        title.setStyleSheet("color: #F2F2F7; background: transparent;")
        head.addWidget(title)
        head.addStretch(1)
        close = QPushButton("✕")
        close.setFixedSize(26, 26)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet("QPushButton { color: #AEAEB2; background: rgba(255,255,255,18); border: 0; "
                            "border-radius: 13px; font-size: 12px; } QPushButton:hover { background: rgba(255,255,255,40); }")
        close.clicked.connect(self.hide)
        head.addWidget(close)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollBar:vertical { width: 6px; "
                                  "background: transparent; } QScrollBar::handle:vertical { background: rgba(255,255,255,60); "
                                  "border-radius: 3px; min-height: 30px; } QScrollBar::add-line, QScrollBar::sub-line "
                                  "{ height: 0; }")
        self.list = QWidget()
        self.list.setStyleSheet("background: transparent;")
        self.rows = QVBoxLayout(self.list)
        self.rows.setContentsMargins(2, 2, 6, 2)
        self.rows.setSpacing(6)
        self.rows.addStretch(1)
        self.scroll.setWidget(self.list)
        root.addWidget(self.scroll, 1)

        self.chip = QLabel("")
        self.chip.setWordWrap(True)
        self.chip.setFont(_font(9))
        self.chip.setStyleSheet("QLabel { background-color: rgba(11,132,255,40); color: #CFE6FF; "
                                "border: 1px solid rgba(11,132,255,120); border-radius: 9px; padding: 5px 9px; }")
        self.chip.hide()
        root.addWidget(self.chip)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Message Eva")
        self.input.setFont(_font(10.5))
        self.input.setStyleSheet("""
            QLineEdit { background-color: #1C1C1E; color: #F2F2F7; border: 1px solid #3A3A3C;
                        border-radius: 17px; padding: 8px 14px; selection-background-color: #0B84FF; }
            QLineEdit:focus { border: 1px solid #0B84FF; }""")
        pal = self.input.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(142, 142, 147))
        pal.setColor(QPalette.Text, QColor("#F2F2F7"))
        pal.setColor(QPalette.Base, QColor("#1C1C1E"))
        self.input.setPalette(pal)
        self.input.returnPressed.connect(self._submit)
        root.addWidget(self.input)
        self._context = None
        self._bubbles = []
        self._cards = {}
        self.hide()

    # ---- the conversation --------------------------------------------------------------
    def add_message(self, role, text, when=None, scroll=True):
        b = MessageBubble(role, text, when, max_width=self.W - 110)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if b.role == "me":
            row.addStretch(1)
            row.addWidget(b)
        else:
            row.addWidget(b)
            row.addStretch(1)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setLayout(row)
        self.rows.insertWidget(self.rows.count() - 1, holder)
        self._bubbles.append(holder)
        while len(self._bubbles) > 200:
            old = self._bubbles.pop(0)
            old.setParent(None)
        if scroll:
            QTimer.singleShot(30, self.scroll_to_last)
        return b

    def put_card(self, card: dict, scroll=True):
        """A task card: added where the task happened, or updated in place as it runs."""
        cid = (card or {}).get("id")
        if not cid:
            return None
        w = self._cards.get(cid)
        if w is not None:
            w.update_card(card)
        else:
            w = TaskCardWidget(card, width=self.W - 46)
            w.action.connect(self.card_action)
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.addWidget(w)
            row.addStretch(1)
            holder = QWidget()
            holder.setStyleSheet("background: transparent;")
            holder.setLayout(row)
            self.rows.insertWidget(self.rows.count() - 1, holder)
            self._bubbles.append(holder)
            self._cards[cid] = w
            while len(self._bubbles) > 200:
                old = self._bubbles.pop(0)
                for k in [k for k, v in self._cards.items() if v.parent() is old]:
                    del self._cards[k]
                old.setParent(None)
        if scroll:
            QTimer.singleShot(30, self.scroll_to_last)
        return w

    def load_history(self, items):
        for it in items:
            if it.get("role") == "card":
                self.put_card(it.get("card") or {}, scroll=False)
                continue
            self.add_message(it.get("role"), it.get("text"), it.get("ts"), scroll=False)
        QTimer.singleShot(30, self.scroll_to_last)

    def scroll_to_last(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---- context chip (selected text / marked area) --------------------------------------
    def set_context(self, kind=None, value=None, label=""):
        self._context = (kind, value) if kind else None
        if kind:
            self.chip.setText(label)
            self.chip.show()
            self.input.setPlaceholderText("Ask about it, or press Enter" if kind == "region"
                                          else "What should I do with it?")
        else:
            self.chip.hide()
            self.input.setPlaceholderText("Message Eva")

    def take_context(self):
        ctx, self._context = self._context, None
        return ctx

    def _submit(self):
        text = self.input.text().strip()
        if text or (self._context and self._context[0] == "region"):
            self.message_submitted.emit(text)
        else:
            self._context = None
        self.input.clear()
        self.set_context(None)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.input.clear()
            self.set_context(None)
            self.hide()
        else:
            super().keyPressEvent(event)

    def open_at(self, pos: QPoint, beside=None):
        """Opens at pos, kept fully on screen; to her left when there is no room on her right."""
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        a = screen.availableGeometry()
        x, y = pos.x(), pos.y()
        if beside is not None and x + self.width() > a.right():
            x = beside.left() - self.width() - 10
        x = max(a.left() + 4, min(x, a.right() - self.width() - 4))
        y = max(a.top() + 4, min(y, a.bottom() - self.height() - 4))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        QTimer.singleShot(30, self.scroll_to_last)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 18, 18)
        paint_shadow(p, path, 0.8)
        p.setPen(QColor(255, 255, 255, 26))
        p.setBrush(PANEL_BG)
        p.drawPath(path)


# ---- her words beside her --------------------------------------------------------------------
class FloatingBubble(QWidget):
    """Her latest message above her head, or the typing dots while she
    thinks. show_text keeps the old Bubble API."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.on_show_all = None            # () -> open the chat window at this message
        self.on_notepad = None             # (full text) -> open it in Notepad
        self._mode = "text"                # text | status (what she is doing, with moving dots)
        self._status = ""
        self._pending_status = None
        self._full = ""
        self._dots = 0
        self._anchor = None
        self._hover = False
        t = theme()
        self._bg, self._fg = QColor(t["float"][0]), QColor(t["float"][1])
        self._link_colour, self._dot_rgb = t["link"], t["dots"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 14 + TAIL)
        lay.setSpacing(6)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setFont(_font(10.5))
        self.label.setMaximumWidth(270)
        pal = self.label.palette()
        pal.setColor(QPalette.WindowText, self._fg)
        self.label.setPalette(pal)
        self.label.setStyleSheet("background: transparent;")
        lay.addWidget(self.label)
        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(12)
        self.btn_all = self._link("Show all")
        self.btn_note = self._link("Notepad")
        self.btn_all.clicked.connect(self._show_all)
        self.btn_note.clicked.connect(lambda: self.on_notepad and self.on_notepad(self._full))
        al.addWidget(self.btn_all)
        al.addWidget(self.btn_note)
        al.addStretch(1)
        lay.addWidget(self.actions)
        self.actions.hide()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._maybe_hide)
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._tick_dots)
        self.hide()

    def _link(self, text):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFont(_font(9.5, bold=True))
        b.setStyleSheet(f"QPushButton {{ color: {self._link_colour}; background: transparent; border: 0; "
                        "padding: 0; text-decoration: underline; }")
        return b

    # ---- what she says ---------------------------------------------------------------
    def show_text(self, text: str, duration_ms: int = 5000, anchor: QPoint = None):
        text = str(text or "").strip()
        if not text:
            return
        self._mode = "text"
        self._dot_timer.stop()
        self._full = text
        long = len(text) > FLOAT_PREVIEW_CHARS
        shown = text
        if long:
            shown = text[:FLOAT_PREVIEW_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + " …"
        self.label.setText(shown)
        fit_width(self.label, shown, 270)
        self.label.show()
        self.actions.setVisible(long)
        self._place(anchor)
        self.show()
        self.raise_()
        # long answers stay up long enough to read (and while the mouse is over them)
        self._hide_timer.start(max(duration_ms, min(60000, 4000 + 55 * len(text))) if long else duration_ms)

    def show_typing(self, anchor: QPoint = None):
        """She is thinking: the bubble says so ("Thinking ...")."""
        self.show_status("Thinking", anchor)

    def show_status(self, text: str, anchor: QPoint = None):
        """What she is doing right now, in her bubble, with moving dots: "Thinking ...",
        "Looking for the Play button ...". 2026-10-11: the typing dots above her and the
        "Thinking..." line below her said the same thing twice; now the bubble says it once
        and the line below shows only the microphone. A message she has just said is never
        covered: the status waits until it has been read."""
        text = str(text or "").strip().rstrip(".…") or "Thinking"
        if anchor is not None:
            self._anchor = anchor
        if self._mode == "text" and self.isVisible() and self._hide_timer.isActive():
            self._pending_status = text
            return
        self._pending_status = None
        if self._mode == "status" and getattr(self, "_status", None) == text and self.isVisible():
            return
        self._status = text
        self._mode = "status"
        self._hide_timer.stop()
        self.actions.hide()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._dots = 0
        self._render_status()
        self.label.show()
        self._place(None)
        self._dot_timer.start(420)
        self.show()
        self.raise_()

    def _render_status(self):
        dots = " " + "\u00b7" * (1 + self._dots % 3)
        shown = self._status + dots
        # width fixed for the longest dot count, so the bubble does not twitch
        fit_width(self.label, self._status + " \u00b7\u00b7\u00b7", 270)
        self.label.setText(shown)

    def hide_typing(self):
        self.hide_status()

    def hide_status(self):
        self._pending_status = None
        if self._mode in ("typing", "status"):
            self._dot_timer.stop()
            self.hide()

    def _place(self, anchor):
        if self._mode in ("text", "status"):
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.adjustSize()
        if anchor is not None:
            self._anchor = anchor
        self._position()

    def follow(self, anchor: QPoint):
        """She moved: the bubble goes with her (2026-10-11: "when EVA is
        moving the bubble gets left on the last position")."""
        self._anchor = anchor
        if self.isVisible():
            self._position()

    def _position(self):
        if self._anchor is None:
            return
        screen = QApplication.screenAt(self._anchor) or QApplication.primaryScreen()
        a = screen.availableGeometry()
        # tail at her head: the bubble sits above-right of the anchor
        x = self._anchor.x() - 26
        y = self._anchor.y() - self.height() - 4
        if x + self.width() > a.right():
            x = self._anchor.x() - self.width() + 26
        x = max(a.left() + 2, min(x, a.right() - self.width() - 2))
        y = max(a.top() + 2, min(y, a.bottom() - self.height() - 2))
        tail = max(18, min(self.width() - 18, self._anchor.x() - x))
        if tail != getattr(self, "_tail_x", None):
            self._tail_x = tail
            self.update()
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _tick_dots(self):
        self._dots = (self._dots + 1) % 30
        if self._mode == "status":
            self._render_status()
        self.update()

    def _show_all(self):
        if self.on_show_all:
            self.on_show_all()
        self.hide()

    def enterEvent(self, e):
        self._hover = True

    def leaveEvent(self, e):
        self._hover = False
        if not self._hide_timer.isActive() and self._mode == "text":
            self._hide_timer.start(2500)

    def _maybe_hide(self):
        if self._hover:
            self._hide_timer.start(1500)
            return
        pending = getattr(self, "_pending_status", None)
        self.hide()
        if pending:                                     # she is still working: say what on
            self._mode = "status-wait"
            self.show_status(pending)

    def mousePressEvent(self, e):
        if self._mode == "text" and not self.actions.isVisible():
            self.hide()                # a click dismisses a short message

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 2, -4, -TAIL - 4)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS, RADIUS)
        # tail pointing down toward her head
        tx = float(getattr(self, "_tail_x", 30))
        t = QPainterPath()
        t.moveTo(tx - 9, r.bottom() - 2)
        t.cubicTo(tx - 4, r.bottom() + 2, tx - 2, r.bottom() + TAIL, tx - 6, r.bottom() + TAIL + 2)
        t.cubicTo(tx + 4, r.bottom() + TAIL, tx + 9, r.bottom() + 3, tx + 12, r.bottom() - 2)
        t.closeSubpath()
        path = path.united(t)
        paint_shadow(p, path, 1.4)
        p.setPen(QColor(0, 0, 0, 22))
        p.setBrush(bubble_brush(r, self._bg))
        p.drawPath(path)
        if self._mode == "typing":
            cy = r.center().y()
            for i in range(3):
                phase = (self._dots / 30.0 * 3 - i) % 3
                k = max(0.0, 1 - abs(phase - 0.5) * 1.4)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*self._dot_rgb, int(110 + 145 * k)))
                rad = 4.2 + 1.0 * k
                p.drawEllipse(QPointF(r.center().x() - 13 + 13 * i, cy - 1.5 * k), rad, rad)


class StatusPill(QLabel):
    """The small status line under her, painted so it reads on any background."""
    _DOTS = ["", ".", "..", "..."]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFont(_font(8.5, bold=True))
        self.setAlignment(Qt.AlignCenter)
        self.setContentsMargins(10, 3, 10, 4)
        pal = self.palette()
        pal.setColor(QPalette.WindowText, QColor("#3A3A3C"))
        self.setPalette(pal)
        self._base = "Starting"            # "Listening" only once the microphone is really read (2026-10-28)
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.on_render = None
        self._render()
        self.show()

    def set_status(self, text: str, animated: bool = False):
        if text == self._base and animated == self._timer.isActive():
            return
        self._base, self._frame = text, 0
        self._timer.start(700) if animated else self._timer.stop()
        self._render()

    def _advance(self):
        self._frame = (self._frame + 1) % len(self._DOTS)
        self._render()

    def _render(self):
        self.setText(self._base + (self._DOTS[self._frame] if self._timer.isActive() else ""))
        self.adjustSize()
        if self.on_render:
            self.on_render()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -2)
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        paint_shadow(p, path, 0.8)
        p.setPen(QColor(0, 0, 0, 20))
        p.setBrush(QColor(242, 242, 247, 240))
        p.drawPath(path)
        p.end()
        super().paintEvent(e)


def now():
    return time.time()
