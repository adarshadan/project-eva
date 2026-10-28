"""The working parts: permission gate, body language, the agent, her voice, the conversation
history and the runtime (front lane + task lane)."""
import threading

import config
import logger

log = logger.get_logger("__main__")

# Moments in the conversation, answered with her body (2026-10-11).
MOMENT_GESTURES = {"thanked": "p_heart", "greeted": "p_wave", "unclear": "p_scratch_head",
                   "error": "p_facepalm", "paused": "p_shh", "resumed": "p_wave"}


def install(ctx):
    import audit
    import browser_agent
    import desktop_control as dc
    import embodiment
    import idle_behavior
    import memory
    import permissions
    import recorder
    import scheduler
    import voice
    from avatar_renderer import AvatarRenderer
    from avatar_ui import ask_approval
    from core.agent import Agent
    from core.connections import CONNECTIONS
    from core.learning import Learner
    from core.runtime import Runtime
    from core.speaker import Speaker

    avatar, bridge = ctx.avatar, ctx.bridge
    show_text, on_state, speaker_status, mic = ctx.show_text, ctx.on_state, ctx.speaker_status, ctx.mic

    # ---- the pieces -------------------------------------------------------------------
    audit_log = audit.AuditLog(config.AUDIT_LOG_PATH or None)
    memory_store = None
    if config.MEMORY_ENABLED:
        try:
            memory_store = memory.Memory(config.MEMORY_DB_PATH or None)
        except Exception:
            log.exception("Memory could not be opened -- continuing without it this session")

    gate = permissions.PermissionGate(lambda req: bridge.run_sync(lambda: ask_approval(req, parent=avatar),
                                                                   timeout=600))
    performer = embodiment.Performer(
        AvatarRenderer(avatar, bridge.fire), mode=config.EMBODIMENT_MODE,
        status_delay=config.EMBODIMENT_STATUS_DELAY,
        should_abort=lambda: ctx.runtime.stop_requested() if getattr(ctx, "runtime", None) else False,
        pressure=lambda: ctx.runtime.should_abort() if getattr(ctx, "runtime", None) else False)
    learner = Learner(audit=audit_log)

    def screen_vocabulary():
        """The open windows' titles: the best evidence for a misheard name ("model game" -> Wordle)."""
        try:
            fg = dc.get_foreground_app().get("title") or ""
            titles = [w["title"] for w in dc.list_windows().get("windows", [])
                      if not w.get("is_eva_herself") and w.get("title")][:8]
            if not titles:
                return ""
            short = [t if len(t) <= 70 else t[:67] + "..." for t in titles]
            return ("ON SCREEN NOW (window titles, front first" + (f"; in front: {fg[:70]}" if fg else "")
                    + "): " + " | ".join(short))
        except Exception:
            return ""

    def extra_context():
        return "\n\n".join(p for p in (CONNECTIONS.prompt_note(), screen_vocabulary()) if p)

    agent = Agent(gate, performer=performer, audit=audit_log, memory=memory_store,
                  on_state=on_state, record_step=recorder.record_step,
                  on_turn_done=learner.consider, extra_context=extra_context)

    def read_screen_for_stuck_check():
        """The stuck check's "what is on screen now": the window's structure; failing that,
        the words on screen (OCR). No vision model -- it may be the thing that is busy."""
        import structure
        text = ""
        try:
            text = structure.describe_screen()
        except Exception:
            log.exception("Structure description failed")
        if text and "Controls:" in text:
            return text
        try:
            import screen_text
            import vision as _v
            img, origin = _v._capture(None)
            words = screen_text.ocr_words(img) if img is not None else None
            if words:
                lines = {}
                for w in words:
                    lines.setdefault(w[5], []).append(w)
                rows = [" ".join(x[0] for x in sorted(ws, key=lambda w: w[1])) for ws in lines.values()]
                rows = [r for r in rows if len(r) >= 3][:30]
                return (text + " " if text else "") + "Words on screen: " + " | ".join(rows)[:1500]
        except Exception:
            log.exception("OCR description failed")
        return text
    agent.screen_reader = read_screen_for_stuck_check

    def on_voice_error(err):
        show_text(f"(I couldn't speak that: {err[:80]})", 4000)

    def pause_mic():
        if mic["muted"] or not voice.is_listening():
            return False
        voice.stop_always_listening(wait_for_stop=True)
        return True

    speaker = Speaker(voice, show_bubble=show_text, set_status=speaker_status, on_error=on_voice_error,
                      pause_mic=pause_mic, resume_mic=lambda: ctx.start_listening())

    def on_heard_activity():
        performer.event("heard")          # a glance: she noticed you spoke
        if config.PRESENCE:
            bridge.fire(avatar.acknowledge)   # a small nod and a smile: she heard you
        idle_behavior.touch_input()        # talking to her is activity, not idleness

    def on_task_result(meta, ok, error):
        if meta.get("schedule_id"):
            scheduler.report_run(meta["schedule_id"], ok, error)

    # The conversation, kept for the chat window (chat_store.py): what the
    # person said or typed, and everything Eva answered -- long answers in full.
    import chat_store
    history = chat_store.ChatHistory()

    def on_message(role, text):
        text = chat_store.clean_person_text(text) if role == "me" else text
        if history.add(role, text):
            bridge.fire(lambda: avatar.chat_popup.add_message(role, text))

    def bubble_to_notepad(text):
        from core.longform import LongForm

        def work():
            try:
                lf = LongForm()
                lf._open_file(lf.save(text))
            except Exception:
                log.exception("Could not open the message in Notepad")
        threading.Thread(target=work, name="bubble-notepad", daemon=True).start()
    avatar.bubble.on_notepad = bubble_to_notepad

    def on_moment(kind):
        g = MOMENT_GESTURES.get(kind)
        if g:
            bridge.fire(lambda: avatar.play(g, loop=False))
    avatar.chat_popup.load_history(history.recent(80))

    runtime = Runtime(agent, speaker, show_text=show_text, on_heard=on_heard_activity,
                      stop_hooks=[browser_agent.request_abort, recorder.request_abort],
                      clear_hooks=[browser_agent.clear_abort, recorder.clear_abort],
                      on_task_result=on_task_result, on_message=on_message, on_moment=on_moment)

    # ---- task cards in the chat window (core/task_card.py) ------------------------------
    def on_task_card(card, final):
        if final:
            history.put_card(card)
            if getattr(ctx, "feeling", None) is not None:
                ctx.feeling.finished(card)          # done: delight, bigger for a bigger job; failed: oops
        bridge.fire(lambda: avatar.chat_popup.put_card(card))
    agent.on_task_card = on_task_card
    if getattr(ctx, "feeling", None) is not None:
        agent.on_feeling = ctx.feeling.felt         # "[proud] ..." at the start of her reply

    def on_card_action(name, card_id):
        if name == "stop":
            runtime.stop()
            return
        card = history.card(card_id)
        if card is None:
            return
        if name == "again":
            log.info(f"Task card: run again -- {card.get('task')!r}")
            runtime.submit(card.get("task") or "", "chat")
        elif name == "replay" and card.get("replay"):
            import os
            page = os.path.join(card["replay"], "index.html")
            try:
                os.startfile(page)                      # the person's own browser
            except (OSError, AttributeError):
                show_text("That step-by-step page is no longer there.", 3000)
        elif name == "undo":
            def work():
                from core import task_card as tc
                c = tc.TaskCard.from_dict(card)
                ok, said = tc.undo(c)
                log.info(f"Task card: undo {card_id} -> {ok}: {said}")
                d = {**card, "undone": c.undone, "summary": c.summary()}
                history.put_card(d)
                bridge.fire(lambda: avatar.chat_popup.put_card(d))
                show_text(said, 4000)
            threading.Thread(target=work, name="eva-card-undo", daemon=True).start()
    avatar.chat_popup.card_action.connect(on_card_action)

    ctx.audit_log, ctx.gate, ctx.performer, ctx.agent = audit_log, gate, performer, agent
    ctx.speaker, ctx.runtime = speaker, runtime
