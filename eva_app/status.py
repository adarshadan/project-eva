"""The status line and her face while she works: one place decides what the pill says."""
import logger

log = logger.get_logger("__main__")

MOOD = {"thinking": ("Surprised", 0.35), "error": ("Sad", 0.6)}


def install(ctx):
    avatar, bridge = ctx.avatar, ctx.bridge
    ctx.mic = {"muted": False}
    mic = ctx.mic

    def show_text(text, ms=5000):
        bridge.fire(lambda: avatar.show_text(text, duration_ms=ms))

    # ---- status line: one place decides what it says -------------------------------
    state = {"thinking": False}

    def idle_status():
        runtime = getattr(ctx, "runtime", None)
        if runtime is not None and runtime.paused:
            return "Paused"
        if mic["muted"]:
            return "Muted"
        try:
            import voice
            if not voice.capturing():
                return "Starting"           # 2026-10-28: "it says Listening but actually it is not"
        except Exception:
            pass
        return "Listening"

    def speaker_status(text):
        shown = text or ("Thinking" if state["thinking"] else idle_status())
        bridge.fire(lambda: avatar.set_status(shown, animated=shown in ("Speaking", "Thinking")))

    def on_state(kind, detail=""):
        if kind == "thinking":
            state["thinking"] = True
            bridge.fire(lambda: (avatar.set_expression(*MOOD["thinking"]),
                                 avatar.set_status("Thinking", animated=True),
                                 avatar.show_typing(True)))          # iMessage's typing dots
        elif kind == "waiting":               # wait_for_person: her turn is paused until the person acts
            bridge.fire(lambda: (avatar.clear_expression(), avatar.show_typing(False),
                                 avatar.set_status("Waiting for you", animated=False)))
        elif kind == "error":
            bridge.fire(lambda: avatar.set_expression(*MOOD["error"]))
        elif kind == "idle":
            state["thinking"] = False
            bridge.fire(lambda: (avatar.clear_expression(), avatar.show_typing(False)))
            if not ctx.speaker.busy():
                speaker_status(None)

    ctx.show_text, ctx.idle_status, ctx.speaker_status, ctx.on_state = show_text, idle_status, speaker_status, on_state
