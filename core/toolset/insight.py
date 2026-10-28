"""Looking back at her own work: replays of failed tasks (core/replay.py) and
her health over a long session (core/vitals.py)."""
import os
import sys
import webbrowser

from core import replay, vitals
from core.effects import Effect as E, Surface as S
from core.registry import INT, STR, register


def _open(path):
    if sys.platform == "win32":
        os.startfile(path)                       # the person's default browser
    else:
        webbrowser.open("file://" + path)


def failed_tasks(limit=5):
    rows = replay.failures()[:max(1, min(10, int(limit or 5)))]
    if not rows:
        return {"ok": True, "failed_tasks": [], "note": "no failed task has been kept"}
    return {"ok": True, "failed_tasks": [{"which": i + 1, **{k: r[k] for k in ("task", "when", "why", "steps",
                                                                                "failed_step")}}
                                         for i, r in enumerate(rows)]}


def show_failed_task(which=1):
    rows = replay.failures()
    i = max(1, int(which or 1))
    if not rows:
        return {"ok": False, "error": "no failed task has been kept"}
    if i > len(rows):
        return {"ok": False, "error": f"only {len(rows)} failed task(s) are kept"}
    r = rows[i - 1]
    page = os.path.join(r["folder"], "index.html")
    try:
        _open(page)
    except Exception as e:
        return {"ok": False, "error": f"could not open the replay page: {e}", "path": page}
    return {"ok": True, "opened": page, "task": r["task"], "why": r["why"], "failed_step": r["failed_step"]}


def eva_health():
    v = vitals.VITALS["v"]
    if v is None:
        return {"ok": False, "error": "the health monitor is not running"}
    return {"ok": True, "now": v.summary(), "findings": v.findings, "samples": len(v.history)}


register("failed_tasks", "Tasks of yours that went wrong recently and kept a step-by-step replay (newest first): "
         "what was asked, why it failed, which step. Use for 'what went wrong earlier?'.",
         {"limit": INT("How many, 1-10. Default 5.")}, surface=S.SELF, effect=E.OBSERVE, fn=failed_tasks,
         embodied=False)
register("show_failed_task", "Open the step-by-step replay of a failed task in the browser (each step, its result, "
         "and a picture of the screen after it). which=1 is the most recent.",
         {"which": INT("1 = most recent. Default 1.")}, surface=S.SELF, effect=E.OBSERVE, fn=show_failed_task,
         embodied=False)
register("eva_health", "Your own health in a long session: memory, threads, handles, and any steady growth "
         "(a leak) found over the last hour.", surface=S.SELF, effect=E.OBSERVE, fn=eva_health, embodied=False)


# ---- predictive help (predictive.py) -------------------------------------------------------
def my_routines():
    import config
    import predictive
    rs = predictive.routines().routines()
    out = {"ok": True, "on": bool(getattr(config, "PREDICTIVE_HELP", False)),
           "routines": [{"what": r["label"], "usually_at": f"{r['minute'] // 60:02d}:{r['minute'] % 60:02d}",
                         "seen_on_days": r["days"], "weekdays_only": r["weekdays_only"]} for r in rs]}
    if not out["on"]:
        out["note"] = ("predictive help is off: nothing is being noticed. change_setting PREDICTIVE_HELP true "
                       "switches it on (it only ever offers, and never anything to do with money)")
    elif not rs:
        out["note"] = "nothing seen often enough yet (a routine needs a few days at about the same time)"
    return out


def forget_routine(what="all"):
    import predictive
    n = predictive.routines().forget(what)
    return {"ok": True, "forgotten": n,
            "note": "and it will not be learned again" if n and str(what).lower() not in ("all", "") else ""}


register("my_routines", "What you have noticed the person usually opens and when (predictive help), and whether "
         "it is switched on. For 'what have you learned about my day?', 'what routines do you know?'.",
         surface=S.SELF, effect=E.OBSERVE, fn=my_routines, embodied=False)
register("forget_routine", "Forget a routine you noticed ('stop offering Spotify' -> what='spotify'), or all of "
         "them (what='all'). A forgotten one is not learned again.",
         {"what": STR("Words from its name, or 'all'.")}, surface=S.SELF, effect=E.MODIFY, fn=forget_routine,
         embodied=False)
