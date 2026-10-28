"""
Eva -- reminders and scheduled instructions, one-off or repeating.

  "in 20 minutes, summarise my newest download"        one-off
  "every weekday at 9:00, give me my briefing"          repeating

Persisted to scheduled.json so pending items survive a restart; anything that
fell due while Eva was closed fires once shortly after the next launch.

Repeating items (after open-dots' schedules): the next run is always worked
out from NOW, so a PC that was off for three days runs a missed daily task
once, not three times. A task that fails three times in a row is paused, and
one that fails because the person has to act (a rejected API key, a missing
login) is paused at once -- instead of failing every morning forever. The
person is told, and resume_scheduled starts it again.
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta

from logger import get_logger

log = get_logger(__name__)

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduled.json")
_lock = threading.Lock()
# Woken whenever an item is added, changed or cancelled, so _loop sleeps
# exactly until the next due time instead of polling.
_wake = threading.Condition(_lock)
_items = {}
_on_due = None
_on_paused = None

REPEATS = ("hourly", "daily", "weekdays", "weekly")
MAX_CONSECUTIVE_FAILURES = 3
_NEEDS_PERSON = ("401", "403", "api key", "rejected", "not allowed", "login", "sign in", "credentials")


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _save():  # caller must hold _lock
    try:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(_items.values()), f, indent=2)
        os.replace(tmp, _FILE)
    except OSError:
        log.exception("Could not save scheduled items")


def _load():
    if not os.path.exists(_FILE):
        return
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            for item in json.load(f):
                _items[item["id"]] = item
        log.info(f"Loaded {len(_items)} scheduled item(s).")
    except Exception:
        log.exception("Could not load scheduled items")


def _hhmm(at_time):
    try:
        hh, mm = (int(p) for p in str(at_time).strip().split(":")[:2])
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
        return hh, mm
    except ValueError:
        raise ValueError("at_time must be 24-hour HH:MM, e.g. '18:30'")


def next_occurrence(repeat: str, at_time, after: datetime, weekday=None) -> datetime:
    """The first time strictly after `after` that a repeating item runs."""
    if repeat == "hourly":
        minute = _hhmm(at_time)[1] if at_time else 0
        t = after.replace(minute=minute, second=0, microsecond=0)
        return t if t > after else t + timedelta(hours=1)
    hh, mm = _hhmm(at_time)
    t = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if t <= after:
        t += timedelta(days=1)
    if repeat == "weekdays":
        while t.weekday() >= 5:
            t += timedelta(days=1)
    elif repeat == "weekly":
        target = after.weekday() if weekday is None else int(weekday)
        while t.weekday() != target:
            t += timedelta(days=1)
    return t


def _due_ts(minutes_from_now, at_time, repeat=None, weekday=None) -> float:
    now = datetime.now()
    if repeat:
        if repeat not in REPEATS:
            raise ValueError(f"repeat must be one of {', '.join(REPEATS)}")
        if repeat != "hourly" and not at_time:
            raise ValueError("a repeating schedule needs at_time as 24-hour HH:MM")
        return next_occurrence(repeat, at_time, now, weekday).timestamp()
    if at_time:
        hh, mm = _hhmm(at_time)
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)  # "at 9:00" said at 10:00 means tomorrow
        return target.timestamp()
    if minutes_from_now is not None and float(minutes_from_now) > 0:
        return time.time() + float(minutes_from_now) * 60
    raise ValueError("give minutes_from_now (> 0) or at_time as 24-hour HH:MM")


def _add(kind: str, text: str, minutes_from_now, at_time, repeat=None):
    repeat = (str(repeat).strip().lower() or None) if repeat else None
    try:
        due = _due_ts(minutes_from_now, at_time, repeat)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    item = {"id": uuid.uuid4().hex[:8], "kind": kind, "text": text, "due_ts": due}
    if repeat:
        item.update(repeat=repeat, at_time=at_time, weekday=datetime.fromtimestamp(due).weekday(),
                    failures=0, paused=None)
    with _wake:
        _items[item["id"]] = item
        _save()
        _wake.notify()          # the new item may be sooner than the current sleep
    log.info(f"Scheduled {kind} {item['id']} ({repeat or 'once'}) for {_fmt(due)}: {text!r}")
    out = {"ok": True, "schedule_id": item["id"], "next": _fmt(due)}
    if repeat:
        out["repeats"] = repeat
    return out


def schedule_reminder(message: str, minutes_from_now=None, at_time=None, repeat=None):
    return _add("reminder", message, minutes_from_now, at_time, repeat)


def schedule_task(instruction: str, minutes_from_now=None, at_time=None, repeat=None):
    return _add("task", instruction, minutes_from_now, at_time, repeat)


def _row(i):
    row = {"schedule_id": i["id"], "kind": i["kind"], "text": i["text"], "next": _fmt(i["due_ts"])}
    if i.get("repeat"):
        row["repeats"] = i["repeat"]
    if i.get("paused"):
        row["paused"] = i["paused"]
    return row


def list_scheduled():
    with _lock:
        items = sorted(_items.values(), key=lambda i: i["due_ts"])
        rows = [_row(i) for i in items]
    return {"ok": True, "scheduled": rows, "items": rows}


def cancel_scheduled(schedule_id: str):
    with _wake:
        if _items.pop(schedule_id, None) is None:
            return {"ok": False, "error": f"no scheduled item with id {schedule_id}"}
        _save()
        _wake.notify()
    return {"ok": True}


def resume_scheduled(schedule_id: str):
    with _wake:
        item = _items.get(schedule_id)
        if item is None:
            return {"ok": False, "error": f"no scheduled item with id {schedule_id}"}
        if not item.get("paused"):
            return {"ok": True, "note": "it was not paused", "next": _fmt(item["due_ts"])}
        # From now: occurrences missed while paused do not all fire at once.
        item["due_ts"] = next_occurrence(item["repeat"], item.get("at_time"), datetime.now(),
                                         item.get("weekday")).timestamp()
        item["paused"], item["failures"] = None, 0
        _save()
        _wake.notify()
        return {"ok": True, "next": _fmt(item["due_ts"])}


def report_run(schedule_id: str, ok: bool, error: str = ""):
    """The runtime reports how a scheduled task went. Repeated or blocking
    failures pause a repeating item."""
    paused = None
    with _wake:
        item = _items.get(schedule_id)
        if item is None or not item.get("repeat"):
            return None
        if ok:
            if item.get("failures"):
                item["failures"] = 0
                _save()
            return None
        item["failures"] = int(item.get("failures") or 0) + 1
        needs_person = any(k in (error or "").lower() for k in _NEEDS_PERSON)
        if needs_person or item["failures"] >= MAX_CONSECUTIVE_FAILURES:
            reason = (f"it needs you first: {error[:160]}" if needs_person
                      else f"it failed {item['failures']} times in a row (last: {error[:120]})")
            item["paused"] = reason
            paused = dict(item)
        _save()
        _wake.notify()
    if paused:
        log.warning(f"Paused schedule {schedule_id}: {paused['paused']}")
        if _on_paused:
            try:
                _on_paused(paused)
            except Exception:
                log.exception("on_paused failed")
    return paused


def start(on_due, on_paused=None):
    """on_due(item) is called on this module's own background thread."""
    global _on_due, _on_paused
    _on_due, _on_paused = on_due, on_paused
    with _lock:
        _load()
    threading.Thread(target=_loop, daemon=True, name="eva-scheduler").start()


def _next_due_ts():
    return min((i["due_ts"] for i in _items.values() if not i.get("paused")), default=None)


def take_due(now: float):
    """Removes one-off items that are due and moves repeating ones on to their
    next occurrence (from now). Returns what is due. Caller holds no lock."""
    with _wake:
        due = [dict(i) for i in _items.values() if not i.get("paused") and i["due_ts"] <= now]
        for i in due:
            item = _items[i["id"]]
            if item.get("repeat"):
                item["due_ts"] = next_occurrence(item["repeat"], item.get("at_time"),
                                                 datetime.fromtimestamp(max(now, item["due_ts"])),
                                                 item.get("weekday")).timestamp()
            else:
                del _items[i["id"]]
        if due:
            _save()
    return due


def _loop():
    while True:
        with _wake:
            nxt = _next_due_ts()
            timeout = 60 if nxt is None else max(0.05, nxt - time.time())
            _wake.wait(timeout=timeout)
        for item in take_due(time.time()):
            try:
                _on_due(item)
            except Exception:
                log.exception(f"Scheduled item {item['id']} failed")
