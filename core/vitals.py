"""Eva -- her own health over long sessions (hours or days).

Every SAMPLE_SECONDS (5 min) one line goes to eva.log with what the process
holds: memory (RSS), threads, Windows handles, Python objects tracked by the
garbage collector. A leak shows up as steady growth, so each sample is
compared with the last hour (straight-line fit over the samples):

  memory   growing more than 60 MB an hour, and 200 MB above where it started
  threads  more than 150, or growing more than 20 an hour
  handles  growing more than 3,000 an hour

When one of those holds she first lets go of what she can (a full garbage
collection on the GUI thread, the vision answer cache), then logs a
WARNING naming the trend once an hour, and the tray Status shows it.
psutil is in requirements.txt; without it only the Python numbers are kept.
"""
import collections
import gc
import os
import threading
import time

from logger import get_logger

log = get_logger(__name__)

SAMPLE_SECONDS = 300
WINDOW = 12                  # samples in "the last hour"
MB = 1024 * 1024
FULL_GC_REQUESTED = threading.Event()
LIMITS = {"rss_mb_per_h": 60, "rss_mb_over_start": 200, "threads_max": 150, "threads_per_h": 20,
          "handles_per_h": 3000}


def _proc():
    try:
        import psutil
        return psutil.Process(os.getpid())
    except Exception:
        return None


def sample(proc=None):
    proc = proc if proc is not None else _proc()
    s = {"t": time.time(), "threads": threading.active_count(), "gc": len(gc.get_objects())}
    if proc is not None:
        try:
            s["rss_mb"] = round(proc.memory_info().rss / MB, 1)
            s["threads"] = proc.num_threads()
            if hasattr(proc, "num_handles"):
                s["handles"] = proc.num_handles()
        except Exception:
            pass
    return s


def _slope_per_hour(rows, key):
    pts = [(r["t"], r[key]) for r in rows if key in r]
    if len(pts) < 4:
        return None
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mv = sum(v for _, v in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    if den <= 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in pts) / den * 3600.0


def judge(history, limits=None):
    """Plain-words findings about the trend, [] when healthy."""
    lim = dict(LIMITS, **(limits or {}))
    if not history:
        return []
    last, first = history[-1], history[0]
    recent = list(history)[-WINDOW:]
    out = []
    rss_rate = _slope_per_hour(recent, "rss_mb")
    if rss_rate is not None and rss_rate > lim["rss_mb_per_h"] and \
            last.get("rss_mb", 0) - first.get("rss_mb", 0) > lim["rss_mb_over_start"]:
        out.append(f"memory is growing {rss_rate:.0f} MB an hour (now {last['rss_mb']:.0f} MB, "
                   f"started at {first['rss_mb']:.0f} MB)")
    th_rate = _slope_per_hour(recent, "threads")
    if last.get("threads", 0) > lim["threads_max"]:
        out.append(f"{last['threads']} threads are running")
    elif th_rate is not None and th_rate > lim["threads_per_h"]:
        out.append(f"threads are growing {th_rate:.0f} an hour (now {last['threads']})")
    h_rate = _slope_per_hour(recent, "handles")
    if h_rate is not None and h_rate > lim["handles_per_h"]:
        out.append(f"Windows handles are growing {h_rate:.0f} an hour (now {last.get('handles')})")
    return out


def release_caches():
    """What she can safely let go of. Each part fails soft."""
    freed = []
    try:
        import vision
        vision.clear_cache()
        freed.append("vision cache")
    except Exception:
        pass
    # Never gc.collect() here: collecting on this thread can finalise Qt objects
    # off the GUI thread (HANDOFF 36). The GUI's once-a-second collector does a
    # full pass when asked.
    FULL_GC_REQUESTED.set()
    freed.append("a full garbage collection (on the GUI thread)")
    return freed


class Vitals:
    def __init__(self, interval=SAMPLE_SECONDS, keep=288, sampler=sample, clock=time.monotonic):
        self.interval = interval
        self.history = collections.deque(maxlen=keep)       # 24 h at 5 min
        self.findings = []
        self._sampler = sampler
        self._clock = clock
        self._last_warn = -1e9
        self._stop = threading.Event()
        self._proc = _proc()

    def tick(self):
        s = self._sampler(self._proc) if self._sampler is sample else self._sampler()
        self.history.append(s)
        log.info("Vitals: " + ", ".join(f"{k}={v}" for k, v in s.items() if k != "t"))
        found = judge(self.history)
        if found and not self.findings:
            freed = release_caches()                        # first let go of what she can
            log.info(f"Vitals: released {', '.join(freed)}; re-measured at the next sample")
        self.findings = found
        if found and self._clock() - self._last_warn > 3600:
            self._last_warn = self._clock()
            log.warning("Long-session health: " + "; ".join(found) +
                        " -- send eva.log if Eva becomes slow (lines starting 'Vitals:').")
        return s

    def summary(self):
        if not self.history:
            return "not measured yet"
        s = self.history[-1]
        parts = [f"{s['rss_mb']:.0f} MB" if "rss_mb" in s else None, f"{s['threads']} threads",
                 f"{s['handles']} handles" if "handles" in s else None]
        text = ", ".join(p for p in parts if p)
        return text + ("  <-- " + "; ".join(self.findings) if self.findings else "")

    def start(self):
        def run():
            while not self._stop.wait(self.interval if self.history else 30):
                try:
                    self.tick()
                except Exception:
                    log.exception("Vitals sample failed")
        threading.Thread(target=run, name="eva-vitals", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()


VITALS = {"v": None}
