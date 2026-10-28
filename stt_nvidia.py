"""Eva -- speech-to-text with NVIDIA's free speech recognisers (same key as her model).

2026-10-28: the kept phrases (~/.eva/hearing) showed the start of the voice is captured, yet the free
Google recogniser misheard words ("Centre button" for "enter button"). build.nvidia.com serves speech
recognisers over Riva gRPC with the NVIDIA key; tools/asr_trial.py compares them on the kept phrases.

    config.STT_BACKEND = "nvidia"
    config.STT_NVIDIA_MODEL = "parakeet-tdt-0.6b-v2" | "whisper-large-v3" | "canary-1b"

transcribe(wav_bytes) -> text ("" = heard nothing), or None when the service could not be used -- then
voice.recognize falls back to Google, so hearing never stops because a service is down. After a failure
it rests STT_NVIDIA_RETRY_SECONDS before trying again. Needs: pip install nvidia-riva-client.
"""
import threading
import time

import config
from logger import get_logger

log = get_logger(__name__)

SERVER = "grpc.nvcf.nvidia.com:443"
MODELS = {"whisper-large-v3": ("b702f636-f60c-4a3d-a6f4-f3568c13bd7d", "en"),
          "parakeet-tdt-0.6b-v2": ("d3fe9151-442b-4204-a70d-5fcc597fd610", "en-US"),
          "canary-1b": ("b0e8b4a5-217c-40b7-9b96-17d84e666317", "en-US")}

_state = {"service": None, "model": None, "resting_until": 0.0}
_lock = threading.Lock()


def enabled() -> bool:
    return str(getattr(config, "STT_BACKEND", "google")).lower() == "nvidia"


def _key():
    try:
        import secret_store
        k = secret_store.get("NVIDIA_API_KEY", "")
        if k:
            return k
    except Exception:
        pass
    return getattr(config, "NVIDIA_API_KEY", "")


def _service(model):
    import riva.client
    with _lock:
        if _state["service"] is None or _state["model"] != model:
            fid = MODELS[model][0]
            auth = riva.client.Auth(uri=SERVER, use_ssl=True,
                                    metadata_args=[["function-id", fid], ["authorization", f"Bearer {_key()}"]])
            _state["service"], _state["model"] = riva.client.ASRService(auth), model
        return _state["service"]


def transcribe(wav: bytes, clock=time.monotonic):
    if not enabled() or clock() < _state["resting_until"]:
        return None
    model = str(getattr(config, "STT_NVIDIA_MODEL", "parakeet-tdt-0.6b-v2"))
    if model not in MODELS:
        log.warning(f"Unknown STT_NVIDIA_MODEL {model!r}; using Google")
        return None
    try:
        import riva.client
        try:
            import providers
            providers.pace("nvidia/asr")              # the same per-key budget as every other request
        except Exception:
            pass
        cfg = riva.client.RecognitionConfig(language_code=MODELS[model][1], max_alternatives=1,
                                            enable_automatic_punctuation=True, audio_channel_count=1,
                                            sample_rate_hertz=16000,
                                            encoding=riva.client.AudioEncoding.LINEAR_PCM)
        t = clock()
        resp = _service(model).offline_recognize(wav, cfg)
        text = " ".join(r.alternatives[0].transcript.strip() for r in resp.results if r.alternatives).strip()
        log.info(f"NVIDIA {model} heard {text!r} in {clock() - t:.2f} s")
        return text
    except Exception as e:
        _state["resting_until"] = clock() + float(getattr(config, "STT_NVIDIA_RETRY_SECONDS", 60))
        with _lock:
            _state["service"] = None
        log.warning(f"NVIDIA speech recognition failed ({type(e).__name__}: {str(e)[:160]}) -- using Google "
                    f"for a minute")
        return None
