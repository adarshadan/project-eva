"""Eva core -- the runtime: two lanes, one owner of "what happens next".

FRONT LANE (the caller's thread: mic listener, chat box, scheduler). Answers
instantly, without the model, for the things that must never wait behind a
task: "stop", a plain greeting, and "what are you doing?" while she is busy.
Everything else is queued.

TASK LANE (one worker thread). Takes the next batch from the queue and runs
it through the agent. One task at a time, so state cannot be corrupted by
overlapping turns (the old "doom loop"). Priority: scheduled work, then the
newest person message (several queued ones are merged, because people add to
a request), then observations, which are dropped if anything else waits.

Everything Eva says, from either lane, goes through the Speaker, so the task
lane never blocks on audio and a stop silences her at once.
"""
import queue
import random
import re
import threading
import time

import config
from core import llm
from logger import get_logger

log = get_logger(__name__)

PERSON = ("voice", "chat")

GREETING = re.compile(r"^\s*(hi|hello|hey|hola|namaste)(\s+eva)?[\s!.,?]*$", re.IGNORECASE)
# 2026-10-11: moments her body answers (main.py turns each into a gesture).
THANKS = re.compile(r"\b(thanks|thank you|thankyou|thx|ty|cheers|much appreciated|you'?re (?:the best|awesome|amazing))\b",
                    re.IGNORECASE)
UNCLEAR = re.compile(r"(not sure what you mean|didn'?t (?:quite )?catch|could you (?:say|repeat|clarify)|"
                     r"did you mean|what do you mean|can you rephrase|i don'?t understand)", re.IGNORECASE)
GREETING_REPLIES = ["Hi! What can I do for you?", "Hello! I'm listening.", "Hey there. What do you need?"]

# Kept to unambiguous phrases so her own speech, re-heard, cannot trip it.
STOP_PHRASE = re.compile(
    r"^(?:eva[ ,]+)?(?:please[ ,]+)?"
    r"(?:stop|quiet|silence|shush|shut up|cancel|abort|enough|never ?mind|forget it|be quiet|"
    r"stop talking|stop it|don'?t respond|do not respond|no response|don'?t answer)"
    # Only filler may follow: "stop it now", "cancel that, Eva" -- never a new
    # request ("cancel my meeting tomorrow" is a task, not a stop).
    r"(?:[\s,]+(?:it|that|this|now|please|eva|everything|all|talking|right now|for now|thanks|thank you))*"
    r"[\s.!?,]*$", re.IGNORECASE)

RESUME_PHRASE = re.compile(r"^(?:eva[ ,]+)?(?:resume|wake up|unpause|come back|you can continue)\b.{0,20}$",
                           re.IGNORECASE)

STATUS_QUESTION = re.compile(
    r"^(?:eva[ ,]+)?(?:what are you (?:doing|up to|working on)|are you (?:done|finished|still working)|"
    r"what'?s taking so long|status)\??$", re.IGNORECASE)


# A stop said inside a longer phrase ("open Chrome -- stop stop, whatever you
# are doing") must still stop her; eva.log 2026-10-10 15:13:20 queued that as a
# request instead.
STOP_ANYWHERE = re.compile(
    r"\bstop\W+stop\b|\bstop (?:whatever|what) you(?:'re| are) doing\b|"
    r"\b(?:stop|cancel) (?:everything|all of (?:it|that))\b|\bstop (?:it|that|talking) now\b", re.IGNORECASE)


def is_stop_phrase(text: str) -> bool:
    if STOP_ANYWHERE.search(text or ""):
        return True
    # A short utterance ending in "stop", however the recogniser split it: "Dewas top"
    # (Eva stop), "ok you are stop" -- eva.log 2026-10-11 19:38: she kept working through both.
    words = re.findall(r"[a-z']+", (text or "").lower())
    joined = "".join(words)
    if 1 <= len(words) <= 4 and len(joined) <= 16 and joined.endswith("stop") and not joined.endswith("nonstop") \
            and "bus" not in words and not {"don't", "dont", "never", "not"} & set(words):
        return True
    # Speech recognition stutters on short commands ("don't don't respond").
    collapsed = re.sub(r"\b([\w']+)(\s+\1\b)+", r"\1", (text or "").strip(), flags=re.IGNORECASE)
    return bool(STOP_PHRASE.match(collapsed))


class Runtime:
    def __init__(self, agent, speaker, *, show_text, on_heard=None, stop_hooks=(), clear_hooks=(),
                 on_task_result=None, longform=None, on_message=None, on_moment=None):
        self.agent = agent
        self.speaker = speaker
        from core.longform import LongForm
        self.longform = longform or LongForm()     # long replies: one spoken line, the text shown
        self._on_message = on_message or (lambda role, text: None)   # the chat window's history
        self._on_moment = on_moment or (lambda kind: None)          # her body reacting: thanked, greeted...
        self._show_text = show_text                 # (text, ms), thread-safe
        self._on_heard = on_heard or (lambda: None)
        self._stop_hooks = list(stop_hooks)         # e.g. browser_agent.request_abort
        self._clear_hooks = list(clear_hooks)       # e.g. browser_agent.clear_abort
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._mode = "voice"
        self.paused = False
        self.person_seq = 0             # counts person messages and stops (hand-off resume uses it)
        self._on_task_result = on_task_result      # (meta, ok, error) -- e.g. scheduler.report_run
        self.health = {"at": None, "status": "no request yet", "detail": ""}
        self.pending_offer = None       # predictive help: {"r": routine, "at": time} awaiting yes / no
        self.last_person_at = time.time()   # the person's last message: how long nobody needed her (boredom)
        agent.on_say = self._say_while_working
        self._worker = threading.Thread(target=self._loop, name="eva-task-lane", daemon=True)

    def start(self):
        self._worker.start()

    # ---- front lane -------------------------------------------------------------------
    def heard(self, text: str):
        """Called on the mic listener's thread."""
        log.info(f"Heard: {text!r}")
        # Checked BEFORE the echo filter: a stop is usually said while she talks.
        if is_stop_phrase(text):
            self.stop()
            return
        if self.paused and not RESUME_PHRASE.match(text.strip()):
            log.info(f"Paused -- ignoring {text!r}")
            return
        # Her own voice, coming back through the speakers, is recognised by its
        # WORDS (after agenticSeek's echo filter), not only by timing.
        if self.speaker.is_echo(text):
            log.info(f"Ignoring her own voice heard back: {text!r}")
            return
        if self.speaker.ignoring_mic():
            # Not her words, while she is talking: the person is interrupting.
            if config.BARGE_IN and len(text.split()) >= 2:
                log.info(f"Person spoke over Eva -- stopping speech for: {text!r}")
                self.speaker.stop()
            else:
                log.info(f"Ignoring heard text while Eva is speaking: {text!r}")
                return
        self._on_heard()
        self.submit(text, "voice")

    def submit(self, text: str, mode: str = "chat", meta: dict = None):
        text = (text or "").strip()
        if not text:
            return
        if mode in PERSON and RESUME_PHRASE.match(text) and self.paused:
            self.set_paused(False)
            return
        if self.paused:
            log.info(f"Paused -- not running {mode} message {text!r}")
            if meta and self._on_task_result:
                self._report(meta, False, "Eva was paused")
            return
        if mode in PERSON:
            self.last_person_at = time.time()
            self._note("me", text)
            if THANKS.search(text):
                self._moment("thanked")
            if is_stop_phrase(text):
                self.stop()
                return
            if self.pending_offer is not None and self._answer_offer(text, mode):
                return
            if GREETING.match(text) and not self.agent.busy:
                self._moment("greeted")
                self._reply(random.choice(GREETING_REPLIES), speak=(mode == "voice"))
                return
            if STATUS_QUESTION.match(text) and self.agent.busy:
                doing = self.agent.activity or "working on your request"
                self._reply(f"I'm {doing[0].lower() + doing[1:]}. Say stop if you want me to stop.",
                            speak=(mode == "voice"))
                return
            if mode == "voice" or self.agent.busy:
                self._show_text(f'Heard: "{text}"', 1500)    # she caught it; the answer follows
        elif mode == "observation" and not config.PROACTIVE_COMMENTARY_ENABLED:
            return
        if mode in PERSON:
            self.person_seq += 1
        if mode == "voice":
            from core import hearing
            text = hearing.annotate(text)          # how sure the recogniser was, what else it heard
        self._q.put((text, mode, meta or {}))

    # ---- predictive help (predictive.py) ---------------------------------------------
    OFFER_ANSWER_SECONDS = 120

    def offer(self, routine, text):
        """Say an offer; the person's next short yes / no answers it."""
        self.pending_offer = {"r": routine, "at": time.time()}
        log.info(f"Offering a routine: {routine['key']} ({text!r})")
        self._reply(text, speak=True)

    def _answer_offer(self, text, mode):
        """True when `text` answered the pending offer (and was handled)."""
        po, self.pending_offer = self.pending_offer, None
        if time.time() - po["at"] > self.OFFER_ANSWER_SECONDS:
            return False
        import predictive
        kind = predictive.answer_kind(text)
        if kind is None:
            return False                      # something else: the offer simply lapses
        r = po["r"]
        speak = mode == "voice"
        if r.get("source") == "patrol":           # patrol.py: a finding she offered to fix
            import patrol
            patrol.patrol().feedback(r["key"], kind)
            log.info(f"Patrol offer {r['key']}: {kind}")
            if kind == "accepted":
                self.person_seq += 1
                self._q.put((r["task"], mode, {}))       # runs like a request; the gate still asks
            elif kind == "never":
                self._reply(f"Understood -- I won't bring up {r['label']} again.", speak=speak)
            else:
                self._reply("Alright.", speak=speak)
            return True
        predictive.routines().feedback(r["key"], kind)
        log.info(f"Routine offer {r['key']}: {kind}")
        if kind == "accepted":
            self._q.put((f"open {r['label']}", "offer", {"routine": r, "speak": speak}))
        elif kind == "never":
            self._reply(f"Understood -- I won't offer {r['label']} again.", speak=speak)
        else:
            self._reply("Alright.", speak=speak)
        return True

    def _run_offer(self, meta):
        r = meta["routine"]
        action = r.get("action") or {}
        result = self.agent.dispatch(action.get("tool", ""), dict(action.get("args") or {}))
        if isinstance(result, dict) and result.get("ok"):
            said = f"Opening {r['label']}."
        else:
            said = f"I couldn't open {r['label']}: {str((result or {}).get('error', 'it did not start'))[:120]}"
        self._reply(said, speak=meta.get("speak", False))

    def set_paused(self, paused: bool):
        """Pause: stop everything and ignore input until resumed (tray, or "Eva, resume")."""
        if paused:
            self.stop()
        self.paused = bool(paused)
        self._moment("paused" if paused else "resumed")
        log.info("Eva paused." if paused else "Eva resumed.")
        self._show_text("Paused. Say \"Eva, resume\" or use the tray to bring me back." if paused
                        else "I'm back.", 4000)

    def _report(self, meta, ok, error=""):
        try:
            self._on_task_result(meta, ok, error)
        except Exception:
            log.exception("Task result report failed")

    def stop(self):
        """Stops speech, the running task at its next step, and anything queued."""
        self.person_seq += 1
        interrupted = self.speaker.stop()
        self._stop.set()
        for hook in self._stop_hooks:
            try:
                hook()
            except Exception:
                log.exception("Stop hook failed")
        dropped = 0
        while True:
            try:
                self._q.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        log.info(f"Stop requested: speech_interrupted={interrupted} busy={self.agent.busy} queued_dropped={dropped}")
        self._show_text("Okay, stopped.", 1500)

    def queued(self) -> int:
        return self._q.qsize()

    # ---- task lane ----------------------------------------------------------------------
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def should_abort(self) -> bool:
        """A stop ends the running task. What the person SAYS while she works no longer ends it
        (core/agent.py folds it in); only an unprompted observation still gives way to them."""
        if self._stop.is_set():
            return True
        if self._mode == "observation":
            with self._q.mutex:
                return any(item[1] in PERSON for item in self._q.queue)
        return False

    def _handle_takes_interjections(self) -> bool:
        try:
            import inspect
            return "interjections" in inspect.signature(self.agent.handle).parameters
        except (TypeError, ValueError):
            return False

    def interjection_pending(self) -> bool:
        with self._q.mutex:
            return any(item[1] in PERSON for item in self._q.queue)

    def take_interjections(self) -> list:
        """The person's messages waiting in the queue, removed from it: [(text, mode)]."""
        with self._q.mutex:
            mine = [item for item in self._q.queue if item[1] in PERSON]
            for item in mine:
                self._q.queue.remove(item)
        return [(t, m) for t, m, _ in mine]

    def _next_batch(self):
        items = [self._q.get()]
        while True:
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break
        person = [i for i in items if i[1] in PERSON]
        scheduled = [i for i in items if i[1] == "scheduled"]
        observations = [i for i in items if i[1] == "observation"]
        offers = [i for i in items if i[1] == "offer"]
        if len(person) > 1:
            log.info(f"Merging {len(person)} queued person messages into one turn.")
            person = [("\n".join(i[0] for i in person), person[-1][1], {})]
        return (offers + scheduled + person) or observations[-1:]

    def _speaks(self) -> bool:
        return self._mode in ("voice", "scheduled")

    def _moment(self, kind):
        try:
            self._on_moment(kind)
        except Exception:
            log.exception("Moment hook failed")

    def _note(self, role, text):
        try:
            self._on_message(role, text)
        except Exception:
            log.exception("Chat history hook failed")

    def _reply(self, said, *, speak, shown=None):
        """Everything Eva answers goes through here: the bubble and voice, and
        the chat window's history (the whole text of a long reply)."""
        self._note("eva", shown or said)
        self.speaker.say(said, speak=speak, show=shown)

    def _say_while_working(self, text: str):
        line = self.longform.interim(text)
        if line:
            self._reply(line, speak=self._speaks())
        else:
            log.info(f"Not saying a long line while working: {text[:80]!r}...")

    def _loop(self):
        while True:
            for text, mode, meta in self._next_batch():
                self._stop.clear()                  # a stop applies to what was running
                for hook in self._clear_hooks:
                    try:
                        hook()
                    except Exception:
                        log.exception("Clear hook failed")
                self._mode = mode
                if mode == "offer":
                    try:
                        self._run_offer(meta)
                    except Exception:
                        log.exception("Could not carry out the accepted offer")
                    continue
                try:
                    if (mode in PERSON or mode == "scheduled") and self._handle_takes_interjections():
                        reply = self.agent.handle(text, mode=mode, should_abort=self.should_abort,
                                                  interjections=self.take_interjections,
                                                  interjection_pending=self.interjection_pending)
                    else:
                        reply = self.agent.handle(text, mode=mode, should_abort=self.should_abort)
                    self.health = {"at": time.time(), "status": "ok", "detail": ""}
                    if meta and self._on_task_result:
                        tools = list(getattr(self.agent.turn, "tools", []) or [])
                        all_failed = bool(tools) and not any(ok for _, ok in tools)
                        self._report(meta, not all_failed, "every step failed" if all_failed else "")
                    shown = reply
                    if reply and mode != "observation":
                        reply, shown = self.longform.shape_ex(
                            reply, read_aloud=bool(getattr(getattr(self.agent, "turn", None), "read_aloud", False)))
                    if reply:
                        log.info(f"{mode.capitalize()} reply: {reply!r}" + (
                            f" (showing {len(shown)} chars)" if shown and shown != reply else ""))
                        if mode != "observation" and UNCLEAR.search(reply or ""):
                            self._moment("unclear")      # she scratches her head while she asks
                        self._reply(reply, speak=self._speaks(), shown=shown)
                except Exception as e:
                    log.exception(f"Failure handling {mode} message {text!r}")
                    sentence = llm.explain_error(e)
                    self.health = {"at": time.time(), "status": "FAILED", "detail": sentence}
                    if meta and self._on_task_result:
                        self._report(meta, False, f"{sentence} ({str(e)[:80]})")
                    # Observations fail silently; the person did not ask for anything.
                    if mode != "observation":
                        self._moment("error")
                        self._reply(sentence, speak=self._speaks())
