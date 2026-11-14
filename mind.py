"""Eva -- Phase 5: the mind (docs/life/LIFE_DESIGN.md).

A slow loop. Every 60-120 s, or sooner on a notable event (the person came back, a long focus session ended,
music started), a small fast model is shown a ~10-line summary of the moment and answers with JSON:

    {"mood": "<one feeling word>", "intention": "<one of INTENTIONS>", "say": "<optional line, <= 80 chars>"}

  * mood      -> the existing feeling system (core/feeling.py LEXICON words only);
  * intention -> biases the page's behaviour selector for a few minutes (never moves her body directly, and it
                 cannot unlock anything focus mode forbids);
  * say       -> spoken ONLY when the person is not focused, typing or on a call, she is not busy, the person is
                 there, and at least SPEAK_GAP_S since her last mind line. Checked when the answer ARRIVES.

Hard rules: at most one model call per max(60, EVA_MIND_MIN_SECONDS) seconds whatever the events do; the call runs
on a worker thread (never the GUI thread); any error or junk answer changes nothing and is silent (an error also
rests the loop for two minutes). The summary is built from a whitelist: a window's coarse KIND only, never a
title; no clipboard, no file names, no text from the screen can reach the model.

Pure stdlib (+ config and core.feeling's word list); the model client is injectable (`_client`, like core/reader).
"""
import json
import random
import re
import threading
import time

import config
from logger import get_logger

log = get_logger(__name__)

INTENTIONS = ("be_quiet", "be_playful", "be_curious", "keep_company", "rest")
WINDOW_KINDS = ("editor", "browser", "video", "game", "call", "notification", "other")
HARD_MIN_GAP_S = 60.0          # whatever the config says, never more than one call a minute
REGULAR_GAP_S = (60.0, 120.0)  # the usual cadence
FIRST_CALL_AFTER_S = 20.0
ERROR_REST_S = 120.0
SPEAK_GAP_S = 600.0            # at most one spoken line per 10 minutes
SAY_MAX = 80
INTENTION_TTL_S = 240          # "a few minutes"
AWAY_IDLE_S = 900              # nobody there: no point asking (an event such as a return still counts)
FOCUS_EVENT_S = 600            # a focus session this long is worth noticing when it ends

_client = None                 # the checks put a scripted client here (same pattern as core/reader.py)


def _words():
    from core.feeling import LEXICON
    return LEXICON


def model() -> str:
    return str(getattr(config, "EVA_MIND_MODEL", "") or getattr(config, "READER_MODEL", "") or "")


# ---- the summary: whitelisted fields only ------------------------------------------------------------------
def snapshot_from(world, busy=False, music=False, drives=None, hour=None):
    """The ONLY fields the mind ever sees, copied out of the world sample. Titles, the window list, the cursor and
    everything else in the sample are dropped here."""
    w = world if isinstance(world, dict) else {}
    win = w.get("window") if isinstance(w.get("window"), dict) else {}
    kind = win.get("kind") if win.get("kind") in WINDOW_KINDS else "other"
    d = {}
    for k in ("energy", "curiosity", "boredom", "social", "focusRespect"):
        try:
            d[k] = max(0.0, min(1.0, float((drives or {}).get(k))))
        except (TypeError, ValueError):
            pass
    if hour is None:
        lt = time.localtime()
        hour = lt.tm_hour + lt.tm_min / 60.0
    return {"hour": float(hour), "weekday": time.localtime().tm_wday, "window_kind": kind,
            "focus": bool(w.get("focus")), "full": bool(w.get("full")),
            "keys": _num(w.get("keys")), "idle": _num(w.get("idle")),
            "busy": bool(busy), "music": bool(music), "drives": d}


def _num(v):
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0


def _part_of_day(hour):
    return ("night" if hour < 5 else "early morning" if hour < 8 else "morning" if hour < 12 else "afternoon" if hour < 17
            else "evening" if hour < 22 else "night")


def _level(v):
    return "low" if v < 0.33 else "medium" if v < 0.66 else "high"


_ACTIVITY = {"editor": "writing or coding in an editor", "browser": "browsing the web", "video": "watching a video",
             "game": "playing a game", "call": "in a call or meeting", "notification": "looking at a notification",
             "other": "working in some other app"}


def build_summary(snap, recent=(), last_mood=None, last_intention=None, since_say=None):
    """~10 lines of plain text for the model. Nothing but whitelisted values and fixed phrases."""
    h = float(snap.get("hour", 12.0))
    kind = snap.get("window_kind") if snap.get("window_kind") in WINDOW_KINDS else "other"
    keys, idle = _num(snap.get("keys")), _num(snap.get("idle"))
    typing = "typing now" if keys > 0 else "not typing"
    idle_s = "just moved the mouse or typed" if idle < 5 else f"idle for about {int(idle // 60)} min" if idle >= 90 else f"idle for {int(idle)} s"
    d = snap.get("drives") or {}
    drives = ", ".join(f"{n} {_level(d[k])}" for n, k in (("energy", "energy"), ("curiosity", "curiosity"),
                                                         ("boredom", "boredom"), ("wanting company", "social"),
                                                         ("respect for focus", "focusRespect")) if k in d)
    lines = [
        f"Time: {_part_of_day(h)}, {int(h) % 24:02d}:{int((h % 1) * 60):02d}, {'weekend' if snap.get('weekday', 0) >= 5 else 'weekday'}.",
        f"The person is {_ACTIVITY[kind]}; {typing}; {idle_s}.",
        "The person is concentrating (focus mode)." if snap.get("focus") else "The person is not in focus mode.",
        "Music is playing on the PC." if snap.get("music") else "No music is playing.",
        "Eva is busy with a task for the person." if snap.get("busy") else "Eva is free.",
    ]
    for r in list(recent)[-2:]:
        lines.append(f"Recent: {str(r)[:90]}.")
    if drives:
        lines.append(f"Eva's drives: {drives}.")
    if last_mood or last_intention:
        lines.append(f"Eva's last mood: {last_mood or 'none'}; last intention: {last_intention or 'none'}.")
    if since_say is not None:
        lines.append(f"Eva last spoke {int(since_say // 60)} min ago." if since_say < 3600 else "Eva has not spoken for a long while.")
    return "\n".join(lines[:10])


PROMPT = """You are the inner mind of Eva, a cheerful, inquisitive, friendly and respectful AI companion who lives on the person's desktop as a small avatar. From the summary below decide how she feels and what she is inclined to do next.

Reply with ONE JSON object and nothing else:
{{"mood": "<one word from: {moods}>", "intention": "<one of: {intentions}>", "say": "<optional short warm line, at most {say_max} characters, or an empty string>"}}

Rules:
- If the person is concentrating, typing or in a call: intention be_quiet and say "".
- say is rarely needed. When used it is friendly, brief, never about what is on their screen, never a question that demands an answer.
- Never mention windows, files, titles or private details.

Summary:
{summary}
"""


def build_prompt(summary):
    return PROMPT.format(moods=", ".join(sorted(_words())), intentions=", ".join(INTENTIONS), say_max=SAY_MAX,
                         summary=summary)


# ---- the answer: defensive parsing ---------------------------------------------------------------------------
def parse_reply(text):
    """{"mood": word|None, "intention": name|None, "say": line|None} or None when nothing usable came back. Junk,
    prose around the JSON, a <think> block, wrong types, unknown words: never an exception, never a guess."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"<think>.*?</think>", "", text[:6000], flags=re.S)
    obj = None
    start = text.find("{")
    tries = 0
    while start != -1 and obj is None and tries < 6:
        tries += 1
        end = text.rfind("}")
        while end > start and obj is None:
            try:
                cand = json.loads(text[start:end + 1])
                if isinstance(cand, dict):
                    obj = cand
            except Exception:
                pass
            end = text.rfind("}", start, end)
        start = text.find("{", start + 1)
    if obj is None:
        return None
    mood = obj.get("mood")
    mood = mood.strip().lower() if isinstance(mood, str) else None
    if mood not in _words():
        mood = None
    intention = obj.get("intention")
    intention = intention.strip().lower().replace(" ", "_") if isinstance(intention, str) else None
    if intention not in INTENTIONS:
        intention = None
    say = clean_say(obj.get("say"))
    if mood is None and intention is None and say is None:
        return None
    return {"mood": mood, "intention": intention, "say": say}


def clean_say(v):
    """One short plain line, or None. Too long, links, markup or control characters -> dropped, not cut."""
    if not isinstance(v, str):
        return None
    t = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", v)).strip().strip('"“”').strip()
    if not t or len(t) > SAY_MAX:
        return None
    low = t.lower()
    if "http" in low or "www." in low or any(c in t for c in "<>{}[]`\\"):
        return None
    return t


# ---- the model call (worker thread) ----------------------------------------------------------------------------
def ask_model(prompt, timeout=None):
    """The model's raw answer text. Like core/reader._ask: the configured small fast model, thinking off, a short
    timeout, no retries. Raises on any problem; the caller is silent about it."""
    import providers
    ref = model()
    if not ref:
        raise RuntimeError("no mind model configured")
    timeout = float(timeout or getattr(config, "EVA_MIND_TIMEOUT", 20))
    kw = dict(model=providers.parse(ref)[1], max_tokens=300, temperature=0.8, timeout=timeout,
              messages=[{"role": "user", "content": prompt}])
    if providers.is_nim(ref) or _client is not None:
        kw["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    if _client is None:
        providers.pace(ref)
    resp = (_client or providers.client(ref, timeout=timeout, max_retries=0)).chat.completions.create(**kw)
    return resp.choices[0].message.content or ""


class Mind:
    """apply_mood(word), apply_intention(name, ttl_s), speak(text): all called from `step` (the caller's thread,
    the GUI timer). `step(snap)` is cheap and never blocks: the model call is a daemon thread."""

    def __init__(self, apply_mood, apply_intention, speak, clock=time.monotonic, rng=None, threaded=True, ask=None):
        self.apply_mood, self.apply_intention, self.speak = apply_mood, apply_intention, speak
        self.clock = clock
        self.rng = rng or random.Random()
        self.threaded = threaded
        self._ask = ask or ask_model
        self._lock = threading.Lock()
        self._result = None
        self._inflight = False
        self._started = None
        self._last_call = None
        self._next_at = None
        self._rest_until = 0.0
        self._pending_event = False
        self._last_say = None
        self.calls = 0
        self.recent = []                       # [(time, text)]
        self.last_mood = None
        self.last_intention = None
        # event detection
        self._prev_idle = 0.0
        self._prev_music = False
        self._focus_since = None
        self._prev_focus = False

    # -- config (read live so a flag change applies at once) ---------------------------------------------------
    @property
    def min_gap(self):
        return max(HARD_MIN_GAP_S, float(getattr(config, "EVA_MIND_MIN_SECONDS", 60) or 60))

    # -- events ----------------------------------------------------------------------------------------------
    def note(self, text, now=None):
        now = self.clock() if now is None else now
        self.recent.append((now, text))
        del self.recent[:-6]
        self._pending_event = True

    def observe(self, snap, now):
        idle, music, focus = snap.get("idle", 0.0), bool(snap.get("music")), bool(snap.get("focus"))
        if self._prev_idle >= 120 and idle < 5:
            self.note(f"the person came back after about {int(self._prev_idle // 60)} min away", now)
        if music and not self._prev_music:
            self.note("music started playing", now)
        if focus and not self._prev_focus:
            self._focus_since = now
        if self._prev_focus and not focus and self._focus_since is not None:
            mins = int((now - self._focus_since) // 60)
            if now - self._focus_since >= FOCUS_EVENT_S:
                self.note(f"a long focus session just ended (about {mins} min)", now)
            self._focus_since = None
        self._prev_idle, self._prev_music, self._prev_focus = idle, music, focus

    # -- speaking gate ---------------------------------------------------------------------------------------
    def speak_block(self, snap, now=None):
        """None when she may speak a line now, else the reason."""
        now = self.clock() if now is None else now
        if not getattr(config, "EVA_MIND_SPEAK", True):
            return "flag_off"
        if snap.get("focus") or snap.get("full"):
            return "focus"
        if snap.get("window_kind") == "call":
            return "call"
        if _num(snap.get("keys")) > 0:
            return "typing"
        if snap.get("busy"):
            return "busy"
        if _num(snap.get("idle")) >= AWAY_IDLE_S:
            return "away"
        if self._last_say is not None and now - self._last_say < SPEAK_GAP_S:
            return "spacing"
        return None

    # -- the loop --------------------------------------------------------------------------------------------
    def step(self, snap):
        """Call every few seconds with a fresh snapshot (snapshot_from). Applies a finished answer, notices events,
        starts at most one call. Returns True when a call was started."""
        now = self.clock()
        if self._started is None:
            self._started = now
            self._next_at = now + FIRST_CALL_AFTER_S
        self._apply_pending(snap, now)
        self.observe(snap, now)
        if not getattr(config, "EVA_MIND", True) or self._inflight:
            return False
        if now < self._rest_until or not self._call_allowed(now):
            return False
        if snap.get("busy"):
            return False                                       # she is working for the person: no idle thoughts
        due = now >= self._next_at
        if not (due or self._pending_event):
            return False
        if due and not self._pending_event and _num(snap.get("idle")) >= AWAY_IDLE_S:
            self._next_at = now + self.min_gap                 # nobody is there: look again later
            return False
        self._start_call(snap, now)
        if not self.threaded:
            self._apply_pending(snap, self.clock())
        return True

    def _call_allowed(self, now):
        return self._last_call is None or now - self._last_call >= self.min_gap

    def _start_call(self, snap, now):
        if not self._call_allowed(now):                        # the hard limit, checked where the call is made
            return
        self._last_call = now
        self._next_at = now + max(self.min_gap, self.rng.uniform(*REGULAR_GAP_S))
        self._pending_event = False
        self.calls += 1
        since_say = None if self._last_say is None else now - self._last_say
        recent = [text for (at, text) in self.recent if now - at < 900]
        prompt = build_prompt(build_summary(snap, recent, self.last_mood, self.last_intention, since_say))
        self._inflight = True
        if self.threaded:
            threading.Thread(target=self._work, args=(prompt,), daemon=True, name="eva-mind").start()
        else:
            self._work(prompt)

    def _work(self, prompt):
        parsed = None
        try:
            parsed = parse_reply(self._ask(prompt))
        except Exception as e:                                 # silent: no mood change, rest a while
            self._rest_until = self.clock() + ERROR_REST_S
            log.info(f"Mind: no answer ({type(e).__name__}: {str(e)[:100]})")
        with self._lock:
            self._result = parsed
            self._inflight = False

    def _apply_pending(self, snap, now):
        with self._lock:
            res, self._result = self._result, None
        if not res:
            return
        try:
            if res["mood"]:
                self.last_mood = res["mood"]
                self.apply_mood(res["mood"])
            if res["intention"]:
                self.last_intention = res["intention"]
                self.apply_intention(res["intention"], INTENTION_TTL_S)
            if res["say"]:
                why = self.speak_block(snap, now)
                if why is None:
                    self._last_say = now
                    self.speak(res["say"])
                else:
                    log.info(f"Mind: a line was held back ({why})")
        except Exception:
            log.exception("Mind: applying the answer failed")
