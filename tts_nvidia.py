"""Eva -- her voice from NVIDIA's free Magpie speech synthesis (same key as her model).

2026-10-28 (tools/tts_trial.py, four lines from one word to a long sentence): time to the first audio was
1.43 s on average with the Microsoft Edge voice and about 0.5 s with Magpie (0.21 s streaming). Eva speaks
sentence by sentence and prepares the next while one plays (core/speaker.py), so the first sentence's
synthesis is what the person waits for.

    config.TTS_BACKEND = "nvidia" | "edge"
    config.TTS_NVIDIA_VOICE = "Magpie-Multilingual.EN-US.Aria" (also .Mia, .Jason, ... with moods: .Mia.Calm)

synthesize(text) -> path of a .wav (the caller deletes it), or raises; voice.synthesize then falls back to
the Edge voice, then the Windows voice. After a failure it rests TTS_NVIDIA_RETRY_SECONDS.
Needs: pip install nvidia-riva-client.
"""
import os
import tempfile
import threading
import time
import wave

import config
from logger import get_logger

log = get_logger(__name__)

SERVER = "grpc.nvcf.nvidia.com:443"
FUNCTION_ID = "877104f7-e885-42b9-8de8-f6e4c6303969"      # magpie-tts-multilingual (build.nvidia.com)
RATE = 44100

_state = {"service": None, "resting_until": 0.0}
_lock = threading.Lock()


def enabled() -> bool:
    return str(getattr(config, "TTS_BACKEND", "edge")).lower() == "nvidia"


def resting(clock=time.monotonic) -> bool:
    return clock() < _state["resting_until"]


def _key():
    try:
        import secret_store
        k = secret_store.get("NVIDIA_API_KEY", "")
        if k:
            return k
    except Exception:
        pass
    return getattr(config, "NVIDIA_API_KEY", "")


def _service():
    import riva.client
    with _lock:
        if _state["service"] is None:
            auth = riva.client.Auth(uri=SERVER, use_ssl=True,
                                    metadata_args=[["function-id", FUNCTION_ID], ["authorization", f"Bearer {_key()}"]])
            _state["service"] = riva.client.SpeechSynthesisService(auth)
        return _state["service"]


def synthesize(text: str, clock=time.monotonic) -> str:
    if resting(clock):
        raise RuntimeError("NVIDIA voice resting after a failure")
    voice = str(getattr(config, "TTS_NVIDIA_VOICE", "") or "Magpie-Multilingual.EN-US.Aria")
    try:
        import riva.client
        try:
            import providers
            providers.pace("nvidia/tts")
        except Exception:
            pass
        t = clock()
        audio = _service().synthesize(text, voice, "en-US", encoding=riva.client.AudioEncoding.LINEAR_PCM,
                                      sample_rate_hz=RATE).audio
        if not audio:
            raise RuntimeError("no audio came back")
        fd, path = tempfile.mkstemp(prefix="eva_tts_", suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(audio)
        log.info(f"NVIDIA voice: {len(text)} chars in {clock() - t:.2f} s")
        return path
    except Exception as e:
        _state["resting_until"] = clock() + float(getattr(config, "TTS_NVIDIA_RETRY_SECONDS", 120))
        with _lock:
            _state["service"] = None
        log.warning(f"NVIDIA voice failed ({type(e).__name__}: {str(e)[:150]}) -- using the Edge voice for now")
        raise
