"""The ring menu around her and the tray menu: chat, mic, stop, pause, status, failed tasks,
activity and memory, skills to review, settings, API key, restart, quit."""
import os
import sys
import time

import config
import logger

log = logger.get_logger("__main__")


def install(ctx):
    import browser_agent
    import scheduler
    import settings_store
    import ubiquity
    import voice
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon
    from core import registry
    from core.connections import CONNECTIONS
    from core.skills import LIBRARY, user_root
    app, avatar, bridge, menu = ctx.app, ctx.avatar, ctx.bridge, ctx.menu
    runtime, agent, mic, speaker_status = ctx.runtime, ctx.agent, ctx.mic, ctx.speaker_status
    in_thread, restart, restart_elevated = ctx.in_thread, ctx.restart, ctx.restart_elevated
    toggle_mic, action_scan, region_picker, _vitals = ctx.toggle_mic, ctx.action_scan, ctx.region_picker, ctx.vitals

    menu.chat_requested.connect(avatar.open_chat)
    menu.mic_toggle_requested.connect(in_thread(toggle_mic))
    menu.action_requested.connect(in_thread(action_scan))
    menu.quit_requested.connect(app.quit)
    menu.restart_requested.connect(restart)

    tray = QSystemTrayIcon(app)
    tray.setIcon(app.style().standardIcon(app.style().StandardPixmap.SP_ComputerIcon))
    tray.setToolTip("Eva")
    tray_menu = QMenu()
    tray_menu.addAction(f"Chat   ({ubiquity.pretty(config.HOTKEY_CHAT)})" if config.HOTKEY_CHAT else "Chat",
                        avatar.open_chat)
    tray_menu.addAction(f"Ask about part of the screen   ({ubiquity.pretty(config.HOTKEY_REGION)})"
                        if config.HOTKEY_REGION else "Ask about part of the screen", region_picker.start)
    tray_mic = tray_menu.addAction("Mute Mic", in_thread(toggle_mic))
    tray_menu.addAction("Stop", in_thread(runtime.stop))

    def toggle_pause():
        runtime.set_paused(not runtime.paused)
        bridge.fire(lambda: tray_pause.setText("Resume Eva" if runtime.paused else "Pause Eva"))
        bridge.fire(lambda: menu.set_paused(runtime.paused))
        speaker_status(None)
    tray_pause = tray_menu.addAction("Pause Eva", in_thread(toggle_pause))
    menu.stop_requested.connect(in_thread(runtime.stop))
    menu.pause_requested.connect(in_thread(toggle_pause))
    menu.paused_state = lambda: runtime.paused       # also paused by voice ("Eva, resume")
    tray_menu.addSeparator()

    def vision_status():
        import vision as _v
        cal = _v._calibration().get(config.VISION_MODEL_ID, {})
        how = (f"coordinates measured as {cal['convention']}" if cal.get("convention")
               else "coordinates assumed 0-1000 (not measured yet)")
        gone = f"; unavailable: {', '.join(sorted(_v._UNAVAILABLE))}" if _v._UNAVAILABLE else ""
        return f"{config.VISION_MODEL_ID}, {how}{gone}"

    def show_health():
        from core import llm as _llm
        h = runtime.health
        model = (f"{h['status']} {int(time.time() - h['at'])}s ago" + (f" -- {h['detail']}" if h["detail"] else "")
                 if h["at"] else "no request yet")
        try:
            browser = f"open at {browser_agent._page.url[:60]}" if browser_agent._page is not None else "not running"
        except Exception:
            browser = "running but not responding"
        try:
            beat = int(time.monotonic() - avatar.stage.view.page().last_heartbeat)
            render = f"last frame {beat}s ago" + ("  <-- STALLED" if beat > 8 else "")
        except Exception as e:
            render = f"unknown ({e})"
        report = "\n".join([
            f"Microphone: {'listening' if voice.is_listening() else 'not listening'}{' (muted)' if mic['muted'] else ''}",
            f"Speech: {'speaking' if voice.is_speaking() else 'idle'} ({voice.voice_source()} voice)",
            f"Model: {_llm.last_model()}: {model}",
            f"Now: {agent.activity or ('thinking' if agent.busy else 'idle')}; queued {runtime.queued()}",
            f"Browser: {browser}", f"Avatar render: {render}",
            f"Scheduled: {len(scheduler.list_scheduled().get('items', []))} item(s)",
            f"Skills: {len(LIBRARY.all())}; connected services: {CONNECTIONS.summary()}",
            f"Paused: {'yes' if runtime.paused else 'no'}",
            f"Process: {_vitals.VITALS['v'].summary() if _vitals.VITALS['v'] else 'not measured'}",
            f"Vision: {vision_status()}",
            f"Log: {logger.LOG_PATH}"])
        log.info("Health check:\n" + report)
        bridge.fire(lambda: tray.showMessage("Eva -- status", report, msecs=15000))
    tray_menu.addAction("Status", show_health)

    def open_failed_tasks():
        from core.toolset import insight
        r = insight.show_failed_task(1)
        if not r.get("ok"):
            tray.showMessage("Eva", "No failed task has been kept -- nothing has gone wrong lately.", msecs=5000)
    tray_menu.addAction("Failed tasks", open_failed_tasks)

    activity = {"w": None}

    def open_activity(skills_tab=False):
        try:
            from activity_ui import ActivityWindow
            if activity["w"] is None:
                activity["w"] = ActivityWindow(agent.audit, agent.memory, in_thread(runtime.stop))
                activity["w"].on_skills_changed = lambda: refresh_skill_label()
            activity["w"].show()
            if skills_tab:
                activity["w"].show_skills()
            activity["w"].raise_()
            activity["w"].activateWindow()
        except Exception:
            log.exception("Could not open the activity window")
            tray.showMessage("Eva", "Could not open the activity window -- see eva.log.", msecs=6000)

    def set_api_key():
        try:
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            import secret_store
            text, ok = QInputDialog.getText(None, "Eva - API key",
                                            "Paste your NVIDIA API key (stored encrypted for this Windows user):",
                                            QLineEdit.EchoMode.Password)
            if ok and text.strip():
                secret_store.set("NVIDIA_API_KEY", text.strip())
                log.info("API key saved to the encrypted store (value not logged). Restarting.")
                restart()
        except Exception as e:
            log.exception("Could not save the API key")
            tray.showMessage("Eva", f"Could not save the key: {e}", msecs=8000)

    def open_skills_folder():
        folder = user_root()
        os.makedirs(folder, exist_ok=True)
        try:
            os.startfile(folder)                     # Windows Explorer
        except Exception as e:
            tray.showMessage("Eva", f"Skills are in {folder} ({e})", msecs=8000)

    tray_menu.addAction("Activity and memory", open_activity)
    tray_skills = tray_menu.addAction("Skills to review", lambda: open_activity(skills_tab=True))

    # Skills Eva wrote on her own wait for the person (core/skills.py). One time,
    # the ones she learned before this existed join the queue too.
    from core import skills as _skills
    try:
        moved = _skills.move_learned_to_review_once()
    except Exception:
        log.exception("Could not move learned skills to review")
        moved = 0

    def refresh_skill_label():
        n = _skills.pending_count()
        tray_skills.setText(f"Skills to review ({n})" if n else "Skills to review")

    def on_skill_pending(name):
        def show():
            refresh_skill_label()
            tray.showMessage("Eva learned something", f"I wrote down how to do \"{name}\". I'll use it once you "
                             "approve it (tray > Skills to review).", msecs=7000)
        bridge.fire(show)
    _skills.on_pending = on_skill_pending
    refresh_skill_label()
    if moved:
        bridge.fire(lambda: tray.showMessage(
            "Eva", f"{moved} skill(s) I learned on my own are waiting for your review "
                   "(tray > Skills to review).", msecs=8000))
    tray_menu.addAction("Skills folder", open_skills_folder)

    # Settings (settings_store.py / settings_ui.py): the window, her tools, live re-apply.
    def settings_saved(res):
        if res.get("restart"):
            tray.showMessage("Eva", f"Saved. {', '.join(res['restart'])} change(s) after a restart.", msecs=6000)

    def open_settings_window():
        try:
            import settings_ui
            settings_ui.open_settings(on_saved=settings_saved)
        except Exception:
            log.exception("Could not open the Settings window")
            tray.showMessage("Eva", "Could not open Settings -- see eva.log.", msecs=6000)

    def open_settings_tool():
        bridge.fire(open_settings_window)
        return {"ok": True, "opened": "Settings"}
    registry.bind("open_settings", open_settings_tool)
    settings_store.LISTENERS.append(lambda changed: bridge.fire(avatar.apply_settings))
    tray_menu.addAction("Settings", open_settings_window)
    tray_menu.addAction("Set API key", set_api_key)
    tray_menu.addAction("Restart Eva", restart)
    from core import elevation as _elev_tray
    if sys.platform == "win32" and not _elev_tray.self_elevated():
        tray_menu.addAction("Restart as administrator", restart_elevated)
    tray_menu.addAction("Quit Eva", app.quit)
    tray.setContextMenu(tray_menu)
    tray.show()

    ctx.tray, ctx.tray_menu, ctx.tray_mic = tray, tray_menu, tray_mic
