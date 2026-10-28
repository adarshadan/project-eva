"""Eva -- deciding what needs the person's approval, and asking properly.

Two rules, and only two: ask before something DESTRUCTIVE, and ask before
something that SPENDS MONEY. Everything else Eva just does.

WHY THE FRAMEWORK WRITES THE QUESTION. Approval used to work by the model
calling confirm("I'm about to tidy your downloads -- go ahead?"). The
summary was the model's own words, so a confused or manipulated model could
describe a deletion as tidying and the person would approve what they were
told, not what would happen. Here the question is assembled from the actual
arguments and the actual target -- the literal command, the literal path,
the literal button label on the literal page -- and the model never sees
the dialog or writes a word of it.

ASSESSMENT COMES FIRST. assess() turns (tool, arguments) into an effect,
looking at what is really being touched: a click on "Place order" is
FINANCIAL, the identical click on "Search" is not; write_file is
destructive only when it would replace something.

APPROVAL SCOPE. A person asked to approve five file moves in a row will
stop reading. So a DESTRUCTIVE approval can be granted "for this task"
(this request only, this tool only). A FINANCIAL approval never can: every
payment step asks again, whatever was allowed before.

If nobody can be asked (no approver wired in), gated actions are refused.
"""
import json
import os
from dataclasses import dataclass
from typing import Callable, Optional

import capabilities
import finance_guard
import system_guard
from capabilities import Effect
from logger import get_logger

log = get_logger(__name__)

# Tools where "allow for this task" is offered. Deliberately not process
# termination or destructive keystrokes: those are one-off by nature.
TASK_SCOPE_TOOLS = {"move_file", "write_file", "run_command"}

_CLICKS = {"click_coords": "Click", "right_click_coords": "Right-click",
           "double_click_coords": "Double-click", "drag_and_drop": "Drag"}


@dataclass(frozen=True)
class Assessment:
    tool: str
    effect: Effect
    gated: bool
    title: str          # what this is, in the framework's words
    detail: str         # the literal specifics
    reason: str         # why she is asking
    task_scope: bool    # whether "allow for this task" is offered
    key: str            # grant key for task scope


@dataclass(frozen=True)
class ApprovalRequest:
    tool: str
    effect: str
    title: str
    detail: str
    reason: str
    task_scope: bool
    key: str


@dataclass
class Verdict:
    allowed: bool
    how: str                      # auto | task-grant | once | task | denied | no-approver
    assessment: Optional[Assessment] = None


class DesktopProbe:
    """Facts about the target of an action, read at the moment of asking."""

    def element_text(self, window_handle, element_id) -> str:
        import desktop_control as dc
        return dc.describe_element(window_handle, element_id)

    def window_title(self, window_handle=None) -> str:
        import desktop_control as dc
        return dc.window_title(window_handle)

    def control_at(self, x, y) -> str:
        """The label of the control under a screen point, read from the control."""
        import desktop_control as dc
        return dc.name_at_point(x, y)

    def focused_text(self) -> str:
        import desktop_control as dc
        return dc.focused_element_name() or ""

    def file_size(self, path):
        try:
            return os.path.getsize(path)
        except OSError:
            return None

    def process_name(self, pid):
        try:
            import psutil
            return psutil.Process(int(pid)).name()
        except Exception:
            return None


def _safe(fn, *a, default=""):
    try:
        return fn(*a) or default
    except Exception:
        return default


def _resolve_path(path):
    try:
        from system_tools import _resolve
        return _resolve(str(path))
    except Exception:
        return os.path.expanduser(str(path))


def _generic(name, args):
    try:
        text = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        text = str(args)
    return f"{name}({text[:280]}{'…' if len(text) > 280 else ''})"


def _financial_reason(name, args, probe):
    """Whether THIS call, against THIS target, involves money."""
    if name in ("click_element", "act_on_element"):
        if name == "act_on_element" and str(args.get("action", "auto")) == "get_state":
            return None
        wh, eid = args.get("window_handle"), args.get("element_id")
        return finance_guard.native_reason(
            "click", _safe(probe.element_text, wh, eid), _safe(probe.window_title, wh))
    if name in _CLICKS:
        return finance_guard.native_reason("click_blind", "", _safe(probe.window_title, None))
    if name in ("type_text", "type_into_element"):
        return finance_guard.native_reason("type", typed=args.get("text"))
    if name == "send_hotkey":
        keys = str(args.get("keys", "")).lower().replace(" ", "")
        if keys in ("enter", "return"):
            return finance_guard.native_reason(
                "enter", window_title=_safe(probe.window_title, args.get("window_handle")))
    return None


_PRESSING = ("auto", "invoke", "click", "toggle", "select", "")


def _destructive_reason(name, args, probe):
    """Whether THIS press, on THIS control, cannot be taken back (restart, delete, uninstall...).
    Judged from the control's own label -- eva.log 2026-10-17 11:21: 'Restart now' was invoked
    without asking because only money was judged per control."""
    if name in ("click_element", "act_on_element"):
        if name == "act_on_element" and str(args.get("action", "auto") or "auto") not in _PRESSING:
            return None
        return system_guard.control_reason(_safe(probe.element_text, args.get("window_handle"),
                                                 args.get("element_id")))
    if name == "ui_navigate":
        if str(args.get("action", "click") or "click") not in _PRESSING:
            return None
        # its last rung clicks wherever the TARGET label appears, so the label itself is judged,
        # and the real control too when an id was given
        return (system_guard.control_reason(args.get("target"))
                or (system_guard.control_reason(_safe(probe.element_text, args.get("window_handle"),
                                                      args.get("element_id")))
                    if args.get("element_id") else None))
    if name in ("click_coords", "double_click_coords"):
        return system_guard.control_reason(_safe(getattr(probe, 'control_at', None), args.get("x"), args.get("y")))
    if name == "send_hotkey":
        keys = str(args.get("keys", "")).lower().replace(" ", "")
        if keys in ("enter", "return", "space"):
            return system_guard.control_reason(_safe(getattr(probe, 'focused_text', None)))
    return None


def describe_literal(name, args, probe):
    """The exact thing about to happen, built only from the arguments."""
    a = args or {}
    if name == "move_file":
        return f"Move  {a.get('source')}\nto      {a.get('destination')}"
    if name == "write_file":
        path = _resolve_path(a.get("path", ""))
        size = probe.file_size(path)
        new = len(str(a.get("content", "")).encode("utf-8"))
        if size is None:
            return f"Write {new:,} bytes to {path}"
        return f"Replace the contents of {path}\n({size:,} bytes now, {new:,} bytes after)"
    if name == "run_command":
        cwd = f"\nin folder: {a.get('cwd')}" if a.get("cwd") else ""
        return f"Run this program:\n{a.get('command')}{cwd}"
    if name == "end_process":
        who = probe.process_name(a.get("pid"))
        how = "Force-kill" if a.get("force") else "Ask to exit"
        return f"{how} process {a.get('pid')}" + (f"  ({who})" if who else "")
    if name == "forget_all":
        return "Erase everything Eva has remembered about you"
    if name == "send_hotkey":
        title = _safe(probe.window_title, a.get("window_handle"))
        return f"Press {a.get('keys')}" + (f"\nin “{title}”" if title else "")
    if name in ("click_element", "act_on_element"):
        wh = a.get("window_handle")
        target = _safe(probe.element_text, wh, a.get("element_id")) or f"element {a.get('element_id')}"
        verb = str(a.get("action", "click")) if name == "act_on_element" else "Click"
        return f"{verb.capitalize()} “{target}”\nin “{_safe(probe.window_title, wh)}”"
    if name == "ui_navigate":
        return f"{str(a.get('action', 'click')).capitalize()} “{a.get('target')}”\n" \
               f"in “{_safe(probe.window_title, a.get('window_handle'))}”"
    if name in _CLICKS:
        target = _safe(getattr(probe, 'control_at', None), a.get("x", a.get("from_x")), a.get("y", a.get("from_y")))
        return f"{_CLICKS[name]} at ({a.get('x', a.get('from_x'))}, {a.get('y', a.get('from_y'))})" \
               + (f" on “{target}”" if target else "") + f"\nin “{_safe(probe.window_title, None)}”"
    if name in ("type_text", "type_into_element"):
        return f"Type text in “{_safe(probe.window_title, a.get('window_handle'))}”"
    return _generic(name, a)


def grant_key(name, args) -> str:
    if name == "run_command":
        import command_policy
        argv, err = command_policy.parse(str((args or {}).get("command", "")))
        return f"run_command:{command_policy.program_name(argv) if argv and not err else '?'}"
    return name


def assess(name, args, probe=None) -> Assessment:
    probe = probe or DesktopProbe()
    args = args or {}
    effect = capabilities.effect_of(name, args)
    money = _financial_reason(name, args, probe)
    harm = None if money else _destructive_reason(name, args, probe)
    if money:
        effect = Effect.FINANCIAL
    elif harm:
        effect = Effect.DESTROY
    gated = effect in capabilities.confirm_effects()
    if effect == Effect.FINANCIAL:
        title, reason = "This involves money", f"Because {money}." if money else "It spends money."
    elif effect == Effect.DESTROY:
        title = "This can't be undone"
        reason = f"Because {harm}." if harm else "It could lose or overwrite something."
    else:
        title, reason = capabilities.describe(name, args) or name, ""
    detail = describe_literal(name, args, probe) if gated else ""
    return Assessment(name, effect, gated, title, detail, reason,
                      task_scope=(effect == Effect.DESTROY and name in TASK_SCOPE_TOOLS and not harm),
                      key=grant_key(name, args))


def request_from(a: Assessment) -> ApprovalRequest:
    return ApprovalRequest(a.tool, a.effect.name, a.title, a.detail, a.reason, a.task_scope, a.key)


class PermissionGate:
    """Decides, asks if it must, and remembers task-scoped grants."""

    def __init__(self, approve: Optional[Callable[[ApprovalRequest], str]] = None, probe=None):
        # approve(request) -> "once" | "task" | "deny"
        self._approve = approve
        self.probe = probe
        self._grants = set()
        self.turn = 0

    def begin_turn(self):
        """A new request from the person starts with no grants."""
        self._grants = set()
        self.turn += 1

    def authorize(self, name, args) -> Verdict:
        a = assess(name, args, self.probe)
        if not a.gated:
            return Verdict(True, "auto", a)
        if a.task_scope and a.key in self._grants:
            return Verdict(True, "task-grant", a)
        return self._ask(a)

    def request(self, req: ApprovalRequest) -> Verdict:
        """For approvals raised from inside a tool (a browser step)."""
        if req.task_scope and req.key in self._grants and req.effect != "FINANCIAL":
            return Verdict(True, "task-grant")
        return self._ask_request(req, None)

    def _ask(self, a: Assessment) -> Verdict:
        return self._ask_request(request_from(a), a)

    def _ask_request(self, req: ApprovalRequest, a) -> Verdict:
        if self._approve is None:
            log.warning(f"{req.tool}: approval needed but no one can be asked -- refused")
            return Verdict(False, "no-approver", a)
        try:
            answer = str(self._approve(req)).lower()
        except Exception:
            log.exception("The approval dialog failed -- refusing the action")
            return Verdict(False, "denied", a)
        if answer == "task" and req.task_scope and req.effect != "FINANCIAL":
            self._grants.add(req.key)
            return Verdict(True, "task", a)
        if answer in ("once", "task"):
            return Verdict(True, "once", a)
        return Verdict(False, "denied", a)


DENIED_MESSAGE = ("The person declined this action. Do not retry it, and do not look for another "
                  "way to do the same thing. Say what you did not do and ask what they'd like instead.")
