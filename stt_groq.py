"""Eva -- speech-to-text with Groq's free Whisper, the SECOND recogniser (2026-11-10).

Her hearing depended on one provider: when NVIDIA's speech service failed she fell back to the free Google
recogniser, which mishears more. With a Groq key (free, no card: console.groq.com -> API Keys, then
set_key.bat GROQ_API_KEY) Whisper large-v3-turbo is tried before Google. Without a key this module does
nothing.

transcribe(wav_bytes) -> text ("" = heard nothing), or None when Groq could not be used (no key, an error,
or resting after one) -- the caller then goes on to the next recogniser.
"""
import time

import config
from logger import get_logger

log = get_logger(__name__)

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_state = {"resting_until": 0.0}


def _key():
    try:
        import providers
        return providers.api_key("groq")
    except Exception:
        return ""


def enabled() -> bool:
    return bool(getattr(config, "STT_GROQ_FALLBACK", True)) and bool(_key())


def transcribe(wav: bytes, clock=time.monotonic, post=None):
    if not wav or not enabled() or clock() < _state["resting_until"]:
        return None
    try:
        if post is None:
            import requests
            post = requests.post
        t = clock()
        r = post(URL, headers={"Authorization": f"Bearer {_key()}"},
                 files={"file": ("phrase.wav", wav, "audio/wav")},
                 data={"model": str(getattr(config, "STT_GROQ_MODEL", "whisper-large-v3-turbo")),
                       "language": "en", "response_format": "json", "temperature": "0"},
                 timeout=float(getattr(config, "STT_GROQ_TIMEOUT", 8)))
        if getattr(r, "status_code", 200) != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {str(getattr(r, 'text', ''))[:120]}")
        text = str((r.json() or {}).get("text") or "").strip()
        log.info(f"Groq Whisper heard {text!r} in {clock() - t:.2f} s")
        return text
    except Exception as e:
        _state["resting_until"] = clock() + float(getattr(config, "STT_GROQ_RETRY_SECONDS", 60))
        log.warning(f"Groq speech recognition failed ({type(e).__name__}: {str(e)[:160]}) -- resting a minute")
        return None
