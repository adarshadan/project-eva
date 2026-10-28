"""Eva core -- the one bridge from background threads to the Qt GUI thread.

Native automation (pywinauto/COM) must never run on the GUI thread, and Qt
widgets must only be touched on it; mixing the two caused the OLE apartment
crash. These two functions are the only crossing point.
"""
import threading

from PySide6.QtCore import QObject, Qt, Signal


class GuiBridge(QObject):
    request = Signal(object)

    def __init__(self):
        super().__init__()
        self.request.connect(self._run, Qt.QueuedConnection)

    def _run(self, fn):
        fn()

    def run_sync(self, fn, timeout=30):
        """Runs fn() on the GUI thread and waits for its result. Only for fn
        that completes on its own -- never one that waits for a later GUI-thread
        callback (that would deadlock until the timeout)."""
        if threading.current_thread() is threading.main_thread():
            return fn()
        box, done = {}, threading.Event()

        def wrapper():
            try:
                box["value"] = fn()
            finally:
                done.set()
        self.request.emit(wrapper)
        done.wait(timeout=timeout)
        return box.get("value")

    def fire(self, fn):
        """Schedules fn on the GUI thread without waiting."""
        self.request.emit(fn)
