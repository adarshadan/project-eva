"""Where Eva's own windows are (so vision masks them and clicks never land on them), and moving
them out of the way of a click; the vision calibration and desktop notifications."""
import logger

log = logger.get_logger("__main__")


def install(ctx):
    import desktop_control as dc
    import system_tools
    from PySide6.QtCore import QTimer
    app, avatar, bridge = ctx.app, ctx.avatar, ctx.bridge

    # Where Eva's own windows are, in screen pixels, for the vision cache to mask
    # (vision.SELF_RECTS). Read on the GUI thread; the task thread only reads the list.
    import vision as _vision

    def note_own_windows():
        try:
            rects = []
            for w in app.topLevelWidgets():
                if not w.isVisible():
                    continue
                g = w.frameGeometry()
                k = w.screen().devicePixelRatio() if w.screen() else 1.0
                rects.append((int(g.left() * k) - 4, int(g.top() * k) - 4,
                              int((g.right() + 1) * k) + 4, int((g.bottom() + 1) * k) + 4))
            _vision.SELF_RECTS[:] = rects
            g = avatar.frameGeometry()
            k = avatar.screen().devicePixelRatio() if avatar.screen() else 1.0
            _vision.AVATAR_RECT[0] = (int(g.left() * k), int(g.top() * k), int((g.right() + 1) * k),
                                      int((g.bottom() + 1) * k))
        except Exception:
            pass

    def make_way(x, y):
        """A click or keystroke target at screen pixel (x, y) is under one of Eva's own windows
        (eva.log 2026-10-17 12:19: typing meant for Notepad went into her chat box): move them off it."""
        def work():
            for w in app.topLevelWidgets():
                if not w.isVisible():
                    continue
                k = w.screen().devicePixelRatio() if w.screen() else 1.0
                px, py = int(x / k), int(y / k)
                g = w.frameGeometry()
                if not g.adjusted(-6, -6, 6, 6).contains(px, py):
                    continue
                if w is avatar:
                    avatar.keep_clear_of((px - 24, py - 24, px + 24, py + 24))
                    continue
                scr = (w.screen() or app.primaryScreen()).availableGeometry()
                nx = px - g.width() - 24 if px - scr.left() > scr.right() - px else px + 24
                w.move(max(scr.left(), min(nx, scr.right() - g.width())), g.top())
            note_own_windows()
        bridge.run_sync(work, timeout=5)
    dc.MAKE_WAY = make_way

    ctx.gui_timer(400, note_own_windows)
    # Measure how the vision model writes coordinates, once per model (vision.calibrate).
    QTimer.singleShot(25000, lambda: _vision.calibrate_in_background())
    system_tools.set_notifier(lambda title, message: bridge.fire(lambda: ctx.tray.showMessage(title, message)))
