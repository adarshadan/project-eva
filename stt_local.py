"""Eva -- optional local speech recognition (Phonon-2 via `fermion serve`).

Why: Eva's recognition has been Google's free web recognizer. It needs the
internet, sends every utterance off the machine, and (per eva.log) garbles
proper nouns. Fermion Research's Phonon-2 is a 164 MB English model that runs
on a normal CPU (Windows included) and exposes an OpenAI-compatible endpoint:
  POST /v1/audio/transcriptions (multipart: file=<wav>)  ->  {"text": "..."}
  GET  /health
Source: github.com/fermionresearch/phonon (docs/server.md). Its published
accuracy figures are the vendor's own; whether it beats Google FOR YOUR VOICE
is what `doctor.bat` and a day of use will show -- hence the fallback below.

Design: transcribe() tries the local server; if it is not running or fails,
the caller falls back to Google. A failure is remembered for a short while so
a dead server costs one fast probe, not a timeout on every utterance.
"""
import json
import time
import urllib.error
import urllib.request
import uuid

import config
from logger import get_logger

log = get_logger(__name__)

_state = {"down_until": 0.0, "last_error": None, "ok_count": 0, "fail_count": 0}


def enabled() -> bool:
    return str(getattr(config, "STT_BACKEND", "google")).lower() == "phonon"


def _base() -> str:
    return str(getattr(config, "STT_LOCAL_URL", "http://127.0.0.1:8000")).rstrip("/")


def _multipart(wav: bytes):
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"clip.wav\"\r\n"
         "Content-Type: audio/wav\r\n\r\n").encode() + wav + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def health(timeout: float = 2.0):
    """(ok, detail). Used by the doctor and by the cool-down probe."""
    try:
        with urllib.request.urlopen(_base() + "/health", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return data.get("status") == "ok", data.get("model") or data
    except Exception as e:
        return False, str(e)


def transcribe(wav_bytes: bytes):
    """Returns the transcript (possibly empty when nothing intelligible), or
    None when the local server could not be used -- the caller then falls back."""
    if time.monotonic() < _state["down_until"]:
        return None
    body, ctype = _multipart(wav_bytes)
    req = urllib.request.Request(_base() + "/v1/audio/transcriptions", data=body,
                                 headers={"Content-Type": ctype})
    key = getattr(config, "STT_LOCAL_API_KEY", "")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=float(getattr(config, "STT_LOCAL_TIMEOUT", 15))) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return _fail(f"HTTP {e.code}: {e.read()[:200]!r}")
    except Exception as e:
        return _fail(str(e))
    text = (data.get("text") or "").strip()
    _state["ok_count"] += 1
    if _state["ok_count"] <= 3 or _state["ok_count"] % 25 == 0:
        log.info(f"Local STT ok in {time.monotonic() - t0:.2f}s: {text!r}")
    return text


def _fail(why: str):
    _state["fail_count"] += 1
    _state["last_error"] = why
    _state["down_until"] = time.monotonic() + float(getattr(config, "STT_LOCAL_RETRY_SECONDS", 30))
    log.warning(f"Local STT unavailable ({why}); using Google for the next "
                f"{getattr(config, 'STT_LOCAL_RETRY_SECONDS', 30)}s. Start it with start_phonon.bat.")
    return None
