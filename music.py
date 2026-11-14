"""Listening to music (Phase 4): what the PC plays, beat by beat.

Capture is WASAPI loopback -- the sound going to the speakers, never the microphone -- through
PyAudioWPatch (a PyAudio fork; the module is `pyaudiowpatch`, so it does not clash with the plain
PyAudio the microphone uses). It is imported lazily: without it, or off Windows, the feature is simply
off and one line says so in the log.

Privacy: audio is analysed in memory only. The only audio held is the current 1024-sample frame
remainder; everything kept after that is numbers (an onset envelope, levels). Nothing is recorded,
written to disk or sent anywhere.

The analysis is pure numpy and works on any mono signal, so the checks feed it synthetic ones:
  OnsetDetector   100 Hz frames: spectral-flux onset strength, RMS, low (<200 Hz) and high (>4 kHz) energy
  estimate_tempo  autocorrelation of the onset envelope, 70-180 BPM, plus a confidence
  beat_phase      where the last beat fell (comb over the recent envelope)
  MusicAnalyzer   puts them together: {playing, bpm, phase, energy, low, high, conf}
"Playing" means music: a steady beat that holds for a few seconds on sustained sound. Speech,
notifications and silence do not count.
MusicListener runs the capture and the analysis on a background thread; the GUI thread only reads
`latest()`.
"""
import collections
import importlib.util
import math
import queue
import sys
import threading
import time

import numpy as np

import config
from logger import get_logger

log = get_logger(__name__)

BPM_MIN, BPM_MAX = 70.0, 180.0
FLOOR_DB = -52.0                  # quieter than this is silence
CONF_ON, CONF_OFF = 0.30, 0.18    # beat confidence to start / keep "playing"
STEADY_S = 2.0                    # a steady beat this long before she believes it is music
MESSY_OFF_S = 4.0                 # no steady beat for this long (silence ends it after 1.5 s)

_logged = set()


def log_once(key, msg):
    """One warning per reason per process: a missing library must not fill the log."""
    if key in _logged:
        return
    _logged.add(key)
    log.warning(msg)


# ------------------------------------------------------------------------------------------------
# Pure analysis
# ------------------------------------------------------------------------------------------------
def _detrend(env, fps):
    """Onset envelope minus its local mean (0.5 s), half-wave rectified: the peaks that stand out."""
    k = max(3, int(0.5 * fps) | 1)
    mean = np.convolve(env, np.ones(k) / k, mode="same")
    return np.maximum(np.asarray(env, dtype=np.float64) - mean, 0.0)


def estimate_tempo(env, fps, lo=BPM_MIN, hi=BPM_MAX, prior=120.0):
    """(bpm, confidence) from an onset envelope sampled at `fps`. (0, 0) if there is too little of it.

    Autocorrelation at the lag of each candidate tempo plus half the correlation two beats away (a real
    pulse repeats), and a mild log-tempo prior that settles octave doubt toward everyday dance tempos.
    Confidence is the normalised autocorrelation at the chosen lag: ~0.8 for a steady pulse, low for
    speech or noise."""
    env = np.asarray(env, dtype=np.float64)
    n = len(env)
    if n < int(3.0 * fps):
        return 0.0, 0.0
    x = _detrend(env, fps)
    # a steady tone or hiss has no onsets: its envelope is flat, and normalising would turn
    # numerical dust into a perfect pulse
    if float(env.max()) < 2.0 or float(x.max()) < 1.0 * float(env.mean()):
        return 0.0, 0.0
    x = x - x.mean()
    energy = float((x * x).sum())
    if energy <= 1e-12:
        return 0.0, 0.0
    maxlag = int(fps * 60.0 / lo * 2.0) + 3
    if maxlag >= n:
        return 0.0, 0.0
    ac = np.correlate(x, x, mode="full")[n - 1:n + maxlag]
    r = ac / (n - np.arange(len(ac))) / (energy / n)        # unbiased, r[0] = 1
    # onsets 30-90 ms apart over and over are a buzz or a pure tone's numerical flutter, not a beat
    if float(r[3:10].max()) > 0.6:
        return 0.0, 0.0
    lags = np.arange(len(r))
    bpms = np.arange(lo, hi + 0.01, 0.25)
    lag = fps * 60.0 / bpms
    r1 = np.interp(lag, lags, r)
    r2 = np.interp(2.0 * lag, lags, r)
    w = np.exp(-0.5 * (np.log2(bpms / prior) / 0.9) ** 2)
    score = (r1 + 0.5 * r2) * w
    i = int(np.argmax(score))
    lo_i, hi_i = int(math.floor(lag[i])), int(math.ceil(lag[i]))
    # a pulse repeats: the correlation two beats on counts as much as one beat on (chance does not)
    r2_i = min(len(r) - 1, int(round(2.0 * lag[i])))
    conf = 0.5 * (float(max(r[lo_i], r[hi_i])) + max(0.0, float(r[r2_i])))
    return float(bpms[i]), max(0.0, min(1.0, conf))


def beat_phase(env, fps, bpm, seconds=6.0):
    """Index (in `env`, fractional) of the last beat of a pulse of `bpm`: the comb offset whose beats
    land on the most onset energy over the last `seconds`."""
    env = np.asarray(env, dtype=np.float64)
    n = min(len(env), int(seconds * fps))
    if n < 8 or bpm <= 0:
        return None
    x = _detrend(env, fps)[-n:]
    x = np.convolve(x, [0.25, 0.5, 0.25], mode="same")
    period = fps * 60.0 / bpm
    offsets = np.arange(0.0, period, 0.5)
    ks = np.arange(0, int(n / period) + 2)
    pos = offsets[:, None] + ks[None, :] * period
    valid = pos <= n - 1
    vals = np.interp(pos.ravel(), np.arange(n), x).reshape(pos.shape) * valid
    score = vals.sum(axis=1) / np.maximum(1, valid.sum(axis=1))
    best = float(offsets[int(np.argmax(score))])
    last = best + math.floor((n - 1 - best) / period) * period
    return (len(env) - n) + last


class OnsetDetector:
    """Frames the stream (Hann 1024, hop = sr/100) and yields per frame:
    (flux, rms, low, high) -- onset strength and levels. Holds only the unprocessed remainder."""

    def __init__(self, sr):
        self.sr = int(sr)
        self.win = 1024 if self.sr <= 48000 else 2048
        self.hop = max(1, self.sr // 100)
        self.fps = self.sr / self.hop
        self.window = np.hanning(self.win).astype(np.float32)
        f = np.fft.rfftfreq(self.win, 1.0 / self.sr)
        # onset strength is the flux of six log-spaced bands, bass weighted up: a kick drum is a few
        # bins, hats are hundreds, and the beat people dance to is the bass
        edges = [30, 100, 250, 700, 1800, 4500, 9000]
        self._bands = [((f >= lo) & (f < hi)) for lo, hi in zip(edges[:-1], edges[1:])]
        self._band_w = np.array([2.5, 1.5, 1.0, 0.8, 0.6, 0.5])
        self._low = (f >= 20) & (f < 200)
        self._high = (f >= 4000) & (f <= 16000)
        self._buf = np.zeros(0, dtype=np.float32)
        self._prev = None

    def feed(self, x):
        x = np.asarray(x, dtype=np.float32).ravel()
        self._buf = np.concatenate([self._buf, x]) if len(self._buf) else x.copy()
        if len(self._buf) < self.win:
            return []
        n = (len(self._buf) - self.win) // self.hop + 1
        frames = np.lib.stride_tricks.sliding_window_view(self._buf, self.win)[::self.hop][:n]
        spec = np.abs(np.fft.rfft(frames * self.window, axis=1))
        self._buf = self._buf[n * self.hop:]
        comp = np.log1p(spec * 10.0)
        if self._prev is None:
            self._prev = comp[0]
        prev = np.vstack([self._prev[None, :], comp[:-1]])
        self._prev = comp[-1]
        d = np.maximum(comp - prev, 0.0)
        flux = sum(w * d[:, m].mean(axis=1) for w, m in zip(self._band_w, self._bands)) * 40.0
        rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
        low = np.sqrt((spec[:, self._low] ** 2).sum(axis=1)) / self.win
        high = np.sqrt((spec[:, self._high] ** 2).sum(axis=1)) / self.win
        return list(zip(flux.tolist(), rms.tolist(), low.tolist(), high.tolist()))


class MusicAnalyzer:
    """feed() mono float samples at `sr` (any block size); state() is what the page needs."""

    def __init__(self, sr):
        self.det = OnsetDetector(sr)
        self.sr = self.det.sr
        self.fps = self.det.fps
        keep = int(12 * self.fps)
        self.env = collections.deque(maxlen=keep)
        self.rms = collections.deque(maxlen=int(5 * self.fps))
        self.frames = 0                      # frames processed so far
        self.samples = 0                     # samples fed so far (the audio clock)
        self._power = 0.0
        self._ref_db = -40.0
        self._low_s = self._high_s = 0.0
        self._low_peak = self._high_peak = 1e-6
        self.energy = self.low = self.high = 0.0
        self.bpm = 0.0
        self.conf = 0.0
        self.playing = False
        self._anchor = None
        self._bpm_s = 0.0
        self._jump = 0
        self._recent = collections.deque(maxlen=8)
        self._steady_since = None
        self._lost_since = None
        self._last_analysis = -1.0

    # -- feeding -------------------------------------------------------------------------------
    def feed(self, x):
        x = np.asarray(x, dtype=np.float32).ravel()
        self.samples += len(x)
        a_pow = 1.0 - math.exp(-1.0 / (0.4 * self.fps))
        a_band = 1.0 - math.exp(-1.0 / (0.12 * self.fps))
        for flux, rms, low, high in self.det.feed(x):
            self.frames += 1
            self.env.append(flux)
            self.rms.append(rms)
            self._power += a_pow * (rms * rms - self._power)
            db = 10.0 * math.log10(self._power + 1e-12)
            self._ref_db = max(db, self._ref_db - 0.004, -40.0)      # slow peak: ~0.4 dB per second
            self.energy = max(0.0, min(1.0, (db - (self._ref_db - 30.0)) / 30.0))
            self._low_s += a_band * (low - self._low_s)
            self._high_s += a_band * (high - self._high_s)
            self._low_peak = max(self._low_s, self._low_peak * 0.9995, 1e-6)
            self._high_peak = max(self._high_s, self._high_peak * 0.9995, 1e-6)
            self.low = min(1.0, self._low_s / self._low_peak) if self._low_s > 1e-5 else 0.0
            self.high = min(1.0, self._high_s / self._high_peak) if self._high_s > 1e-5 else 0.0
        now = self.samples / self.sr
        if self.frames and now - self._last_analysis >= 0.25:
            self._last_analysis = now
            self._analyse(now)

    # -- the slower decisions, ~4 times a second ----------------------------------------------
    def _frame_time(self, index):
        """Seconds (audio clock) at the centre of frame `index`."""
        return (index * self.det.hop + self.det.win / 2.0) / self.sr

    def _active_fraction(self):
        r = np.fromiter(self.rms, dtype=np.float64)
        slots = int(4)
        need = slots * int(self.fps)
        if len(r) < need:
            r = np.concatenate([np.zeros(need - len(r)), r])
        r = r[-need:].reshape(slots, int(self.fps))
        floor = 10.0 ** (FLOOR_DB / 20.0)
        return float((r.max(axis=1) > floor).mean())

    def _analyse(self, now):
        env = np.fromiter(self.env, dtype=np.float64)
        bpm, conf = estimate_tempo(env, self.fps)
        active = self._active_fraction()
        if bpm > 0:
            self._recent.append(bpm)
        stable = False
        if len(self._recent) >= 6:
            tail = list(self._recent)[-6:]
            med = float(np.median(tail))
            stable = (max(tail) - min(tail)) / med < 0.05
        self.conf = conf if active >= 0.5 else 0.0
        # tempo: smoothed, and a big jump needs three agreeing estimates
        if bpm > 0:
            if self._bpm_s <= 0:
                self._bpm_s = bpm
            elif abs(bpm - self._bpm_s) / self._bpm_s > 0.06:
                self._jump += 1
                if self._jump >= 3:
                    self._bpm_s, self._anchor, self._jump = bpm, None, 0
            else:
                self._jump = 0
                self._bpm_s += 0.35 * (bpm - self._bpm_s)
        self.bpm = self._bpm_s
        # beat grid: the last beat in the audio clock, nudged gently so the phase is continuous
        if self._bpm_s > 0 and conf > CONF_OFF * 0.6:
            idx = beat_phase(env, self.fps, self._bpm_s)
            if idx is not None:
                first = self.frames - len(env)
                est = self._frame_time(first + idx)
                period = 60.0 / self._bpm_s
                if self._anchor is None:
                    self._anchor = est
                else:
                    proj = self._anchor + period * round((est - self._anchor) / period)
                    self._anchor = proj + 0.3 * (est - proj)
        # is it music? a steady beat on sustained sound
        quiet = self._quiet_for(1.5)
        thresh = CONF_OFF if self.playing else CONF_ON
        steady = active >= 0.75 and conf >= thresh and stable
        if steady:
            self._lost_since = None
            if self._steady_since is None:
                self._steady_since = now
            if not self.playing and now - self._steady_since >= STEADY_S:
                self.playing = True
        else:
            self._steady_since = None
            if self.playing and quiet:
                self._stop_playing()
            elif self.playing:
                if self._lost_since is None:
                    self._lost_since = now
                if now - self._lost_since >= MESSY_OFF_S:
                    self._stop_playing()

    def _stop_playing(self):
        self.playing = False
        self._anchor = None
        self._recent.clear()
        self._bpm_s = 0.0
        self._steady_since = self._lost_since = None

    def _quiet_for(self, seconds):
        n = int(seconds * self.fps)
        if len(self.rms) < n:
            return False
        tail = list(self.rms)[-n:]
        return max(tail) <= 10.0 ** (FLOOR_DB / 20.0)

    # -- what the page gets --------------------------------------------------------------------
    def phase(self):
        """0..1 within the beat; 0 is the beat itself."""
        if self._anchor is None or self._bpm_s <= 0:
            return 0.0
        period = 60.0 / self._bpm_s
        return ((self.samples / self.sr - self._anchor) / period) % 1.0

    def state(self):
        return {"playing": bool(self.playing),
                "bpm": round(float(self.bpm), 1),
                "phase": round(self.phase(), 3),
                "energy": round(float(self.energy), 3),
                "low": round(float(self.low), 3),
                "high": round(float(self.high), 3),
                "conf": round(float(self.conf), 3)}


# ------------------------------------------------------------------------------------------------
# Capture (Windows only; imported lazily)
# ------------------------------------------------------------------------------------------------
class WasapiLoopback:
    """The default speakers' loopback through PyAudioWPatch. The callback only queues bytes."""
    module = "pyaudiowpatch"

    @classmethod
    def available(cls):
        if not sys.platform.startswith("win"):
            return False
        try:
            return importlib.util.find_spec(cls.module) is not None
        except (ValueError, ImportError):
            return False

    def __init__(self):
        self.sr = 0
        self._pa = None
        self._stream = None
        self._channels = 2
        self._q = queue.Queue(maxsize=256)

    def open(self):
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            dev = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not dev.get("isLoopbackDevice"):
                for lb in pa.get_loopback_device_info_generator():
                    if dev["name"] in lb["name"]:
                        dev = lb
                        break
                else:
                    raise RuntimeError("no loopback device for the default speakers")
            self._channels = max(1, int(dev["maxInputChannels"]))
            self.sr = int(dev["defaultSampleRate"])
            q = self._q

            def callback(in_data, frame_count, time_info, status):
                try:
                    q.put_nowait(in_data)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(in_data)
                    except Exception:
                        pass
                return (None, pyaudio.paContinue)

            self._stream = pa.open(format=pyaudio.paInt16, channels=self._channels, rate=self.sr,
                                   frames_per_buffer=1024, input=True,
                                   input_device_index=int(dev["index"]), stream_callback=callback)
            self._pa = pa
        except Exception:
            pa.terminate()
            raise

    def read(self, timeout=0.1):
        """Mono float32 samples, or None when nothing arrived (loopback is silent while nothing plays)."""
        try:
            data = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if self._channels > 1:
            a = a[:len(a) - len(a) % self._channels].reshape(-1, self._channels).mean(axis=1)
        return a

    def close(self):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        self._stream = self._pa = None


class MusicListener:
    """Capture + analysis on a background thread. The GUI thread only calls latest().

    `backend_factory` is injectable (checks use a fake); the default is WasapiLoopback."""
    REOPEN_S = 30.0          # when nothing plays, re-open so a changed default speaker is picked up

    def __init__(self, backend_factory=None):
        self._factory = backend_factory or WasapiLoopback
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = None
        self.thread_ident = None

    def start(self):
        if not getattr(config, "EVA_MUSIC", True):
            return False
        avail = getattr(self._factory, "available", None)
        if callable(avail) and not avail():
            log_once("music-lib", "Music listening is off: the loopback library (PyAudioWPatch, "
                                  "Windows only) is not available.")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="eva-music", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2.0)

    def latest(self):
        with self._lock:
            return dict(self._state) if self._state else None

    def _publish(self, state):
        with self._lock:
            self._state = state

    def _run(self):
        self.thread_ident = threading.get_ident()
        while not self._stop.is_set():
            backend = None
            try:
                backend = self._factory()
                backend.open()
                analyzer = MusicAnalyzer(backend.sr)
                opened = time.monotonic()
                pad = np.zeros(int(backend.sr * 0.1), dtype=np.float32)
                while not self._stop.is_set():
                    block = backend.read(0.1)
                    analyzer.feed(pad if block is None else block)
                    self._publish(analyzer.state())
                    if not analyzer.playing and time.monotonic() - opened > self.REOPEN_S:
                        break
            except Exception as e:
                log_once("music-run", f"Music listening stopped: {type(e).__name__}: {e}")
                self._stop.wait(30.0)
            finally:
                if backend is not None:
                    try:
                        backend.close()
                    except Exception:
                        pass
