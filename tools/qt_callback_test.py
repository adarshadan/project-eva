"""Reproduces the 2026-10-11 native crash (Qt6Core QMetaObject::Connection::~Connection).

A worker thread hands the GUI a BOUND METHOD of an object it owns (Event.set);
the GUI passes it to QTimer.singleShot; the worker then drops the Event. PySide
tracks the bound method's object weakly and tears the Qt connection down on
the WORKER thread -> access violation. Wrapping the callable in a plain
function made on the GUI thread (avatar_ui._gui_owned) fixes it.

    python tools/qt_callback_test.py bound     # Eva before the fix: crashes
    python tools/qt_callback_test.py wrapped   # the fix: survives
"""
import faulthandler
import sys
import threading

faulthandler.enable()
from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "wrapped"
app = QCoreApplication([])


class Bridge(QObject):
    request = Signal(object)

    def __init__(self):
        super().__init__()
        self.request.connect(self._run, Qt.QueuedConnection)

    def _run(self, fn):
        fn()


bridge = Bridge()


def gui_side(on_ready):
    QTimer.singleShot(1, on_ready if mode == "bound" else (lambda *a: on_ready(*a)))


def reach():
    done = threading.Event()
    bridge.request.emit(lambda: gui_side(done.set))
    return done.wait(2)


def worker():
    for _ in range(3000):
        reach()
    print(f"{mode}: survived 3000 reaches")
    bridge.request.emit(lambda: app.quit())    # queued to the GUI thread (a worker has no event loop)


threading.Thread(target=worker, daemon=True).start()
QTimer.singleShot(120000, app.quit)
app.exec()
