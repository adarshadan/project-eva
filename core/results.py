"""Eva core -- shaping tool results before the model (and eva.log) sees them.

Carried over unchanged from the previous brain.py, where each of these fixed a
confirmed problem: one shape for success and failure, a usable message for a
call with the wrong arguments, credentials redacted from every result, and a
size cap so one ui_tree dump cannot multiply the cost of every later request.
"""
import inspect
import json
import re

import system_tools
from logger import get_logger

log = get_logger(__name__)

# Every tool result is appended to history and resent on every subsequent
# request for the rest of the turn. ui_tree on a complex app returns
# thousands of elements, read_file can return a whole document, and a
# Playwright timeout message carries the full selector -- each of which
# quietly multiplies token cost for many turns afterwards. Capped centrally
# here rather than tool by tool, so a new tool cannot reintroduce it.
MAX_TOOL_RESULT_CHARS = 8000
MAX_TOOL_ERROR_CHARS = 500


def _json_safe(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        pass
    try:
        return json.loads(json.dumps(value, default=lambda o: float(o) if type(o).__name__ == "Decimal" else
                                     (sorted(o, key=str) if isinstance(o, (set, frozenset)) else str(o))))
    except Exception:
        return {"ok": True, "result": str(value)[:4000]}


def _normalise_result(result):
    """Gives every tool result the same shape: an explicit "ok".

    Tools variously returned {"ok": False, "error": ...}, a bare
    {"error": ...} with no ok key, a plain data dict, or a string. The
    model then had to infer success three different ways, and a bare
    {"error": ...} in particular reads as data rather than a failure.
    One contract instead: ok is present and correct on everything.

    And JSON-safe: a Decimal (Windows Search sizes, eva.log 2026-10-11 22:58), a
    datetime, a Path or a set once ended the whole turn when the result was
    written out -- and an unserialisable result also slipped past secret
    redaction. Everything is reduced to plain JSON types here, once."""
    result = _json_safe(result)
    if isinstance(result, dict):
        if "ok" not in result:
            result = {"ok": "error" not in result, **result}
        return result
    if result is None:
        return {"ok": True}
    return {"ok": True, "result": result}


def _argument_error(name, fn, args, exc):
    """Turns a wrong-arguments TypeError into something the model can act on.

    eva.log 2026-09-26: the model passed window_handle to send_hotkey --
    reasonable, since every other action tool takes one -- and got a bare
    "got an unexpected keyword argument 'window_handle'" plus a traceback,
    twice in one session. Telling it what the tool actually accepts is
    cheaper and more reliable than making it infer the signature from an
    error string.

    Only a TypeError raised by the CALL is treated this way; one raised
    inside the tool's own body is a real fault and still logged with its
    traceback."""
    message = str(exc)
    signature_problem = ("unexpected keyword argument" in message
                         or "required positional argument" in message
                         or "positional arguments but" in message)
    if not signature_problem:
        log.exception(f"Tool '{name}' raised TypeError during execution, args={args}")
        return {"error": f"{name} failed: {message}"}
    try:
        params = inspect.signature(fn).parameters
        accepted = [p for p in params if not p.startswith("_")]
        required = [p for p, v in params.items()
                    if v.default is inspect.Parameter.empty and not p.startswith("_")]
    except (TypeError, ValueError):
        accepted = required = None
    log.warning(f"Tool '{name}' called with wrong arguments {sorted(args)}: {message}")
    out = {"ok": False, "error": f"{name} does not take those arguments: {message}"}
    if accepted is not None:
        out["accepts"] = accepted
        out["required"] = required
        out["hint"] = f"call {name}({', '.join(accepted)}) -- drop anything not in that list"
    return out


def _redact_secrets(result):
    """Last line of defence before a tool result enters the model's context
    and eva.log. read_file, read_window_text and ui_tree can all surface a
    credential the person never meant to hand over; get_clipboard already
    did, verbatim, on 2026-09-26. Redacting here covers every tool at once,
    including ones added later."""
    try:
        encoded = json.dumps(result)
    except (TypeError, ValueError):
        return result
    if not system_tools.looks_like_secret(encoded):
        return result
    log.warning("Redacting credential-like content from a tool result.")
    for pattern in system_tools._SECRET_PATTERNS:
        encoded = pattern.sub("[REDACTED CREDENTIAL]", encoded)
    try:
        return json.loads(encoded)
    except ValueError:
        return {"error": "result contained credential-like content and was withheld"}


_COMMS_FILLER = {"i", "ll", "will", "now", "then", "the", "a", "an", "to", "for",
                 "you", "and", "it", "into", "your", "me", "im", "ive", "let",
                 "s", "ok", "okay", "sure", "just", "going", "go", "am", "is", "be"}
# Two comms lines this similar are the same line for our purposes.
COMMS_SIMILARITY = 0.75


def _comms_tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _COMMS_FILLER}


def _is_near_duplicate(text: str, previous: list) -> bool:
    """Whether Eva has already said this, allowing for rewording.

    Exact matching was not enough. eva.log 2026-09-26 has three successive
    spoken announcements of one intent -- "I'll write a 1000-word passage
    about greenhouse gas pollution for you", "I'll create a 1000-word
    passage ... and write it into Notepad", "I'll now create a 1000-word
    passage about greenhouse gas pollution" -- differing by a verb. All
    three were spoken aloud, back to back, while nothing was done."""
    tokens = _comms_tokens(text)
    if len(tokens) < 3:          # too short to judge; treat as new
        return False
    for prior in previous:
        if not prior:
            continue
        overlap = len(tokens & prior) / len(tokens | prior)
        if overlap >= COMMS_SIMILARITY:
            return True
    return False


def _cap_tool_result(name: str, result):
    if isinstance(result, dict) and isinstance(result.get("error"), str):
        err = result["error"]
        if len(err) > MAX_TOOL_ERROR_CHARS:
            result = {**result, "error": err[:MAX_TOOL_ERROR_CHARS] + " ...[truncated]"}
    try:
        encoded = json.dumps(result)
    except (TypeError, ValueError):
        return result
    if len(encoded) <= MAX_TOOL_RESULT_CHARS:
        return result
    log.info(f"Tool result from {name} truncated: {len(encoded)} -> ~{MAX_TOOL_RESULT_CHARS} chars.")
    # Lists are the usual culprit (ui_tree elements, list_folder entries):
    # halve until it fits, so the model still sees representative data and
    # is told plainly that there was more.
    if isinstance(result, dict):
        # Lists first (ui_tree elements, list_folder entries): halve until
        # it fits, so the model still sees representative data.
        for key, value in result.items():
            if isinstance(value, list) and len(value) > 1:
                kept = value
                while kept and len(json.dumps({**result, key: kept})) > MAX_TOOL_RESULT_CHARS:
                    kept = kept[:len(kept) // 2]
                return {**result, key: kept, "truncated": True,
                        "omitted": len(value) - len(kept)}

        # Then a single long string field -- a file's text, a page's
        # content. Cutting the STRING keeps the result a readable result.
        # eva.log 2026-09-26 23:40 shows the old fallback turning a
        # perfectly good read_file into {"truncated": true, "preview":
        # "{\"path\": \"C:\\\\Users..."} -- the file's content wrapped in
        # escaped JSON inside a string, which the model then had to unpick.
        longest = max((k for k, v in result.items() if isinstance(v, str)),
                      key=lambda k: len(result[k]), default=None)
        if longest is not None and len(result[longest]) > 200:
            overhead = len(json.dumps({**result, longest: ""}))
            room = max(200, MAX_TOOL_RESULT_CHARS - overhead - 200)
            kept = result[longest][:room]
            out = {**result, longest: kept, "truncated": True,
                   "omitted_chars": len(result[longest]) - len(kept)}
            if "next_offset" not in out and "offset" in out:
                out["next_offset"] = out["offset"] + len(kept)
                out["note"] = (f"only the first {len(kept)} characters are shown; call again "
                               f"with offset={out['next_offset']} for the rest")
            return out

    return {"ok": False, "truncated": True,
            "error": "the result was too large to return and could not be summarised",
            "preview": encoded[:2000]}

