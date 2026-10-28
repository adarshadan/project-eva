"""Reminders and scheduled tasks (scheduler.py)."""
import logger

log = logger.get_logger("__main__")


def install(ctx):
    import scheduler
    bridge, runtime, speaker = ctx.bridge, ctx.runtime, ctx.speaker

    # ---- reminders and scheduled tasks ---------------------------------------------------
    def on_due(item):
        if item["kind"] == "reminder":
            log.info(f"Reminder: {item['text']}")
            bridge.fire(lambda: ctx.tray.showMessage("Eva reminder", item["text"]))
            if not runtime.paused:
                speaker.say(f"Reminder: {item['text']}")
        else:
            log.info(f"Running scheduled task: {item['text']!r}")
            runtime.submit(item["text"], "scheduled", meta={"schedule_id": item["id"]})

    def on_schedule_paused(item):
        msg = f"I've paused the schedule \"{item['text'][:60]}\" because {item['paused']}."
        bridge.fire(lambda: ctx.tray.showMessage("Eva paused a schedule", msg))
        speaker.say(msg)
    scheduler.start(on_due, on_schedule_paused)
