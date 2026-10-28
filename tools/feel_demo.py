"""Her feelings on the real avatar: each reaction in turn, with its mood, then a walk (smooth motion).

    python tools/feel_demo.py        (about 25 s; the page's console goes to the log)
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

STEPS = [("excited", 0.92, 0.8, "delight", 1.0), ("eager", 0.85, 0.45, "eager", 0.9),
         ("curious", 0.7, 0.3, "curious", 0.9), ("proud", 0.7, 0.8, "proud", 0.9),
         ("sorry", 0.35, -0.45, "oops", 0.8), ("calm", 0.35, 0.3, None, 0)]


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import avatar_ui
    app = QApplication.instance() or QApplication(sys.argv)
    a = avatar_ui.AvatarWidget()
    a.show()
    screen = QApplication.primaryScreen().availableGeometry()
    a.move(screen.center().x() - a.width() // 2, screen.bottom() - a.height())
    state = {"ready": False, "i": 0}

    shots = os.path.join(HERE, "tools", "feel_shots")
    os.makedirs(shots, exist_ok=True)

    def shot(name):
        st = a.stage
        g = st.geometry()
        img = QApplication.primaryScreen().grabWindow(0, g.x(), g.y(), g.width(), g.height())
        img.save(os.path.join(shots, f"{name}.png"))

    def poll():
        if not state["ready"]:
            a.view.page().runJavaScript("window.EVA_READY === true", lambda r: state.__setitem__("ready", bool(r)))
            QTimer.singleShot(300, poll)
            return
        i = state["i"]
        if i < len(STEPS):
            name, e, v, react, size = STEPS[i]
            print(f"{time.strftime('%H:%M:%S')} feeling {name}", flush=True)
            a.set_affect(e, v, 3)
            if react:
                a.emote(react, size)
            QTimer.singleShot(650, lambda n=name: shot(n))       # near the reaction's peak
            state["i"] += 1
            QTimer.singleShot(3000, poll)
            return
        if i == len(STEPS):
            print(f"{time.strftime('%H:%M:%S')} walk", flush=True)
            a.walk_to(screen.left() + screen.width() // 4, screen.bottom())
            QTimer.singleShot(900, lambda: shot("walk1"))
            QTimer.singleShot(1150, lambda: shot("walk2"))
            state["i"] += 1
            QTimer.singleShot(4500, poll)
            return
        a.view.page().runJavaScript("JSON.stringify({renders: window.__renders || 0, presence: window.EVA_PRESENCE})",
                                    lambda r: (print("page:", r, flush=True), app.quit()))

    QTimer.singleShot(500, poll)
    QTimer.singleShot(60000, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
