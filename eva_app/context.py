"""The shared context every feature installs into."""
import threading


class Ctx:
    """Attributes are added by the features as they install (see main.py for the order)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.timers = []               # every QTimer stays referenced for the life of the app

    def in_thread(self, fn):
        """A Qt slot that runs fn on a new daemon thread (desktop work never on the GUI thread)."""
        return lambda *_: threading.Thread(target=fn, daemon=True).start()

    def gui_timer(self, ms, fn, start=True):
        from PySide6.QtCore import QTimer
        t = QTimer()
        t.timeout.connect(fn)
        if start:
            t.start(ms)
        self.timers.append(t)
        return t
