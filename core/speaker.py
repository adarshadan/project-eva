"""Eva core -- her voice, on its own thread.

Before, speaking was a tool call: the worker that runs tasks sat blocked for
the whole playback (26 s for one passage in eva.log), so nothing else could
happen while she talked, and a stop had to wait for the tool to return.

Now the agent hands text to the Speaker and carries on. The Speaker:
  * shows the bubble and speaks, one utterance at a time, in order;
  * splits long text at sentence boundaries and synthesizes the next part
    while the current one plays, so she starts talking after the first
    sentence is ready instead of after the whole passage;
  * stops instantly on stop(), dropping anything queued;
  * owns the "ignore the mic while I am talking" window, which used to be
    scattered through main.py.
"""
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config
from logger import get_logger

log = get_logger(__name__)

CHUNK_MIN_CHARS = 160        # later parts are grouped so pauses are rare
_SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[A-Z0-9\"'(])")


def split_for_speech(text: str) -> list:
    sentences = [s.strip() for s in _SENTENCE.split(" ".join(text.split())) if s.strip()]
    if not sentences:
        return []
    chunks, buf = [sentences[0]], ""
    for s in sentences[1:]:
        buf = f"{buf} {s}".strip()
        if len(buf) >= CHUNK_MIN_CHARS:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


# After she stops, the recogniser can still deliver a phrase it was hearing
# while she spoke -- but only for a moment. (20 s once swallowed a real request
# that happened to share "in West Bengal" with her previous answer.)
ECHO_WINDOW_SECONDS = 3.0
ECHO_COVERAGE = 0.6          # share of the heard words that must be her own run of words
_WORD = re.compile(r"[a-z0-9']+")


def _words(text):
    return _WORD.findall((text or "").lower())


def matches_spoken(heard: str, spoken: list, run: int = 3) -> bool:
    """Whether heard text is her own speech coming back (ideas from
    agenticSeek's echo filter, written for Eva). Most of the heard words must
    be covered by runs of `run`+ consecutive words she said -- one shared
    phrase ("in West Bengal") inside a new request is NOT an echo. Short
    phrases must consist entirely of her words."""
    h = _words(heard)
    if not h or not spoken:
        return False
    said_words = [w for t in spoken for w in _words(t)]
    said_text = " " + " ".join(said_words) + " "
    if len(h) >= run:
        covered = [False] * len(h)
        for i in range(len(h) - run + 1):
            if " " + " ".join(h[i:i + run]) + " " in said_text:
                for j in range(i, i + run):
                    covered[j] = True
        return sum(covered) / len(h) >= ECHO_COVERAGE
    vocab = set(said_words)
    return len(h) >= 2 and sum(w in vocab for w in h) == len(h)


def reading_ms(text: str) -> int:
    return max(4000, min(15000, len(text) * 70))


class Speaker:
    def __init__(self, voice_module, show_bubble, set_status, *, on_error=None,
                 pause_mic=None, resume_mic=None):
        self._voice = voice_module
        self._show_bubble = show_bubble        # (text, duration_ms) -> None, thread-safe
        self._set_status = set_status          # (text or None) -> None; None = back to idle
        self._on_error = on_error
        self._pause_mic, self._resume_mic = pause_mic, resume_mic
        self._q = queue.Queue()
        self._gen = 0
        self._lock = threading.Lock()
        self._ignore_until = 0.0
        self._pending = 0                      # utterances queued or playing
        self._recent = []                      # (time, text) she said, for the echo filter
        self._synth = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-synth")
        threading.Thread(target=self._loop, name="speaker", daemon=True).start()

    # ---- public ------------------------------------------------------------------
    def say(self, text: str, *, speak: bool = True, bubble: bool = True, show: str = None):
        """Say `text`; the bubble shows `show` (the whole of a long reply) or
        the same text."""
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            gen = self._gen
            self._pending += 1
        self._q.put((gen, text, speak, bubble, (show or "").strip() or text))

    def stop(self) -> bool:
        """Cuts off speech and drops everything queued. True if she was talking."""
        with self._lock:
            self._gen += 1
        dropped = 0
        while True:
            try:
                self._q.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        with self._lock:
            self._pending -= dropped
        interrupted = self._voice.stop_speaking()
        self._ignore_until = 0.0
        return bool(interrupted or dropped)

    def is_echo(self, heard: str) -> bool:
        """True if the microphone is hearing her own recent words."""
        now = time.time()
        talking = self.busy()                  # takes the lock itself; never call it holding the lock
        with self._lock:
            self._recent = [(t, x) for t, x in self._recent if now - t < 120]
            recent = [x for t, x in self._recent]
        window = talking or now < self._ignore_until + ECHO_WINDOW_SECONDS
        return window and matches_spoken(heard, recent[-6:])

    def ignoring_mic(self) -> bool:
        """True while her own voice may still be reaching the microphone."""
        return time.time() < self._ignore_until

    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    def wait_idle(self, timeout: float = 10.0) -> bool:
        end = time.time() + timeout
        while self.busy() and time.time() < end:
            time.sleep(0.05)
        return not self.busy()

    def shutdown(self):
        self.stop()
        self._synth.shutdown(wait=False, cancel_futures=True)

    # ---- the voice thread ----------------------------------------------------------
    def _current(self, gen) -> bool:
        with self._lock:
            return gen == self._gen

    def _loop(self):
        while True:
            gen, text, speak, bubble, shown = self._q.get()
            try:
                if self._current(gen):
                    if bubble:
                        self._show_bubble(shown, reading_ms(shown))
                    if speak:
                        self._speak(gen, text)
            except Exception:
                log.exception("Speaker failed on an utterance -- continuing")
            finally:
                with self._lock:
                    self._pending -= 1
                    idle = self._pending <= 0
                if idle:
                    self._set_status(None)

    def _speak(self, gen, text):
        chunks = split_for_speech(text)
        if not chunks:
            return
        with self._lock:
            self._recent.append((time.time(), text))
        paused = False
        if config.PAUSE_MIC_WHILE_SPEAKING and self._pause_mic:
            paused = bool(self._pause_mic())
        self._set_status("Speaking")
        # Generous estimate in case playback fails before it finishes.
        self._ignore_until = time.time() + 1.5 + len(text) * 0.075
        pending = self._synth.submit(self._voice.synthesize, chunks[0])
        try:
            for i in range(len(chunks)):
                try:
                    path = pending.result(timeout=75)
                except Exception as e:
                    log.error(f"Speech synthesis failed: {e}")
                    if self._on_error:
                        self._on_error(str(e))
                    pending = None
                    return
                pending = (self._synth.submit(self._voice.synthesize, chunks[i + 1])
                           if i + 1 < len(chunks) and self._current(gen) else None)
                try:
                    if not self._current(gen):
                        return
                    if self._voice.play_file(path):
                        return                            # interrupted
                except Exception as e:
                    log.error(f"Playback failed: {e}")
                    if self._on_error:
                        self._on_error(str(e))
                    return
                finally:
                    self._voice.discard(path)
        finally:
            if pending is not None:                       # a part synthesized but never played
                pending.add_done_callback(lambda f: f.exception() is None and self._voice.discard(f.result()))
            self._ignore_until = time.time() + config.MIC_IGNORE_TAIL_SECONDS
            if paused and self._resume_mic:
                self._resume_mic()
