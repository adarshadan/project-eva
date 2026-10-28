"""Her own browser's steps get the same body language and the same money gate, and a
"confirm you are human" page is handed to the person and resumed afterwards."""
import threading
import time

import logger

log = logger.get_logger("__main__")


def install(ctx):
    import browser_agent
    avatar, bridge, runtime, agent, speaker, gate = (ctx.avatar, ctx.bridge, ctx.runtime, ctx.agent,
                                                    ctx.speaker, ctx.gate)

    # Browser steps get the same body language and the same money gate.
    def browser_point(x, y):
        done = threading.Event()
        bridge.fire(lambda: avatar.perform_gesture_at(x, y, gesture="click", on_ready=lambda: done.set()))
        done.wait(timeout=15)
    browser_agent.on_screen_action = browser_point
    browser_agent.approval_hook = gate.request

    # The person's OWN browser, through the Eva Browser Bridge extension (browser_link.py): the
    # same approval dialog for money and can't-be-undone steps. Local only (127.0.0.1 + token).
    import browser_link
    browser_link.approval_hook = gate.request
    browser_link.start()

    # A hand-off belongs to the request that hit it. When the page clears, she
    # resumes THAT request -- named in the message -- and only if nothing newer
    # came from the person and it is recent. "Carry on with the task you were
    # doing", with no task named, let a stale request come back (2026-10-11).
    handoff = {"request": None, "at": 0.0, "seq": 0}

    def on_verification_needed(info):
        msg = "This site wants you to confirm you're human. I've brought the browser forward and I'll carry on when it's done."
        log.info(f"Hand-off: {info.get('url')}")
        handoff.update(request=getattr(agent.turn, "text", None), at=time.monotonic(),
                       seq=runtime.person_seq)
        bridge.fire(lambda: ctx.tray.showMessage("Eva needs you for a moment", msg))
        speaker.say(msg)

    def on_verification_done(info):
        request, age = handoff["request"], time.monotonic() - handoff["at"]
        newer = runtime.person_seq != handoff["seq"]
        handoff.update(request=None)
        if not request or newer or age > 600:
            log.info(f"Verification cleared at {info.get('url')} -- NOT resuming "
                     f"({'no request' if not request else 'the person asked for something since' if newer else 'too old'}).")
            return
        log.info(f"Verification cleared at {info.get('url')} -- resuming: {request[:80]!r}")
        runtime.submit(f"[system] The person finished the verification check on {info.get('url')}. "
                       f"Resume this request of theirs, and nothing else: {request!r}", "scheduled")
    browser_agent.on_handoff = on_verification_needed
    browser_agent.on_resumed = on_verification_done
