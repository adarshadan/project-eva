"""Eva core -- the model client.

Streaming chat completions with tool calling, plus every defence this project
learned the hard way, carried over intact from the previous brain.py:
  * a bounded timeout (a stream that stopped sending chunks once froze Eva for good);
  * retry for transient failures, including a provider "overloaded" error raised
    mid-stream as a bare APIError, and a 200 response whose body is the
    provider's own error envelope;
  * removal of <think> blocks and of reasoning that leaks into the answer.
New here: explain_error(), which turns a failure into one sentence a person can
act on (a rejected key, no access to the model, no network) instead of a
traceback in a speech bubble.
"""
import json
import threading
import re
import time

import config
import providers
from logger import get_logger

log = get_logger(__name__)

_CLIENT = {"c": None}          # the checks put a scripted client here; Eva itself uses providers.client



def _echo(text):
    """The live console echo of her thinking and reply. Never allowed to break a turn: a console that
    cannot show a character (cp1252 and U+202F, 2026-10-28 benchmark) or no console at all (pythonw)."""
    try:
        print(text, end="", flush=True)
    except (UnicodeEncodeError, OSError, ValueError, AttributeError):
        try:
            import sys
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(str(text).encode(enc, "replace").decode(enc, "replace"), end="", flush=True)
        except Exception:
            pass

def _client(ref=None):
    return _CLIENT["c"] or providers.client(ref or config.MODEL_ID, timeout=90.0)


# ---- the main model chain (plan Phase 1: no single provider can take Eva down) ------------------
# MODEL_ID first, then MODEL_FALLBACKS, each "provider:model" or a bare NVIDIA model id. A model
# that keeps answering "busy" is retried a few times, then the next one answers; one that is gone,
# refused or has no key is skipped and rested for REST_SECONDS so later turns start with one that
# works. The person hears nothing about it unless every model fails.
REST_SECONDS = 300
_resting = {}                  # ref -> monotonic time it may be tried again
_last = {"ref": None}


def chain() -> list:
    refs = list(dict.fromkeys([config.MODEL_ID] + list(getattr(config, "MODEL_FALLBACKS", ()) or ())))
    if _CLIENT["c"] is not None:
        return refs[:1] if len(refs) == 1 else refs
    out = []
    for ref in refs:
        ok, why = providers.usable(ref)
        if ok:
            out.append(ref)
        else:
            log.warning(f"Model {ref} skipped: {why}")
    if not out:
        return refs[:1]                     # nothing usable: try the primary so the error explains why
    now = time.monotonic()
    awake = [r for r in out if _resting.get(r, 0) <= now]
    return awake + [r for r in out if r not in awake]


def last_model() -> str:
    return _last["ref"] or config.MODEL_ID


def _model_gone(e) -> bool:
    """The provider will not serve this model to us at all: retired, unknown, refused, no key."""
    status = getattr(e, "status_code", None)
    text = str(e).lower()
    return status in (401, 402, 403, 404, 410) or any(
        t in text for t in ("not found", "does not exist", "deprecated", "end of life", "no longer available",
                            "unknown model", "model_not_found", "not supported", "authentication"))


def explain_error(exc) -> str:
    """One plain sentence for the person. The full error is in eva.log."""
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    name = type(exc).__name__
    if status == 401 or "authentication" in name.lower() or "401" in text:
        return "My API key was rejected. Right-click my tray icon, choose Set API key, and paste a valid key."
    if status == 403 or "permissiondenied" in name.lower() or "authorization failed" in text:
        return ("The API key works but isn't allowed to use this model (403). Check the key's access on "
                "build.nvidia.com, or set a model it can use in config.py.")
    if status == 404 or "notfound" in name.lower():
        return f"The model {last_model()} wasn't found. Check MODEL_ID in config.py."
    if status == 429 or "ratelimit" in name.lower():
        return "I've hit the provider's rate limit. Give me a minute and ask again."
    if "connection" in name.lower() or "timeout" in name.lower() or "timed out" in text:
        return "I can't reach the model service right now. Check the internet connection."
    if status and 500 <= int(status) < 600:
        return "The model service is having trouble right now. Try again in a moment."
    return "Something went wrong on my side. The details are in eva.log."


# Matches well-formed <think>...</think> or <thinking>...</thinking>
# blocks (some providers fold reasoning into content this way instead of
# a separate reasoning_content field). A second pattern below catches an
# UNCLOSED tag -- if a response gets cut off before the closing tag,
# everything from the opening tag onward would otherwise leak straight
# into what the user sees/hears.
_THINK_TAG = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.IGNORECASE | re.DOTALL)
_UNCLOSED_THINK_TAG = re.compile(r"<think(?:ing)?>.*$", re.IGNORECASE | re.DOTALL)


# --- Reasoning-leak guard -------------------------------------------------
# Confirmed in eva.log: on some turns the provider emits the SAME text in
# BOTH reasoning_content and content, then keeps generating in the content
# channel -- so Eva spoke her own planning notes aloud ("We need to resize
# the foreground window... Let's do that."), sometimes with a raw tool-call
# JSON blob on the end and the real tool call never made. Observed only on
# turns AFTER a prior assistant message carried reasoning_content back into
# history, which the model then treats as a prefill to continue from.
#
# Two defences: config.SEND_REASONING_BACK (root cause, off by default) and
# this detector (belt and braces, provider-independent).
# Confirmed in eva.log 2026-09-26: the endpoint returned HTTP 200 with the
# assistant content set to a gateway error envelope --
#   [ERROR: Agent failed (Function process_single_item_agent timed out
#    after 90.0 seconds), API failed (API request returned None ...)]
# No such function exists anywhere in this codebase; it is the provider's
# own internal error, handed back as if it were Eva's reply, so it sailed
# past every error path (no exception, no non-200) and got spoken aloud
# verbatim. Treated as a failed call and retried instead.
_PROVIDER_ERROR_ENVELOPE = re.compile(
    r"^\s*\[?\s*ERROR\s*:.*(?:API (?:request |call )?(?:failed|returned None)"
    r"|timed out after|all retries|Agent failed).*\]?\s*$",
    re.IGNORECASE | re.DOTALL)


def _is_provider_error(content: str) -> bool:
    return bool(content) and bool(_PROVIDER_ERROR_ENVELOPE.match(content.strip()))


_MIN_LEAK_PREFIX = 40          # chars of exact overlap before it counts
_TRAILING_JSON = re.compile(r"\s*\{[^{}]*\}\s*$", re.DOTALL)


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


# Second layer, for the case the prefix check above cannot see: a model
# that writes its planning into the content channel ONLY, with no
# reasoning_content and no <think> tags, so there is nothing to compare
# against. Detected by shape, not style -- planning voice ("We need to...",
# "Let's use...") PLUS hard evidence that this is machinery rather than
# speech: a bare tool-argument JSON blob, or a literal tool name. Both
# conditions are required, so an ordinary reply that happens to start
# "Let's see..." is never suppressed.
_PLANNING_VOICE = re.compile(
    r"\b(?:we|i)\s+(?:need to|should|could|can|ll|will)\b|\blet'?s\b|\bprobably\b|\bnot sure\b",
    re.IGNORECASE)
_BARE_JSON_BLOB = re.compile(r"\{[^{}]*\}")
_TOOL_NAME_MENTION = re.compile(
    r"\b(?:use|call|maybe|try)\s+[a-z]+_[a-z_]+\b|\b[a-z]+_[a-z_]+\(", re.IGNORECASE)


def _looks_like_bare_planning(content: str) -> bool:
    """Whether a reply is the model's planning notes leaking into its answer.

    Requires planning voice AND a bare tool-argument JSON blob -- the shape
    of a tool call that was written out as text instead of being made. A
    tool NAME alone is not enough: "Let's try browser_open on that URL" is
    a perfectly good thing to say to a person, and used to be discarded. A
    tool name only counts when the reply is also long enough to be notes
    rather than a sentence."""
    if not content or not _PLANNING_VOICE.search(content):
        return False
    if _BARE_JSON_BLOB.search(content):
        return True
    return len(content) > 240 and bool(_TOOL_NAME_MENTION.search(content))


def _detect_reasoning_leak(content: str, reasoning: str | None) -> bool:
    """True when content is this turn's reasoning duplicated into the
    answer channel. Deliberately strict: an exact shared prefix, not a
    fuzzy style match, so a genuine reply is never mistaken for a leak."""
    if not content or not reasoning:
        return False
    return _common_prefix_len(content, reasoning) >= _MIN_LEAK_PREFIX


# ---- a call that cannot run away ------------------------------------------------------
# eva.log 2026-10-12 00:05:44: the model's reasoning went round in circles over a Wordle board
# ("Wait row3 seems incomplete... Actually...") and the call never ended -- 70 s of nothing,
# no way to stop it, until Eva was closed. The stream had no limit of any kind: the client
# timeout only fires when NOTHING arrives, and this kept arriving.
ABORT_POLL_SECONDS = 0.2
_ABORT = threading.local()                  # .fn: () -> "stop" | "interjection" | None
_THINK = threading.local()                  # .effort / .seconds: a call that must think harder (planning)


class thinking:
    """with llm.thinking("medium", 90): ... -- the calls inside reason harder and may take longer.
    For planning a multi-step job (core/operator.py): the default ("low", 45 s) is right for the many
    small turns and wrong for working out what to do next in a long task."""

    def __init__(self, effort="medium", seconds=90):
        self.effort, self.seconds = effort, seconds

    def __enter__(self):
        self.prev = (getattr(_THINK, "effort", None), getattr(_THINK, "seconds", None))
        _THINK.effort, _THINK.seconds = self.effort, self.seconds
        return self

    def __exit__(self, *a):
        _THINK.effort, _THINK.seconds = self.prev
        return False


def set_abort(fn):
    """The current thread's check, read between streamed chunks (core/agent.py sets it)."""
    _ABORT.fn = fn


def _repeating(text, window=240, times=3):
    """True when the tail of the reasoning has already appeared `times` times: a loop."""
    if len(text) < window * times:
        return False
    tail = text[-window:]
    return text.count(tail) >= times


def _runaway(reasoning_len, reasoning_text, started, answered):
    if answered:
        return None
    if time.monotonic() - started > float(getattr(_THINK, "seconds", None) or getattr(config, "REASONING_MAX_SECONDS", 45)):
        return "thought for too long"
    if reasoning_len > int(getattr(config, "REASONING_MAX_CHARS", 16000)):
        return "thought at too great length"
    if reasoning_len > 1500 and _repeating(reasoning_text):
        return "went round in circles"
    return None


def _call_model(messages: list[dict], system_prompt: str, tools: list, ref: str = None) -> dict:
    """Streams the response so reasoning and the final answer are visible
    live in the terminal as they're generated -- useful for a "thinking"
    model like Kimi K3 where a silent multi-second wait otherwise gives no
    indication anything is happening. The rest of the app still gets the
    same fully-accumulated result as before; nothing downstream changes."""
    ref = ref or config.MODEL_ID
    kw = dict(
        model=providers.parse(ref)[1],
        messages=providers.clean_messages(ref, [{"role": "system", "content": system_prompt}, *messages]),
        tools=tools,
        tool_choice="auto",
        # Lower than a typical chat default: for tool-calling decisions
        # (which tool, which arguments) determinism matters more than
        # variety, and a lower temperature measurably reduces hallucinated
        # or inconsistent tool choices.
        temperature=0.4,
        top_p=0.95,
        stream=True,
    )
    if providers.is_nim(ref) or _CLIENT["c"] is not None:
        # Nemotron 3 exposes a real reasoning budget (NVIDIA endpoints only -- strict providers
        # reject unknown fields). Kimi K3 reasoned at length on every turn with no way to dial it
        # down, the documented cause of the latency seen in every session. Tunable in config.py.
        kw["extra_body"] = {"reasoning_effort": getattr(_THINK, "effort", None) or config.REASONING_EFFORT}
    if not tools:                         # a plain question (planning): no tool list at all
        kw.pop("tools", None)
        kw.pop("tool_choice", None)
    if _CLIENT["c"] is None:
        providers.pace(ref)               # one shared budget per provider (providers.pace)
    stream = _client(ref).chat.completions.create(**kw)

    content_parts, reasoning_parts = [], []
    tool_call_chunks = {}  # index -> {"id", "name", "arguments"} accumulated across chunks
    printed_thinking_header = printed_answer_header = False
    started, reasoning_len, checked_at = time.monotonic(), 0, 0
    abort_fn = getattr(_ABORT, "fn", None)

    def cut(kind, why):
        try:
            stream.close()
        except Exception:
            pass
        log.warning(f"Model call cut short: {why} ({reasoning_len} characters of reasoning, "
                    f"{time.monotonic() - started:.0f} s)")
        return {"content": "", "reasoning_content": None, "reasoning_leak": False, "provider_error": False,
                "tool_calls": [], kind: why}

    # A stop must not wait for the next chunk: while the model composes a long tool call (a
    # 300-word story to type) NOTHING arrives, and "stop" waited 19 s (eva.log 2026-10-17
    # 12:12:28 -> 12:12:47). A watcher checks every 0.2 s and closes the stream at once.
    stopped, finished = {"why": None}, threading.Event()
    if abort_fn is not None:
        def _watch():
            while not finished.wait(ABORT_POLL_SECONDS):
                try:
                    why = abort_fn()
                except Exception:
                    why = None
                if why:
                    stopped["why"] = why
                    try:
                        stream.close()
                    except Exception:
                        pass
                    return
        threading.Thread(target=_watch, daemon=True, name="eva-llm-abort").start()
    try:
        for chunk in stream:
            if not stopped["why"] and abort_fn is not None:     # at once while chunks are flowing
                stopped["why"] = abort_fn()
            if stopped["why"]:
                break
            if reasoning_len - checked_at > 400:                 # checking every chunk would be wasteful
                checked_at = reasoning_len
                why = _runaway(reasoning_len, "".join(reasoning_parts), started,
                               bool(content_parts or tool_call_chunks))
                if why:
                    return cut("runaway", why)
            # Some providers send keepalive/usage-only chunks (e.g. a final
            # chunk carrying token-usage stats when stream_options.include_usage
            # is set) with an EMPTY choices list -- chunk.choices[0] on one of
            # those is an IndexError that would kill this call outright.
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            reasoning_piece = getattr(delta, "reasoning_content", None)
            if reasoning_piece:
                if not printed_thinking_header:
                    _echo("\n[Eva thinking] ")
                    printed_thinking_header = True
                _echo(reasoning_piece)
                reasoning_parts.append(reasoning_piece)
                reasoning_len += len(reasoning_piece)

            if delta.content:
                if not printed_answer_header:
                    _echo("\n[Eva] ")
                    printed_answer_header = True
                _echo(delta.content)
                content_parts.append(delta.content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    entry = tool_call_chunks.setdefault(tc_delta.index, {"id": None, "name": None, "arguments": ""})
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments
    except Exception:
        if not stopped["why"]:
            raise
    finally:
        finished.set()
    if stopped["why"]:
        return cut("aborted", stopped["why"])

    if printed_thinking_header or printed_answer_header:
        print()  # tidy newline once streaming for this turn is done

    content = "".join(content_parts).strip()
    # Providers that fold reasoning into content as inline <think>/<thinking>
    # tags (rather than a separate reasoning_content stream) still get
    # stripped here for what's actually used downstream -- the live
    # terminal view above will show the raw tags for those providers, only
    # the final accumulated value used for bubble/TTS/history is cleaned.
    content = _THINK_TAG.sub("", content)
    # A response cut short before its closing tag would otherwise leak
    # everything from the opening tag onward straight into what the user
    # sees/hears -- this catches that case specifically.
    content = _UNCLOSED_THINK_TAG.sub("", content).strip()

    reasoning_content = "".join(reasoning_parts).strip() or None
    if reasoning_content:
        log.info(f"Model reasoning: {reasoning_content[:500]!r}")

    # See _detect_reasoning_leak. When the content channel is a copy of the
    # reasoning channel there is no trustworthy user-facing text anywhere in
    # it, so it is discarded WHOLE rather than salvaged by trimming -- a
    # partial trim still left planning prose and tool JSON to be spoken.
    # The caller recovers: tool calls (if any) still run, and a
    # content-only leak is re-asked for a clean reply.
    provider_error = _is_provider_error(content)
    if provider_error:
        log.error(f"Provider returned an error envelope as content: {content[:200]!r}")
        content = ""

    reasoning_leak = (_detect_reasoning_leak(content, reasoning_content)
                      or _looks_like_bare_planning(content))
    if reasoning_leak:
        log.warning(f"Reasoning leaked into content -- discarding: {content[:300]!r}")
        content = ""
    log.info(f"Model content: {content!r}")

    tool_calls = []
    for idx in sorted(tool_call_chunks):
        entry = tool_call_chunks[idx]
        try:
            args = json.loads(entry["arguments"]) if entry["arguments"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({"id": entry["id"], "name": entry["name"], "args": args})

    return {
        "content": content,
        # Some models (Kimi K3 explicitly, per NVIDIA's own model card) are
        # documented as requiring the full prior assistant message --
        # including reasoning_content -- to be sent back on every
        # subsequent turn of a multi-turn/tool-calling conversation, or
        # they may re-derive reasoning from scratch each turn instead of
        # building on it. Captured here, threaded through by the caller.
        "reasoning_content": reasoning_content,
        "reasoning_leak": reasoning_leak,
        "provider_error": provider_error,
        "tool_calls": tool_calls,
    }


FALLBACK_ATTEMPTS = 2          # tries on a busy model before the next in the chain answers


class _ProviderErrorEnvelope(Exception):
    """Raised internally so a 200-with-an-error-body retries like a
    transport failure would. Never escapes _call_model_with_retry."""


def call(messages: list[dict], system_prompt: str, tools: list, attempts: int = 5) -> dict:
    """Retries anything transient: rate limits, timeouts, dropped
    connections, and provider-side overload.

    The overload case is why this was widened. A provider returning
    "Service temporarily overloaded" MID-STREAM raises a bare
    openai.APIError -- not RateLimitError, not APITimeoutError, and its
    class name contains neither "Timeout" nor "Connection", so it matched
    none of the earlier branches and escaped retry completely. Confirmed
    in practice: it killed three of four interactions in a single
    session, in each case AFTER the requested action had already run, so
    the work happened and then the turn died reporting failure.

    APIError is the base class of real errors too (a malformed request,
    an unknown model), and retrying those five times just wastes time
    without any chance of succeeding -- so only messages that look
    transient are retried, and anything else is raised immediately.
    """
    from openai import RateLimitError, APITimeoutError, APIConnectionError, APIError

    TRANSIENT = ("overload", "temporarily", "unavailable", "try again",
                 "capacity", "timeout", "timed out", "502", "503", "504")

    def transient(e) -> bool:
        if isinstance(e, _ProviderErrorEnvelope):
            return True
        if isinstance(e, (RateLimitError, APITimeoutError, APIConnectionError)):
            return True
        name = type(e).__name__
        if "Timeout" in name or "Connection" in name:
            return True
        if isinstance(e, APIError):
            return any(t in str(e).lower() for t in TRANSIENT)
        return False

    refs = chain()
    last_error = None
    for i, ref in enumerate(refs):
        final = i == len(refs) - 1
        tries = attempts if final else min(attempts, FALLBACK_ATTEMPTS)
        delay = 1.5
        for attempt in range(tries):
            try:
                out = _call_model(messages, system_prompt, tools, ref=ref)
                # A provider error envelope means this call produced nothing
                # usable, even though it came back 200 with no exception.
                # Retried on the same backoff as a transport failure.
                if out.get("provider_error") and not out["tool_calls"] and attempt < tries - 1:
                    raise _ProviderErrorEnvelope("provider returned an error envelope as content")
                if _last["ref"] != ref:
                    if ref != refs[0] or _last["ref"] is not None:
                        log.info(f"Model now answering: {providers.describe(ref)}")
                    _last["ref"] = ref
                _resting.pop(ref, None)
                return out
            except Exception as e:
                last_error = e
                if _model_gone(e) and not final:
                    _resting[ref] = time.monotonic() + REST_SECONDS
                    log.error(f"Model {providers.describe(ref)} unavailable ({type(e).__name__}: {str(e)[:120]}) "
                              f"-- using {providers.describe(refs[i + 1])}")
                    break
                if not transient(e):
                    raise
                if attempt == tries - 1:
                    if final:
                        raise
                    _resting[ref] = time.monotonic() + REST_SECONDS / 2
                    log.warning(f"Model {providers.describe(ref)} still busy after {tries} tries -- "
                                f"using {providers.describe(refs[i + 1])}")
                    break
                told = providers.retry_after(e) if isinstance(e, RateLimitError) else 0.0
                if isinstance(e, RateLimitError) and not told:
                    # 2026-10-28: NVIDIA's 429 carries no Retry-After, and a fast model met the limit again
                    # after 1.5 s; a minute-window limit needs a real pause, for every caller.
                    told = min(60.0, 15.0 * (attempt + 1))
                if told:
                    providers.hold(ref, told)      # every caller waits, not only this one
                log.warning(f"{type(e).__name__}: {e} -- retrying in {max(delay, told)}s "
                            f"(attempt {attempt + 1}/{tries})")
                time.sleep(0 if told else delay)   # pace() does the waiting when the provider said how long
                delay *= 2
    raise last_error or RuntimeError("no model to call")


_REAL_CALL = call        # the checks script llm.call; this one stays the real chain
