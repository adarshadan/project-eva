"""Eva -- model providers (release plan, Phase 1: "one free model provider" was a release blocker).

A model reference is either a bare model id, served by the default provider (NVIDIA, as before):

    "nvidia/nemotron-3-super-120b-a12b"

or "provider:model-id", served by any OpenAI-compatible endpoint:

    "openrouter:nvidia/nemotron-3-super-120b-a12b:free"
    "openai:gpt-4.1-mini"
    "ollama:qwen3:8b"                 (local, no key)
    "lmstudio:qwen2.5-7b-instruct"    (local, no key)

Presets below; config.PROVIDERS adds or overrides any (an Azure deployment, a company gateway):

    PROVIDERS = {"azure": {"base_url": "https://<name>.openai.azure.com/openai/v1", "key": "AZURE_OPENAI_KEY"}}

Keys live in the encrypted store (set_key.bat <KEY_NAME>), or an environment variable of the same
name. Only providers flagged `nim` get NVIDIA's request extras (reasoning_effort,
chat_template_kwargs) and the non-standard `reasoning_content` field in history -- strict providers
reject unknown fields with a 400.
"""
import os
import threading

import config
from logger import get_logger

log = get_logger(__name__)

DEFAULT = "nvidia"
PRESETS = {
    "nvidia":     {"base_url": "https://integrate.api.nvidia.com/v1", "key": "NVIDIA_API_KEY", "nim": True},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key": "OPENROUTER_API_KEY"},
    "openai":     {"base_url": "https://api.openai.com/v1", "key": "OPENAI_API_KEY"},
    "groq":       {"base_url": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY"},
    "together":   {"base_url": "https://api.together.xyz/v1", "key": "TOGETHER_API_KEY"},
    "ollama":     {"base_url": "http://localhost:11434/v1", "key": None, "local": True},
    "lmstudio":   {"base_url": "http://localhost:1234/v1", "key": None, "local": True},
}
_PLACEHOLDER = "PASTE_YOUR_KEY_HERE"
_clients = {}
_lock = threading.Lock()


def all_providers() -> dict:
    out = {k: dict(v) for k, v in PRESETS.items()}
    for name, spec in (getattr(config, "PROVIDERS", None) or {}).items():
        out[str(name).lower()] = {**out.get(str(name).lower(), {}), **dict(spec)}
    out[DEFAULT]["base_url"] = getattr(config, "NVIDIA_BASE_URL", out[DEFAULT]["base_url"])
    return out


def parse(ref: str):
    """(provider name, model id). Only a KNOWN provider name before the first ':' is a prefix,
    so model ids that contain ':' themselves (':free', 'qwen3:8b') stay intact."""
    ref = str(ref or "").strip()
    head, sep, rest = ref.partition(":")
    if sep and head.lower() in all_providers() and rest:
        return head.lower(), rest
    return DEFAULT, ref


def spec(name: str) -> dict:
    p = all_providers().get(name)
    if p is None:
        raise KeyError(f"unknown model provider {name!r}")
    return p


def api_key(name: str) -> str:
    p = spec(name)
    if p.get("local") or not p.get("key"):
        return "local"                    # the client library insists on some key
    if name == DEFAULT:
        key = getattr(config, "NVIDIA_API_KEY", "")
    else:
        key = os.environ.get(p["key"], "")
        if not key:
            try:
                import secret_store
                key = secret_store.get(p["key"], "")
            except Exception:
                key = ""
    return "" if (not key or _PLACEHOLDER in key) else key


def usable(ref: str):
    """(True, '') or (False, why) -- a provider without its key is skipped, not tried."""
    try:
        name, model = parse(ref)
        p = spec(name)
    except KeyError as e:
        return False, str(e)
    if not model:
        return False, "no model id"
    if not api_key(name):
        return False, f"no key for {name} (set_key.bat {p.get('key')})"
    return True, ""


def is_nim(ref: str) -> bool:
    try:
        return bool(spec(parse(ref)[0]).get("nim"))
    except KeyError:
        return False


def client(ref: str, timeout: float = 90.0, max_retries=None):
    """One cached OpenAI-compatible client per (provider, timeout, retries)."""
    name, _ = parse(ref)
    key = (name, float(timeout), max_retries)
    with _lock:
        c = _clients.get(key)
        if c is None:
            from openai import OpenAI
            kw = dict(api_key=api_key(name), base_url=spec(name)["base_url"], timeout=timeout)
            if max_retries is not None:
                kw["max_retries"] = max_retries
            c = _clients[key] = OpenAI(**kw)
        return c


# ---- pacing: one request budget per provider, shared by every caller ------------------------------
# Eva 2.0 (2026-10-28): text, vision and the browser agent each called the model on their own, so a busy
# step spent three requests at once and tripped NVIDIA's ~40-a-minute limit (429s, then stalls). Every
# call now asks pace() first: under the provider's PROVIDER_RPM it returns at once; at the limit it waits
# for the oldest request in the last minute to age out. A 429's Retry-After holds that provider too.
_recent = {}             # provider -> [monotonic times of requests in the last minute]
_hold_until = {}         # provider -> monotonic time before which nothing is sent (Retry-After)
_pace_lock = threading.Lock()
PACE_MAX_WAIT = 30.0     # never wait longer than this for one request: a slow answer beats none


def rpm(name: str) -> int:
    table = getattr(config, "PROVIDER_RPM", None) or {}
    try:
        return int(table.get(name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def pace(ref: str, clock=None, sleep=None) -> float:
    """Waits, if needed, so this provider stays under its budget. Returns the seconds waited."""
    import time as _t
    clock = clock or _t.monotonic
    sleep = sleep or _t.sleep
    try:
        name, _ = parse(ref)
    except Exception:
        return 0.0
    limit = rpm(name)
    waited = 0.0
    while True:
        with _pace_lock:
            now = clock()
            times = [t for t in _recent.get(name, []) if now - t < 60.0]
            hold = _hold_until.get(name, 0.0) - now
            if hold > 0:
                wait = hold
            elif limit and len(times) >= limit:
                wait = 60.0 - (now - times[0]) + 0.05
            else:
                times.append(now)
                _recent[name] = times
                return waited
            _recent[name] = times
        wait = min(wait, PACE_MAX_WAIT - waited)
        if wait <= 0:                              # waited long enough: send it anyway
            with _pace_lock:
                _recent.setdefault(name, []).append(clock())
            return waited
        if waited == 0.0:
            log.info(f"Pacing {name}: waiting {wait:.1f} s to stay under {limit or '?'} requests a minute")
        sleep(wait)
        waited += wait


def hold(ref: str, seconds: float, clock=None):
    """A 429 said to wait: nothing goes to this provider before then."""
    import time as _t
    clock = clock or _t.monotonic
    try:
        name, _ = parse(ref)
    except Exception:
        return
    with _pace_lock:
        _hold_until[name] = max(_hold_until.get(name, 0.0), clock() + max(0.0, float(seconds)))


def retry_after(error) -> float:
    """Seconds from a rate-limit error's Retry-After header, or 0."""
    try:
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
        v = headers.get("retry-after") or headers.get("Retry-After")
        return max(0.0, min(60.0, float(v))) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def reset_clients():
    """After a key changes (Set API key)."""
    with _lock:
        _clients.clear()


_STANDARD_MESSAGE_KEYS = {"role", "content", "name", "tool_calls", "tool_call_id"}


def clean_messages(ref: str, messages: list) -> list:
    """History in the shape THIS provider accepts: NVIDIA's extra fields only for NIM endpoints."""
    if is_nim(ref):
        return messages
    return [{k: v for k, v in m.items() if k in _STANDARD_MESSAGE_KEYS} for m in messages]


def describe(ref: str) -> str:
    name, model = parse(ref)
    return model if name == DEFAULT else f"{model} via {name}"
