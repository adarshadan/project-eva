"""Her feeling (core/feeling.py) wired to what happens: spoken to, working, done, failed, her own words."""
import logger

log = logger.get_logger("__main__")


def install(ctx):
    import config
    from core.feeling import Feeling
    avatar, bridge = ctx.avatar, ctx.bridge
    if not getattr(config, "FEELINGS", True):
        ctx.feeling = None
        return
    def emote(name, size):
        log.info("Feeling: %s (%.2f)", name, size)     # so a log shows when her body reacted
        bridge.fire(lambda: avatar.emote(name, size))
    feeling = Feeling(lambda e, v, hold: bridge.fire(lambda: avatar.set_affect(e, v, hold)), emote)
    ctx.feeling = feeling
    inner = ctx.on_state
    state = {"busy": False}

    def on_state(kind, detail=""):
        try:
            if kind == "thinking" and not state["busy"]:
                state["busy"] = True
                feeling.spoken_to()                 # the person asked: eager to start
            elif kind == "working":
                feeling.working()
            elif kind == "idle":
                state["busy"] = False
        except Exception:
            log.exception("Feeling hook failed")
        return inner(kind, detail)
    ctx.on_state = on_state
