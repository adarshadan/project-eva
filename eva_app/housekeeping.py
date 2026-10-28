"""Long-running health: staying above other windows without flicker, garbage collection on the
GUI thread, process vitals, the GUI watchdog, the one-time flicker measurement, lip sync."""
import gc
import time

import logger

log = logger.get_logger("__main__")


def install(ctx):
    import avatar_ui as _aui
    import idle_behavior
    import voice
    from PySide6.QtCore import QTimer
    avatar, bridge = ctx.avatar, ctx.bridge

    # One OCR engine for the session, created now so her first read of the screen is fast.
    import threading as _th
    import screen_text as _st
    _th.Thread(target=_st.warm, daemon=True, name="eva-ocr-warm").start()

    # Stay above other windows. Only when a window that is not hers actually
    # sits above her and overlaps her, or her topmost flag was lost, are her
    # windows re-ordered (one atomic batch, no focus taken). Re-ordering on
    # every foreground change, and raising the full-screen overlay with her,
    # made the compositor redraw her for nothing -- seen as flicker.
    zorder = {"against": None, "count": 0, "since": 0.0}
    zorder_err = {}

    def zorder_tick():
        try:
            # A full-screen video or game in front: do not fight it for the top.
            if idle_behavior.is_fullscreen_app_active():
                return
            lost = _aui.topmost_lost()
            cover = None if lost else _aui.covering_window()
            if not (lost or cover):
                return
            # A video or Picture-in-Picture window over her: let it be (media.py).
            # Two always-on-top windows re-raising each other over a playing
            # video is exactly where she kept crashing.
            import media
            if cover and media.is_video_title(media.window_title(cover)):
                return
            # Another always-on-top window that raises itself again (the Start
            # menu, a game overlay) would turn this into a tug of war, which
            # is flicker of its own: after 3 tries against the same window in
            # 10 s, leave it alone until something else changes.
            now = time.monotonic()
            if cover and cover == zorder["against"] and now - zorder["since"] < 10:
                zorder["count"] += 1
                if zorder["count"] > 3:
                    return
            else:
                zorder.update(against=cover, count=1, since=now)
            _aui.keep_on_top()
            zorder_err.clear()
        except Exception as e:
            # Twice a second: the same failure is logged in full once, then
            # counted (eva.log 2026-10-11 13:50 had six identical tracebacks).
            key = f"{type(e).__name__}: {e}"[:160]
            if zorder_err.get("key") != key:
                zorder_err.update(key=key, n=0)
                log.exception("keep_on_top failed -- will retry")
            else:
                zorder_err["n"] += 1
                if zorder_err["n"] in (10, 100, 1000):
                    log.warning(f"keep_on_top still failing ({zorder_err['n']} more times): {key}")
    # Garbage collection on the GUI thread only (2026-10-11 15:34 crash: an
    # access violation in Qt6Core.dll on the TASK thread, at the instant it
    # called into the reach -- no Python frame of its own, which is what a
    # cyclic garbage collection looks like when it runs on whatever thread
    # happens to allocate and destroys a Qt object there). Python's automatic
    # collector is switched off; this timer runs it here, where Qt objects
    # may be destroyed. Reference counting still frees everything else at once.
    gc.disable()
    gc_state = {"n": 0}
    # Long-session health (core/vitals.py): one sample every 5 minutes, warns on steady growth.
    from core import vitals as _vitals
    _vitals.VITALS["v"] = _vitals.Vitals().start()

    def collect_garbage():
        gc_state["n"] += 1
        n = gc_state["n"]
        full = _vitals.FULL_GC_REQUESTED.is_set()
        _vitals.FULL_GC_REQUESTED.clear()
        gc.collect(2 if full or n % 60 == 0 else 1 if n % 10 == 0 else 0)

    gc_timer = QTimer()
    gc_timer.timeout.connect(collect_garbage)
    gc_timer.start(1000)
    QTimer.singleShot(20000, gc.freeze)          # start-up objects: never scanned again

    zorder_timer = QTimer()
    zorder_timer.timeout.connect(zorder_tick)
    zorder_timer.start(500)

    # Where the GUI thread hangs, if it ever does (see gui_watchdog.py).
    import gui_watchdog
    gui_watchdog.install(ctx.gui_timer)

    # Measure the flicker once, on the real screen, while she is idle
    # (flicker_probe.py); the number goes to eva.log.
    import flicker_probe
    # The probe runs on its own thread: it may only ask the GUI thread about
    # the avatar, never touch the widget itself.
    flicker_probe.run(lambda: bridge.run_sync(lambda: (avatar.x(), avatar.y(), avatar.width(), avatar.height()),
                                              timeout=5),
                      lambda: bool(bridge.run_sync(lambda: avatar._walk_target is not None or avatar.isHidden(),
                                                   timeout=5)))

    # Mouth and head follow whether audio is actually playing (another process).
    speech_poll = QTimer()
    speech_poll.timeout.connect(lambda: avatar.set_speaking(voice.is_speaking()))
    speech_poll.start(120)

    ctx.timers += [gc_timer, zorder_timer, speech_poll]
    ctx.vitals = _vitals
