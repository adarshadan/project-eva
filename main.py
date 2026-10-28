"""
Eva -- entry point and composition root. Wiring only: every decision lives in core/, and the
wiring of each feature lives in eva_app/ (one module per feature, each with install(ctx)).

    core/registry.py + core/toolset/   every capability, declared once
    core/agent.py                      the conversation and the one dispatch path
    core/runtime.py                    front lane (instant) + task lane (one worker)
    core/speaker.py                    her voice, on its own thread
    core/llm.py, core/prompt.py        the model client and the system prompt
    eva_app/                           the application's features, installed in order below

Thread rule: Qt widgets only on the GUI thread (bridge.run_sync / bridge.fire);
native desktop automation never on it.
"""
import os
# Before QApplication exists: stops Qt re-asserting its own DPI awareness on
# top of what pyautogui/pywinauto set at import (the "Access is denied" warning).
os.environ.setdefault("QT_QPA_PLATFORM", "windows:dpiawareness=1")

import sys

import logger  # file+console logging and crash hooks; import first
log = logger.get_logger(__name__)

import config  # noqa: E402

from eva_app.startup import (CORE_MODULES, RESTART_EXIT_CODE, startup_self_test,  # noqa: E402,F401
                             _check_stage_padding)


def main():
    from eva_app import startup
    startup.prepare()                    # crash reports, renderer flags, every module imports

    # avatar_ui (and with it Qt WebEngine) must be imported before the QApplication exists.
    from PySide6.QtWidgets import QApplication
    from avatar_ui import AvatarWidget, RadialMenu
    from core.gui import GuiBridge
    import providers
    from eva_app.context import Ctx
    from eva_app import (status, feeling, brain, helpers, activities, browser, mic, housekeeping, reach, lifecycle,
                         tray, own_windows, schedule, life, welcome)

    app = QApplication(sys.argv)
    # Her own windows (body, chat, bubbles) are tool windows, which Qt does not count: closing
    # Settings or Activity -- the only "normal" windows -- ended Eva (2026-10-17 13:20, live test).
    # She quits only when asked (ring menu or tray > Quit Eva).
    app.setQuitOnLastWindowClosed(False)
    startup.install_qt_logging()

    avatar = AvatarWidget()
    avatar.show()
    menu = RadialMenu(avatar)
    bridge = GuiBridge()
    log.info(f"Avatar geometry: widget={avatar.size().width()}x{avatar.size().height()} "
             f"devicePixelRatio={QApplication.primaryScreen().devicePixelRatio()}")

    key_missing = not any(providers.usable(r)[0] for r in [config.MODEL_ID, *getattr(config, "MODEL_FALLBACKS", ())])
    if key_missing:
        log.warning("NVIDIA_API_KEY is not set. Tray icon > Set API key (stored encrypted), or run set_key.bat.")

    ctx = Ctx(app=app, avatar=avatar, menu=menu, bridge=bridge, key_missing=key_missing)
    # The order is the dependency order: each feature uses what the ones before it put on ctx.
    for feature in (status,          # status line, her face while working        -> show_text, on_state
                    feeling,         # her feeling: energy and mood shown by her whole body -> feeling
                    brain,           # gate, agent, voice, history, runtime         -> agent, speaker, runtime
                    helpers,         # background helpers
                    activities,      # repeats/games, presence tools, recordings, skills, services
                    browser,         # her browser: body language, money gate, human-check hand-off
                    mic,             # microphone                                    -> start_listening
                    housekeeping,    # on top, garbage collection, vitals, watchdog  -> vitals
                    reach,           # click, chat box, hotkeys, file index         -> region_picker
                    lifecycle,       # restart, admin restart, WebGL fallback       -> restart
                    tray,            # ring menu + tray menu, settings              -> tray
                    own_windows,     # her windows: masked for vision, out of the way of clicks
                    schedule,        # reminders and scheduled tasks
                    life,            # idle life, predictive help, suggestions, commentary
                    welcome):        # the first-run welcome tour                   -> open_welcome
        feature.install(ctx)
    # Every tool is bound by now -- the last binding happens inside the features above --
    # so this check sees them all (open_settings was once reported unbound: checked too early).
    startup_self_test(after_binding=True)

    lifecycle.start(ctx)                 # runtime, greeting, microphone, goodbye on quit
    app._eva_timers = tuple(ctx.timers)  # every timer stays referenced for the life of the app
    startup.finish(app, app.exec())      # the relaunch a Restart asked for, or the exit


if __name__ == "__main__":
    main()
