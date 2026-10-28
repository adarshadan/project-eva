"""The microphone: always listening, mute and unmute."""
import logger

log = logger.get_logger("__main__")


def install(ctx):
    import voice
    avatar, bridge, runtime, mic, show_text = ctx.avatar, ctx.bridge, ctx.runtime, ctx.mic, ctx.show_text

    # ---- microphone -------------------------------------------------------------------
    def on_mic_error(msg):
        log.error(f"Mic listener error: {msg}")
        show_text("I'm having trouble reaching speech recognition. Check the internet connection.", 6000)

    taps = []           # one-shot listeners (the welcome's microphone test): fn(text), then removed

    def heard(text):
        while taps:
            fn = taps.pop(0)
            try:
                fn(text)
            except Exception:
                log.exception("A microphone tap failed")
        runtime.heard(text)

    def hear_once(fn):
        taps.append(fn)

        def cancel():
            if fn in taps:
                taps.remove(fn)
        return cancel

    def start_listening():
        voice.start_always_listening(heard, on_error=on_mic_error)

    # "Listening" only once the microphone is really being read
    voice.on_capture_started(lambda: ctx.speaker_status(None))

    def toggle_mic():
        if mic["muted"]:
            start_listening()
            mic["muted"] = False
        else:
            voice.stop_always_listening()
            mic["muted"] = True
            bridge.fire(lambda: avatar.play("p_shh", loop=False))     # "shh": the mic is off
        bridge.fire(lambda: ctx.menu.set_mic_label(mic["muted"]))
        bridge.fire(lambda: ctx.tray_mic.setText("Unmute Mic" if mic["muted"] else "Mute Mic"))
        show_text("Mic off." if mic["muted"] else "Mic on. I'm listening.", 2500)
        ctx.speaker_status(None)

    ctx.start_listening, ctx.toggle_mic, ctx.hear_once = start_listening, toggle_mic, hear_once
