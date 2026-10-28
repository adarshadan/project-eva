"""Eva flicker bench: which render setting stops the avatar flickering on THIS PC?

The 2026-10-11 screen recording showed the character missing from about one
frame in six, in bursts of 1-3 frames, while the separately painted status
label never flickered: the 3-D stage presents empty frames. Several settings
can cause or cure that (how Qt composes the window, how Chromium draws,
whether the canvas keeps its last image, the frame rate), and which one works
depends on the GPU and driver -- so this measures instead of guessing.

For each setting a fresh process (the settings must be in place before Qt
starts) opens the real avatar stage exactly as Eva does, waits for the model,
then photographs the screen where she stands 15 times a second for 6 seconds
and counts the frames where she is missing (flicker_probe.missing_frames).

    python tools/flicker_bench.py            # all settings, saves the winner
    python tools/flicker_bench.py --no-save  # measure only

The winner is written to ~/.eva/render.json only if it is clearly better than
Eva's current setting; config.py reads that file at start-up.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# (label, RENDER_MODE, QT_GRAPHICS_API, AVATAR_PRESERVE_BUFFER, AVATAR_FPS, QT_COMPOSITION)
# QT_COMPOSITION "software" only takes effect with RENDER_MODE "software"
# (render_setup.py): with a GPU mode it would switch WebGL off.
NEW_DEFAULT = "keep last image"
VARIANTS = [
    ("previous build",               "default",     "",       False, 30, "gpu"),
    (NEW_DEFAULT,                    "default",     "",       True,  30, "gpu"),
    ("keep last image, 60 fps",      "default",     "",       True,  60, "gpu"),
    ("Qt on OpenGL",                 "default",     "opengl", True,  30, "gpu"),
    ("Chromium ANGLE D3D11",         "angle_d3d11", "",       True,  30, "gpu"),
    ("no GPU compositing",           "no_gpu_comp", "",       True,  30, "gpu"),
    ("software (slow, reference)",   "software",    "",       True,  30, "software"),
]
CHILD_TIMEOUT = 75


def child(mode, api, pdb, fps, comp="gpu"):
    """Runs in its own process: one setting, one measurement, one JSON line."""
    import config
    flags = (config.WEBENGINE_FLAGS + " " + config.RENDER_MODE_FLAGS.get(mode, "")).strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
    import types
    sys.path.insert(0, ROOT)
    from render_setup import apply_qt_graphics
    apply_qt_graphics(types.SimpleNamespace(QT_GRAPHICS_API=api, QT_COMPOSITION=comp, RENDER_MODE=mode))
    from PySide6.QtCore import QMetaObject, Qt, QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    import avatar_ui
    import flicker_probe

    stage = avatar_ui.DesktopStage(lambda: None)
    web = os.environ.get("EVA_BENCH_WEB") or avatar_ui.WEB_DIR     # override only for testing the bench
    url = QUrl.fromLocalFile(os.path.join(web, "avatar_3d.html"))
    url.setQuery(f"pdb={1 if pdb else 0}&fps={fps}")
    stage.view.load(url)
    w, h = 200, 380                                    # Eva's hit box at 100 % scale
    sw, sh = int(w * avatar_ui.STAGE_PAD_X), int(h * avatar_ui.STAGE_PAD_Y)
    geo = app.primaryScreen().availableGeometry()
    sx, sy = geo.right() - sw - 40, geo.bottom() - sh
    stage.setGeometry(sx, sy, sw, sh)
    box = (sx + (sw - w) // 2, sy + (sh - h) // 2, w, h)
    # The desktop where she will stand, photographed BEFORE she appears: a
    # frame that looks like this is a frame she was missing from. (Comparing
    # against the background, not against her usual image, means a setting
    # that never draws her at all cannot score as "no flicker".)
    import mss
    with mss.mss() as sct:
        background = flicker_probe._grab_grey(sct, flicker_probe.body_rect(*box))
    state = {"start": time.monotonic(), "measuring": False}
    stage.show()
    result = {"ok": False, "error": "the avatar did not load within 45 s"}

    def measure():
        try:
            _measure()
        except Exception as e:
            result.update(ok=False, error=f"measuring failed: {e}")
        # from this worker thread: a queued call, which the GUI thread runs
        QMetaObject.invokeMethod(app, "quit", Qt.QueuedConnection)

    def _measure():
        import mss
        rect = flicker_probe.body_rect(*box)
        frames = []
        with mss.mss() as sct:
            for _ in range(flicker_probe.SAMPLES):
                frames.append(flicker_probe._grab_grey(sct, rect))
                time.sleep(flicker_probe.INTERVAL)
        n = len(background)
        drawn = [sum(abs(a - b) for a, b in zip(f, background)) / n > flicker_probe.MISSING_DIFF / 2
                 for f in frames]
        result.update(ok=True, missing=drawn.count(False), frames=len(frames), drawn=drawn.count(True), error="")

    def poll():
        if state["measuring"]:
            return
        if time.monotonic() - state["start"] > 45:
            app.quit()
            return

        def ready(v):
            if v and not state["measuring"]:
                state["measuring"] = True
                import threading
                QTimer.singleShot(3000, lambda: threading.Thread(target=measure, daemon=True).start())
        stage.view.page().runJavaScript("!!window.EVA_READY", ready)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(500)
    app.exec()
    print("BENCH_RESULT " + json.dumps(result), flush=True)


def run_variant(v):
    label, mode, api, pdb, fps, comp = v
    cmd = [sys.executable, os.path.abspath(__file__), "--child", mode, api or "-", "1" if pdb else "0", str(fps), comp]
    try:
        # The test windows log to their own file, not Eva's eva.log.
        env = dict(os.environ, EVA_LOG_PATH=os.path.join(ROOT, "eva-flicker-bench.log"))
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=CHILD_TIMEOUT, cwd=ROOT, env=env)
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "did not finish (hung)"}
    for line in out.splitlines():
        if line.startswith("BENCH_RESULT "):
            return json.loads(line[len("BENCH_RESULT "):])
    return {"ok": False, "error": f"crashed (exit {p.returncode}): {out.strip()[-160:]}"}


def choose(results):
    """The best usable variant: drawn in most frames, fewest missing frames;
    software only wins if nothing else is close (it is slow)."""
    usable = [(v, r) for v, r in results if r.get("ok") and r["frames"] and r["drawn"] >= 0.5 * r["frames"]]
    if not usable:
        return None
    pct = lambda r: 100.0 * r["missing"] / r["frames"]          # noqa: E731
    best = min(usable, key=lambda vr: (pct(vr[1]) + (2.0 if vr[0][1] == "software" else 0.0)
                                       ))
    return best


def main(argv):
    if argv[:1] == ["--child"]:
        mode, api, pdb, fps = argv[1], ("" if argv[2] == "-" else argv[2]), argv[3] == "1", int(argv[4])
        child(mode, api, pdb, fps, argv[5] if len(argv) > 5 else "gpu")
        return 0
    save = "--no-save" not in argv
    print("Eva flicker test -- keep other windows away from the bottom-right corner.\n")
    results = []
    for v in VARIANTS:
        print(f"  {v[0]:<32}", end="", flush=True)
        r = run_variant(v)
        results.append((v, r))
        if r.get("ok"):
            print(f"{100.0 * r['missing'] / r['frames']:5.1f} % of frames missing"
                  + ("" if r["drawn"] >= 0.5 * r["frames"] else "   (mostly NOT DRAWN -- unusable)"))
        else:
            print(f"  failed: {r.get('error')}")
    report = os.path.join(ROOT, "eva-flicker-report.txt")
    with open(report, "w", encoding="utf-8") as f:
        for v, r in results:
            f.write(json.dumps({"variant": v[0], "RENDER_MODE": v[1], "QT_GRAPHICS_API": v[2],
                                "AVATAR_PRESERVE_BUFFER": v[3], "AVATAR_FPS": v[4], "QT_COMPOSITION": v[5],
                                **r}) + "\n")
    best = choose(results)
    if not best:
        print("\nNo setting drew her reliably. Send eva-flicker-report.txt.")
        return 1
    (label, mode, api, pdb, fps, comp), r = best
    pct = 100.0 * r["missing"] / r["frames"]
    print(f"\nBest: {label} ({pct:.1f} % missing).")
    current = next((res for v, res in results if v[0] == NEW_DEFAULT), None)
    cur_pct = 100.0 * current["missing"] / current["frames"] if current and current.get("ok") else 100.0
    if save and (pct + 3.0 <= cur_pct or label == NEW_DEFAULT):
        path = os.path.join(os.path.expanduser("~"), ".eva", "render.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"RENDER_MODE": mode, "QT_GRAPHICS_API": api, "QT_COMPOSITION": comp, "AVATAR_PRESERVE_BUFFER": pdb,
                       "AVATAR_FPS": fps, "measured": f"{pct:.1f}% missing", "variant": label}, f, indent=2)
        print(f"Saved to {path}; Eva uses it from her next start.")
    else:
        print("Not saved (no clear improvement over the new default).")
    print("Report: eva-flicker-report.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
