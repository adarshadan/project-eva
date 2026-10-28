"""Eva -- an append-only record of what she did, and what she was allowed to.

One JSON line per tool call: when, which tool, what effect it had, whether
she asked and what the answer was, and how it turned out. It exists so that
"why is this file gone?" or "did she really place that order?" has an
answer that does not depend on anyone's memory of a session, including hers.

Arguments are summarised, not copied: file contents and typed text are
recorded as a length, never verbatim, and anything credential-shaped is
redacted. The log is rotated (5 MB x 3) so it cannot fill the disk.
"""
import json
import logging
import os
import re
import threading
import time
from logging.handlers import RotatingFileHandler

_LOCK = threading.Lock()
_CONTENT_KEYS = {"content", "text", "payload", "value"}      # recorded by length only
_SECRET = [
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}"), re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"), re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S{6,}"),
]


def default_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".eva", "audit.jsonl")


def summarise_args(args) -> dict:
    out = {}
    for key, value in (args or {}).items():
        if key in _CONTENT_KEYS and isinstance(value, (str, dict, list)):
            out[key] = f"<{len(json.dumps(value, default=str)) if not isinstance(value, str) else len(value)} chars>"
        elif isinstance(value, str) and len(value) > 160:
            out[key] = value[:100] + f"…<{len(value)} chars>"
        else:
            out[key] = value
    return out


def redact(text: str) -> str:
    for pattern in _SECRET:
        text = pattern.sub("[REDACTED]", text)
    return text


class AuditLog:
    def __init__(self, path=None, max_bytes=5_000_000, backups=3, clock=time.time):
        self.path = path or default_path()
        self._clock = clock
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # A private logger per file, not propagated: the audit trail must
        # never end up interleaved with eva.log, or lost when it rotates.
        self._log = logging.getLogger(f"eva.audit.{abs(hash(self.path))}")
        self._log.setLevel(logging.INFO)
        self._log.propagate = False
        if not self._log.handlers:
            handler = RotatingFileHandler(self.path, maxBytes=max_bytes, backupCount=backups,
                                          encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._log.addHandler(handler)

    def record(self, **event):
        """Never raises: failing to write an audit line must not stop the work."""
        try:
            event.setdefault("ts", round(self._clock(), 3))
            if "args" in event:
                event["args"] = summarise_args(event["args"])
            line = redact(json.dumps(event, ensure_ascii=False, default=str))
            with _LOCK:
                self._log.info(line)
        except Exception:
            logging.getLogger(__name__).exception("Could not write an audit record")

    def recent(self, n=20, tool=None):
        rows = []
        # Oldest first: rotated backups (.3 -> .1) and then the live file, so
        # "the last 20 things she did" still works right after a rotation.
        for path in [f"{self.path}.{i}" for i in (3, 2, 1)] + [self.path]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if tool is None or row.get("tool") == tool:
                            rows.append(row)
            except OSError:
                pass
        return rows[-n:]
