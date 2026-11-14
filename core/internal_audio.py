"""Eva -- internal audio hearing via WASAPI loopback (core/internal_audio.py).

Captures what the PC is playing through its speakers or headphones (YouTube,
meetings, videos, music, games) using PyAudioWPatch loopback, downmixes to
16 kHz mono PCM, and transcribes the spoken speech using the active STT engine.
"""
import io
import queue
import time
import wave
from typing import Optional

import numpy as np

import config
from logger import get_logger

log = get_logger(__name__)


def is_available() -> bool:
    try:
        import music
        return music.WasapiLoopback.available()
    except Exception:
        return False


def capture_internal_audio(seconds: float = 5.0, transcribe: bool = True) -> dict:
    """Capture seconds of internal audio from default output speakers/headphones."""
    import music
    if not music.WasapiLoopback.available():
        return {
            "ok": False,
            "error": "Internal audio capture requires Windows and pyaudiowpatch",
            "playing": False,
            "text": ""
        }

    seconds = max(1.0, min(float(seconds or 5.0), 30.0))
    lb = music.WasapiLoopback()
    try:
        lb.open()
    except Exception as e:
        log.warning("Could not open WASAPI loopback: %s", e)
        return {
            "ok": False,
            "error": f"Could not access speakers loopback: {e}",
            "playing": False,
            "text": ""
        }

    chunks = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            chunk = lb.read(timeout=0.1)
            if chunk is not None and len(chunk) > 0:
                chunks.append(chunk)
    finally:
        lb.close()

    if not chunks:
        return {
            "ok": True,
            "seconds": seconds,
            "playing": False,
            "rms": 0.0,
            "text": "",
            "note": "No audio was playing through the speakers during this time (silence)."
        }

    all_samples = np.concatenate(chunks)
    rms = float(np.sqrt(np.mean(all_samples ** 2)))
    peak = float(np.max(np.abs(all_samples)))
    is_playing = rms > 0.005 or peak > 0.02

    if not is_playing or not transcribe:
        return {
            "ok": True,
            "seconds": seconds,
            "playing": is_playing,
            "rms": round(rms, 4),
            "peak": round(peak, 4),
            "text": "",
            "note": "Internal audio is silent or too faint to transcribe." if not is_playing else "Audio captured."
        }

    # Resample to 16,000 Hz 16-bit mono PCM for speech-to-text
    src_sr = lb.sr or 48000
    target_sr = 16000
    target_len = int(len(all_samples) * target_sr / src_sr)
    if target_len <= 0:
        return {"ok": True, "seconds": seconds, "playing": False, "rms": rms, "text": ""}

    resampled = np.interp(
        np.linspace(0, len(all_samples) - 1, target_len),
        np.arange(len(all_samples)),
        all_samples
    )
    pcm_16 = (np.clip(resampled, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()

    text = _transcribe_pcm(pcm_16, target_sr)
    return {
        "ok": True,
        "seconds": seconds,
        "playing": True,
        "rms": round(rms, 4),
        "text": text or "",
        "heard": bool(text)
    }


def _transcribe_pcm(pcm_16: bytes, sample_rate: int = 16000) -> str:
    """Send PCM audio to active STT engine (NVIDIA / Groq / Google)."""
    # 1. Try NVIDIA STT if enabled
    try:
        import stt_nvidia
        if stt_nvidia.enabled():
            txt = stt_nvidia.transcribe(pcm_16)
            if txt:
                return txt.strip()
    except Exception as e:
        log.debug("Internal audio NVIDIA STT error: %s", e)

    # 2. Try speech_recognition with Google fallback
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        audio_data = sr.AudioData(pcm_16, sample_rate, 2)
        try:
            return r.recognize_google(audio_data).strip()
        except sr.UnknownValueError:
            return ""
        except Exception as e:
            log.debug("Internal audio Google STT error: %s", e)
    except Exception:
        pass

    return ""
