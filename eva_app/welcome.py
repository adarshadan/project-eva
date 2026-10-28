"""The first-run welcome (onboarding.py, welcome_ui.py): shown once, again from tray > Welcome tour."""
import logger

log = logger.get_logger("__main__")


def install(ctx):
    import onboarding
    import settings_store
    from PySide6.QtCore import QTimer
    avatar, runtime, speaker, bridge = ctx.avatar, ctx.runtime, ctx.speaker, ctx.bridge
    holder = {"w": None}

    class Hooks:
        @staticmethod
        def say(text):
            speaker.say(text)

        @staticmethod
        def save_settings(changes):
            try:
                return settings_store.save(changes)
            except Exception:
                log.exception("Welcome: could not save a setting")
                return {}

        @staticmethod
        def mic_muted():
            return ctx.mic["muted"]

        @staticmethod
        def toggle_mic():
            ctx.in_thread(ctx.toggle_mic)()

        @staticmethod
        def hear_once(fn):
            return ctx.hear_once(fn)

        @staticmethod
        def run_task(text):
            log.info(f"Welcome: first task -- {text[:80]!r}")
            avatar.open_chat()                    # the task card shows there
            runtime.submit(text, "chat")

        @staticmethod
        def done(how, restart):
            onboarding.mark(how)
            log.info(f"Welcome tour {how}" + (f"; restart for {', '.join(restart)}" if restart else ""))
            holder["w"] = None
            if restart and how == "finished":
                speaker.say("I'll restart in a moment to change my size.")
                QTimer.singleShot(4000, ctx.restart)

    def open_tour():
        try:
            import welcome_ui
            if holder["w"] is not None:
                holder["w"].raise_()
                holder["w"].activateWindow()
                return
            holder["w"] = welcome_ui.open_welcome(Hooks, key_missing=bool(ctx.key_missing))
        except Exception:
            log.exception("Could not open the welcome tour")

    ctx.open_welcome = open_tour
    ctx.welcome_due = onboarding.needed()
    ctx.tray_menu.addAction("Welcome tour", open_tour)
    if ctx.welcome_due:
        log.info("First run of this version: the welcome tour opens after the greeting.")
        QTimer.singleShot(2500, open_tour)
