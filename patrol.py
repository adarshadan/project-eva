"""Eva -- looking around the PC for inefficiencies when she is bored, and OFFERING to fix them.

2026-10-17: "starts to look around the pc to manage inefficiencies". When nobody has needed her
for a while (boredom 2+, life.py) and the person is around but not in the middle of typing, she
may do one patrol: a few cheap, local checks -- no model, no screenshots, nothing leaves the PC:

    disk        a fixed drive over 90% full
    downloads   Downloads holding many old files (older than 30 days) or a lot of space
    memory      memory over 88% used, with the program using the most named
    cpu         one program using a whole core or more, two looks in a row
    recycle     the Recycle Bin holding more than 2 GB
    windows     more than 15 windows open
    battery     below 20% and not charging (a heads-up only)

At most ONE finding is offered per patrol, as a question ("... want me to ...?"). Yes runs a
plain-language task through the normal agent -- every destructive step still asks at the
permission gate, and the tasks themselves say "move or delete nothing until I agree". No snoozes
the finding for a week; "don't ask again" stops that kind for good; an unanswered offer is not
repeated for a day. Never while she is busy, an offer is pending, the screen is full-screen, or
the person typed in the last 15 seconds. State in ~/.eva/patrol.json.
"""
import json
import os
import threading
import time

from logger import get_logger

log = get_logger(__name__)

PATH = os.path.join(os.path.expanduser("~"), ".eva", "patrol.json")
PATROL_GAP_SECONDS = 30 * 60
REOFFER_SECONDS = 24 * 3600
NO_SNOOZE_SECONDS = 7 * 24 * 3600
OLD_DAYS = 30
GB = 1024 ** 3


def _gb(n):
    return f"{n / GB:.1f} GB"


# ---- the checks (each returns a finding dict or None; probes are injectable for the checks) -----
def check_disk(probes):
    for d in probes.disks():                       # [(mount, total, free)]
        mount, total, free = d
        if total > 20 * GB and free / total < 0.10:
            used = 100 - int(100 * free / total)
            return {"kind": f"disk:{mount}", "label": f"drive {mount}",
                    "text": f"Your {mount} drive is {used}% full ({_gb(free)} free). Want me to find what's taking "
                            f"the space?",
                    "task": f"Find the largest folders and files on drive {mount} and list them with their sizes, "
                            f"biggest first. Do not delete or move anything."}
    return None


def check_downloads(probes):
    old, size = probes.old_downloads(OLD_DAYS)     # (count, bytes) of files older than OLD_DAYS
    if old >= 150 or size >= 3 * GB:
        return {"kind": "downloads", "label": "your Downloads folder",
                "text": f"Your Downloads folder has {old} files older than a month ({_gb(size)}). Want me to "
                        f"sort out what's there?",
                "task": "Look through my Downloads folder: group the files older than 30 days by type with "
                        "their sizes, point out duplicates and installers I no longer need, and propose a tidy-up. "
                        "Move or delete nothing until I agree."}
    return None


def check_memory(probes):
    pct, top = probes.memory()                     # (percent used, (name, bytes) or None)
    if pct >= 88 and top:
        return {"kind": "memory", "label": "memory",
                "text": f"Your PC is using {int(pct)}% of its memory; {top[0]} alone is using {_gb(top[1])}. "
                        f"Want me to show you what you could close?",
                "task": "Show me which programs are using the most memory right now and suggest which I could "
                        "close. Close nothing without asking me."}
    return None


def check_cpu(probes, state):
    hog = probes.cpu_hog()                         # (name, percent) or None; percent of one core
    prev, state["cpu_prev"] = state.get("cpu_prev"), hog[0] if hog else None
    if hog and prev == hog[0]:                     # the same program, two patrols in a row
        return {"kind": f"cpu:{hog[0].lower()}", "label": hog[0],
                "text": f"{hog[0]} keeps using a lot of processor time ({int(hog[1])}%). Want me to look into it?",
                "task": f"{hog[0]} has been using a lot of CPU. Find out what it is and whether it is needed "
                        f"right now, and tell me. Do not end it without asking."}
    return None


def check_recycle(probes):
    size = probes.recycle_bin_bytes()
    if size is not None and size >= 2 * GB:
        return {"kind": "recycle", "label": "the Recycle Bin",
                "text": f"Your Recycle Bin is holding {_gb(size)}. Want me to empty it?",
                "task": "Empty the Recycle Bin."}       # destructive: the permission gate asks again
    return None


def check_windows(probes):
    n = probes.window_count()
    if n > 15:
        return {"kind": "windows", "label": "open windows",
                "text": f"You have {n} windows open. Want me to list the ones you haven't used in a while?",
                "task": "List my open windows, mark the ones I haven't used recently, and suggest which I could "
                        "close. Close only the ones I pick."}
    return None


def check_battery(probes):
    b = probes.battery()                           # (percent, plugged) or None
    if b and b[0] < 20 and not b[1]:
        return {"kind": "battery", "label": "battery", "info": True,
                "text": f"Heads-up: your battery is at {int(b[0])}% and not charging."}
    return None


ORDER = ("disk", "memory", "battery", "cpu", "recycle", "downloads", "windows")


class Patrol:
    def __init__(self, probes, path=PATH, clock=time.time):
        self.probes, self.path, self.clock = probes, path, clock
        self.state = self._load()
        self.last_patrol = 0.0
        self._lock = threading.Lock()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning(f"Patrol state not saved: {e}")

    def _quiet(self, kind):
        k = self.state.get("kinds", {}).get(kind, {})
        if k.get("never"):
            return True
        return self.clock() < float(k.get("until", 0))

    def due(self):
        return self.clock() - self.last_patrol >= PATROL_GAP_SECONDS

    def run(self):
        """One patrol: the first finding not snoozed, or None."""
        with self._lock:
            self.last_patrol = self.clock()
            checks = {"disk": lambda: check_disk(self.probes), "memory": lambda: check_memory(self.probes),
                      "battery": lambda: check_battery(self.probes),
                      "cpu": lambda: check_cpu(self.probes, self.state),
                      "recycle": lambda: check_recycle(self.probes),
                      "downloads": lambda: check_downloads(self.probes),
                      "windows": lambda: check_windows(self.probes)}
            for name in ORDER:
                try:
                    f = checks[name]()
                except Exception as e:
                    log.info(f"Patrol check {name} skipped: {e}")
                    continue
                if f and not self._quiet(f["kind"]):
                    self._mark(f["kind"], until=self.clock() + REOFFER_SECONDS)
                    log.info(f"Patrol found: {f['kind']} -- {f['text']}")
                    return f
            self._save()
            return None

    def _mark(self, kind, until=None, never=None):
        kinds = self.state.setdefault("kinds", {})
        k = kinds.setdefault(kind, {})
        if until is not None:
            k["until"] = until
        if never is not None:
            k["never"] = never
        self._save()

    def feedback(self, kind, answer):
        """answer: 'accepted' | 'declined' | 'never' (predictive.answer_kind)."""
        if answer == "never":
            self._mark(kind, never=True)
        elif answer == "declined":
            self._mark(kind, until=self.clock() + NO_SNOOZE_SECONDS)


# ---- the real probes (Windows; psutil) -------------------------------------------------------
class SystemProbes:
    def disks(self):
        import psutil
        out = []
        for p in psutil.disk_partitions(all=False):
            if "cdrom" in (p.opts or "") or not p.fstype:
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            out.append((p.mountpoint.rstrip("\\") or p.mountpoint, u.total, u.free))
        return out

    def old_downloads(self, days):
        folder = os.path.join(os.path.expanduser("~"), "Downloads")
        cutoff, count, size, seen = time.time() - days * 86400, 0, 0, 0
        try:
            for entry in os.scandir(folder):
                seen += 1
                if seen > 5000:
                    break
                try:
                    st = entry.stat()
                except OSError:
                    continue
                if entry.is_file() and st.st_mtime < cutoff:
                    count += 1
                    size += st.st_size
        except OSError:
            pass
        return count, size

    def memory(self):
        import psutil
        vm = psutil.virtual_memory()
        top, best = None, 0
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                rss = p.info["memory_info"].rss
            except Exception:
                continue
            if rss > best:
                best, top = rss, (p.info["name"] or "a program").replace(".exe", "")
        return vm.percent, ((top, best) if top else None)

    def cpu_hog(self):
        import desktop_control as dc
        jobs = dc.list_background_jobs(top_n=3).get("jobs", [])
        jobs = [j for j in jobs if (j.get("name") or "").lower() not in ("system idle process", "idle")]
        if jobs and (jobs[0].get("cpu") or 0) >= 90:
            return (jobs[0]["name"].replace(".exe", ""), jobs[0]["cpu"])
        return None

    def recycle_bin_bytes(self):
        try:
            import ctypes
            from ctypes import wintypes

            class SHQUERYRBINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong),
                            ("i64NumItems", ctypes.c_longlong)]
            info = SHQUERYRBINFO()
            info.cbSize = ctypes.sizeof(info)
            if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
                return int(info.i64Size)
        except Exception:
            pass
        return None

    def window_count(self):
        import desktop_control as dc
        return len(dc.list_windows().get("windows", []))

    def battery(self):
        import psutil
        b = psutil.sensors_battery()
        return (b.percent, b.power_plugged) if b else None


_PATROL = {"p": None}


def patrol():
    if _PATROL["p"] is None:
        _PATROL["p"] = Patrol(SystemProbes())
    return _PATROL["p"]
