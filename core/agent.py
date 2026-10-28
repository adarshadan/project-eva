"""Eva core -- the agent: one conversation, one turn loop, one dispatch path.

What changed from the previous brain.py, and why:
  * Speech is not a tool. The model's reply text is what Eva says. The old
    design made her call tts_speak and show_bubble, and in the 2026-10-10 log
    8 of 10 tool calls were her talking -- about half of every turn's model
    calls existed only to speak, re-speak, or fail to stop speaking. The
    duplicate-speech guards, the "speech-only round" nudges and the futile
    bubble detection all existed to contain that, and are gone with it.
  * Every call goes through ONE pipeline, driven by the tool's registry entry:
    argument filter -> permission gate -> deadline -> performer -> result
    shaping -> audit -> procedure memory. No per-tool wiring anywhere else.
  * A stop or a newer message is honoured between EACH tool call, not only
    between model rounds, so a long round no longer has to finish first.
  * Framework notes to the model are tagged [system] so they are never
    mistaken for the person speaking.
"""
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import capabilities
import config
import deadline as deadline_mod
import permissions
from core import checkpoints, elevation, llm, prompt, registry, replay, skills, toolbox
from core import feeling as feeling_mod
from core.results import (_argument_error, _cap_tool_result, _comms_tokens, _is_near_duplicate,
                          _normalise_result, _redact_secrets)
from logger import get_logger

log = get_logger(__name__)

ASKED_TO_LEARN = re.compile(r"\b(remember (?:how|this|that)|save (?:this|that|it) as a skill|learn (?:this|how)|"
                            r"make (?:this|that|it) a skill|note (?:down )?how)\b", re.I)

MAX_HISTORY_MESSAGES = 60        # see _trim_history
KEEP_TOOL_DETAIL_MESSAGES = 20
MAX_TOOL_ROUNDS = 12             # a hard ceiling; then a direct answer is forced
MAX_LEAK_RECOVERIES = 2

SKIPPED = {"ok": False, "skipped": True,
           "error": "not run: the person interrupted before this step. Nothing was done."}


@dataclass
class TurnState:
    text: str = ""
    mode: str = "voice"
    tainted: bool = False                 # read third-party text this turn
    tools: list = field(default_factory=list)          # (name, ok)
    said: list = field(default_factory=list)           # token sets of lines already spoken
    said_text: set = field(default_factory=set)        # the same lines, normalised
    results: list = field(default_factory=list)        # (tool, effect, result) for the evidence check
    claim_checked: bool = False
    stalled: list = field(default_factory=list)        # acting steps in a row that moved nothing
    stuck_checks: int = 0
    read_aloud: bool = False              # she chose "[read aloud]": the whole reply is spoken
    screen_steps: int = 0                 # on-screen steps she drove herself since the operator last ran
    handed_over_nudged: bool = False

    def already_said(self, line: str) -> bool:
        return _normal(line) in self.said_text or _is_near_duplicate(line, self.said)

    def mark_said(self, line: str):
        self.said.append(_comms_tokens(line))
        self.said_text.add(_normal(line))


def _normal(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


class Agent:
    def __init__(self, gate: permissions.PermissionGate, *, performer=None, audit=None, memory=None,
                 on_say: Callable[[str], None] = None, on_state: Callable[[str, str], None] = None,
                 record_step: Callable[[dict], None] = None, on_turn_done: Callable[[dict], None] = None,
                 extra_context: Callable[[], str] = None):
        self.messages = []
        self.gate = gate
        self.performer = performer
        self.audit = audit
        self.memory = memory
        self.on_say = on_say or (lambda text: None)
        self._on_state = on_state or (lambda kind, detail="": None)
        self._record_step = record_step
        self.turn = TurnState()
        self.trace = None                 # this turn's step-by-step replay (core/replay.py)
        self.on_replay_kept = None        # fn(folder) when a failed task's replay was kept
        self.on_task_card = None          # fn(card_dict, final) -- the chat window's task card (core/task_card)
        self.on_feeling = None            # fn(word) -- "[proud] ..." at the start of a reply (core/feeling)
        self.card = None
        self._should_abort = None
        self.tool_observers = []          # fn(name, args, result) after every finished call (predictive.py)
        self.screen_reader = None         # () -> what is on screen now, in words (main.py); for STUCK CHECK
        self._take_interjections = lambda: []
        self._interjection_pending = lambda: False
        self.busy = False
        self.activity = ""                # what she is doing right now, in words
        self._memory_text = ""
        self._on_turn_done = on_turn_done
        self._extra_context = extra_context or (lambda: "")
        self._previous_had_tools = False
        self._bind_memory_tools()
        self.toolbox = toolbox.Toolbox()
        registry.bind("use_tools", self.toolbox.use)
        # Saving a skill from a task that read a web page is refused: the page
        # must not be able to write her procedures.
        # A skill goes straight into use only when the person asked for it in
        # this turn ("remember how to...", "save this as a skill"); anything
        # Eva decides to learn on her own waits for their review.
        registry.bind("operate_computer", self.operate)
        registry.bind("wait_for_person", self._wait_tool)
        registry.bind("save_skill", lambda name, description, instructions: skills.save_skill(
            name, description, instructions, tainted=self.turn.tainted,
            pending=not ASKED_TO_LEARN.search(self.turn.text or "")))

    # ---- operating the computer; waiting for the person (core/operator.py) ----------
    def wait_for_person(self, say, minutes=5):
        """Says `say`, then waits until the person speaks or types again; returns their words
        (None when they did not answer in time or said stop)."""
        try:
            minutes = max(0.2, min(float(minutes or 5), 15))
        except (TypeError, ValueError):
            minutes = 5
        if say:
            self.on_say(str(say))
        self._on_state("waiting", "waiting for you")
        log.info(f"Waiting for the person (up to {minutes:g} min): {str(say)[:100]!r}")
        end = time.monotonic() + minutes * 60
        should_abort = self._should_abort or (lambda: False)
        while time.monotonic() < end:
            if should_abort():
                return None
            said = []
            try:
                said = self._take_interjections() or []
            except Exception:
                pass
            if said:
                text = " ".join(t for t, _ in said)
                self.turn.text = f"{self.turn.text}\n{text}"
                log.info(f"The person answered: {text!r}")
                self._on_state("working", self.activity or "")
                return text
            time.sleep(0.3)
        return None

    def _wait_tool(self, say, minutes=5):
        text = self.wait_for_person(say, minutes)
        if text is None:
            return {"ok": False, "error": "the person did not answer in time (or said stop) -- ask again later; "
                                          "do not do their part for them"}
        return {"ok": True, "the_person_said": text}

    def operate(self, goal, max_steps=25):
        from core import operator as op

        def notes():
            try:
                return [t for t, _ in (self._take_interjections() or [])]
            except Exception:
                return []
        def plan(prompt_text, hard):
            effort = "medium" if hard else str(getattr(config, "OPERATOR_PLANNER_EFFORT", "low"))
            with llm.thinking(effort, 90 if hard else 45):
                return llm.call([{"role": "user", "content": prompt_text}], op.PLANNER_SYSTEM, None)["content"]
        o = op.Operator(self.dispatch, self._operator_observe, self._operator_decide,
                        wait_person=lambda q: self.wait_for_person(q, 5),
                        should_stop=self._should_abort or (lambda: False), take_notes=notes,
                        to_screen=op.to_screen_vision, max_steps=max_steps,
                        plan=plan if getattr(config, "OPERATOR_PLANNER", True) else None)
        result = o.run(goal)
        try:                                     # live test 2026-10-28: the board readied, then played by hand
            import game_play
            if game_play.prompt_block() and isinstance(result, dict) and result.get("ok"):
                result = {**result, "game": (
                    "the game is still in progress (end_game was not called). If it is not won or over yet, "
                    "call operate_computer again with the whole game: \"play until it is won or over\". Do not "
                    "make the moves yourself.")}
        except Exception:
            log.exception("Could not check the game after the operator")
        return result

    _operator_observe = staticmethod(lambda: __import__("core.operator", fromlist=["x"]).observe_screen())
    _operator_decide = staticmethod(lambda p, obs: __import__("core.operator", fromlist=["x"]).decide_with_vision(p, obs))

    # ---- the model's tool list ----------------------------------------------------
    def tool_schemas(self):
        """Core tools and the groups she opened (core/toolbox.py); every tool when TOOL_GROUPS is off."""
        if not getattr(config, "TOOL_GROUPS", True) or getattr(self, "toolbox", None) is None:
            return registry.schemas()
        try:
            return self.toolbox.schemas(registry.all_tools())
        except Exception:
            log.exception("Tool groups failed -- sending every tool")
            return registry.schemas()

    def _live_groups(self):
        """Groups opened by live state, not by guessing: a game in progress, the person's browser linked."""
        out = []
        try:
            import game_play
            if game_play.prompt_block():
                out.append("games")
        except Exception:
            pass
        try:
            import browser_link
            if browser_link.status()["browsers"]:
                out.append("web_pages")
        except Exception:
            pass
        return out

    # ---- the task card (chat window) ----------------------------------------------
    def _emit_card(self, final=False):
        card, hook = self.card, self.on_task_card
        if card is None or hook is None or not card.acted:
            return
        try:
            hook(card.to_dict(), final)
        except Exception:
            log.exception("Task card hook failed")

    def _card_step(self, name, args, result):
        if self.card is None:
            return
        try:
            self.card.step(name, args, capabilities.effect_of(name, args).name, result)
        except Exception:
            log.exception("Could not add a step to the task card")
            return
        self._emit_card()

    def _finish_card(self, crashed, why_unconfirmed, last_bad, admits=False):
        card = self.card
        if card is None:
            return
        try:
            stopped = bool(self._should_abort and self._should_abort())
        except Exception:
            stopped = False
        card.finish(crashed=crashed, stopped=stopped and not crashed, unconfirmed=why_unconfirmed,
                    last_failed=last_bad, admitted=admits)
        self._emit_card(final=True)

    # ---- one tool call ------------------------------------------------------------
    def _trace_step(self, name, args, result, started):
        self._card_step(name, args, result)
        if self.trace is None:
            return
        try:
            self.trace.step(name, args, capabilities.effect_of(name, args).name, result,
                            int((time.monotonic() - started) * 1000))
        except Exception:
            log.exception("Could not trace a step")

    def dispatch(self, name: str, args) -> dict:
        """Runs one tool call through the whole pipeline. Public: replayed
        recordings use it, so every replayed step is judged and performed too."""
        started = time.monotonic()
        tool = registry.get(name)
        if tool is None or tool.fn is None:
            log.error(f"Model called unknown tool: {name!r} args={args}")
            self._on_state("error", "")
            return {"ok": False, "error": f"There is no tool called {name!r}."}
        args = dict(args) if isinstance(args, dict) else {}
        tb = getattr(self, "toolbox", None)
        was_closed = tb is not None and name != "use_tools" and toolbox.group_of(name) not in (None, *tb.opened())
        if was_closed:
            tb.noticed(name)                     # named in the index: it runs, and its group opens
        # Only declared parameters reach the implementation. An undeclared one
        # is either a harmless guess or an attempt to reach an internal option
        # (focus_window's allow_own, say); neither should run.
        extra = sorted(set(args) - set(tool.params))
        if extra:
            log.warning(f"{name}: dropping undeclared arguments {extra}")
            args = {k: v for k, v in args.items() if k in tool.params}
        log.info(f"Tool call: {name}({args})")

        # An administrator window cannot be acted on by a normal program
        # (core/elevation.py): say so instead of trying and guessing.
        try:                                   # fair play while a game is on (game_play.py)
            import game_play
            refused = game_play.fair_play_check(name, args, self.turn.text)
        except Exception:
            refused = None
        if refused:
            log.info(f"Tool result: {name} -> {refused}")
            self._trace_step(name, args, refused, started)
            return refused
        refused = elevation.check(tool, args)
        if refused:
            log.info(f"Tool result: {name} -> {refused}")
            self._trace_step(name, args, refused, started)
            return refused

        verdict = self.gate.authorize(name, args)
        if not verdict.allowed:
            log.warning(f"{name} not allowed ({verdict.how})")
            result = {"ok": False, "denied": True, "error": permissions.DENIED_MESSAGE}
            self._audit(tool, args, verdict, result, started)
            self._trace_step(name, args, result, started)
            return result

        self.activity = capabilities.describe(name, args) or name.replace("_", " ")
        self._on_state("working", self.activity)

        pending_undo = checkpoints.prepare(name, args) if tool.checkpoint else None

        def execute():
            if tool.deadline:
                out = deadline_mod.run_with_deadline(name, lambda: tool.fn(**args), tool.deadline)
            else:
                out = tool.fn(**args)
            if tool.record_step and self._record_step:
                try:
                    self._record_step(tool.record_step(args))
                except Exception:
                    log.exception(f"Could not record a step for {name}")
            return out

        try:
            result = self.performer.perform(name, args, execute) if self.performer else execute()
        except TypeError as e:
            result = _argument_error(name, tool.fn, args, e)
            if was_closed:                       # she had not seen its parameters: show them for the retry
                result = {**result, "parameters": {k: str(v.get("description", v.get("type", "")))[:120]
                                                   for k, v in tool.params.items()},
                          "required": list(tool.required)}
        except Exception as e:
            log.exception(f"Tool '{name}' raised during execution, args={args}")
            result = {"error": f"{name} failed: {e}"}

        result = _normalise_result(result)
        if tool.checkpoint:
            checkpoints.commit(pending_undo, result)
            if pending_undo and pending_undo.get("id") and self.card is not None:
                self.card.changed(pending_undo["id"])
            if pending_undo and result.get("ok"):
                result = {**result, "undo": "this change can be undone with undo_last_change"}
        result = self._add_remembered_hints(name, args, result)
        result = _cap_tool_result(name, result)
        result = _redact_secrets(result)
        log.info(f"Tool result: {name} -> {result}")
        if tool.web:
            self.turn.tainted = True
        self._audit(tool, args, verdict, result, started)
        self._trace_step(name, args, result, started)
        for observe in list(self.tool_observers):
            try:
                observe(name, args, result)
            except Exception:
                log.exception("A tool observer failed")
        self._learn(tool, args, result)
        if name == "load_skill":
            self._activity_from_skill(result)
        if isinstance(result, dict) and result.get("error"):
            self._on_state("error", "")
        return result

    # ---- one turn -------------------------------------------------------------------
    def handle(self, text: str, mode: str = "voice", should_abort: Callable[[], bool] = None,
               interjections: Callable[[], list] = None, interjection_pending: Callable[[], bool] = None) -> Optional[str]:
        """Answers one message. Returns the final reply to speak (None for
        silence, or when interrupted). Lines Eva says while working are
        delivered through on_say as they arrive.

        interjections() -> [(text, mode)]: what the person said WHILE she works. It joins this
        turn at the next step instead of ending it (eva.log 2026-10-12 00:01-00:05: six
        corrections each restarted the task, and she never finished)."""
        should_abort = should_abort or (lambda: False)
        self._take_interjections = interjections or (lambda: [])
        self._interjection_pending = interjection_pending or (lambda: False)
        self._trim_history()
        mark = len(self.messages)
        self.messages.append({"role": "user", "content": f"[{mode}] {text}"})
        self.gate.begin_turn()                     # "allow for this task" lasts one request
        self.turn = TurnState(text=text, mode=mode)
        try:
            self.toolbox.new_turn(self._live_groups())
        except Exception:
            log.exception("Could not refresh the tool groups")
        from core import workers
        if workers.is_helper_note(text):
            self.turn.tainted = True       # a helper's report may carry web text: never learned from
        self._memory_text = self._context_block(text)
        mark_turn = len(self.messages) - 1
        self._should_abort = should_abort
        try:
            from core.task_card import TaskCard
            self.card = TaskCard(text) if mode != "observation" and not self.turn.tainted else None
        except Exception:
            log.exception("Could not start the task card")
            self.card = None
        try:
            self.trace = replay.Trace(text) if getattr(config, "REPLAY_FAILED_TASKS", True) else None
        except Exception:
            log.exception("Could not start the task trace")
            self.trace = None
        self.busy = True
        self._set_activity("thinking")
        self._on_state("thinking", "")
        reply, crashed = None, False
        try:
            reply = self._run(should_abort)
            self._finish_episode(reply)
            self._hand_off(mark_turn, reply)
            return reply
        except Exception:
            # Roll back: a stranded user message was once answered by the next,
            # unrelated turn.
            crashed = True
            del self.messages[mark:]
            raise
        finally:
            self._finish_trace(reply, crashed)
            self.busy = False
            self.activity = ""
            self._set_activity("idle")
            self._on_state("idle", "")

    def _context_now(self):
        """The turn's context plus what must survive history trimming: the game in progress."""
        try:
            import game_play
            block = game_play.prompt_block()
        except Exception:
            block = ""
        try:
            import browser_link
            live = browser_link.status()["browsers"]
        except Exception:
            live = []
        link = (f"THE PERSON'S OWN BROWSER is connected ({', '.join(live)}): for anything on a page they have "
                "open or a site where they are signed in, use page_tabs, page_elements, page_read, page_click, "
                "page_type and page_go -- exact elements, their session. Your own automation browser "
                "(browser_open/browser_act) is for pages they do not need to see.") if live else ""
        return "\n\n".join(p for p in (self._memory_text, block, link) if p)

    def _fold_in_interjections(self) -> bool:
        """The person spoke while she worked: their words join this turn now."""
        try:
            said = self._take_interjections() or []
        except Exception:
            said = []
        for text, mode in said:
            log.info(f"Folded into the running task: {text!r}")
            self.messages.append({"role": "user", "content": (
                f"[{mode}, said while you were working] {text}\n[system] This changes or corrects what you are "
                "doing: take it into account from now on (it may override your plan). Stop only if they asked you to.")})
            self.turn.text = f"{self.turn.text}\n{text}"
        return bool(said)

    def _run(self, should_abort) -> Optional[str]:
        rounds = 0
        leak_recoveries = 0
        runaways = 0
        while True:
            if should_abort():
                log.info("Turn interrupted -- stop requested.")
                return None
            self._fold_in_interjections()

            def abort_check():
                if should_abort():
                    return "stop requested"
                if self._interjection_pending():
                    return "the person said something new"
                return None
            llm.set_abort(abort_check)
            try:
                out = llm.call(self.messages, prompt.build(self._context_now()), self.tool_schemas())
            finally:
                llm.set_abort(None)
            if out.get("aborted"):
                if should_abort():
                    log.info("Turn interrupted mid-thought -- stop requested.")
                    return None
                continue                         # re-plan with what the person just said
            if self._interjection_pending():
                log.info("The person spoke while she was deciding -- re-planning with what they said.")
                continue                         # this plan did not know it: do not act on it
            if out.get("runaway"):
                runaways += 1
                if runaways > 1:
                    return ("I'm going round in circles on this one and could not decide. Could you tell me the "
                            "next step, or say it a different way?")
                self.messages.append({"role": "user", "content": (
                    f"[system] Your own thinking {out['runaway']} and was cut off. Do not re-derive everything: "
                    "decide NOW from what you already know -- take ONE next action, or tell the person in one "
                    "short sentence what you are unsure about.")})
                continue

            if out.get("reasoning_leak") and not out["tool_calls"]:
                if leak_recoveries < MAX_LEAK_RECOVERIES:
                    leak_recoveries += 1
                    log.warning("Reasoning leaked with no tool call -- asking again for a clean reply.")
                    self.messages.append({"role": "user", "content": (
                        "[system] Your last message was your private planning, not a reply, so it was "
                        "discarded. Either call the tool you meant to call, or give only the short reply "
                        "the person should hear.")})
                    continue
                return "Sorry, I lost my train of thought. Could you say that again?"

            content = out["content"]
            from core import longform as _longform
            aloud, content = _longform.take_aloud(content)    # "[read aloud] ..." -> spoken in full
            word, content = feeling_mod.take(content)          # "[proud] Done." -> her body shows it, unsaid
            if not aloud:
                aloud, content = _longform.take_aloud(content)
            if aloud:
                self.turn.read_aloud = True
            if word and self.on_feeling is not None:
                try:
                    self.on_feeling(word)
                except Exception:
                    log.exception("Feeling hook failed")
            assistant = {"role": "assistant", "content": content}
            if out.get("reasoning_content") and config.SEND_REASONING_BACK:
                assistant["reasoning_content"] = out["reasoning_content"]

            if not out["tool_calls"]:
                self.messages.append(assistant)
                problem = None if self.turn.claim_checked else (
                    unsupported_claim(content, self.turn.results)
                    or claim_without_action(content, self.turn.text, self.turn.results))
                unlooked = None if (self.turn.claim_checked or problem
                                    or not getattr(config, "CHECK_BEFORE_DONE", True)) else \
                    needs_final_look(content, self.turn.results)
                if unlooked:
                    self.turn.claim_checked = True
                    log.info(f"Success claimed without a look at the result ({unlooked}) -- asking her to check.")
                    request = " ".join(str(self.turn.text or "").split("\n[speech-to-text")[0].split())[:300]
                    self.messages.append({"role": "user", "content": (
                        f"[system] Before you report: {unlooked}. Look once now (read_window_text, look_at_screen "
                        f"or list_folder, whichever shows the result) and compare what you see with what was "
                        f"asked: \"{request}\". If it matches, say so briefly. If it does not, finish the job or "
                        "say plainly what is missing. Report only what the look shows.")})
                    continue
                if problem:
                    self.turn.claim_checked = True
                    log.warning(f"Reply claims success the results do not support ({problem}) -- asking again.")
                    self.messages.append({"role": "user", "content": (
                        f"[system] Your reply says the task is done, but {problem}. Do not claim success "
                        "without evidence. Either check now (read the window back, look at the screen, list "
                        "the folder) or tell the person plainly what happened and what did not.")})
                    continue
                return self._final(content)

            # A line said alongside tool calls is spoken now, while she works.
            line = clean_reply(content)
            if line and not self.turn.already_said(line):
                self.turn.mark_said(line)
                self.on_say(line)

            rounds += 1
            if rounds > MAX_TOOL_ROUNDS + 4 * self.turn.stuck_checks:      # a stuck check earns room to recover
                log.warning(f"Hit MAX_TOOL_ROUNDS ({MAX_TOOL_ROUNDS}) -- forcing a direct answer.")
                self.messages.append(assistant)
                self.messages.append({"role": "user", "content": (
                    "[system] You have used many tool rounds without finishing. Stop calling tools and "
                    "tell the person, in one or two sentences, what you did and what is blocking you.")})
                # Tools are still offered: some endpoints reject an empty tool list,
                # and any tool call made now is simply not run.
                final = llm.call(self.messages, prompt.build(self._context_now()), self.tool_schemas())
                text = final["content"] or "I got stuck partway through that. Want me to try again?"
                self.messages.append({"role": "assistant", "content": text})
                return self._final(text)

            assistant["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                for tc in out["tool_calls"]]
            self.messages.append(assistant)
            for tc in out["tool_calls"]:
                # Every tool call must get a result message, or the history is
                # invalid for the next request; skipped ones say so.
                if should_abort():
                    result = SKIPPED
                elif self._interjection_pending():
                    result = {"ok": False, "skipped": True,
                              "error": "not run: the person said something new while you were working -- re-plan"}
                else:
                    result = self.dispatch(tc["name"], tc["args"])
                self.turn.tools.append((tc["name"], bool(result.get("ok"))))
                self.turn.results.append((tc["name"], capabilities.effect_of(tc["name"], tc["args"]), result))
                self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                      "content": json.dumps(result, default=str)})
                self._note_progress(tc["name"], tc["args"], result)
                self._count_screen_step(tc["name"])
            self._stuck_check()
            self._handover_check()

    def _finish_trace(self, reply, crashed):
        """A failed task keeps its step-by-step replay (core/replay.py)."""
        trace, self.trace = self.trace, None
        try:
            problem = unsupported_claim("done", self.turn.results) or ""
            from core.effects import Effect
            acting = [r for r in self.turn.results
                      if r[1] != Effect.OBSERVE and not (isinstance(r[2], dict) and r[2].get("skipped"))]
            last_bad = bool(acting) and isinstance(acting[-1][2], dict) and acting[-1][2].get("ok") is False
        except Exception:
            log.exception("Could not judge the task")
            problem, last_bad = "", False
        card = self.card
        admits = bool(reply) and bool(_ADMITS.search(str(reply)))
        self._finish_card(crashed, (problem[0].upper() + problem[1:]) if problem else "", last_bad, admits)
        if trace is None:
            return
        try:
            why = ("the task stopped with an internal error" if crashed else
                   (problem[0].upper() + problem[1:]) if problem else
                   f"the last step ({trace.steps[-1]['tool']}) failed"
                   if trace.steps and not trace.steps[-1]["ok"] else "")
        except Exception:
            log.exception("Could not judge the task trace")
            return

        def kept(folder):
            if card is not None and card.acted and self.on_task_card:
                card.replay = folder
                try:
                    self.on_task_card(card.to_dict(), True)
                except Exception:
                    log.exception("Task card hook failed")
            if self.on_replay_kept:
                self.on_replay_kept(folder)

        # On the replay worker, after this task's screenshots: never on the reply's path.
        trace.finish_later(reply, failed=bool(why), why=why, then=kept)

    # ---- noticing that she is stuck --------------------------------------------------------
    STUCK_AFTER = 3

    def _note_progress(self, name, args, result):
        """eva.log 2026-10-11 23:40-23:45 (Wordle): typed, clicked a tile, clicked Enter, typed
        again -- 14 actions, none of which moved the game on, until the round limit ended it.
        Each acting step is judged: moved things on, or not."""
        from core.effects import Effect
        try:
            eff = capabilities.effect_of(name, args)
        except Exception:
            return
        if eff == Effect.OBSERVE or not isinstance(result, dict) or result.get("skipped"):
            return
        stuck = (result.get("ok") is False or result.get("screen_changed") is False
                 or (result.get("verified") is False and bool(result.get("error"))))
        if stuck:
            why = result.get("error") or result.get("note") or ("nothing on screen changed"
                                                                if result.get("screen_changed") is False else "failed")
            self.turn.stalled.append(f"{name}: {str(why)[:90]}")
        else:
            self.turn.stalled = []

    # ---- one loop for screen work (Eva 2.0 design, phase 5) ---------------------------------
    def _count_screen_step(self, name):
        if name in ("operate_computer", "wait_for_person"):
            self.turn.screen_steps = 0
            return
        tool = registry.get(name)
        if getattr(getattr(tool, "surface", None), "value", "") in _SCREEN_SURFACES:
            self.turn.screen_steps += 1

    def _handover_check(self):
        """Live Wordle 2026-10-28, twice: told to hand a many-step screen job to operate_computer, the model
        still drove it one call at a time and ran out of steps. After ONSCREEN_HANDOFF screen steps of her
        own in one task, she is told -- once -- to hand the whole task to the operator. A judgement from what
        she has done, not from words in the request."""
        limit = int(getattr(config, "ONSCREEN_HANDOFF", 6) or 0)
        if not limit or self.turn.handed_over_nudged or self.turn.screen_steps < limit:
            return
        if registry.get("operate_computer") is None:
            return
        self.turn.handed_over_nudged = True
        request = " ".join(str(self.turn.text or "").split("\n[speech-to-text")[0].split())[:300]
        log.info(f"{self.turn.screen_steps} screen steps driven by hand -- telling her to hand over to the operator")
        self.messages.append({"role": "user", "content": (
            f"[system] HAND IT OVER. You have made {self.turn.screen_steps} screen steps yourself in this task. "
            "A job of many steps on screen is done by operate_computer, which looks before every action and "
            "keeps track. Unless the task is already finished, call operate_computer now with the WHOLE task as "
            f"the person asked it (\"{request}\"), including anything still to set up, and what finished looks "
            "like. Do not continue click by click.")})

    def _stuck_check(self):
        """After STUCK_AFTER acting steps in a row that moved nothing, she stops and looks at
        the WHOLE screen -- something is usually in the way (a dialog, a cookie or sign-in
        banner, a different page, the wrong field focused) -- before acting again."""
        if len(self.turn.stalled) < self.STUCK_AFTER or self.turn.stuck_checks >= 2:
            return
        steps = "; ".join(self.turn.stalled[-self.STUCK_AFTER:])
        self.turn.stalled = []
        self.turn.stuck_checks += 1
        seen = ""
        if self.screen_reader is not None:
            try:
                seen = self.screen_reader() or ""
            except Exception:
                log.exception("Could not read the screen for the stuck check")
        log.warning(f"Stuck check: {self.STUCK_AFTER} steps in a row moved nothing ({steps[:200]})")
        self.messages.append({"role": "user", "content": (
            f"[system] STUCK CHECK. Your last {self.STUCK_AFTER} actions did not move things forward: {steps}. "
            "Doing the same thing again will not work. Something is probably in the way or different from what "
            "you assume: a dialog, a cookie / sign-in / subscription banner, a different page or tab, the wrong "
            "field focused, a control that is disabled until something else is done.\n"
            + (f"WHAT IS ON SCREEN NOW: {seen}\n" if seen else "Look at the whole screen first (look_at_screen: "
               "what is on screen, is anything covering or blocking?).\n")
            + "Now: say in one short line what is blocking you, then take the step that removes it (close or "
            "answer the dialog, go back to the right page, click into the right field), and only then continue. "
            "If you cannot tell, ask the person one short question.")})

    def _final(self, content) -> Optional[str]:
        reply = clean_reply(content)
        if reply and self.turn.already_said(reply):
            log.info("Final reply repeats what she already said this turn -- not saying it again.")
            return None
        return reply

    def _set_activity(self, name):
        """The task-level pose, held between tool calls (the performer overlays
        the per-tool pose). No-op without a performer."""
        if self.performer is not None:
            try:
                self.performer.set_activity(name)
            except Exception:
                log.exception("Could not set the activity pose")

    def _activity_from_skill(self, result):
        if not (isinstance(result, dict) and result.get("ok") and result.get("name")):
            return
        try:
            from core import activity, skills
            s = skills.LIBRARY.get(result["name"])
            self._set_activity(activity.for_skill(result["name"], getattr(s, "category", "") if s else ""))
        except Exception:
            log.exception("Could not set the activity from the skill")

    # ---- audit and memory ------------------------------------------------------------
    def _audit(self, tool, args, verdict, result, started):
        if self.audit is None:
            return
        ok = bool(isinstance(result, dict) and result.get("ok", "error" not in result))
        a = verdict.assessment
        try:
            self.audit.record(
                turn=self.gate.turn, tool=tool.name, args=args,
                effect=a.effect.name if a else None, decision=verdict.how, ok=ok,
                verified=(result.get("verified") if isinstance(result, dict) else None),
                denied=bool(isinstance(result, dict) and result.get("denied")),
                error=(str(result.get("error"))[:200] if isinstance(result, dict) and result.get("error") else None),
                ms=int((time.monotonic() - started) * 1000))
        except Exception:
            log.exception("Audit record failed")

    def _bind_memory_tools(self):
        mem = self.memory
        if mem is None:
            off = lambda **_: {"ok": False, "error": "memory is turned off (config.MEMORY_ENABLED)"}  # noqa: E731
            for n in ("remember", "recall", "forget", "forget_all"):
                registry.bind(n, off)
            return

        def remember(key, value, category="note"):
            # A note written after reading third-party text is kept but marked
            # untrusted and never injected: a page can try to write itself into
            # Eva's memory.
            return mem.remember(key, value, category, trusted=not self.turn.tainted)
        registry.bind("remember", remember)
        registry.bind("recall", lambda query="", limit=8: mem.recall(query, limit))
        registry.bind("forget", lambda key: mem.forget(key))
        registry.bind("forget_all", lambda: mem.forget_all())

    def _context_block(self, text):
        parts = [self._memory_block(text)]
        try:
            parts.append(skills.LIBRARY.catalog())
        except Exception:
            log.exception("Skills catalogue unavailable -- continuing without it")
        if getattr(config, "TOOL_GROUPS", True):
            try:
                parts.append(toolbox.index(registry.names()))
            except Exception:
                log.exception("Tool group index unavailable")
        try:
            parts.append(self._extra_context())
        except Exception:
            log.exception("Extra context failed -- continuing without it")
        return "\n\n".join(p for p in parts if p)

    def _hand_off(self, mark, reply):
        """Gives the finished turn to the learner (skills from experience)."""
        had_previous = self._previous_had_tools
        self._previous_had_tools = bool(self.turn.tools)
        if self._on_turn_done is None:
            return
        try:
            self._on_turn_done({"text": self.turn.text, "mode": self.turn.mode, "tools": list(self.turn.tools),
                                "tainted": self.turn.tainted, "reply": reply,
                                "had_previous_task": had_previous,
                                # its last action failed or could not be confirmed: nothing to learn
                                "ended_unverified": unsupported_claim("done", self.turn.results) is not None,
                                "messages": [dict(m) for m in self.messages[mark:]]})
        except Exception:
            log.exception("Turn hand-off failed")

    def _memory_block(self, text):
        if self.memory is None:
            return ""
        try:
            return self.memory.prompt_block(text)
        except Exception:
            log.exception("Memory lookup failed -- continuing without it")
            return ""

    def _app_for(self, args):
        probe = self.gate.probe or permissions.DesktopProbe()
        try:
            import memory as memory_module
            return memory_module.app_key(probe.window_title(args.get("window_handle"))), probe
        except Exception:
            return "", probe

    def _add_remembered_hints(self, name, args, result):
        if self.memory is None or name != "ui_tree" or not isinstance(result, dict) or not result.get("ok", True):
            return result
        app, _ = self._app_for(args)
        hints = self.memory.hints_for(app) if app else []
        return {**result, "remembered": hints} if hints else result

    def _learn(self, tool, args, result):
        """Procedure memory: only verified successes (and real failures) teach."""
        if self.memory is None or not tool.learnable or not isinstance(result, dict) or result.get("denied"):
            return
        verified, ok = result.get("verified"), bool(result.get("ok"))
        if (not ok and "error" not in result) or (ok and verified is not True):
            return
        try:
            app, probe = self._app_for(args)
            target = args.get("target") or probe.element_text(args.get("window_handle"), args.get("element_id"))
            if app and target:
                self.memory.learn(app, tool.name, target, ok)
        except Exception:
            log.exception("Could not record what worked")

    def _finish_episode(self, reply):
        if self.memory is None or not self.turn.tools:
            return
        names = [n for n, _ in self.turn.tools]
        failed = sum(1 for _, ok in self.turn.tools if not ok)
        outcome = "ok" if not failed else ("failed" if failed == len(names) else "partial")
        try:
            self.memory.add_episode(self.turn.text, names, outcome)
        except Exception:
            log.exception("Could not record the episode")

    def _trim_history(self):
        """self.messages was never bounded -- every API call resent the
        ENTIRE accumulated conversation, including every past tool result
        (list_windows/ui_tree can be verbose). Token usage per request
        crept up as a session went on until it exceeded whatever TPM/RPM
        budget the provider allows -- this is the actual cause of "works
        for a while, then hits the rate limit", on any provider, not
        something specific to one. Only called between conversation turns
        (the start of handle_user_message, before the new user message is
        appended), when the message list is always in a complete state --
        no tool_calls without their matching tool results -- so trimming
        whole turns from the oldest end here is always safe."""
        if len(self.messages) <= MAX_HISTORY_MESSAGES:
            return
        original_len = len(self.messages)

        # Before dropping whole turns, drop the BULK instead. One tool-heavy
        # turn is easily 20 messages of ui_tree dumps and tool results, so
        # the old trim threw away entire earlier exchanges to stay under the
        # cap -- "41 -> 24" and "39 -> 19" in eva.log, each of which
        # discarded what the person had actually asked for earlier. The
        # conversation is the part worth keeping; the tool transcript of a
        # finished turn is not. Tool traffic from the most recent turns is
        # left intact, since the model may still be working from it.
        recent_from = max(0, len(self.messages) - KEEP_TOOL_DETAIL_MESSAGES)
        compacted, dropped = [], 0
        for idx, m in enumerate(self.messages):
            if idx >= recent_from or m.get("role") == "user":
                compacted.append(m)
                continue
            if m.get("role") == "tool":
                dropped += 1
                continue
            if m.get("role") == "assistant" and m.get("tool_calls"):
                names = ", ".join(tc["function"]["name"] for tc in m["tool_calls"])
                compacted.append({"role": "assistant",
                                  "content": m.get("content") or f"(used {names})"})
                continue
            compacted.append(m)
        if dropped:
            self.messages = compacted
            log.info(f"Compacted {dropped} old tool results out of history "
                     f"({original_len} -> {len(self.messages)} messages).")

        if len(self.messages) <= MAX_HISTORY_MESSAGES:
            return
        user_indices = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]
        while len(self.messages) > MAX_HISTORY_MESSAGES and len(user_indices) > 1:
            self.messages = self.messages[user_indices[1]:]
            user_indices = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]
        if len(self.messages) > MAX_HISTORY_MESSAGES and len(user_indices) <= 1:
            # One user message that spawned dozens of tool rounds: the loop
            # above cannot help (there is no older turn to drop), so history
            # grew unbounded from a single turn -- exactly what
            # MAX_HISTORY_MESSAGES exists to prevent. Drop from the MIDDLE
            # of the tool sequence, keeping the user message itself and the
            # most recent rounds, and never splitting a tool_calls message
            # from its matching tool results.
            head = self.messages[:user_indices[0] + 1] if user_indices else []
            tail = self.messages[-(MAX_HISTORY_MESSAGES - len(head) - 1):]
            while tail and tail[0].get("role") == "tool":
                tail = tail[1:]      # never start with an orphaned tool result
            self.messages = head + [{
                "role": "assistant",
                "content": "(earlier steps of this task were trimmed to save context)",
            }] + tail

        if len(self.messages) != original_len:
            log.info(f"Trimmed conversation history: {original_len} -> {len(self.messages)} messages.")


# ---- evidence before claims (after superpowers' verification-before-completion) ---------
# eva.log 2026-10-11 13:51: "Expanded all registry folders." after a keypress
# that changed nothing -- "expanded" was not on the list, so it went unchecked.
_CLAIM = re.compile(r"\b(done|finished|completed|all set|opened|closed|saved|sent|moved|typed|written|wrote|"
                    r"created|deleted|installed|minimi[sz]ed|maximi[sz]ed|played|expanded|collapsed|clicked|"
                    r"pressed|selected|switched|launched|started|enabled|disabled|turned (?:on|off)|changed|"
                    r"updated|renamed|copied|pasted|applied|removed|uninstalled|downloaded|restarted|"
                    r"navigated|scrolled|filled|submitted|it'?s (?:open|ready|there)|"
                    r"(?:is|are) (?:now )?(?:playing|paused|open|closed|back|in front|muted|unmuted|ready)|"
                    r"now (?:playing|paused|open|in front)|should now be|"
                    r"i'?ve (?:done|opened|saved|sent|moved|typed|written|created|closed))\b", re.I)
# Her own reply says the job did not get done (21:52 on the PC: "...but I can't see the From field" under
# a card that said Done, because a later small step had worked).
_ADMITS = re.compile(r"\b(can'?t|cannot|couldn'?t|could not|unable to|wasn'?t able|not able to|"
                     r"(?:didn'?t|did not) (?:work|manage|get|succeed|respond|load|accept|go through)|"
                     r"(?:isn'?t|is not|wasn'?t|was not|aren'?t|are not) (?:working|responding|loading|accepting|letting)|"
                     r"not responding|unsuccessful|unfortunately|failed to|got stuck)\b", re.I)
_HEDGE = re.compile(r"\b(couldn'?t|could not|can'?t|cannot|didn'?t|did not|wasn'?t|not sure|unable|failed|"
                    r"unverified|may not|might not|not confirm|no confirmation|unclear|declined|stopped|"
                    r"not (?:done|finished|saved|sent|open)|nothing (?:happened|changed)|no change|"
                    r"try again|instead)\b", re.I)


# A request to DO something on the computer (not a question, not chat).
_ACTION_REQUEST = re.compile(
    r"^\s*(?:\[\w+\]\s*)?(?:(?:ok(?:ay)?|now|eva|please|can you|could you|would you|just|and|then)[\s,]+)*"
    r"(?:pause|play|resume|open|close|click|press|tap|start|stop|scroll|type|write|move|drag|minimi[sz]e|maximi[sz]e|"
    r"restore|go to|switch|mute|unmute|turn|select|save|delete|remove|send|launch|run|show|hide|skip|bring|put|"
    r"rename|copy|paste|install|download|search|find|navigate|focus|exit|quit|log ?in|sign ?in|accept|reject|"
    r"enable|disable|increase|decrease|raise|lower|set|change|fill|submit)\b", re.I)


def claim_without_action(reply, turn_text, results):
    """A reply saying something on screen is now done/changed, in a turn that asked
    for an action and called NO tool. eva.log 2026-10-11 19:26:47: "please pause it"
    -> "The video is paused." with nothing done."""
    if results or not reply or not _CLAIM.search(reply) or _HEDGE.search(reply):
        return None
    if not _ACTION_REQUEST.search(str(turn_text or "").split("\n")[0]):
        return None
    return "no tool was called this turn, so nothing was actually done"


_SCREEN_SURFACES = {"screen", "window", "browser"}
# Steps that end by looking for themselves: their own result is the check.
_SELF_CHECKING = {"operate_computer", "wait_for_person"}


def needs_final_look(reply, results):
    """Eva 2.0 "check before reporting": a success claim after on-screen work that nothing has looked at
    since. Returns why a last look is needed, or None. Not asked when the last step verified itself, when
    only one step was taken and it verified, or when she already looked after it."""
    if not reply or not results or not _CLAIM.search(reply) or _HEDGE.search(reply):
        return None
    from core.effects import Effect
    acting = []
    for i, (n, eff, r) in enumerate(results):
        if eff == Effect.OBSERVE or (isinstance(r, dict) and r.get("skipped")):
            continue
        tool = registry.get(n)
        surface = getattr(getattr(tool, "surface", None), "value", "")
        if surface in _SCREEN_SURFACES:
            acting.append(i)
    if not acting:
        return None
    last = acting[-1]
    name, _eff, r = results[last]
    if name in _SELF_CHECKING and isinstance(r, dict) and r.get("ok"):
        return None
    if any(eff == Effect.OBSERVE and isinstance(rr, dict) and rr.get("ok") is not False
           for _n, eff, rr in results[last + 1:]):
        return None
    if len(acting) == 1 and isinstance(r, dict) and r.get("verified") is True:
        return None
    steps = len(acting)
    return (f"you took {steps} on-screen steps and have not looked at the result since the last one ({name})"
            if steps > 1 else f"nothing has checked what {name} did on screen")


def unsupported_claim(reply, results):
    """What contradicts a success claim, or None. Looks at the most recent
    action (anything beyond observing): a failed or unverified one, or any
    declined step, does not support "done"."""
    if not reply or not results or not _CLAIM.search(reply) or _HEDGE.search(reply):
        return None
    from core.effects import Effect
    if any(isinstance(r, dict) and r.get("denied") for _, _, r in results):
        return "a step was declined by the person"
    acted = [i for i, (n, eff, r) in enumerate(results)
             if eff != Effect.OBSERVE and not (isinstance(r, dict) and r.get("skipped"))]
    if not acted:
        return None
    last = acted[-1]
    name, _eff, r = results[last]
    if not isinstance(r, dict):
        return None
    if r.get("ok") is False:
        return f"the last action ({name}) failed: {str(r.get('error', ''))[:120]}"
    # She looked afterwards (read the window, listed windows, looked at the screen): the claim
    # rests on that check, not on the action's own report. eva.log 2026-10-17 14:00: the folder
    # had opened -- list_windows showed it -- yet "could not be verified" sent her round again.
    checked = any(eff == Effect.OBSERVE and isinstance(rr, dict) and rr.get("ok") is not False
                  for _n, eff, rr in results[last + 1:])
    if checked:
        return None
    if r.get("verified") is False or r.get("warning"):
        return f"the last action ({name}) could not be verified"
    if r.get("screen_changed") is False:
        return f"the last click ({name}) did not change the screen"
    return None


# ---- reply hygiene --------------------------------------------------------------------
_ECHO_LINE = re.compile(r"^\s*\[(voice|chat|observation|scheduled|system)\b[^\]]*\].*$", re.I | re.M)
_NON_REPLY = re.compile(r"^[\(\[]?\s*(?:no response|no reply|none|n/?a|silence|nothing|\.\.\.)\s*[\)\]]?\.?$",
                        re.IGNORECASE)
_MARKDOWN = re.compile(r"(\*\*|__|`+|^#{1,6}\s+|^\s*[-*•]\s+)", re.M)
# Internals are not for the person: 'The Recycle Bin window is now open (handle 67068, title "Recycle
# Bin - File Explorer").' (eva.log 2026-10-17 12:10).
_INTERNAL_ASIDE = re.compile(r"\s*\((?=[^()]*\b(?:window[_ ])?(?:handle|hwnd|element[_ ]id|pid)\b\s*[:=#]?\s*[\w'\"#-])"
                             r"[^()]*\)", re.I)
_INTERNAL_INLINE = re.compile(r",?\s*\b(?:with\s+)?(?:window\s+)?(?:handle|hwnd)\s*[:=#]?\s*\d+\b", re.I)


def clean_reply(text) -> Optional[str]:
    """What is actually said: no echoed input tags ("[voice] ..."), no
    "(no response)" placeholders, no markdown symbols read aloud."""
    if not text:
        return None
    text = _ECHO_LINE.sub("", text)
    text = _MARKDOWN.sub("", text)
    text = _INTERNAL_INLINE.sub("", _INTERNAL_ASIDE.sub("", text))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text or _NON_REPLY.match(text):
        return None
    if not re.search(r"[^\W_]", text):       # "...?" (2026-10-28 09:57): nothing to say -- and every voice
        return None                           # failed on it, putting her on the Windows voice for 5 minutes
    # A stage direction is not speech: "(No response needed; waiting for user to say stop.)"
    # was read aloud (eva.log 2026-10-11 19:29:11).
    if re.fullmatch(r"\(\s*[^()]*\)\.?|\[\s*[^\[\]]*\]\.?", text):
        return None
    return text
