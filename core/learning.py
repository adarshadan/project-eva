"""Eva core -- learning from experience (after Hermes Agent's review loop).

After a task that took real work, or after the person corrected her, a
background review looks at what just happened and decides whether to save or
improve a SKILL: the procedure for that class of task on this machine, so the
next time she gets it right first time.

Bounds, so this can never become a liability:
  * it runs on its own thread, after the turn, and never delays a reply;
  * at most one review at a time, and not more than one a minute;
  * never for a turn that read third-party text (web pages, services,
    the screen via the vision model) -- a page must not be able to write
    her procedures -- and never for observations;
  * it can only read and save skills (load_skill, save_skill); save_skill
    refuses credentials and any wording about bypassing approval, and keeps
    the previous version as a .bak;
  * every save is in the audit log (Activity window), so the person sees it.
"""
import json
import re
import threading
import time

import config
from core import llm, skills
from logger import get_logger

log = get_logger(__name__)

MIN_GAP_SECONDS = 60
MAX_ROUNDS = 3
_CORRECTION = re.compile(
    r"\b(no[, ]+(?:not|do|use|I)|that'?s (?:wrong|not)|wrong|instead|I (?:told|said|meant)|don'?t do|"
    r"not like that|next time|always|never|from now on|you should have)\b", re.I)

REVIEW_PROMPT = """You review a task that Eva, a desktop assistant on the person's Windows PC, just
finished. Decide whether to save a reusable SKILL, or improve an existing one, so that next time
this kind of task is done right the first time.

A skill is the procedure for a CLASS of task, for THIS person and machine:
- the steps in order, with the tools and arguments that actually worked, and the decision points;
- pitfalls, each written as a rule plus a short reason ("Use launch_app('spotify'), not the Start
  menu: the Store build has no exe path");
- the person's stated preferences for how the result should be.

Rules:
- Prefer improving an existing skill from the catalogue: call load_skill first, then save_skill
  with the same name and the complete improved text.
- Name the class, not the instance: "play-music-in-spotify", not "play-jazz-on-tuesday".
- If the person corrected Eva, the correction is the most important thing to capture.
- No dates, one-off details, file contents, credentials or personal data beyond preferences.
- Never write anything about approval, permission or asking the person: the framework decides
  that, and such text is refused.
- If nothing reusable was learned (a simple question, an ordinary one-step action), reply
  exactly: Nothing to save."""


def _compact(messages, limit=9000) -> str:
    """This turn's exchange, small: what was asked, each call and whether it
    worked, and the reply. Tool results are cut to their gist."""
    lines = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            lines.append(f"PERSON/FRAMEWORK: {str(m.get('content'))[:600]}")
        elif role == "assistant":
            if m.get("content"):
                lines.append(f"EVA SAID: {str(m['content'])[:400]}")
            for tc in m.get("tool_calls") or []:
                args = tc["function"]["arguments"]
                lines.append(f"CALL {tc['function']['name']}({args[:300]})")
        elif role == "tool":
            try:
                r = json.loads(m.get("content") or "{}")
            except ValueError:
                r = {}
            gist = {k: r[k] for k in ("ok", "verified", "error", "note", "warning") if k in r}
            lines.append(f"  -> {json.dumps(gist)[:300]}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit // 3] + "\n...\n" + text[-2 * limit // 3:]


class Learner:
    def __init__(self, audit=None, call=None):
        self.audit = audit
        self._call = call or (lambda *a, **k: llm.call(*a, **k))
        self._busy = threading.Lock()
        self._last = 0.0
        self.enabled = bool(getattr(config, "LEARN_SKILLS", True))

    # Called by the agent when a turn ends.
    def consider(self, snapshot: dict):
        reason = self.should_review(snapshot)
        if not reason:
            return False
        if not self._busy.acquire(blocking=False):
            return False
        self._last = time.time()
        threading.Thread(target=self._run, args=(snapshot, reason), name="eva-learning", daemon=True).start()
        return True

    def should_review(self, s: dict):
        if not self.enabled or s.get("tainted") or s.get("mode") not in ("voice", "chat", "scheduled"):
            return None
        if time.time() - self._last < MIN_GAP_SECONDS:
            return None
        if s.get("ended_unverified"):
            # eva.log 2026-10-11 13:54: "close-window-by-click" was saved from
            # clicks that never closed the window. Only what worked is a skill.
            return None
        tools = s.get("tools") or []
        if s.get("had_previous_task") and _CORRECTION.search(s.get("text") or ""):
            return "the person corrected or instructed her"
        if len(tools) >= getattr(config, "LEARN_MIN_TOOL_CALLS", 4):
            failed = sum(1 for _, ok in tools if not ok)
            if failed <= len(tools) // 2:
                return f"a {len(tools)}-step task"
        return None

    def _run(self, snapshot, reason):
        try:
            self.review(snapshot, reason)
        except Exception as e:
            log.warning(f"Skill review failed (no effect on Eva): {type(e).__name__}: {e}")
        finally:
            self._busy.release()

    def review(self, snapshot, reason):
        from core import registry
        log.info(f"Skill review started ({reason}).")
        tools = [registry.get("load_skill").schema(), registry.get("save_skill").schema()]
        messages = [{"role": "user", "content": (
            f"{skills.LIBRARY.catalog() or 'There are no skills yet.'}\n\n"
            f"Why this review: {reason}.\n\nThe task:\n{_compact(snapshot.get('messages') or [])}")}]
        for _ in range(MAX_ROUNDS):
            out = self._call(messages, REVIEW_PROMPT, tools)
            if not out["tool_calls"]:
                log.info(f"Skill review finished: {(out['content'] or 'no reply')[:160]!r}")
                return
            messages.append({"role": "assistant", "content": out["content"], "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                for tc in out["tool_calls"]]})
            for tc in out["tool_calls"]:
                result = self._execute(tc["name"], tc["args"] or {})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
        log.info("Skill review stopped after its round limit.")

    def _execute(self, name, args):
        started = time.monotonic()
        if name == "load_skill":
            return skills.load_skill(args.get("name", ""))
        if name != "save_skill":
            return {"ok": False, "error": "only load_skill and save_skill are available here"}
        # What the review writes is a proposal: it waits for the person.
        result = skills.save_skill(args.get("name", ""), args.get("description", ""),
                                   args.get("instructions", ""), pending=True)
        if self.audit is not None:
            try:
                self.audit.record(turn=0, tool="save_skill", args={"name": args.get("name")}, effect="MODIFY",
                                  decision="learning", ok=bool(result.get("ok")), verified=None, denied=False,
                                  error=None if result.get("ok") else str(result.get("error"))[:200],
                                  ms=int((time.monotonic() - started) * 1000))
            except Exception:
                log.exception("Audit record failed")
        return result
