"""Eva -- predictive help: offering what you usually do, at the time you usually do it.

What she notices (only while config.PREDICTIVE_HELP is on, only on this PC,
in ~/.eva/routines.json, the last 28 days):
  * the first time each day a program comes to the front (Outlook, Teams,
    VS Code, Spotify...) -- the program only, never window contents;
  * websites she opened for you (navigate_browser), by site name only.

A routine is something first opened on at least PREDICTIVE_MIN_SEEN (3)
different days at about the same time (within PREDICTIVE_WINDOW_MINUTES,
45), on weekdays only if it was only ever seen on weekdays. When one is due
and not yet open today, she OFFERS it once: "You usually open Outlook around
now -- shall I open it?" Yes runs it (through the normal permission gate);
no counts against it (two noes mute it for two weeks); "don't ask again"
stops it for good. At most one offer every 20 minutes, never while she is
busy, a game or repeat is running, the screen is full-screen, or the person
is away.

NEVER anything to do with money: banking, payments, wallets, UPI, trading,
crypto, accounting or tax programs and sites are not recorded at all (the
finance guard's payment patterns plus FINANCIAL_WORDS), and the only action
she can offer is opening a program or a site -- never a click or a purchase.
"""
import json
import os
import re
import threading
import time
from urllib.parse import urlparse

from logger import get_logger

log = get_logger(__name__)

PATH = os.path.join(os.path.expanduser("~"), ".eva", "routines.json")
KEEP_DAYS = 28
OFFER_GAP_SECONDS = 20 * 60
MUTE_DAYS = 14

FINANCIAL_WORDS = re.compile(
    r"(bank|banking|netbank|\bpay\b|payment|paytm|phonepe|gpay|google ?pay|bhim|\bupi\b|wallet|paypal|razorpay|"
    r"stripe|trading|trader|broker|stock|shares|demat|zerodha|kite\.|groww|upstox|angel ?one|icici|hdfc|sbi\b|"
    r"axis ?bank|kotak|crypto|bitcoin|binance|coinbase|wazir|invest|mutual ?fund|\bsip\b|loan|credit ?card|"
    r"tally|quickbooks|accounting|invoice|\btax\b|income ?tax|gst|insurance|amazon\.(in|com)/(gp/buy|checkout)|"
    r"checkout|\bcart\b|billing)", re.I)

# Shell and system windows that come to the front on their own.
IGNORED_EXES = {"explorer", "searchhost", "searchapp", "shellexperiencehost", "startmenuexperiencehost",
                "applicationframehost", "textinputhost", "lockapp", "logonui", "systemsettings", "taskmgr",
                "python", "pythonw", "py", "rundll32", "dllhost", "consent", "sihost", "ctfmon", "dwm",
                "openwith", "pickerhost", "securityhealthsystray", "werfault", "msiexec", "setup", "installer",
                "credentialuibroker", "useroobebroker", "screenclippinghost", "snippingtool"}
BROWSERS = {"chrome", "msedge", "brave", "opera", "firefox", "vivaldi", "arc"}


def _config(name, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def is_financial(*texts) -> bool:
    joined = " ".join(str(t or "") for t in texts)
    if FINANCIAL_WORDS.search(joined):
        return True
    try:
        import finance_guard
        return any(finance_guard.is_payment_context(url=str(t or ""), title=str(t or "")) for t in texts)
    except Exception:
        return False


def _stem(path):
    """'C:\\Office\\OUTLOOK.EXE' -> 'OUTLOOK', on any OS (paths here are Windows paths)."""
    return os.path.splitext(re.split(r"[\\/]", str(path or ""))[-1])[0]


def _label_from(exe, title):
    stem = _stem(exe)
    tail = str(title or "").split(" - ")[-1].strip()
    if 2 <= len(tail) <= 30 and not re.search(r"[@\\/:]", tail):
        return tail
    return stem[:1].upper() + stem[1:] if stem else "that program"


class Routines:
    def __init__(self, path=None, clock=time.time, local=time.localtime):
        self.path = path or PATH
        self._clock, self._local = clock, local
        self._lock = threading.Lock()
        self.data = {"events": [], "items": {}, "feedback": {}, "offered": {}}
        self.last_offer_at = 0.0
        self._load()

    # ---- storage ----------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            for k in self.data:
                if isinstance(d.get(k), type(self.data[k])):
                    self.data[k] = d[k]
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Could not save routines")

    def _today(self, t=None):
        lt = self._local(t if t is not None else self._clock())
        return time.strftime("%Y-%m-%d", lt), lt.tm_hour * 60 + lt.tm_min, lt.tm_wday

    # ---- noticing ---------------------------------------------------------------------
    def observe(self, key, label, action, t=None):
        """First time today `key` was opened. Returns True if recorded."""
        if not _config("PREDICTIVE_HELP", False):
            return False
        if is_financial(key, label, json.dumps(action)):
            return False
        day, minute, wd = self._today(t)
        with self._lock:
            if any(e["item"] == key and e["day"] == day for e in self.data["events"]):
                return False
            self.data["events"].append({"item": key, "day": day, "min": minute, "wd": wd})
            self.data["items"][key] = {"label": label, "action": action}
            cutoff = time.strftime("%Y-%m-%d", self._local((t or self._clock()) - KEEP_DAYS * 86400))
            self.data["events"] = [e for e in self.data["events"] if e["day"] >= cutoff]
            self._save()
        return True

    def observe_foreground(self, exe_path, title, t=None):
        exe = _stem(exe_path).lower()
        if not exe or exe in IGNORED_EXES:
            return False
        if is_financial(exe_path, title):
            return False
        return self.observe(f"app:{exe}", _label_from(exe_path, title),
                            {"tool": "launch_app", "args": {"name_or_path": str(exe_path)}}, t)

    def observe_tool(self, name, args, result, browser_exe=None, t=None):
        """Things she opened for the person."""
        if not (isinstance(result, dict) and result.get("ok")):
            return False
        args = args or {}
        if name == "navigate_browser":
            url = str(args.get("url") or "")
            host = urlparse(url if "://" in url else "https://" + url).hostname or ""
            host = host[4:] if host.startswith("www.") else host
            if not host or is_financial(url, host):
                return False
            exe = browser_exe or "msedge"
            return self.observe(f"site:{host}", host,
                                {"tool": "launch_app", "args": {"name_or_path": exe, "argument": f"https://{host}"}}, t)
        return False

    # ---- what she has learned ---------------------------------------------------------
    def routines(self, t=None):
        min_seen = int(_config("PREDICTIVE_MIN_SEEN", 3))
        window = int(_config("PREDICTIVE_WINDOW_MINUTES", 45))
        by = {}
        for e in self.data["events"]:
            by.setdefault(e["item"], []).append(e)
        out = []
        for key, evs in by.items():
            if len({e["day"] for e in evs}) < min_seen:
                continue
            mins = sorted(e["min"] for e in evs)
            centre = mins[len(mins) // 2]
            near = [e for e in evs if abs(e["min"] - centre) <= window]
            if len({e["day"] for e in near}) < min_seen:
                continue
            item = self.data["items"].get(key, {})
            out.append({"key": key, "label": item.get("label", key), "action": item.get("action"),
                        "minute": centre, "days": len({e["day"] for e in near}),
                        "weekdays_only": all(e["wd"] < 5 for e in near)})
        return sorted(out, key=lambda r: r["minute"])

    def due(self, t=None):
        """The routine to offer now, or None."""
        if not _config("PREDICTIVE_HELP", False):
            return None
        now = t if t is not None else self._clock()
        if now - self.last_offer_at < OFFER_GAP_SECONDS:
            return None
        day, minute, wd = self._today(now)
        window = int(_config("PREDICTIVE_WINDOW_MINUTES", 45))
        opened_today = {e["item"] for e in self.data["events"] if e["day"] == day}
        for r in self.routines(now):
            fb = self.data["feedback"].get(r["key"], {})
            if fb.get("never") or fb.get("muted_until", 0) > now:
                continue
            if r["weekdays_only"] and wd >= 5:
                continue
            if not (r["minute"] - 10 <= minute <= r["minute"] + window):
                continue
            if r["key"] in opened_today or self.data["offered"].get(r["key"]) == day:
                continue
            if not r.get("action") or r["action"].get("tool") != "launch_app" or is_financial(
                    r["key"], r["label"], json.dumps(r["action"])):
                continue
            return r
        return None

    def mark_offered(self, key, t=None):
        now = t if t is not None else self._clock()
        with self._lock:
            self.data["offered"][key] = self._today(now)[0]
            self.last_offer_at = now
            self._save()

    @staticmethod
    def offer_text(r):
        hh, mm = divmod(r["minute"], 60)
        when = time.strftime("%I:%M %p", time.struct_time((2000, 1, 1, hh, mm, 0, 0, 1, -1))).lstrip("0")
        return f"You usually open {r['label']} around {when}. Shall I open it?"

    def feedback(self, key, answer, t=None):
        """answer: accepted | declined | never."""
        now = t if t is not None else self._clock()
        with self._lock:
            fb = self.data["feedback"].setdefault(key, {"declined": 0})
            if answer == "accepted":
                fb["declined"] = 0
            elif answer == "declined":
                fb["declined"] = fb.get("declined", 0) + 1
                if fb["declined"] >= 2:
                    fb["muted_until"] = now + MUTE_DAYS * 86400
                    fb["declined"] = 0
            elif answer == "never":
                fb["never"] = True
            self._save()

    def forget(self, what="all"):
        what = str(what or "all").strip().lower()
        with self._lock:
            if what in ("all", "everything", ""):
                n = len(self.data["items"])
                self.data = {"events": [], "items": {}, "feedback": {}, "offered": {}}
                self._save()
                return n
            keys = [k for k, v in self.data["items"].items()
                    if what in k.lower() or what in str(v.get("label", "")).lower()]
            for k in keys:
                self.data["items"].pop(k, None)
                self.data["feedback"][k] = {"never": True}          # and do not learn it again
            self.data["events"] = [e for e in self.data["events"] if e["item"] not in keys]
            self._save()
            return len(keys)


ROUTINES = {"r": None}


def routines():
    if ROUTINES["r"] is None:
        ROUTINES["r"] = Routines()
    return ROUTINES["r"]


# ---- answers to an offer ---------------------------------------------------------------
YES = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|please( do)?|go ahead|do it|open it|yes please|sounds good)"
                 r"[\s!.,]*(eva)?[\s!.,]*$", re.I)
NO = re.compile(r"^\s*(no|nope|nah|not now|no thanks|no thank you|later|skip( it)?|don'?t)[\s!.,]*$", re.I)
NEVER = re.compile(r"\b(never|don'?t (ever )?(ask|offer)( me)?( (this|that))? again|stop (asking|offering))\b", re.I)


def answer_kind(text):
    t = str(text or "")
    if NEVER.search(t):
        return "never"
    if YES.match(t):
        return "accepted"
    if NO.match(t):
        return "declined"
    return None


# ---- the watcher (wired in main.py) ----------------------------------------------------
class Watcher:
    """Looks at the window in front every OBSERVE_SECONDS and considers an
    offer every OFFER_CHECK_SECONDS. Every probe is passed in (main.py), so
    the checks can drive it without a desktop."""
    OBSERVE_SECONDS = 20
    OFFER_CHECK_SECONDS = 60
    ACTIVE_WITHIN = 120            # the person used mouse or keyboard this recently

    def __init__(self, store, *, foreground, exe_of, own, idle_seconds, busy, fullscreen, offer, clock=time.time):
        self.store = store
        self.p = {"foreground": foreground, "exe_of": exe_of, "own": own, "idle_seconds": idle_seconds,
                  "busy": busy, "fullscreen": fullscreen, "offer": offer}
        self._clock = clock
        self._stop = threading.Event()
        self._last_offer_check = 0.0

    def _probe(self, name, *a, default=None):
        try:
            return self.p[name](*a)
        except Exception:
            return default

    def observe_once(self):
        if not _config("PREDICTIVE_HELP", False):
            return False
        handle, title = self._probe("foreground", default=(None, "")) or (None, "")
        if not handle or self._probe("own", handle, default=True):
            return False
        exe = self._probe("exe_of", handle)
        return bool(exe) and self.store.observe_foreground(exe, title)

    def offer_once(self):
        if not _config("PREDICTIVE_HELP", False):
            return None
        if self._probe("busy", default=True):
            return None
        if (self._probe("idle_seconds", default=1e9) or 0) > self.ACTIVE_WITHIN:
            return None                       # away: an offer would be said to an empty room
        handle, _ = self._probe("foreground", default=(None, "")) or (None, "")
        if handle and self._probe("fullscreen", handle, default=False):
            return None                       # a film, a game, a presentation
        r = self.store.due()
        if not r:
            return None
        self.store.mark_offered(r["key"])
        self._probe("offer", r, Routines.offer_text(r))
        return r

    def tick(self):
        self.observe_once()
        now = self._clock()
        if now - self._last_offer_check >= self.OFFER_CHECK_SECONDS:
            self._last_offer_check = now
            self.offer_once()

    def start(self):
        def run():
            while not self._stop.wait(self.OBSERVE_SECONDS):
                try:
                    self.tick()
                except Exception:
                    log.exception("Routine watcher tick failed")
        threading.Thread(target=run, name="eva-routines", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
