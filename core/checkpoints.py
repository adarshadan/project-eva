"""Eva core -- undo for the files Eva changes (after Hermes' checkpoints).

Before write_file or move_file runs, the framework records what is about to
change: a copy of a file that is about to be overwritten or appended to, or
the two paths of a move, or the path of a file being created. The record is
kept only if the action succeeded. undo_last_change then puts things back,
and snapshots the current state first, so an undo can itself be undone.

This is infrastructure, not something the model must remember to do: it runs
for every write and move, from the registry entry (`checkpoint=`).
Stored in ~/.eva/checkpoints: journal.json plus the saved copies; the last 50
changes are kept, and copies older than 14 days are pruned.
"""
import json
import os
import shutil
import threading
import time
import uuid

from logger import get_logger

log = get_logger(__name__)

KEEP = 50
MAX_AGE_DAYS = 14
MAX_COPY_BYTES = 50 * 1024 * 1024
_lock = threading.Lock()


def root() -> str:
    return os.path.join(os.path.expanduser("~"), ".eva", "checkpoints")


def _journal_path():
    return os.path.join(root(), "journal.json")


def _load():
    try:
        with open(_journal_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(entries):
    os.makedirs(root(), exist_ok=True)
    tmp = _journal_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries[-KEEP:], f, indent=1)
    os.replace(tmp, _journal_path())


def _resolve(path):
    from system_tools import _resolve as resolve
    return resolve(path)


def _copy_aside(path):
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_COPY_BYTES:
        return None
    os.makedirs(os.path.join(root(), "files"), exist_ok=True)
    dest = os.path.join(root(), "files", f"{uuid.uuid4().hex[:10]}-{os.path.basename(path)}")
    shutil.copy2(path, dest)
    return dest


def prepare(tool: str, args: dict):
    """Called just before the action runs. Returns a pending record (or None)."""
    try:
        if tool == "write_file":
            path = _resolve(args.get("path", ""))
            mode = str(args.get("mode", "create"))
            if os.path.isfile(path) and mode in ("overwrite", "append"):
                backup = _copy_aside(path)
                if backup is None:
                    return None
                return {"kind": "restore", "path": path, "backup": backup, "what": f"{mode} of {path}"}
            if not os.path.exists(path):
                return {"kind": "created", "path": path, "what": f"creation of {path}"}
        elif tool == "move_file":
            src, dst = _resolve(args.get("source", "")), _resolve(args.get("destination", ""))
            return {"kind": "move", "source": src, "destination": dst, "what": f"move of {src}"}
    except Exception:
        log.exception("Could not prepare an undo point -- the action still runs")
    return None


def commit(pending, result):
    """Keeps the record if the action worked; otherwise discards its copy."""
    if not pending:
        return
    ok = isinstance(result, dict) and result.get("ok")
    if not ok:
        if pending.get("backup"):
            try:
                os.remove(pending["backup"])
            except OSError:
                pass
        return
    if pending["kind"] == "move" and isinstance(result, dict) and result.get("moved_to"):
        pending["destination"] = result["moved_to"]
    pending.update(id=uuid.uuid4().hex[:8], at=time.time())
    with _lock:
        entries = _load()
        entries.append(pending)
        _save(entries)
        _prune(entries)


def _prune(entries):
    keep = {e.get("backup") for e in entries[-KEEP:] if e.get("backup")}
    folder = os.path.join(root(), "files")
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    if not os.path.isdir(folder):
        return
    for n in os.listdir(folder):
        p = os.path.join(folder, n)
        try:
            if p not in keep or os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


def list_changes(limit: int = 10):
    with _lock:
        entries = _load()
    rows = [{"change_id": e["id"], "what": e["what"],
             "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(e["at"])), "undone": bool(e.get("undone"))}
            for e in reversed(entries)][:max(1, min(int(limit or 10), 50))]
    return {"ok": True, "changes": rows}


def undo_change(change_id: str = ""):
    """Reverts one change (the most recent one not yet undone, by default)."""
    with _lock:
        entries = _load()
        candidates = [e for e in entries if not e.get("undone") and (not change_id or e["id"] == change_id)]
        if not candidates:
            return {"ok": False, "error": "there is no change of mine to undo" if not change_id
                    else f"no change with id {change_id} (see list_changes)"}
        e = candidates[-1]
        try:
            result = _revert(e)
        except OSError as exc:
            return {"ok": False, "error": f"could not undo the {e['what']}: {exc}"}
        if result.get("ok"):
            e["undone"] = True
            _save(entries)
        return result


def _revert(e) -> dict:
    if e["kind"] == "restore":
        if not os.path.exists(e["backup"]):
            return {"ok": False, "error": "the saved copy is gone (it may have been pruned)"}
        current = _copy_aside(e["path"]) if os.path.exists(e["path"]) else None
        os.makedirs(os.path.dirname(e["path"]) or ".", exist_ok=True)
        shutil.copy2(e["backup"], e["path"])
        out = {"ok": True, "restored": e["path"], "undid": e["what"]}
        if current:
            out["previous_version_kept_at"] = current
        return out
    if e["kind"] == "created":
        if not os.path.exists(e["path"]):
            return {"ok": True, "undid": e["what"], "note": "the file was already gone"}
        aside = _copy_aside(e["path"])
        if aside is None:
            return {"ok": False, "error": "the file is too large to set aside safely; remove it yourself"}
        os.remove(e["path"])
        return {"ok": True, "undid": e["what"], "removed": e["path"], "copy_kept_at": aside}
    if e["kind"] == "move":
        if not os.path.exists(e["destination"]):
            return {"ok": False, "error": f"{e['destination']} is no longer there"}
        if os.path.exists(e["source"]):
            return {"ok": False, "error": f"something is now at {e['source']}; not overwriting it"}
        os.makedirs(os.path.dirname(e["source"]) or ".", exist_ok=True)
        shutil.move(e["destination"], e["source"])
        return {"ok": True, "undid": e["what"], "moved_back_to": e["source"]}
    return {"ok": False, "error": f"unknown change kind {e['kind']!r}"}
