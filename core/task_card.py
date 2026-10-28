"""Eva core -- a task card: one task's steps, result, Undo and Run again, shown in the chat window.

Plan to v1.0, Phase 2 ("Task cards in the chat window": each multi-step task shows its steps, result,
Undo and Replay, so long tasks are reviewable without logs).

A card exists for every turn that ACTS (clicks, types, opens, changes files -- not one that only
looks). It appears with the first acting step and grows as she works; when the task ends it shows:

    status     done | not confirmed (she could not check the result) | didn't finish | stopped
    steps      each tool call as a plain sentence ("Opening Notepad"), with ok / failed
    Undo       when the task changed files that the checkpoint journal can put back (core/checkpoints)
    Run again  sends the same request again, as if typed
    Steps page the step-by-step replay with screen pictures, kept for a task that went wrong

Pure data (no Qt): chat_ui.TaskCardWidget draws it, chat_store keeps finished cards with the
conversation. Step arguments are never shown -- only the description capabilities gives a call --
so typed text, paths in secrets and the like do not appear in the card.
"""
import itertools
import time

STATUS_TEXT = {"running": "Working", "done": "Done", "not_confirmed": "Done, not confirmed",
               "failed": "Didn't finish", "stopped": "Stopped"}
MAX_STEPS = 60
_SEQ = itertools.count(1)


# Arguments that name WHAT was acted on, safe to show (never typed text, never handles or ids).
_SHOWN_ARGS = ("name", "app", "app_name", "label", "title", "query", "url", "key", "keys", "hotkey", "element_name")


def _what(args):
    if not isinstance(args, dict):
        return ""
    for k in _SHOWN_ARGS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            v = " ".join(v.split())
            return v if len(v) <= 48 else v[:45] + "..."
    v = args.get("element_id")
    if isinstance(v, str) and v.strip() and not v.strip().startswith("#") and not v.strip().isdigit() \
            and len(v) <= 40 and "_" not in v:
        return v.strip()
    return ""


def _label(name, args):
    try:
        import capabilities
        text = capabilities.describe(name, args)
    except Exception:
        text = None
    if not text:
        text = name.replace("_", " ")
        what = _what(args)
        if what:
            text = f"{text}: {what}"
    text = str(text).strip()
    text = " ".join(text.split())
    return (text[:1].upper() + text[1:])[:90]


class TaskCard:
    ACTING = {"INTERACT", "MODIFY", "DESTROY", "EXTERNAL", "FINANCIAL", "SENSITIVE"}

    def __init__(self, task, clock=time.time):
        self.clock = clock
        self.id = f"t{int(clock() * 1000):x}{next(_SEQ)}"
        self.task = " ".join(str(task or "").split("\n[speech-to-text")[0].split())[:300]
        self.started = clock()
        self.ended = None
        self.steps = []
        self.status = "running"
        self.why = ""
        self.changes = []          # checkpoint ids this task made (undo order: newest first)
        self.undone = False
        self.replay = ""           # folder of the step-by-step replay, when one was kept
        self.acted = False

    # ---- while she works -----------------------------------------------------------------
    def step(self, name, args, effect, result):
        ok = bool(isinstance(result, dict) and result.get("ok", "error" not in result))
        if isinstance(result, dict) and result.get("skipped"):
            return None
        state = "ok" if ok else "failed"
        if ok and isinstance(result, dict) and result.get("verified") is False:
            state = "unchecked"
        if isinstance(result, dict) and result.get("denied"):
            state = "declined"
        if str(effect) in self.ACTING:
            self.acted = True
        if len(self.steps) < MAX_STEPS:
            self.steps.append({"n": len(self.steps) + 1, "text": _label(name, args), "state": state,
                               "acting": str(effect) in self.ACTING})
        return self.steps[-1] if self.steps else None

    def changed(self, change_id):
        if change_id and change_id not in self.changes:
            self.changes.append(change_id)

    # ---- when it ends --------------------------------------------------------------------
    def finish(self, *, crashed=False, stopped=False, unconfirmed="", last_failed=False, admitted=False):
        self.ended = self.clock()
        if crashed:
            self.status, self.why = "failed", "an internal error stopped the task"
        elif stopped:
            self.status, self.why = "stopped", "you asked her to stop"
        elif last_failed:
            last = next((s for s in reversed(self.steps) if s.get("acting", True)), None)
            self.status = "failed"
            if last and last["state"] == "declined":         # 15:27 on the PC: a declined move, not a failure
                self.why = f"you declined a step ({last['text'].lower()})"
            else:
                bad = next((s for s in reversed(self.steps) if s["state"] == "failed"), None)
                self.why = f"the last step failed ({bad['text'].lower()})" if bad else "the last step failed"
        elif admitted:
            self.status, self.why = "failed", "she said she could not finish it"
        elif unconfirmed:
            self.status, self.why = "not_confirmed", unconfirmed
        else:
            self.status, self.why = "done", ""

    @property
    def can_undo(self):
        return bool(self.changes) and not self.undone and self.status != "running"

    def seconds(self):
        return int(round((self.ended or self.clock()) - self.started))

    def summary(self):
        n = len(self.steps)
        s = self.seconds()
        took = f"{s} s" if s < 90 else f"{s // 60} min {s % 60:02d} s"
        bits = [f"{n} step{'s' if n != 1 else ''}", took]
        if self.changes:
            bits.append(f"{len(self.changes)} file change{'s' if len(self.changes) != 1 else ''}"
                        + (" (undone)" if self.undone else ""))
        return " · ".join(bits)

    def to_dict(self):
        return {"id": self.id, "task": self.task, "started": self.started, "ended": self.ended,
                "status": self.status, "why": self.why, "steps": list(self.steps), "changes": list(self.changes),
                "undone": self.undone, "replay": self.replay, "summary": self.summary()}

    @classmethod
    def from_dict(cls, d):
        c = cls.__new__(cls)
        c.clock = time.time
        c.id = str(d.get("id") or "")
        c.task = str(d.get("task") or "")
        c.started = float(d.get("started") or 0)
        c.ended = d.get("ended")
        c.steps = list(d.get("steps") or [])
        c.status = d.get("status") or "done"
        if c.status == "running":                      # a card saved mid-task (Eva closed): it did not finish
            c.status, c.why = "stopped", "Eva was closed while it ran"
        else:
            c.why = str(d.get("why") or "")
        c.changes = list(d.get("changes") or [])
        c.undone = bool(d.get("undone"))
        c.replay = str(d.get("replay") or "")
        c.acted = True
        return c


def undo(card, undo_change=None):
    """Puts back every file change this task made, newest first. Returns (ok, message)."""
    if undo_change is None:
        from core.checkpoints import undo_change
    if not card.changes:
        return False, "This task changed no files I can put back."
    if card.undone:
        return False, "Already undone."
    done, problems = 0, []
    for cid in reversed(card.changes):
        r = undo_change(cid)
        if r.get("ok"):
            done += 1
        elif "no change with id" not in str(r.get("error", "")):      # already undone on its own: fine
            problems.append(str(r.get("error") or "failed"))
    card.undone = not problems
    if problems:
        return False, f"Put back {done} of {len(card.changes)} changes; {problems[0]}."
    return True, f"Undone: {done} change{'s' if done != 1 else ''} put back."
