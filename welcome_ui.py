"""Eva -- the first-run welcome window (onboarding.py decides what it says; this draws it).

Six short pages with Back / Next and "Skip the tour". Every action goes through the hooks the
application passes in (eva_app/welcome.py), so this window never touches the agent, the microphone
or the key store itself:

    hooks.say(text)              she speaks (voice preview, greetings)
    hooks.save_settings(dict)    settings_store.save -> {"restart": [...]}
    hooks.mic_muted() / hooks.toggle_mic()
    hooks.hear_once(fn)          fn(text) with the next thing the microphone hears; returns cancel()
    hooks.run_task(text)         the request goes to her as if typed; the chat window opens
    hooks.done(how, restart)     'finished' | 'skipped'; restart: a setting that needs one changed

Threads: the key check and the microphone answer arrive on other threads; they reach the window
through Qt signals (queued), never by touching widgets directly.
"""
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout, QWidget)

import onboarding as ob
from chat_ui import FONT_FAMILY

INK, MUTED, ACCENT, OK, BAD, LINE = "#1C1C1E", "#545458", "#0A6CDB", "#1E7E34", "#B3261E", "#D1D1D6"


def _label(text, kind="", wrap=True):
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.RichText if "<" in text else Qt.PlainText)
    if kind:
        lab.setObjectName(kind)
    lab.setOpenExternalLinks(True)
    return lab


class WelcomeWindow(QDialog):
    key_checked = Signal(bool, str)
    heard = Signal(str)

    def __init__(self, hooks, key_missing=False, parent=None):
        super().__init__(parent)
        self.hooks = hooks
        self.setWindowTitle("Welcome to Eva")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumSize(580, 560)
        pal = self.palette()
        for role, colour in ((QPalette.ColorRole.Window, "#FFFFFF"), (QPalette.ColorRole.Base, "#FFFFFF"),
                             (QPalette.ColorRole.Button, "#F2F2F7"), (QPalette.ColorRole.WindowText, INK),
                             (QPalette.ColorRole.Text, INK), (QPalette.ColorRole.ButtonText, INK),
                             (QPalette.ColorRole.PlaceholderText, "#8E8E93")):
            pal.setColor(role, QColor(colour))
        self.setPalette(pal)                    # light everywhere, also in Windows dark mode (settings_ui)
        self.setStyleSheet(f"""
            QDialog, QWidget {{ background: #FFFFFF; color: {INK}; font-family: '{FONT_FAMILY}'; font-size: 10.5pt; }}
            QLabel#h1 {{ font-size: 17pt; font-weight: 600; }}
            QLabel#lead {{ color: {MUTED}; font-size: 11pt; }}
            QLabel#muted {{ color: {MUTED}; font-size: 9.5pt; }}
            QLabel#ok {{ color: {OK}; font-weight: 600; }}
            QLabel#bad {{ color: {BAD}; font-weight: 600; }}
            QLabel#step {{ color: {MUTED}; font-size: 9pt; }}
            QFrame#card {{ border: 1px solid {LINE}; border-radius: 10px; }}
            QLineEdit, QComboBox {{ border: 1px solid #C7C7CC; border-radius: 6px; padding: 5px 8px; background: #FFFFFF; }}
            QComboBox QAbstractItemView {{ color: {INK}; background: #FFFFFF; selection-background-color: {ACCENT};
                selection-color: #FFFFFF; }}
            QPushButton {{ background: #F2F2F7; border: 1px solid #C7C7CC; border-radius: 7px; padding: 6px 16px; }}
            QPushButton:hover {{ background: #E5E5EA; }}
            QPushButton#primary {{ color: #FFFFFF; background: {ACCENT}; border-color: {ACCENT}; font-weight: 600; }}
            QPushButton#primary:hover {{ background: #0858B3; }}
            QPushButton#task {{ text-align: left; padding: 0; }}
            QLabel#tasktitle {{ font-weight: 600; }}
            QPushButton#link {{ border: 0; background: transparent; color: {MUTED}; padding: 6px 4px; }}
            QPushButton#link:hover {{ color: {INK}; }}
            QRadioButton {{ spacing: 7px; }}
            QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid #8E8E93;
                background: #FFFFFF; }}
            QRadioButton::indicator:checked {{ border: 1px solid {ACCENT}; background: qradialgradient(cx: 0.5,
                cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5, stop: 0 {ACCENT}, stop: 0.5 {ACCENT}, stop: 0.56 #FFFFFF,
                stop: 1 #FFFFFF); }}
        """)
        self.restart_needed = []
        self.pages = ob.steps(key_missing)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(12)
        self.counter = _label("", "step")
        root.addWidget(self.counter)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        build = {"hello": self._hello, "key": self._key, "voice": self._voice, "mic": self._mic,
                 "privacy": self._privacy, "try": self._try}
        for name in self.pages:
            page = QWidget()
            col = QVBoxLayout(page)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(10)
            build[name](col)
            col.addStretch(1)
            self.stack.addWidget(page)
        nav = QHBoxLayout()
        self.skip = QPushButton("Skip the tour")
        self.skip.setObjectName("link")
        self.skip.clicked.connect(lambda: self._close("skipped"))
        nav.addWidget(self.skip)
        nav.addStretch(1)
        self.back = QPushButton("Back")
        self.back.clicked.connect(lambda: self._go(-1))
        self.next = QPushButton("Next")
        self.next.setObjectName("primary")
        self.next.setDefault(True)
        self.next.clicked.connect(lambda: self._go(+1))
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        root.addLayout(nav)
        self.key_checked.connect(self._key_result)
        self.heard.connect(self._heard)
        self._go(0)

    # ---- navigation ---------------------------------------------------------------------------
    def _go(self, delta):
        i = max(0, min(self.stack.currentIndex() + delta, self.stack.count() - 1))
        if delta > 0 and self.pages[self.stack.currentIndex()] == "key" and not getattr(self, "_key_ok", False):
            self.key_status.setText("Paste and check a key first – without one I cannot think.")
            self.key_status.setObjectName("bad")
            self._restyle(self.key_status)
            return
        if delta > 0 and self.stack.currentIndex() == self.stack.count() - 1:
            self._close("finished")
            return
        self.stack.setCurrentIndex(i)
        self.counter.setText(f"Step {i + 1} of {self.stack.count()}")
        self.back.setVisible(i > 0)
        last = i == self.stack.count() - 1
        self.next.setText("Finish" if last else "Next")
        self.skip.setVisible(not last)

    def _close(self, how):
        self._cancel_tap()
        try:
            self.hooks.done(how, list(self.restart_needed))
        finally:
            self.accept()

    @staticmethod
    def _restyle(w):
        w.style().unpolish(w)
        w.style().polish(w)

    # ---- 1. hello -------------------------------------------------------------------------------
    def _hello(self, col):
        col.addWidget(_label("Hello, I'm Eva", "h1"))
        col.addWidget(_label("I live on your desktop and do things on your PC for you – just say or type what "
                             "you need.", "lead"))
        card = QFrame()
        card.setObjectName("card")
        g = QVBoxLayout(card)
        g.setContentsMargins(16, 12, 16, 12)
        for line in ("Open apps, find files, fill in forms, tidy folders, read what is on screen",
                     "Work in your own browser, with the sites you are signed in to",
                     "Remember what you tell me, and learn routines – you approve each one",
                     "Show every task as a card in the chat window, with Undo where files changed"):
            g.addWidget(_label(f"•   {line}"))
        col.addWidget(card)
        col.addWidget(_label("<b>I always ask first</b> before anything that cannot be undone (deleting, "
                             "uninstalling, restarting the PC) and anything to do with money. Say <b>stop</b> or "
                             "press Stop at any time."))
        col.addWidget(_label("Talk to me: click me for my menu, or press the chat shortcut. This tour takes "
                             "about a minute.", "muted"))

    # ---- 2. her model key ------------------------------------------------------------------------
    def _key(self, col):
        self._key_ok = False
        col.addWidget(_label("Connect my thinking", "h1"))
        col.addWidget(_label("I think with a language model. I need an API key for it – it is free to create.",
                             "lead"))
        col.addWidget(_label(f'1. Open <a href="{ob.KEY_URL}">build.nvidia.com</a>, sign in and create a key.<br>'
                             "2. Paste it here and press Check."))
        row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("nvapi-...")
        row.addWidget(self.key_edit, 1)
        self.key_btn = QPushButton("Check")
        self.key_btn.setObjectName("primary")
        self.key_btn.clicked.connect(self._check_key)
        row.addWidget(self.key_btn)
        col.addLayout(row)
        self.key_status = _label("", "muted")
        col.addWidget(self.key_status)
        col.addWidget(_label("The key is stored encrypted for your Windows account, never in a file in plain "
                             "text, and never shown again.", "muted"))

    def _check_key(self):
        key = self.key_edit.text()
        ok, why = ob.looks_like_key(key)
        if not ok:
            self.key_checked.emit(False, why)
            return
        self.key_btn.setEnabled(False)
        self.key_status.setText("Checking...")
        self.key_status.setObjectName("muted")
        self._restyle(self.key_status)

        def work():
            ok_, msg = ob.check_key(key)
            if ok_:
                try:
                    ob.save_key(key)
                except Exception as e:
                    ok_, msg = False, f"The key works, but could not be stored: {e}"
            self.key_checked.emit(ok_, msg)
        threading.Thread(target=work, name="eva-welcome-key", daemon=True).start()

    def _key_result(self, ok, msg):
        self._key_ok = self._key_ok or ok
        self.key_btn.setEnabled(True)
        self.key_status.setText(("✓  " if ok else "") + msg + ("  Saved." if ok else ""))
        self.key_status.setObjectName("ok" if ok else "bad")
        self._restyle(self.key_status)
        if ok:
            self.key_edit.clear()
            self.hooks.say("Thank you. Now I can think.")

    # ---- 3. voice and size -----------------------------------------------------------------------
    def _voice(self, col):
        import config
        import settings_store
        col.addWidget(_label("How I sound and look", "h1"))
        col.addWidget(_label("Pick a voice – I'll say a line in it.", "lead"))
        row = QHBoxLayout()
        self.voice_box = QComboBox()
        for v in settings_store.VOICES:
            self.voice_box.addItem(ob.VOICE_NAMES.get(v, v), v)
        cur = getattr(config, "TTS_VOICE", settings_store.VOICES[0])
        if cur in settings_store.VOICES:
            self.voice_box.setCurrentIndex(settings_store.VOICES.index(cur))
        self.voice_box.currentIndexChanged.connect(self._voice_changed)
        row.addWidget(self.voice_box, 1)
        hear = QPushButton("Hear it")
        hear.clicked.connect(lambda: self.hooks.say("Hi! This is how I sound. Shall we get started?"))
        row.addWidget(hear)
        col.addLayout(row)
        col.addSpacing(8)
        col.addWidget(_label("My size on your screen", "lead"))
        sizes = QHBoxLayout()
        self.size_group = QButtonGroup(self)
        current = float(getattr(config, "AVATAR_SCALE", 0.575))
        nearest = min(ob.SIZES, key=lambda s: abs(s[1] - current))[0]
        for name, value in ob.SIZES:
            b = QRadioButton(name)
            b.setChecked(name == nearest)
            b.toggled.connect(lambda on, v=value: on and self._size(v))
            self.size_group.addButton(b)
            sizes.addWidget(b)
        sizes.addStretch(1)
        col.addLayout(sizes)
        self.size_note = _label("", "muted")
        col.addWidget(self.size_note)
        col.addWidget(_label("Both can be changed later: tray icon > Settings, or just ask me (\"talk in a "
                             "British voice\", \"be smaller\").", "muted"))

    def _voice_changed(self, i):
        self.hooks.save_settings({"TTS_VOICE": self.voice_box.itemData(i)})
        self.hooks.say("Hi! This is how I sound.")

    def _size(self, value):
        res = self.hooks.save_settings({"AVATAR_SCALE": value}) or {}
        if res.get("restart"):
            if "Size" not in self.restart_needed:
                self.restart_needed.append("Size")
            self.size_note.setText("I'll change size when I restart – I can do that at the end of the tour.")

    # ---- 4. microphone ----------------------------------------------------------------------------
    def _mic(self, col):
        col.addWidget(_label("Talk to me", "h1"))
        col.addWidget(_label("Press Test and say \"Hello Eva\". I'll show you what I heard.", "lead"))
        row = QHBoxLayout()
        self.mic_btn = QPushButton("Test")
        self.mic_btn.setObjectName("primary")
        self.mic_btn.clicked.connect(self._test_mic)
        row.addWidget(self.mic_btn)
        self.mute_btn = QPushButton()
        self.mute_btn.clicked.connect(self._toggle_mic)
        row.addWidget(self.mute_btn)
        row.addStretch(1)
        col.addLayout(row)
        self.mic_status = _label("", "muted")
        col.addWidget(self.mic_status)
        col.addWidget(_label("While the microphone is on I listen for you; I answer when you talk to me. "
                             "Mute it any time from my menu or the tray. If nothing is heard: Windows Settings "
                             "> Privacy > Microphone must allow desktop apps.", "muted"))
        self._mic_label()

    def _mic_label(self):
        muted = bool(self.hooks.mic_muted())
        self.mute_btn.setText("Turn the microphone on" if muted else "Keep the microphone off")
        self.mic_btn.setEnabled(not muted)

    def _toggle_mic(self):
        self.hooks.toggle_mic()
        QTimer.singleShot(300, self._mic_label)

    def _test_mic(self):
        self.mic_status.setText("Listening... say \"Hello Eva\".")
        self.mic_status.setObjectName("muted")
        self._restyle(self.mic_status)
        self._cancel_tap()
        self._mic_token = token = object()
        self._mic_cancel = self.hooks.hear_once(lambda text: self.heard.emit(text))
        QTimer.singleShot(12000, lambda: self._mic_timeout(token))

    def _cancel_tap(self):
        cancel, self._mic_cancel = getattr(self, "_mic_cancel", None), None
        if callable(cancel):
            cancel()                   # a test that timed out or a closed window hears nothing more

    def _heard(self, text):
        self._mic_token = None
        self._mic_cancel = None
        self.mic_status.setText(f"✓  I heard: “{text}”")
        self.mic_status.setObjectName("ok")
        self._restyle(self.mic_status)

    def _mic_timeout(self, token):
        if getattr(self, "_mic_token", None) is token:
            self._mic_token = None
            self._cancel_tap()
            self.mic_status.setText("I didn't hear anything. Check the microphone is plugged in and allowed, "
                                    "then try again – or type to me instead.")
            self.mic_status.setObjectName("bad")
            self._restyle(self.mic_status)

    # ---- 5. privacy --------------------------------------------------------------------------------
    def _privacy(self, col):
        col.addWidget(_label("What leaves this PC", "h1"))
        col.addWidget(_label("Exactly what is sent, and where – as I am set up right now.", "lead"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        head = (_label("<b>What</b>"), _label("<b>Goes to</b>"))
        grid.addWidget(head[0], 0, 0)
        grid.addWidget(head[1], 0, 1)
        for r, (what, where, local) in enumerate(ob.privacy(), start=1):
            grid.addWidget(_label(what), r, 0, Qt.AlignTop)
            cell = QWidget()
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(1)
            v.addWidget(_label(where))
            if local:
                v.addWidget(_label(local, "muted"))
            grid.addWidget(cell, r, 1, Qt.AlignTop)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 6)
        col.addLayout(grid)
        col.addWidget(_label("Passwords are never typed by me, and no screen picture is kept after a step that "
                             "handles a password or a payment.", "muted"))

    # ---- 6. try it ------------------------------------------------------------------------------------
    def _try(self, col):
        col.addWidget(_label("Try me", "h1"))
        col.addWidget(_label("Pick one – I'll do it now and you can watch the steps in the chat window.", "lead"))
        for label, request, note in ob.FIRST_TASKS:
            b = QPushButton()
            b.setObjectName("task")
            b.setCursor(Qt.PointingHandCursor)
            inner = QVBoxLayout(b)
            inner.setContentsMargins(14, 9, 14, 9)
            inner.setSpacing(2)
            for text, kind in ((label, "tasktitle"), (note, "muted")):
                lab = _label(text, kind)
                lab.setAttribute(Qt.WA_TransparentForMouseEvents)
                lab.setStyleSheet("background: transparent;")
                inner.addWidget(lab)
            b.setMinimumHeight(58)
            b.clicked.connect(lambda _=False, t=request: self._run(t))
            col.addWidget(b)
        col.addWidget(_label("Or close this and just talk to me.", "muted"))

    def _run(self, request):
        self.hooks.run_task(request)
        self._close("finished")


def open_welcome(hooks, key_missing=False):
    w = WelcomeWindow(hooks, key_missing=key_missing)
    w.show()
    w.raise_()
    w.activateWindow()
    return w
