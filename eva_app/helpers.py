"""Background helpers Eva delegates to (core/workers.py)."""
import logger

log = logger.get_logger("__main__")


def install(ctx):
    from core import registry
    from core import workers
    avatar, bridge, runtime, audit_log = ctx.avatar, ctx.bridge, ctx.runtime, ctx.audit_log

    def on_helper_done(job):
        log.info(f"Helper #{job.id} {job.status}: telling Eva.")
        runtime.submit(workers.report_note(job), "scheduled")

    def on_helpers_changed(n):
        bridge.fire(lambda: avatar.set_helpers(n))

    helpers = workers.Helpers(audit=audit_log, on_done=on_helper_done, on_change=on_helpers_changed)
    workers.HELPERS = helpers

    def delegate(task):
        result = helpers.delegate(task)
        if result.get("ok"):
            bridge.fire(lambda: avatar.play("p_delegate", loop=False))   # she hands it over
        return result
    registry.bind("delegate", delegate)
    registry.bind("helpers_status", helpers.status)
    registry.bind("cancel_helper", lambda helper=None: helpers.cancel(helper))
    runtime._stop_hooks.append(helpers.cancel_all)       # "stop" stops the helpers too
