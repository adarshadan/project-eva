"""When nobody needs her: idle life (life.py), predictive help (predictive.py), proactive
suggestions and commentary. All GUI-thread timers."""
import time

import config
import logger

log = logger.get_logger("__main__")


def install(ctx):
    import activities as _activities
    import desktop_control as dc
    import idle_behavior
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QCursor as _QCursor
    from PySide6.QtWidgets import QApplication
    app, avatar, agent, runtime = ctx.app, ctx.avatar, ctx.agent, ctx.runtime

    # ---- idle life (GUI-thread timers) ------------------------------------------------------
    idle_behavior.start_input_listeners()
    # ---- her own life when nobody needs her (life.py) ---------------------------------
    import life as life_mod
    from PySide6.QtCore import QTimer as _QT

    class BodyFacade:
        def sit_on(self, edge_y, x, hwnd):
            avatar.sit_on(edge_y, x, hwnd)

        def sit_here(self):
            avatar.sit_here()

        def stand_up(self):
            avatar.stand_up()

        def get_down(self):
            avatar.get_down()

        def stroll(self):
            avatar.walk_along_taskbar()

        def gesture(self, name):
            avatar.play(name, loop=False)

        def set_activity(self, name):
            avatar.set_activity(name)

        def wave(self, delay_ms=0):
            _QT.singleShot(int(delay_ms), lambda: avatar.play("p_wave", loop=False))

        def state(self):
            b = avatar.body()
            return {"sitting": b["sitting"], "airborne": b["airborne"], "perched_on": b["perched_on"],
                    "walking": avatar._walk_target is not None or b["held"], "placed": b.get("placed", False)}

        def look_at(self, x, y, ms):
            pass            # the page's presence layer follows the cursor itself (attention_tick below)

        def run_around(self):
            return avatar.run_around()

        def patrol(self):
            start_patrol()

        def watch(self, rect):
            avatar.watch_video(rect)

        def stop_watching(self):
            avatar.stop_watching()

        def head(self):
            return avatar.x() + avatar.width() // 2, avatar.y() + int(avatar.height() * 0.15)

        def size(self):
            return avatar.width(), avatar.height()

    class LifeProbes:
        def idle_seconds(self):
            return idle_behavior.get_idle_seconds()["idle_seconds"]

        def busy(self):
            return agent.busy or runtime.queued() > 0 or _activities.manager().current is not None

        def cursor(self):
            p = _QCursor.pos()
            return p.x(), p.y()

        def hour(self):
            return time.localtime().tm_hour

        def foreground(self):
            return dc.get_foreground_app().get("handle")

        def video_window(self):
            fg = dc.get_foreground_app()
            if not fg.get("handle") or not life_mod.VIDEO_TITLE.search(fg.get("title") or ""):
                return None
            r = dc.get_window_rect(fg["handle"])
            return (fg["handle"], (r["left"], r["top"], r["right"], r["bottom"])) if r else None

        def ground(self):
            return QApplication.primaryScreen().availableGeometry().bottom()

        def boredom(self):
            return boredom_level()

        def patrol_due(self):
            import patrol as _patrol
            return (config.PATROL and boredom_level() >= 2 and _patrol.patrol().due() and not _busy()
                    and self.idle_seconds() >= 15 and not runtime.pending_offer and not _fullscreen_now())

        def perch_spots(self):
            spots = []
            for w in dc.list_windows().get("windows", [])[:30]:
                r = dc.get_window_rect(w["handle"])
                if not r or r["top"] <= 0 or r["right"] - r["left"] < 300:
                    continue
                import avatar_ui as _ui
                if _ui._window_iconic(w["handle"]):
                    continue
                spots.append((w["handle"], r["top"], r["left"], r["right"]))
            return spots

    # ---- boredom: how long nobody has needed her ----------------------------------------------
    def boredom_level():
        if agent.busy or runtime.queued():
            return 0
        quiet = time.time() - max(getattr(runtime, "last_person_at", 0.0), getattr(agent, "last_done_at", 0.0) or 0.0)
        return 0 if quiet < 180 else 1 if quiet < 480 else 2 if quiet < 1200 else 3

    def _fullscreen_now():
        try:
            return idle_behavior.is_fullscreen_app_active()
        except Exception:
            return False

    # ---- patrol: looking around the PC for inefficiencies, offering to fix them (patrol.py) -------
    def start_patrol():
        import threading as _th
        import patrol as _patrol
        ctx.bridge.fire(lambda: avatar.play("p_peer", loop=False))      # hand over her eyes, looking about

        def work():
            f = _patrol.patrol().run()
            if not f:
                log.info("Patrol: nothing worth mentioning")
                return
            if f.get("info"):
                ctx.bridge.fire(lambda: avatar.show_text(f["text"]))
                return
            ctx.bridge.fire(lambda: avatar.play("p_checklist", loop=False))
            runtime.offer({"key": f["kind"], "label": f["label"], "task": f["task"], "source": "patrol"},
                          f["text"])
        _th.Thread(target=work, daemon=True, name="eva-patrol").start()

    life_mod.START_AFTER = config.IDLE_ANIMATION_START_SECONDS
    life_mod.NAP_AFTER = config.IDLE_NAP_SECONDS
    eva_life = life_mod.Life(BodyFacade(), LifeProbes())

    def idle_tick():
        if not config.IDLE_LIFE:
            return
        try:
            eva_life.tick()
        except Exception:
            log.exception("Idle life tick failed")

    # The human connection (web/avatar_3d.html presence layer): where the cursor is, and how bored
    # she is. Her eyes follow the cursor near her and meet the person's otherwise.
    last_cur = {"p": None}

    def attention_tick():
        if not config.PRESENCE:
            return
        try:
            p = _QCursor.pos()
            cur = (p.x(), p.y())
            prev, last_cur["p"] = last_cur["p"], cur
            moving = prev is not None and abs(cur[0] - prev[0]) + abs(cur[1] - prev[1]) > 4
            g = avatar.frameGeometry()
            hover = g.contains(p)
            hx, hy = g.center().x(), g.top() + int(g.height() * 0.15)
            near = abs(cur[0] - hx) < 700 and abs(cur[1] - hy) < 600
            if eva_life.napping:
                return
            if near or hover:
                avatar.attend(cur[0], cur[1], moving, hover)
            else:
                avatar.attend(hx, hy, False, False)          # far away: eye contact with the person
            avatar.set_boredom(boredom_level())
        except Exception:
            pass
    idle_timer = QTimer()
    idle_timer.timeout.connect(idle_tick)
    idle_timer.start(1000)
    glance_timer = QTimer()
    glance_timer.timeout.connect(attention_tick)
    glance_timer.start(120)
    app._eva_glance_timer = glance_timer

    # Predictive help (predictive.py): offers what the person usually opens at this
    # time. Off unless switched on (tray > Settings > Initiative); never money.
    import predictive
    import idle_behavior as _idle

    def _exe_of(handle):
        import psutil
        pid = dc._window_pid(int(handle))
        return psutil.Process(pid).exe() if pid else None

    def _fullscreen(handle):
        r = dc.get_window_rect(handle)
        if not r or not r.get("ok", True):
            return False
        for m in dc.get_screen_size().get("monitors", []):
            if r["left"] <= m["left"] and r["top"] <= m["top"] and r["right"] >= m["left"] + m["width"] \
                    and r["bottom"] >= m["top"] + m["height"]:
                return True
        return False

    def _busy():
        from activities import MANAGER as _am
        return bool(agent.busy or runtime.paused or runtime.queued() or runtime.pending_offer
                    or (_am["m"] is not None and _am["m"].current is not None))

    def _fg():
        f = dc.get_foreground_app()
        return f.get("handle"), f.get("title") or ""

    def _browser_exe(args):
        try:
            return _exe_of(args.get("window_handle")) if args.get("window_handle") else None
        except Exception:
            return None
    agent.tool_observers.append(lambda name, args, result: predictive.routines().observe_tool(
        name, args, result, browser_exe=_browser_exe(args or {})) if name == "navigate_browser" else None)
    routine_watcher = predictive.Watcher(
        predictive.routines(), foreground=_fg, exe_of=_exe_of, own=dc.is_own_window,
        idle_seconds=lambda: _idle.get_idle_seconds()["idle_seconds"], busy=_busy, fullscreen=_fullscreen,
        offer=lambda r, text: runtime.offer(r, text)).start()
    app._eva_routines = routine_watcher

    suggestion_timer = QTimer()
    last_suggestion = {"t": 0.0}

    def suggestion_tick():
        if not config.PROACTIVE_SUGGESTIONS or agent.busy:
            return
        if time.time() - last_suggestion["t"] < config.SUGGESTION_MIN_GAP_SECONDS:
            return
        msg = idle_behavior.check_for_suggestion()
        if msg:
            avatar.show_text(msg)
            last_suggestion["t"] = time.time()
    suggestion_timer.timeout.connect(suggestion_tick)
    suggestion_timer.start(config.SUGGESTION_CHECK_INTERVAL_SECONDS * 1000)

    commentary_timer = QTimer()
    fg = {"title": None, "since": 0.0, "last": 0.0, "seen": set()}

    def commentary_tick():
        if not config.PROACTIVE_COMMENTARY_ENABLED or agent.busy:
            return
        title = (dc.get_foreground_app().get("title") or "").strip()
        now = time.time()
        if title != fg["title"]:
            fg["title"], fg["since"] = title, now
            return
        if (not title or title in fg["seen"] or now - fg["since"] < config.COMMENTARY_DWELL_SECONDS
                or now - fg["last"] < config.COMMENTARY_MIN_GAP_SECONDS):
            return
        fg["seen"].add(title)
        fg["last"] = now
        runtime.submit(f"The person has been in a window titled {title!r} for a little while.", "observation")
    commentary_timer.timeout.connect(commentary_tick)
    commentary_timer.start(5000)

    ctx.timers += [idle_timer, glance_timer, suggestion_timer, commentary_timer]
