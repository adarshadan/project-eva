"""Her real voice path on this PC, end to end: voice.synthesize -> the playback helper, with timings; then
an interruption: a long line is cut off after one second (what happens when the person talks over her).

    python tools/voice_live_check.py        (speaks aloud)
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)


def main():
    import config
    import voice
    print(f"TTS_BACKEND={config.TTS_BACKEND} voice={config.TTS_NVIDIA_VOICE}")
    for text in ("Good morning. This is my new voice.", "I won today's Wordle in three guesses."):
        t = time.monotonic()
        path = voice.synthesize(text)
        ready = time.monotonic() - t
        voice.play_file(path)
        print(f"{voice.voice_source():<8} ready in {ready:.2f}s, spoken by {time.monotonic() - t:.2f}s: {text}")
        voice.discard(path)
    long = ("This is a long sentence that keeps going on and on, so that you can talk over me and I stop at once, "
            "the way a person stops when someone else starts speaking.")
    path = voice.synthesize(long)
    stopped_at = {}

    def cut():
        time.sleep(1.0)
        stopped_at["t"] = time.monotonic()
        voice.stop_speaking()
    threading.Thread(target=cut, daemon=True).start()
    interrupted = voice.play_file(path)
    silent = time.monotonic() - stopped_at.get("t", time.monotonic())
    print(f"interruption: stopped={interrupted}, silent {silent * 1000:.0f} ms after stop_speaking()")
    voice.discard(path)
    voice.shutdown_player()


if __name__ == "__main__":
    main()
