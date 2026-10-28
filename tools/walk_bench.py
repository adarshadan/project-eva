"""How smooth is her walk? The real avatar walks across the screen and back; every window step is timed.

2026-10-28 "her motion seems a bit laggy when she moves". Opens the real AvatarWidget (as Eva does), waits
for the model, walks it along the taskbar right, left, right, and records each step of the window: the
interval between steps (a late one is a hitch) and the distance (a big one is a jump), plus the frames the
3-D scene drew. Optionally with Python busy in another thread, as when she is thinking (--load).

    python tools/walk_bench.py              both ways: the old fixed steps and the new smooth walk
    python tools/walk_bench.py --load       the same, with a busy background thread
Writes tools/walk_bench.txt.
"""
import math
import os
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
OUT = os.path.join(HERE, "tools", "walk_bench.txt")


def run(mode, load):
    import config
    config.SMOOTH_MOTION = (mode == "smooth")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import avatar_ui
    app = QApplication.instance() or QApplication(sys.argv)
    a = avatar_ui.AvatarWidget()
    a.show()
    steps = []
    real_move = a.move

    def move(*args):
        real_move(*args)
        if a._walk_target is not None:
            steps.append((time.perf_counter(), a.x(), a.y()))
    a.move = move
    result = {}
    state = {"phase": 0, "ready": False, "renders0": 0}
    stop_load = threading.Event()

    def busy():                                   # like streaming a model reply: pure-Python work in bursts
        while not stop_load.is_set():
            t = time.perf_counter()
            while time.perf_counter() - t < 0.03:
                sum(i * i for i in range(2000))
            time.sleep(0.01)
    if load:
        threading.Thread(target=busy, daemon=True).start()

    screen = QApplication.primaryScreen().availableGeometry()
    xs = [screen.left() + int(screen.width() * 0.75), screen.left() + int(screen.width() * 0.25),
          screen.left() + int(screen.width() * 0.75)]

    def got_renders(v, key):
        result[key] = v or 0

    def poll():
        if not state["ready"]:
            a.view.page().runJavaScript("window.EVA_READY === true", lambda r: state.__setitem__("ready", bool(r)))
            QTimer.singleShot(300, poll)
            return
        if state["phase"] == 0:
            a.move(xs[0] - a.width() // 2, screen.bottom() - a.height())
            state["phase"] = 1
            QTimer.singleShot(1500, poll)
            return
        if state["phase"] in (1, 2, 3):
            if a._walk_target is None:
                if state["phase"] == 1:
                    a.view.page().runJavaScript("window.__renders || 0", lambda v: got_renders(v, "r_start"))
                    state["started"] = time.perf_counter()
                a.walk_to(xs[state["phase"] - 1], screen.bottom())
                state["phase"] += 1
            QTimer.singleShot(200, poll)
            return
        if a._walk_target is None:
            a.view.page().runJavaScript("window.__renders || 0", lambda v: got_renders(v, "r_end"))
            state["ended"] = time.perf_counter()
            QTimer.singleShot(400, app.quit)
            return
        QTimer.singleShot(200, poll)

    QTimer.singleShot(500, poll)
    QTimer.singleShot(90000, app.quit)
    app.exec()
    stop_load.set()
    a.hide()
    return steps, result, state


def summarise(mode, load, steps, result, state):
    if len(steps) < 3:
        return f"{mode:<7} load={load}: too few steps ({len(steps)})"
    gaps = [(b[0] - a[0]) * 1000 for a, b in zip(steps, steps[1:])]
    jumps = [math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(steps, steps[1:])]
    gaps_s = sorted(gaps)
    p95 = gaps_s[int(len(gaps_s) * 0.95) - 1]
    renders = result.get("r_end", 0) - result.get("r_start", 0)
    secs = state.get("ended", 0) - state.get("started", 0) if state.get("ended") else 0
    hitches = sum(1 for g in gaps if g > 50)
    return (f"{mode:<7} load={str(load):<5} steps {len(steps):4d}  interval mean {statistics.mean(gaps):5.1f} ms  "
            f"p95 {p95:5.1f}  max {max(gaps):6.1f}  hitches>50ms {hitches:3d}  jump mean {statistics.mean(jumps):4.1f} px "
            f"max {max(jumps):4.0f}  scene fps ~{(renders / secs) if secs else 0:4.0f}")


def main(argv):
    if len(argv) >= 2 and argv[0] == "--one":                      # child process: one mode
        mode, load = argv[1], argv[2] == "1"
        steps, result, state = run(mode, load)
        print("RESULT " + summarise(mode, load, steps, result, state), flush=True)
        return
    import subprocess
    lines = [f"walk_bench {time.strftime('%Y-%m-%d %H:%M')}"]
    loads = [False, True] if "--load" in argv else [False]
    for load in loads:
        for mode in ("legacy", "smooth"):
            p = subprocess.run([sys.executable, os.path.abspath(__file__), "--one", mode, "1" if load else "0"],
                               capture_output=True, text=True, timeout=150)
            got = [ln[7:] for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
            lines.append(got[0] if got else f"{mode} load={load}: no result ({p.stderr[-300:]})")
            with open(OUT, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
    lines.append("DONE")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
