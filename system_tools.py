"""
Eva -- Phase 1 system capabilities: clipboard, desktop notifications, and
file/document search, reading, and light organisation. Every function
returns a plain dict and never raises, matching the other tool modules,
so a failure reaches the model as a readable error instead of a crash.
"""
import re
import os
import shutil
import time

from logger import get_logger

log = get_logger(__name__)

# ---------------- notifications ----------------
# Set from main.py once the system tray icon exists (Windows toasts are
# shown through QSystemTrayIcon.showMessage -- no extra dependency).
_notifier = None


def set_notifier(fn):
    global _notifier
    _notifier = fn


def notify(title: str, message: str):
    if _notifier is None:
        return {"ok": False, "error": "notifications are not available yet"}
    _notifier(title, message)
    return {"ok": True}


# ---------------- clipboard ----------------
# Confirmed leak, eva.log 2026-09-26: a ctrl+A/ctrl+C that selected nothing
# left the person's live NVIDIA API key on the clipboard, get_clipboard
# returned it verbatim, and it went into the model's context, the provider's
# logs and eva.log. The system prompt forbids reading secrets aloud, but the
# prompt only sees the value AFTER it has already left the machine. This is
# the only place it can actually be stopped.
_SECRET_PATTERNS = [
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # An assignment only counts when the VALUE looks like a real secret.
    # The bare pattern matched ordinary source code and locked Eva out of
    # this very project: eva.log 2026-09-26 23:36 refused to read
    # config.py over the line
    #     NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "PASTE_YOUR_KEY_HERE")
    # and refused main.py for merely mentioning the name. Asked to review
    # her own code, she could not open it.
    #
    # The value must therefore be a long opaque literal -- quoted or bare,
    # since a plain Credentials.txt has no quotes and Eva once read a
    # password out of one -- and must NOT be a placeholder, or an
    # expression (anything of the form name. / name( / name[ , which
    # covers os.environ.get, config.NVIDIA_API_KEY and getpass.getpass()).
    re.compile(r"""(?ix)
        \b(?:api[_-]?key|secret|password|passwd|token|access[_-]?key)\s*[:=]\s*
        (?![A-Za-z_][A-Za-z0-9_]*\s*[.\(\[])        # not an expression
        (?!['"]?(?:PASTE|YOUR|CHANGE|REPLACE|EXAMPLE|DUMMY|FAKE|TODO|XXX|None|True|False))
        ['"]?[A-Za-z0-9+/_\-]{12,}['"]?
    """),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),  # JWT
]


def looks_like_secret(text: str) -> bool:
    return any(p.search(text or "") for p in _SECRET_PATTERNS)


def get_clipboard(max_chars: int = 5000):
    try:
        import pyperclip
        text = pyperclip.paste() or ""
        if looks_like_secret(text):
            log.warning("Clipboard appears to hold a credential -- refusing to return it.")
            return {"error": "the clipboard appears to contain a credential "
                             "(API key, token or password); refusing to read it",
                    "length": len(text)}
        return {"text": text[:max_chars], "truncated": len(text) > max_chars}
    except Exception as e:
        return {"error": str(e)}


def set_clipboard(text: str):
    try:
        import pyperclip
        pyperclip.copy(text)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------- files ----------------
_SKIP_DIRS = {"appdata", "node_modules", ".git", "__pycache__", "venv", ".venv",
              "$recycle.bin", "windows", "program files", "program files (x86)"}
_TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".ts", ".html",
             ".css", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".sql", ".bat", ".ps1"}


def _resolve(path: str) -> str:
    """Relative paths are taken relative to the user's home folder, so the
    model can say "Documents/report.pdf" without knowing the full path."""
    path = os.path.expandvars(os.path.expanduser(path or "~"))
    if not os.path.isabs(path):
        path = os.path.join(os.path.expanduser("~"), path)
    return os.path.normpath(path)


def search_files(query: str, root: str = "~", extensions=None, max_results: int = 20,
                 time_budget_s: float = 8.0):
    """Name-based search (all query words must appear in the file name),
    time-bounded so a search of a large home folder can't stall Eva."""
    root = _resolve(root)
    if not os.path.isdir(root):
        return {"error": f"not a folder: {root}"}
    words = [w.lower() for w in (query or "").split() if w.strip()]
    exts = {"." + e.lower().lstrip(".") for e in (extensions or [])}
    if not words and not exts:
        return {"error": "give a query and/or extensions to search for"}
    results, deadline = [], time.time() + time_budget_s
    for dirpath, dirnames, filenames in os.walk(root):
        # startswith, not exact equality: "program files (x86)" never
        # matched the exact name "program files", so every search walked
        # the whole of it and burned the time budget there.
        dirnames[:] = [d for d in dirnames
                       if not any(d.lower().startswith(skip) for skip in _SKIP_DIRS)
                       and not d.startswith(".")]
        for name in filenames:
            low = name.lower()
            if words and not all(w in low for w in words):
                continue
            if exts and os.path.splitext(low)[1] not in exts:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            results.append({
                "path": full,
                "size_kb": round(st.st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            })
            if len(results) >= max_results:
                return {"results": results, "complete": False, "note": "result limit reached"}
        if time.time() > deadline:
            return {"results": results, "complete": False,
                    "note": "search time limit reached -- narrow the folder or query"}
    return {"results": results, "complete": True}


def read_file(path: str, max_chars: int = 8000, offset: int = 0):
    """Plain text files, .pdf (pypdf) and .docx (python-docx). The model
    summarises/answers from the returned text itself."""
    path = _resolve(path)
    if not os.path.isfile(path):
        return {"error": f"file not found: {path}"}
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:50])
        elif ext == ".docx":
            import docx
            text = "\n".join(p.text for p in docx.Document(path).paragraphs)
        elif ext in _TEXT_EXT or ext == "":
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                # offset + max_chars + 1, not max_chars + 1: reading only
                # the first chunk meant every paged call past the first
                # returned the same opening characters or nothing at all.
                # The +1 is what tells us whether more remains.
                text = f.read(offset + max_chars + 1)
        else:
            return {"error": f"unsupported file type {ext!r} (supported: text files, .pdf, .docx)"}
    except ImportError as e:
        return {"error": f"missing library for {ext} files: {e}"}
    except Exception as e:
        return {"error": str(e)}
    # The system prompt tells Eva not to read secrets aloud, but by the
    # time the prompt applies, the file contents are already in the model's
    # context and in the provider's logs. This is the only point at which
    # it can actually be prevented. Eva once read a password out of a
    # Credentials.txt; the clipboard equivalent leaked a live API key on
    # 2026-09-26.
    if looks_like_secret(text):
        log.warning(f"Refusing to return {path!r} -- it appears to contain credentials.")
        return {"path": path,
                "error": "this file appears to contain credentials (API key, token, "
                         "password or private key); refusing to read it. Tell the person "
                         "what the file looks like, do not try to read it another way.",
                "length": len(text)}
    # Paged, not silently cut off. A 40-page PDF used to come back as its
    # first 8000 characters with a bare truncated:true -- there was no way
    # to read the rest, so anything past the first few pages was simply
    # invisible to Eva however the person asked.
    chunk = text[offset:offset + max_chars]
    if offset and not chunk:
        return {"ok": True, "path": path, "text": "", "offset": offset,
                "truncated": False,
                "note": f"offset {offset} is past the end of the file"}
    more_remains = len(text) > offset + max_chars
    result = {"ok": True, "path": path, "text": chunk, "offset": offset,
              "truncated": more_remains}
    if more_remains:
        result["next_offset"] = offset + max_chars
        result["note"] = (f"showing characters {offset}-{offset + len(chunk)}. More remains: "
                          f"call read_file again with offset={result['next_offset']}.")
    else:
        result["total_length"] = offset + len(chunk)
    return result


def list_folder(path: str = "~", max_entries: int = 200):
    path = _resolve(path)
    try:
        entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        return {"error": str(e)}
    out = [{"name": e.name, "type": "folder" if e.is_dir() else "file"} for e in entries[:max_entries]]
    return {"path": path, "entries": out, "truncated": len(entries) > max_entries}


def create_folder(path: str):
    path = _resolve(path)
    try:
        os.makedirs(path, exist_ok=True)
        return {"ok": True, "path": path}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def move_file(source: str, destination: str):
    """Confirm-gated by the framework (effect DESTROY). Never overwrites an existing file."""
    src, dst = _resolve(source), _resolve(destination)
    if not os.path.exists(src):
        return {"ok": False, "error": f"source not found: {src}"}
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    if os.path.exists(dst):
        return {"ok": False, "error": f"destination already exists: {dst}"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        log.info(f"Moved {src} -> {dst}")
        return {"ok": True, "moved_to": dst}
    except OSError as e:
        return {"ok": False, "error": str(e)}
