"""Continuous activities (repeats, games, reflexes -- activities.py), her presence tools,
recordings, skills and connected services."""
import logger

log = logger.get_logger("__main__")

OUTCOME_MOOD = {"won": "p_cheer", "lost": "p_facepalm", "draw": "p_shrug", "done": "p_nod", "timeout": "p_nod",
                "failed": "p_shrug"}


def install(ctx):
    import activities as _activities
    import desktop_control as dc
    import recorder
    from core import registry
    from core.connections import CONNECTIONS
    from core.presence_impl import bind_presence
    from core.skills import LIBRARY
    avatar, bridge, runtime, agent, idle_status = ctx.avatar, ctx.bridge, ctx.runtime, ctx.agent, ctx.idle_status

    class ActivityBody:
        def keep_clear(self, region):
            bridge.fire(lambda: avatar.keep_clear_of(region))   # never over what she is watching

        def pose(self, on):
            bridge.fire(lambda: avatar.set_activity("playing" if on else "idle"))

        def mood(self, outcome):
            g = OUTCOME_MOOD.get(outcome)
            if g:
                bridge.fire(lambda: avatar.play(g, loop=False))

    def on_activity_status(text):
        shown = text or idle_status()
        bridge.fire(lambda: avatar.set_status(shown, animated=bool(text)))

    def on_activity_end(a):
        said = (a.result or a.outcome or "done").strip()
        said = said[0].upper() + said[1:]
        if not said.endswith((".", "!", "?")):
            said += "."
        runtime._reply(said, speak=runtime._speaks())

    _activities.MANAGER["m"] = _activities.Manager(
        on_status=on_activity_status, on_end=on_activity_end, body=ActivityBody(),
        foreground=lambda: dc.get_foreground_app().get("handle"))
    runtime._stop_hooks.append(lambda: _activities.manager().stop("stop"))   # "stop" ends a repeat or game

    # ---- implementations that need live objects -------------------------------------
    bind_presence(avatar, bridge.run_sync, bridge.fire)
    registry.bind("run_recording", lambda name, params=None: recorder.run_recording(
        name, params, step_runner=lambda tool, args: agent.dispatch(tool, args)))
    LIBRARY.refresh(force=True)          # logs which skills are available
    CONNECTIONS.start()                  # discovers connected services in the background
