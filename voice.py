"""
Eva -- voice. Always-listening by default: runs continuous background
speech recognition and hands recognized phrases to a callback, rather than
requiring a push-to-talk button. stop_always_listening() actually releases
the microphone (not just ignores it) so the mute option is a real one.
"""
import threading
import tempfile
import subprocess
import time
import sys
import asyncio
import os
import edge_tts
import edge_tts.communicate
import edge_tts.constants
from playsound import playsound
import speech_recognition as sr

import config
import stt_local
import stt_nvidia
import tts_nvidia
from logger import get_logger

log = get_logger(__name__)


# One of Edge's free neural voices -- genuinely natural-sounding, unlike
# the legacy SAPI5 engine used before (SAPI5's built-in voices are
# inherently robotic regardless of which one is selected; that's the
# engine generation, not a per-voice difference). To use a different one,
# run `edge-tts --list-voices` for the full catalog -- names follow the
# pattern <locale>-<Name>Neural, e.g. "en-GB-SoniaNeural", "en-US-GuyNeural".
TTS_VOICE = config.TTS_VOICE


# --- Interruptible playback -----------------------------------------------
# playsound() blocks with no way to stop it, so "stop" said mid-sentence
# could not take effect until the utterance finished.
#
# An earlier attempt drove Windows MCI directly from this process. It
# failed, and worse, it broke playback that had been working: eva.log
# 2026-09-26 shows the MCI open failing and then playsound ALSO failing
# with "Error 266 ... Unknown problem while loading the specified device
# driver" on a file it would previously have played. A failed in-process
# MCI open leaves the mpeg device unusable for the rest of the process,
# taking playsound (which is itself MCI-based) down with it.
#
# So playback stays on playsound, exactly as it was before, but runs in a
# CHILD PROCESS. Killing that process stops the audio, which gives
# interruption without touching the audio stack of this one. No new
# dependency; the child is this same interpreter.
_play_lock = threading.Lock()
_play_proc = {"p": None, "stopped": False, "busy": False}


# A persistent player, not one process per utterance. Spawning a fresh
# Python interpreter for every line Eva speaks costs ~0.5s of CPU and a
# burst of disk I/O each time, and on a 4-core machine that shows up
# directly as the avatar's WebGL frame rate stuttering while she talks --
# a regression introduced when playback moved out of process. The helper
# starts once, waits on stdin, and plays whatever path it is handed.
_PLAYER_SRC = r"""
import sys, time
from playsound import playsound

TAIL = float(sys.argv[1]) if len(sys.argv) > 1 else 0.45


def _prime():
    # Wakes the audio endpoint with a short silent clip. Windows puts idle
    # outputs (Bluetooth, HDMI, some USB/onboard) to sleep; the first
    # ~200-400 ms of the next real sound is then swallowed while the device
    # spins up -- heard as the start of a sentence being cut off.
    try:
        import io, wave, winsound
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(b"\x00\x00" * int(22050 * 0.35))
        winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY)
    except Exception:
        pass


def _mci_play(path):
    # Plays through the Windows MCI directly (what playsound does) but keeps
    # the device open for TAIL seconds after MCI reports "stopped". Closing
    # the instant playback ends truncates the last syllable, because the
    # device buffer has not finished draining.
    import ctypes
    mci = ctypes.windll.winmm.mciSendStringW
    buf = ctypes.create_unicode_buffer(255)
    alias = "eva_tts"
    mci("close " + alias, None, 0, 0)
    if mci('open "%s" type mpegvideo alias %s' % (path, alias), None, 0, 0):
        raise RuntimeError("mci open failed")
    try:
        if mci("play %s from 0" % alias, None, 0, 0):
            raise RuntimeError("mci play failed")
        mci("status %s length" % alias, buf, 254, 0)
        try:
            limit = time.time() + int(buf.value) / 1000.0 + 5.0
        except ValueError:
            limit = time.time() + 120.0
        time.sleep(0.05)
        while time.time() < limit:
            mci("status %s mode" % alias, buf, 254, 0)
            if buf.value not in ("playing", "seeking"):
                break
            time.sleep(0.04)
        time.sleep(TAIL)
    finally:
        mci("close " + alias, None, 0, 0)


sys.stdout.write("ready\n"); sys.stdout.flush()
for line in sys.stdin:
    path = line.strip()
    if not path:
        continue
    if path == "PRIME":          # no reply: purely a wake-up
        _prime()
        continue
    try:
        if sys.platform == "win32":
            try:
                _mci_play(path)
            except Exception as exc:
                sys.stderr.write("mci: %s -- falling back\n" % exc)
                playsound(path)
        else:
            playsound(path)
    except Exception as exc:
        sys.stderr.write(str(exc) + "\n"); sys.stderr.flush()
    sys.stdout.write("done\n"); sys.stdout.flush()
"""


def _spawn_player():
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", _PLAYER_SRC, str(config.TTS_TAIL_SECONDS)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if (proc.stdout.readline() or "").strip() != "ready":
        raise RuntimeError("playback helper did not start")
    return proc


_last_audio_end = {"t": 0.0}


def prime_audio():
    """Called as synthesis begins, so the output device is awake by the time
    the sentence is ready. No-op if audio was playing a moment ago."""
    if not config.TTS_PRIME or time.monotonic() - _last_audio_end["t"] < 4.0:
        return
    with _play_lock:
        if _play_proc["busy"]:
            return
    try:
        proc = _player()
        proc.stdin.write("PRIME\n")
        proc.stdin.flush()
    except Exception as e:
        log.debug(f"Audio prime skipped: {e}")


def _player():
    """Returns the live helper, starting or restarting it as needed."""
    proc = _play_proc["p"]
    if proc is None or proc.poll() is not None:
        proc = _spawn_player()
        _play_proc["p"] = proc
    return proc


def _play_interruptible(path: str) -> bool:
    """Plays path, blocking until it finishes OR stop_speaking() is called.
    Returns True if it was stopped early."""
    with _play_lock:
        try:
            proc = _player()
        except Exception as e:
            log.warning(f"Playback helper unavailable ({e}) -- playing inline, not interruptible.")
            playsound(path)
            return False
        _play_proc["stopped"] = False
        _play_proc["busy"] = True
    try:
        proc.stdin.write(path + "\n")
        proc.stdin.flush()
        # Blocks until the helper reports the file finished, or until
        # stop_speaking kills it, which closes stdout and returns "".
        finished = (proc.stdout.readline() or "").strip() == "done"
    except (BrokenPipeError, OSError, ValueError):
        finished = False
    with _play_lock:
        stopped = _play_proc["stopped"]
        _play_proc["busy"] = False
    _last_audio_end["t"] = time.monotonic()
    if not finished and not stopped:
        # The helper died on its own; drop it so the next call respawns.
        with _play_lock:
            _play_proc["p"] = None
        raise RuntimeError("playback helper exited unexpectedly")
    return stopped


def stop_speaking() -> bool:
    """Cuts off whatever Eva is currently saying. Safe to call from any
    thread, including the mic listener thread, and safe when nothing is
    playing. Returns True if speech was actually interrupted."""
    with _play_lock:
        proc = _play_proc["p"]
        if proc is None or not _play_proc["busy"]:
            return False
        _play_proc["stopped"] = True
        _play_proc["p"] = None      # killed; the next call respawns it
    try:
        proc.kill()
    except Exception as e:
        log.warning(f"Could not stop playback: {e}")
        return False
    log.info("Speech interrupted by stop request.")
    return True


def shutdown_player():
    """Stops the persistent playback helper. Without this it outlives the
    app on restart, one orphan per relaunch."""
    with _play_lock:
        proc, _play_proc["p"] = _play_proc["p"], None
        _play_proc["busy"] = False
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass
    return {"ok": True}


def is_speaking() -> bool:
    with _play_lock:
        return _play_proc["busy"]


# --- Where the voice comes from ---------------------------------------------
# 2026-10-17: every line Eva spoke failed with "403 Invalid response status" (eva.log 10:08).
# Microsoft retired the handshake edge-tts 7.2.8 used (around 2026-10-12); edge-tts 7.3.x
# (2026-10-13) speaks the new one, and requirements.txt now asks for it. Two more layers, so
# that the next change on Microsoft's side cannot leave her mute:
#   1. If the voice host refuses (401/403), try once more on the host Edge itself now uses
#      (api.msedgeservices.com, same token), and keep whichever worked.
#   2. If the online voice still fails, speak with the Windows voice (SAPI, offline) and retry
#      the online one only after ONLINE_RETRY_SECONDS, so a dead service costs nothing per line.
_LEGACY_WSS = getattr(edge_tts.communicate, "WSS_URL", "")
_TOKEN = getattr(edge_tts.constants, "TRUSTED_CLIENT_TOKEN", "6A5AA1D4EAFF4E9FB37E23D68491D6F4")
_EDGE_WSS = ("wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
             f"?Ocp-Apim-Subscription-Key={_TOKEN}")
ONLINE_RETRY_SECONDS = 300
_online = {"down_until": 0.0, "switched": False, "local_noted": False, "last": "online"}
_online_lock = threading.Lock()


def _refused(e) -> bool:
    status = getattr(e, "status", None)
    return status in (401, 403) or " 403" in str(e) or " 401" in str(e)


def _synthesize_online(text: str) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        async def _generate():
            await edge_tts.Communicate(text, voice=config.TTS_VOICE).save(tmp_path)
        word_count = len(text.split())
        gen_timeout = max(15, min(60, 5 + word_count / 4))
        asyncio.run(asyncio.wait_for(_generate(), timeout=gen_timeout))
        if os.path.getsize(tmp_path) < 200:
            raise RuntimeError("the online voice returned no audio")
        return tmp_path
    except BaseException:
        discard(tmp_path)
        raise


def _online_attempt(text: str) -> str:
    """The online voice; on a refusal, the other host once."""
    try:
        return _synthesize_online(text)
    except Exception as e:
        if not _refused(e):
            raise
        with _online_lock:
            current = edge_tts.communicate.WSS_URL
            other = _LEGACY_WSS if current == _EDGE_WSS else _EDGE_WSS
            edge_tts.communicate.WSS_URL = other
        log.warning(f"Voice host refused ({e.__class__.__name__}: {str(e)[:60]}); trying "
                    f"{'Edge' if other == _EDGE_WSS else 'the original'} voice host.")
        path = _synthesize_online(text)
        if not _online["switched"]:
            _online["switched"] = True
            log.info("Voice: the other voice host works; keeping it.")
        return path


def _local_voice_name() -> str:
    return str(getattr(config, "TTS_LOCAL_VOICE", "") or "Zira")


def _synthesize_local(text: str) -> str:
    """Windows' own voice (SAPI) into a .wav: offline, always installed."""
    import pythoncom
    import win32com.client
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    pythoncom.CoInitialize()
    try:
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(path, 3)                                   # SSFMCreateForWrite
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        want = _local_voice_name().lower()
        for v in speaker.GetVoices():
            if want in v.GetDescription().lower():
                speaker.Voice = v
                break
        speaker.Rate = int(getattr(config, "TTS_LOCAL_RATE", 1))
        speaker.AudioOutputStream = stream
        speaker.Speak(text)
        stream.Close()
        if os.path.getsize(path) < 200:
            raise RuntimeError("the Windows voice produced no audio")
        return path
    except BaseException:
        discard(path)
        raise
    finally:
        pythoncom.CoUninitialize()


def voice_source() -> str:
    """'nvidia', 'online' or 'windows' -- which voice the last line used (for Status)."""
    return _online["last"]


def synthesize(text: str) -> str:
    """Synthesizes text into a temporary audio file and returns its path (the caller deletes
    it). Online neural voice first; the Windows voice when that fails. Raises only when both
    fail. Bounded: a stalled connection must not hang the speaker."""
    prime_audio()   # device wakes while the sentence is being synthesised
    if tts_nvidia.enabled() and not tts_nvidia.resting():
        try:                                   # NVIDIA Magpie first (tts_nvidia.py): ~3x sooner than Edge
            path = tts_nvidia.synthesize(text)
            if _online["last"] != "nvidia":
                log.info("Voice: NVIDIA Magpie.")
            _online["last"] = "nvidia"
            return path
        except Exception:
            pass                               # logged there; the Edge voice answers instead
    online_error = None
    if time.time() >= _online["down_until"]:
        try:
            path = _online_attempt(text)
            if _online["last"] != "online":
                log.info("Voice: the online voice is back.")
            _online["last"] = "online"
            return path
        except Exception as e:
            online_error = e
            _online["down_until"] = time.time() + ONLINE_RETRY_SECONDS
            log.warning(f"Online voice failed ({str(e)[:120]}); using the Windows voice for the next "
                        f"{ONLINE_RETRY_SECONDS // 60} minutes.")
    if sys.platform != "win32":
        raise online_error or RuntimeError("online voice resting and no Windows voice here")
    try:
        path = _synthesize_local(text)
        _online["last"] = "windows"
        return path
    except Exception as e:
        log.error(f"Windows voice failed too: {e}")
        raise online_error or e


def play_file(path: str) -> bool:
    """Plays a synthesized file, blocking until it ends or stop_speaking() is
    called. Returns True if it was stopped early."""
    return _play_interruptible(path)


def discard(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def _tts_speak_impl(text: str):
    """Synthesize, then play, in one blocking call."""
    try:
        path = synthesize(text)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        return {"ok": True, "interrupted": play_file(path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        discard(path)


_recognizer = sr.Recognizer()

# --- Recogniser tuning ----------------------------------------------------
# Defaults here were the cause of two confirmed complaints: single words
# being missed entirely, and longer requests arriving truncated. Each
# setting below is tunable from config.py without editing this file.
#
# pause_threshold -- how long a silence must last before the phrase is
# considered finished. At 0.8s an ordinary mid-sentence breath ended the
# capture, so the back half of a request was either lost or arrived as a
# separate fragment the model then answered out of context.
_recognizer.pause_threshold = config.STT_PAUSE_THRESHOLD

# phrase_threshold -- minimum duration of speech before a capture counts
# as a phrase at all. The 0.3s default is longer than a spoken "yes",
# "stop", "no" or "Eva", so short commands were discarded as noise before
# ever reaching recognition.
_recognizer.phrase_threshold = config.STT_PHRASE_THRESHOLD

# non_speaking_duration -- audio retained either side of the phrase. Must
# not exceed pause_threshold. Raised a little so the leading consonant of
# a word is not clipped off, which is what turns a one-word command into
# something the recogniser cannot place.
_recognizer.non_speaking_duration = min(config.STT_NON_SPEAKING_DURATION,
                                        config.STT_PAUSE_THRESHOLD)

# dynamic_energy_threshold -- speech_recognition raises the loudness gate
# on its own over time. In a quiet room it drifts upward until a quietly
# spoken single word no longer trips it, and it never recovers within a
# session. Calibration on startup is kept; the drift is not.
_recognizer.dynamic_energy_threshold = config.STT_DYNAMIC_ENERGY

_mic = None
_stop_fn = None
# Session-wide calibration result. See start_always_listening.
_calibration = {"energy": None}
_lock = threading.Lock()


def tts_speak(text: str):
    # No thread-affinity concerns here (each call is its own OS process),
    # so this can run directly on whatever thread calls it -- no executor
    # needed, unlike the old pyttsx3 approach.
    return _tts_speak_impl(text)


STT_DEADLINE_S = 8.0
_stt_pool = None


def _with_deadline(fn, seconds):
    """fn() on a worker, or None after `seconds` (then Google answers). Phrases are recognised one at a time,
    so one request that never returns would leave her "Listening" and deaf for good."""
    global _stt_pool
    import concurrent.futures
    if _stt_pool is None:
        _stt_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt")
    fut = _stt_pool.submit(fn)
    try:
        return fut.result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        log.warning(f"Speech recognition took over {seconds:.0f} s -- using Google for this phrase")
        try:
            stt_nvidia._state["resting_until"] = time.monotonic() + 60
        except Exception:
            pass
        return None
    except Exception:
        return None


def recognize(recognizer, audio):
    """One place for speech-to-text. NVIDIA (STT_BACKEND "nvidia") or local Phonon when enabled and
    reachable, otherwise Google. Raises sr.UnknownValueError when nothing was understood
    (same contract as recognize_google), so both call sites are unchanged."""
    if stt_nvidia.enabled():                             # NVIDIA's free recognisers (stt_nvidia.py)
        try:
            raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        except Exception:
            raw = None
        text = _with_deadline(lambda: stt_nvidia.transcribe(raw), STT_DEADLINE_S) if raw else None
        if text is not None:
            if not text:
                raise sr.UnknownValueError()
            return text
    if stt_local.enabled():
        text = stt_local.transcribe(audio.get_wav_data(convert_rate=16000, convert_width=2))
        if text is not None:
            if not text:
                raise sr.UnknownValueError()
            return text
    # With show_all the recogniser also says how sure it was and what else it may
    # have heard; core/hearing.py passes that to the model with the text.
    try:
        res = recognizer.recognize_google(audio, show_all=True)
    except TypeError:                                    # an old speech_recognition without show_all
        return recognizer.recognize_google(audio)
    alts = res.get("alternative") if isinstance(res, dict) else None
    if not alts:
        raise sr.UnknownValueError()
    best = str(alts[0].get("transcript") or "").strip()
    if not best:
        raise sr.UnknownValueError()
    try:
        from core import hearing
        hearing.note_alternatives(best, [a.get("transcript", "").strip() for a in alts[1:]],
                                  alts[0].get("confidence"))
    except Exception:
        pass
    return best



def stt_listen(timeout_ms: int = 6000):
    """One-shot listen, kept for callers that want a single manual capture
    rather than the continuous mode below."""
    # Opening a second Microphone while listen_in_background holds the same
    # device produces either an outright PyAudio failure or silent garbage,
    # depending on the backend. stt_listen is exposed to the model as a
    # tool, so it WILL get called during continuous mode; refuse clearly
    # instead of corrupting the shared device.
    if is_listening():
        return {"text": None,
                "error": "the microphone is already in continuous listening mode -- "
                         "whatever the person says arrives on its own, no capture needed"}
    with sr.Microphone() as source:
        _recognizer.adjust_for_ambient_noise(source, duration=0.3)
        try:
            audio = _recognizer.listen(source, timeout=timeout_ms / 1000,
                                       phrase_time_limit=config.STT_PHRASE_TIME_LIMIT)
            return {"text": recognize(_recognizer, audio)}
        except sr.WaitTimeoutError:
            return {"text": None, "error": "timeout"}
        except sr.UnknownValueError:
            return {"text": None, "error": "unintelligible"}


def _rms(buf: bytes, width: int = 2) -> float:
    try:
        import audioop                                   # stdlib up to 3.12; audioop-lts after
        return float(audioop.rms(buf, width))
    except Exception:
        import array
        a = array.array("h", buf[: len(buf) - len(buf) % 2])
        return (sum(x * x for x in a) / len(a)) ** 0.5 if a else 0.0


class Endpointer:
    """Where a phrase starts and ends, from a stream of microphone chunks. Pure (checked by run_checks).

    2026-10-17 21:13: "she cuts off my mic at the beginning and end sometimes". Two causes left after the
    capture/recognition split (_ContinuousListener):
      * the BEGINNING: speech_recognition's listen(timeout=1) was called in a loop; every second with
        nobody talking it gave up and started again with an EMPTY buffer, so a request that began just
        then lost its run-up -- the soft first sound ("M" of "make", "O" of "OTP"). Here the ring of the
        last PREROLL seconds is never thrown away; a phrase always starts with it.
      * the END: one pause length for everything. A short opening ("I think", "open") is usually
        followed by the rest after a breath; it now waits SHORT_PAUSE for the rest before ending.
    The loudness gate follows the room (its quietest moment in the last 3 s -- speech has gaps, a fan has
    none; never below the calibration, never above the ceiling), so a fan that starts later cannot keep a
    phrase open for ever.
    feed(chunk) -> the bytes of a finished phrase, or None."""

    def __init__(self, rate, width, chunk, threshold, *, preroll=1.0, start=0.15, pause=1.3, short_pause=2.0,
                 short_speech=1.2, tail=0.5, limit=20.0, min_speech=0.2, cap=1200.0, adapt=True):
        self.width, self.sec = width, chunk / float(rate)
        self.base = float(threshold)
        self.thr = float(threshold)
        self.cap, self.adapt = float(cap), adapt
        import collections
        self.recent = collections.deque(maxlen=max(4, int(round(3.0 / self.sec))))   # the room: last 3 s
        self.ring = collections.deque(maxlen=max(1, int(round(preroll / self.sec))))
        self.ring_e = collections.deque(maxlen=self.ring.maxlen)
        self.meta = {}
        self.start_n = max(1, int(round(start / self.sec)))
        self.pause, self.short_pause, self.short_speech = pause, short_pause, short_speech
        self.tail_n = max(1, int(round(tail / self.sec)))
        self.limit, self.min_speech = limit, min_speech
        self._reset()

    def _reset(self):
        self.frames, self.loud, self.silent, self.speech, self.active = [], 0, 0, 0.0, False

    def feed(self, buf):
        e = _rms(buf, self.width)
        if self.adapt:
            # The room's level = the QUIETEST moment of the last 3 s: speech has gaps between words,
            # a fan or an air conditioner has none. The gate sits above it, never below the
            # calibration and never above the ceiling.
            self.recent.append(e)
            if len(self.recent) == self.recent.maxlen:
                self.thr = min(self.cap, max(self.base, min(self.recent) * 2.2))
        if not self.active:
            self.ring.append(buf)
            self.ring_e.append(e)
            self.loud = self.loud + 1 if e > self.thr else 0
            if self.loud >= self.start_n:
                self.active = True
                self.frames = list(self.ring)            # the run-up is kept, always
                # Was the voice already there at the very start of the run-up? Then the run-up was too
                # short and the first sound may still be lost -- said in the log, and measured per phrase.
                quiet = self.thr * 0.5
                lead = 0
                for v in self.ring_e:
                    if v > quiet:
                        break
                    lead += 1
                self.meta = {"lead_ms": int(lead * self.sec * 1000), "gate": int(self.thr),
                             "clipped_start": bool(self.ring_e) and self.ring_e[0] > quiet}
                self.ring_e.clear()
                self.speech, self.silent = self.loud * self.sec, 0
                self.ring.clear()
            return None
        self.frames.append(buf)
        if e > self.thr * 0.75:                          # hysteresis: trailing-off words still count
            self.speech += self.sec
            self.silent = 0
        else:
            self.silent += 1
        needed = self.short_pause if self.speech < self.short_speech else self.pause
        if self.silent * self.sec >= needed:
            keep = len(self.frames) - self.silent + self.tail_n
            out, speech = b"".join(self.frames[:keep]), self.speech
            self._reset()
            return out if speech >= self.min_speech else None
        if len(self.frames) * self.sec >= self.limit:
            out = b"".join(self.frames)
            log.warning(f"Speech reached the {self.limit:g} s limit and was cut there (config.STT_PHRASE_TIME_LIMIT)")
            tail = self.frames[-self.tail_n:]
            self._reset()
            self.active, self.frames, self.speech = True, list(tail), self.sec   # the rest goes on, unclipped
            return out
        return None


class _ContinuousListener:
    """The microphone is read ALL the time; recognition happens on a second thread.

    eva.log 2026-10-17 16:04-16:12: speech_recognition's listen_in_background runs the
    recognition callback ON the listening thread, so while Google recognised one phrase
    (0.5-2 s) nobody read the microphone: the next words were lost or clipped -- "OTP"
    arrived as "TP", "look up flight" as "look up light", and "plan a trip ... compare
    travel websites" as two fragments with the middle missing. Here one thread only
    captures (Recognizer.listen in a loop: pre-roll, pause detection, phrase limit as
    before) and hands each phrase to a queue; a worker recognises them in order. A slow
    recognition now delays the answer, never the hearing.

    start() returns stop(wait_for_stop=False), the same contract as listen_in_background."""

    def __init__(self, recognizer, source, callback, phrase_time_limit=None):
        import queue as _queue
        self.r, self.source, self.callback, self.limit = recognizer, source, callback, phrase_time_limit
        self.q = _queue.Queue()
        self.running = threading.Event()
        self.capture = threading.Thread(target=self._capture, name="eva-mic-capture", daemon=True)
        self.worker = threading.Thread(target=self._recognise, name="eva-mic-recognise", daemon=True)

    def start(self):
        self.running.set()
        self.capture.start()
        self.worker.start()
        return self.stop

    def stop(self, wait_for_stop=False):
        self.running.clear()
        self.q.put(None)
        if wait_for_stop:
            self.capture.join(timeout=3)

    def _capture(self):
        try:
            with self.source as s:
                stream = getattr(s, "stream", None)
                if stream is not None and getattr(config, "STT_OWN_ENDPOINTING", True):
                    return self._capture_stream(s, stream)
                while self.running.is_set():
                    try:
                        audio = self.r.listen(s, timeout=1, phrase_time_limit=self.limit)
                    except sr.WaitTimeoutError:
                        continue
                    if not self.running.is_set():
                        break
                    seconds = len(audio.frame_data) / float(audio.sample_rate * audio.sample_width)
                    if self.limit and seconds >= self.limit - 0.2:
                        log.warning(f"Speech reached the {self.limit} s limit and was cut there "
                                    "(config.STT_PHRASE_TIME_LIMIT)")
                    self.q.put((audio, seconds, time.monotonic()))
        except Exception as e:
            log.exception("Microphone capture stopped")
            try:
                self.callback_error(e)
            except Exception:
                pass
        finally:
            self.q.put(None)

    def callback_error(self, e):
        pass

    def _capture_stream(self, s, stream):
        """The microphone read without a break; Endpointer decides where phrases start and end."""
        rate, width, chunk = s.SAMPLE_RATE, s.SAMPLE_WIDTH, s.CHUNK
        ep = Endpointer(rate, width, chunk, self.r.energy_threshold,
                        preroll=getattr(config, "STT_PREROLL_SECONDS", 1.0),
                        start=config.STT_PHRASE_THRESHOLD, pause=config.STT_PAUSE_THRESHOLD,
                        short_pause=getattr(config, "STT_SHORT_PHRASE_PAUSE", 2.0),
                        tail=min(config.STT_NON_SPEAKING_DURATION, 0.6),
                        limit=self.limit or 30, cap=config.STT_MAX_ENERGY_THRESHOLD)
        log.info(f"Microphone: continuous capture, {rate} Hz, gate {ep.thr:.0f}, run-up {ep.ring.maxlen * ep.sec:.1f} s")
        _capture_state["started"] = True
        for fn in list(_capture_state["hooks"]):
            try:
                fn()
            except Exception:
                log.exception("A capture-started hook failed")
        settle = int(0.5 * rate / chunk)       # the first 0.5 s is the device switching on (a burst of ~100)
        while self.running.is_set():
            buf = stream.read(chunk)
            if not buf:
                continue
            if settle > 0:
                settle -= 1
                continue
            phrase = ep.feed(buf)
            if phrase and self.running.is_set():
                seconds = len(phrase) / float(rate * width)
                audio = sr.AudioData(phrase, rate, width)
                audio.eva_meta = dict(ep.meta, seconds=round(seconds, 2))
                if audio.eva_meta.get("clipped_start"):
                    log.warning("The voice was already there at the start of the 1 s run-up -- the first sound "
                                "of this phrase may be cut (raise STT_PREROLL_SECONDS)")
                self.q.put((audio, seconds, time.monotonic()))

    def _recognise(self):
        while True:
            item = self.q.get()
            if item is None:
                if not self.running.is_set():
                    return
                continue
            audio, seconds, queued = item
            started = time.monotonic()
            log.info(f"Heard a phrase of {seconds:.1f} s (gate {getattr(audio, 'eva_meta', {}).get('gate', '?')})")
            self.callback(self.r, audio)
            log.debug(f"Phrase of {seconds:.1f} s: waited {started - queued:.2f} s, "
                      f"recognised in {time.monotonic() - started:.2f} s")


HEARING_DIR = os.path.join(os.path.expanduser("~"), ".eva", "hearing")


def keep_phrase(audio, text, folder=None, keep=None):
    """The last STT_KEEP_PHRASES phrases as she heard them (a WAV each) with what was recognised, on this PC
    only (~/.eva/hearing). 2026-10-18 00:11: "speech getting cut off at the start of a sentence" -- whether
    the start is missing from the AUDIO (capture) or only from the TEXT (recognition) can be told from these,
    instead of guessed."""
    keep = int(getattr(config, "STT_KEEP_PHRASES", 20) if keep is None else keep)
    if keep <= 0:
        return None
    folder = folder or HEARING_DIR
    try:
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        path = os.path.join(folder, stamp + ".wav")
        with open(path, "wb") as f:
            f.write(audio.get_wav_data())
        import json as _json
        row = {"at": stamp, "text": text or "", **(getattr(audio, "eva_meta", None) or {})}
        with open(os.path.join(folder, "heard.jsonl"), "a", encoding="utf-8") as f:
            f.write(_json.dumps(row, ensure_ascii=False) + "\n")
        wavs = sorted(n for n in os.listdir(folder) if n.endswith(".wav"))
        for n in wavs[:-keep]:
            try:
                os.remove(os.path.join(folder, n))
            except OSError:
                pass
        return path
    except Exception as e:
        log.info(f"Could not keep the phrase for checking: {e}")
        return None


def _room_gate(energies, skip, floor, factor, cap):
    """(room level, gate) from chunk loudness: the first `skip` chunks are the device switching on (a click
    of 60-270 on 2026-10-28 against a room of 4), so they are not the room. Room = the median of the rest."""
    rest = sorted(energies[skip:]) or sorted(energies) or [0.0]
    room = rest[len(rest) // 2]
    return room, max(float(floor), min(float(cap), room * float(factor)))


def calibrate(source, seconds=None):
    """2026-10-28 "it says Listening but actually it is not": speech_recognition's calibration averaged in
    the microphone's switch-on click, so the gate came out ~95 for a room of 4, and "never below the
    calibration" kept it there -- a voice reaching the mic at 40-80 never opened a phrase."""
    seconds = float(seconds or getattr(config, "STT_CALIBRATION_SECONDS", 1.0))
    chunk = source.CHUNK
    skip = max(1, int(round(0.4 * source.SAMPLE_RATE / chunk)))
    n = skip + max(4, int(round(seconds * source.SAMPLE_RATE / chunk)))
    energies = []
    for _ in range(n):
        buf = source.stream.read(chunk)
        energies.append(_rms(buf, source.SAMPLE_WIDTH))
    return _room_gate(energies, skip, getattr(config, "STT_MIN_GATE", 12),
                      getattr(config, "STT_GATE_FACTOR", 3.0), config.STT_MAX_ENERGY_THRESHOLD)


_HEADSET = ("headset", "hands-free", "handsfree", "buds", "airpods", "earbuds", "earphone", "bluetooth", "jabra",
            "bose", "sony wh", "sony wf", "galaxy buds", "pixel buds", "oppo enco", "oneplus buds")
_chosen = {"index": None, "name": "?"}


def _pick_input_device(devices=None, wanted=None):
    """Which microphone to listen to. 2026-10-28 live: the person spoke into OPPO earbuds (level ~9000) while
    Eva listened to the laptop's built-in array (Windows' default input, level 65-114) -- she heard nothing.
    config.STT_INPUT_DEVICE: "auto" (a connected headset or earbuds first, else the default) or part of a
    device's name. devices: [(index, name)] (injectable); None = ask PyAudio (MME inputs)."""
    wanted = str(wanted if wanted is not None else getattr(config, "STT_INPUT_DEVICE", "auto") or "auto").strip()
    if devices is None:
        devices = []
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            try:
                for i in range(pa.get_device_count()):
                    d = pa.get_device_info_by_index(i)
                    if d.get("maxInputChannels", 0) > 0 and d.get("hostApi") == 0 and "mapper" not in d["name"].lower():
                        devices.append((i, d["name"]))
            finally:
                pa.terminate()
        except Exception:
            devices = []
    pick = None
    if wanted.lower() != "auto":
        pick = next(((i, n) for i, n in devices if wanted.lower() in n.lower()), None)
        if pick is None:
            log.warning(f"No microphone matching {wanted!r}; using auto")
    if pick is None:
        pick = next(((i, n) for i, n in devices if any(h in n.lower() for h in _HEADSET)), None)
    _chosen.update(index=pick[0] if pick else None, name=pick[1] if pick else "the Windows default")
    return _chosen["index"]


def _device_name():
    if _chosen["index"] is not None:
        return _chosen["name"]
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        try:
            return pa.get_default_input_device_info().get("name", "?")
        finally:
            pa.terminate()
    except Exception:
        return "?"


_capture_state = {"started": False, "hooks": []}


def on_capture_started(fn):
    """fn() once the microphone is really being read (the status says Listening only then)."""
    _capture_state["hooks"].append(fn)
    if _capture_state["started"]:
        fn()


def capturing() -> bool:
    return _capture_state["started"]


def start_always_listening(on_heard, on_error=None):
    """Starts continuous background listening. on_heard(text) fires for
    every recognized phrase. Safe to call repeatedly -- no-ops if already
    listening. Runs on speech_recognition's own background thread; on_heard
    should hand off any Qt-touching work itself (it will be called from
    that background thread, not the GUI thread)."""
    global _mic, _stop_fn
    with _lock:
        if _stop_fn is not None:
            return  # already listening
        _mic = sr.Microphone(device_index=_pick_input_device())
        # Calibrate ONCE per session, not on every restart. The mic is
        # stopped and restarted around every single TTS utterance, and
        # adjust_for_ambient_noise BLOCKS for its full duration with the
        # mic open but not listening -- so for ~1s after Eva finished
        # speaking, anything said was simply not captured. That is the
        # confirmed cause of the opening words of a request going missing,
        # and it got worse when calibration time was raised. The room does
        # not change between one utterance and the next; one calibration
        # per session is enough.
        if _calibration["energy"] is None:
            with _mic as source:
                room, gate = calibrate(source)
            _recognizer.energy_threshold = gate
            _calibration["energy"] = gate
            _calibration["room"] = room
            log.info(f"Mic calibrated: room {room:.0f}, gate {gate:.0f} (device: {_device_name()}) "
                     f"pause={_recognizer.pause_threshold} phrase_limit={config.STT_PHRASE_TIME_LIMIT}")
        else:
            _recognizer.energy_threshold = _calibration["energy"]

        def callback(recognizer, audio):
            try:
                text = recognize(recognizer, audio)
                keep_phrase(audio, text)
                if text:
                    on_heard(text)
            except sr.UnknownValueError:
                keep_phrase(audio, "")
                pass  # background noise / nothing intelligible -- expected, ignore
            except sr.RequestError as e:
                if on_error:
                    on_error(str(e))
            except Exception as e:
                # Anything else (a bug in on_heard, a Qt call failing, etc.)
                # must NOT be allowed to propagate out of this callback --
                # speech_recognition's background-listener thread has no
                # protection around this call, so an uncaught exception here
                # silently kills always-listening mode for the rest of the
                # session with no visible error. Surface it instead of
                # crashing the listener.
                if on_error:
                    on_error(f"listener error: {e}")

        # phrase_time_limit is a HARD cut: at 8s any request still being
        # spoken was chopped mid-word and sent for recognition as-is. It
        # cannot be removed entirely (a stuck-open mic would never
        # dispatch), but it should sit well above a normal spoken request
        # and let pause_threshold decide when speech has actually ended.
        listener = _ContinuousListener(_recognizer, _mic, callback,
                                       phrase_time_limit=config.STT_PHRASE_TIME_LIMIT)
        listener.callback_error = lambda e: on_error and on_error(f"the microphone stopped: {e}")
        _stop_fn = listener.start()


def stop_always_listening(wait_for_stop: bool = False):
    """Actually releases the microphone -- this is the real mute, not a
    filter that quietly drops what it hears.

    wait_for_stop=False (the default) returns immediately without waiting
    for the background listener thread to actually finish tearing down --
    fine for the manual mute toggle, where a snappy UI response matters
    more than split-second precision. It is NOT fine for pausing the mic
    right before TTS starts speaking: with the default, the mic can still
    be capturing audio for a brief window after this call returns, so the
    very start of Eva's own speech can bleed into it and get transcribed --
    a real feedback-loop race, not just a hypothetical one. Callers pausing
    for TTS should pass wait_for_stop=True to actually block until the
    microphone is guaranteed closed before speech begins."""
    global _stop_fn
    with _lock:
        if _stop_fn is not None:
            _stop_fn(wait_for_stop=wait_for_stop)
            _stop_fn = None


def recalibrate_mic():
    """Discards the cached calibration so the next start_always_listening
    re-measures the room. For a deliberate call (moved rooms, changed
    headset) -- not run automatically, for the reason above."""
    _calibration["energy"] = None


def is_listening() -> bool:
    return _stop_fn is not None
