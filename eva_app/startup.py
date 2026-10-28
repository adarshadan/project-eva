"""Before the Qt application exists, and after it ends: crash reporting, renderer flags, the
start-up self test, and the relaunch for Restart / Restart as administrator."""
import os
import sys
import threading

import config
import logger

log = logger.get_logger("__main__")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESTART_EXIT_CODE = 75        # run.bat: "start me again"

CORE_MODULES = ("core.registry", "core.effects", "core.toolset", "core.agent", "core.runtime", "core.workers",
                "core.speaker", "core.feeling", "stt_nvidia", "tts_nvidia", "core.llm", "core.prompt", "core.presence_impl", "core.gui",
                "core.skills", "core.learning", "core.connections", "core.web_reach", "core.checkpoints", "core.activity",
                "core.elevation", "core.longform", "crash_probe", "media", "render_setup", "chat_ui", "chat_store",
                "capabilities", "permissions", "finance_guard", "system_guard", "providers", "command_policy", "audit", "memory",
                "ui_navigate", "embodiment", "avatar_renderer", "backend_tools", "voice", "vision",
                "desktop_control", "browser_agent", "browser_link", "patrol", "system_tools", "scheduler", "recorder",
                "idle_behavior", "overlay_effects", "gui_watchdog", "flicker_probe", "ring_layout", "ubiquity", "life",
                "everyday_tools", "file_index", "avatar_ui", "deadline", "app_resolver", "onboarding", "welcome_ui",
                "core.task_card",
                "eva_app.context", "eva_app.status", "eva_app.brain", "eva_app.helpers", "eva_app.activities",
                "eva_app.browser", "eva_app.mic", "eva_app.housekeeping", "eva_app.reach", "eva_app.lifecycle",
                "eva_app.tray", "eva_app.own_windows", "eva_app.schedule", "eva_app.life",
                "eva_app.welcome")


def _check_stage_padding():
    """avatar_ui.py and avatar_3d.html must agree on STAGE_PAD_X/Y, or the
    character renders in the wrong place with no error."""
    import re
    import avatar_ui
    try:
        html = open(os.path.join(HERE, "web", "avatar_3d.html"), encoding="utf-8").read()
    except OSError as e:
        return [f"could not read web/avatar_3d.html: {e}"]
    m = re.search(r"const\s+STAGE_PAD_X\s*=\s*([\d.]+)\s*,\s*STAGE_PAD_Y\s*=\s*([\d.]+)", html)
    if not m:
        return ["STAGE_PAD_X/Y not found in web/avatar_3d.html"]
    js, py = (float(m.group(1)), float(m.group(2))), (avatar_ui.STAGE_PAD_X, avatar_ui.STAGE_PAD_Y)
    return [] if js == py else [f"STAGE_PAD mismatch: avatar_ui.py {py} vs avatar_3d.html {js}"]


def startup_self_test(after_binding=False):
    """Reports, at startup, anything that would otherwise surface later as a
    puzzling failure: a module that will not import, a missing dependency, a
    tool with no implementation."""
    import importlib
    problems = []
    if not after_binding:
        for name in CORE_MODULES:
            try:
                importlib.import_module(name)
            except Exception as e:
                problems.append(f"{name}: {type(e).__name__}: {e}")
        for dep, why in (("PIL", "vision/screenshots"), ("pyperclip", "clipboard"),
                         ("mss", "screen capture"), ("pyautogui", "mouse/keyboard")):
            try:
                importlib.import_module(dep)
            except Exception as e:
                problems.append(f"{dep} ({why}) is not installed: {e}")
        problems += _check_stage_padding()
    else:
        from core import registry
        problems += registry.validate()
    for p in problems:
        log.error(f"STARTUP CHECK FAILED -- {p}")
    if not problems:
        log.info("Startup check passed." if after_binding else "Startup check: all modules import cleanly.")
    return problems


def _disable_console_quickedit():
    """A click inside the console enters Select mode and blocks every write to
    it -- and logging writes to it under a lock, so every thread stalls and Eva
    looks dead. Turning QuickEdit off removes that trap."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
    except Exception as e:
        log.warning(f"Could not disable console QuickEdit: {e}")


def install_qt_logging():
    try:
        from PySide6.QtCore import qInstallMessageHandler
        last = {"m": None}

        def handler(mode, ctx, message):
            if message == last["m"]:
                return
            last["m"] = message
            level = {1: "warning", 2: "error", 3: "error", 4: "info"}.get(int(mode))
            if level:
                getattr(log, level)(f"[Qt] {message}")
        qInstallMessageHandler(handler)
    except Exception as e:
        log.warning(f"Qt message logging unavailable: {e}")


def prepare():
    """Everything before QApplication: crash diagnostics, renderer flags, the import check."""
    logger.install_crash_diagnostics()
    _disable_console_quickedit()
    unclean = logger.previous_run_report()
    if unclean:
        log.warning(f"CRASH REPORT: {unclean}")
    # Every start: crashes Windows recorded that eva.log has not shown yet --
    # module, offset and the function there (crash_probe.py).
    import crash_probe
    threading.Thread(target=crash_probe.report_new_crashes, name="eva-crash-probe", daemon=True).start()
    logger.mark_running()
    log.info("Eva starting up.")
    flags = (config.WEBENGINE_FLAGS + " " + config.RENDER_MODE_FLAGS.get(config.RENDER_MODE, "")).strip()
    if flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " " + flags).strip()
    from render_setup import apply_qt_graphics
    api = apply_qt_graphics(config)
    if getattr(config, "RENDER_FALLBACK", None):
        log.warning(f"This start uses software drawing: {config.RENDER_FALLBACK['reason']} "
                    "(one start only; the next normal start tries the GPU again).")
    log.info(f"Renderer mode: {config.RENDER_MODE}; Chromium flags: {flags or '(none)'}; "
             f"Qt graphics: {api}; preserve buffer: {getattr(config, 'AVATAR_PRESERVE_BUFFER', True)}; "
             f"fps: {getattr(config, 'AVATAR_FPS', 30)}"
             + (f"; from ~/.eva/render.json: {config.RENDER_OVERRIDE}" if getattr(config, "RENDER_OVERRIDE", None) else ""))
    startup_self_test()


def finish(app, code):
    """After the Qt loop ends: the relaunch a Restart asked for, or the plain exit."""
    if getattr(app, "_eva_restart", False) and getattr(app, "_eva_elevate", False) and sys.platform == "win32":
        import ctypes
        import subprocess
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, subprocess.list2cmdline(sys.argv),
                                                 HERE, 1)
        if rc > 32:
            log.info("Restarting as administrator (approved).")
            logger.mark_clean_exit()
            sys.exit(0)
        log.info(f"Restart as administrator not approved (ShellExecute {rc}) -- starting again as before.")
    if getattr(app, "_eva_restart", False):
        logger.mark_clean_exit()                 # a restart is not a crash
        if os.environ.get("EVA_LAUNCHER") == "run.bat":
            sys.exit(RESTART_EXIT_CODE)          # run.bat starts Eva again once this process is gone
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv, cwd=HERE)
        sys.exit(0)
    sys.exit(code)
