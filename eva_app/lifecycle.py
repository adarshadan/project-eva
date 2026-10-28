"""Restart, restart as administrator, the software-drawing fallback, start-up and goodbye."""
import os
import random
import signal
import threading
import time

import config
import logger

log = logger.get_logger("__main__")

WELCOME = ["Hello! I'm Eva, ready when you are.", "Hi there! Good to see you.", "Welcome back!"]
GOODBYE = ["Goodbye!", "See you soon!", "Take care!"]


def install(ctx):
    """restart / restart_elevated (used by the ring menu, the tray and the WebGL fallback)."""
    import avatar_ui as _aui
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from core import registry
    app, bridge = ctx.app, ctx.bridge

    def on_webgl_failed():
        """The page could not create WebGL, so she is invisible. Once: restart
        with software drawing (SwiftShader always works, just slower) and say
        so; if that fails too, say what to do instead of showing nothing."""
        log.error("WebGL could not be created -- the avatar cannot be drawn with the current renderer "
                  f"(RENDER_MODE={config.RENDER_MODE}, Qt: {getattr(config, 'QT_GRAPHICS_API', '') or 'default'}).")
        if getattr(config, "RENDER_FALLBACK", None):
            ctx.tray.showMessage("Eva cannot draw herself",
                                 "Even software drawing failed. Run doctor.bat and send eva-doctor-report.txt and eva.log.")
            return
        try:
            import json as _json
            path = os.path.join(os.path.expanduser("~"), ".eva", "render-fallback.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                _json.dump({"reason": f"WebGL failed with RENDER_MODE={config.RENDER_MODE}",
                            "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        except OSError:
            log.exception("Could not write the render fallback")
            return
        log.warning("Restarting once with software drawing.")
        QTimer.singleShot(300, restart)

    _aui.WEBGL_FAILED["cb"] = lambda: on_webgl_failed()

    def restart():
        """Restart cleanly: every window disappears AT ONCE, Qt and its web
        engine shut down completely, and only then does the new Eva start.

        It used to os.execv() straight away. On Windows that starts the new
        process and leaves the old one to die on its own, so the old avatar
        window hung on screen as a black box while the new one appeared
        (2026-10-11 screenshot). Now: hide everything, quit the Qt loop, and
        exit with RESTART_EXIT_CODE (startup.finish) -- run.bat starts Eva again;
        launched any other way, the new process is started after Qt is gone."""
        log.info("Restart requested -- closing this Eva first.")
        app._eva_restart = True
        for w in list(QApplication.topLevelWidgets()):
            try:
                w.hide()
            except Exception:
                pass
        app.quit()

    def restart_elevated():
        """Restart as administrator, so she can act on administrator windows (Epic Games
        Launcher, WizTree -- eva.log 2026-10-11 22:51-23:02). Windows asks the person to
        approve (UAC); declined, she simply starts again as before."""
        app._eva_elevate = True
        restart()

    def restart_elevated_tool():
        from core import elevation as _elev
        if _elev.self_elevated():
            return {"ok": True, "note": "I am already running as administrator"}
        bridge.fire(restart_elevated)
        return {"ok": True, "restarting": True,
                "note": "Windows will ask the person to approve (a UAC prompt); after that I start again"}
    registry.bind("restart_as_administrator", restart_elevated_tool)

    ctx.restart, ctx.restart_elevated = restart, restart_elevated


def start(ctx):
    """The last step: the runtime starts, she greets, listens, and says goodbye on quit."""
    import browser_agent
    import desktop_control as dc
    import voice
    from PySide6.QtCore import QTimer
    app, avatar, speaker, runtime = ctx.app, ctx.avatar, ctx.speaker, ctx.runtime

    def shutdown_cleanup():
        try:
            import browser_link
            browser_link.stop()
        except Exception:
            pass
        for step in (lambda: avatar.click_forwarder and avatar.click_forwarder.stop(),
                     speaker.shutdown, voice.shutdown_player, browser_agent.close_browser,
                     dc.UIA_SERVER.stop):
            try:
                step()
            except Exception:
                log.exception("Cleanup step failed -- continuing.")

    # ---- start ----------------------------------------------------------------------------
    runtime.start()

    def welcome():
        avatar.play("wave", loop=False)
        if getattr(ctx, "welcome_due", False):
            speaker.say("Hello! I'm Eva. Let me show you around -- it takes about a minute.")
        elif ctx.key_missing:
            speaker.say("I don't have an API key yet. Right-click my tray icon and choose Set API key.")
        else:
            speaker.say(random.choice(WELCOME))
    QTimer.singleShot(800, welcome)
    threading.Thread(target=ctx.start_listening, daemon=True).start()

    def goodbye():
        log.info("Shutting down.")
        # The goodbye below waits for speech on this thread by design; that is
        # not a stall worth a stack report (eva.log 2026-10-11 11:25:10).
        import gui_watchdog
        gui_watchdog.shutdown()
        try:
            voice.stop_always_listening(wait_for_stop=True)
            speaker.stop()
            if not getattr(app, "_eva_restart", False):        # a restart is not a goodbye
                avatar.play("p_wave", loop=False)
                speaker.say(random.choice(GOODBYE))
                speaker.wait_idle(timeout=6)
        except Exception:
            log.exception("Error during the goodbye -- cleaning up regardless.")
        finally:
            shutdown_cleanup()
            if getattr(ctx.file_index, "INDEX", None) is not None:
                ctx.file_index.INDEX.close()
    app.aboutToQuit.connect(goodbye)

    # Ctrl+C reaches Qt's loop promptly on Windows only with a ticking timer.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    ctx.gui_timer(200, lambda: None)
