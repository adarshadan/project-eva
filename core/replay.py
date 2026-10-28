"""Eva -- step-by-step replay of tasks that went wrong.

Every turn that acts on the screen or on files is traced: each tool call (what
she asked for, what came back, how long it took) and, after each on-screen
action, a small screenshot of how the screen looked afterwards. When the task
ends badly -- its last action failed, or her claim of success had no evidence
-- the trace is kept as ~/.eva/replays/<when>-<task>/index.html, a page that
shows the steps in order with their pictures; a task that went well is
thrown away. The newest REPLAY_KEEP (10) failures are kept.

Privacy: nothing leaves the PC; arguments are summarised exactly as in the
audit log (typed text by length, secrets redacted); no screenshot is taken
after a step that handles credentials or payments (effect SENSITIVE or
FINANCIAL); config.REPLAY_SCREENSHOTS = False keeps the steps without
pictures.
"""
import html
import itertools
import json
import os
import queue
import re
import shutil
import threading
import time

import audit
from logger import get_logger

log = get_logger(__name__)

KEEP = 10
SHOT_WIDTH = 960
SHOT_DELAY = 0.15            # let the screen settle after the action
ROOT = os.path.join(os.path.expanduser("~"), ".eva", "replays")


def _config(name, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


# ---- one worker, in order: screenshots, then the save that needs them ----------------
# A single thread and a FIFO queue: a task's "save" job is queued after its own
# screenshot jobs, so it never waits, and a fast run of tasks cannot pile up
# threads (tools/soak_test.py caught one save thread per task doing exactly that).
_jobs = queue.Queue()
_worker = {"t": None}
_worker_lock = threading.Lock()


def _grab(path):
    """Screen -> small JPEG. Module function so checks can replace it."""
    import mss
    from PIL import Image
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[0])
        img = Image.frombytes("RGB", raw.size, raw.rgb)
    if img.width > SHOT_WIDTH:
        img = img.resize((SHOT_WIDTH, int(img.height * SHOT_WIDTH / img.width)))
    img.save(path, "JPEG", quality=62)


def _work():
    while True:
        job = _jobs.get()
        try:
            kind = job[0]
            if kind == "shot":
                _, path, at, done = job
                wait = at + SHOT_DELAY - time.monotonic()      # settle after the action, not after the backlog
                if wait > 0:
                    time.sleep(wait)
                if time.monotonic() - at < 5.0:                 # a picture taken much later would mislead
                    try:
                        _grab(path)
                    except Exception as e:
                        log.debug(f"Replay screenshot skipped: {e}")
                done.set()
            elif kind == "call":
                _, fn = job
                fn()
        except Exception:
            log.exception("Replay job failed")


def _submit(job):
    with _worker_lock:
        if _worker["t"] is None or not _worker["t"].is_alive():
            _worker["t"] = threading.Thread(target=_work, name="eva-replay", daemon=True)
            _worker["t"].start()
    _jobs.put(job)


def _queue_shot(path):
    done = threading.Event()
    _submit(("shot", path, time.monotonic(), done))
    return done


# ---- one task -------------------------------------------------------------------------
_SEQ = itertools.count(1)
ACTING = {"INTERACT", "MODIFY", "DESTROY", "EXTERNAL"}
NO_PICTURE = {"SENSITIVE", "FINANCIAL"}


class Trace:
    def __init__(self, text, root=None):
        self.text = str(text or "").split("\n[speech-to-text")[0].strip()      # the request, not the hearing note
        self.started = time.time()
        self.steps = []
        self.root = root or ROOT
        self.dir = os.path.join(self.root, f"_current-{os.getpid()}-{next(_SEQ)}")     # one per task
        self._pending = []

    def step(self, tool, args, effect, result, ms):
        ok = bool(isinstance(result, dict) and result.get("ok", "error" not in result))
        n = len(self.steps) + 1
        row = {"n": n, "tool": tool, "args": audit.summarise_args(args), "effect": effect, "ok": ok, "ms": ms,
               "error": (str(result.get("error"))[:400] if isinstance(result, dict) and result.get("error") else ""),
               "verified": (result.get("verified") if isinstance(result, dict) else None),
               "note": (str(result.get("note"))[:300] if isinstance(result, dict) and result.get("note") else ""),
               "shot": ""}
        if effect in ACTING and effect not in NO_PICTURE and _config("REPLAY_SCREENSHOTS", True):
            try:
                os.makedirs(self.dir, exist_ok=True)
                name = f"step{n:02d}.jpg"
                self._pending.append(_queue_shot(os.path.join(self.dir, name)))
                row["shot"] = name
            except Exception:
                pass
        self.steps.append(row)
        return row

    @property
    def acted(self):
        return any(s["effect"] in ACTING for s in self.steps)

    def finish_later(self, reply, failed, why="", then=None):
        """finish() on the replay worker, after this task's own screenshots."""
        def job():
            kept = self.finish(reply, failed, why, _waited=True)
            if kept and then:
                then(kept)
        if not self._pending:
            if failed and self.acted:
                _submit(("call", job))
            return
        _submit(("call", job))

    def finish(self, reply, failed, why="", _waited=False):
        """Keep the trace as a replay page when the task failed; else discard.
        Returns the folder kept, or None."""
        if not _waited:
            for e in self._pending:
                e.wait(6.0)
        if not (failed and self.acted):
            shutil.rmtree(self.dir, ignore_errors=True)
            return None
        stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(self.started))
        slug = re.sub(r"[^a-z0-9]+", "-", self.text.lower())[:40].strip("-") or "task"
        final = os.path.join(self.root, f"{stamp}-{slug}")
        os.makedirs(self.root, exist_ok=True)
        if os.path.isdir(self.dir):
            shutil.rmtree(final, ignore_errors=True)
            os.replace(self.dir, final)
        else:
            os.makedirs(final, exist_ok=True)
        for s in self.steps:                      # a picture that never arrived is not linked
            if s["shot"] and not os.path.exists(os.path.join(final, s["shot"])):
                s["shot"] = ""
        info = {"task": audit.redact(self.text), "when": stamp, "reply": audit.redact(str(reply or "")),
                "why": why, "steps": self.steps}
        with open(os.path.join(final, "trace.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1, default=str)
        with open(os.path.join(final, "index.html"), "w", encoding="utf-8") as f:
            f.write(render(info))
        prune(self.root)
        log.info(f"Task did not go well -- step-by-step replay kept: {final}")
        return final


def prune(root=None, keep=None):
    root = root or ROOT
    keep = keep or int(_config("REPLAY_KEEP", KEEP))
    try:
        kept = sorted(d for d in os.listdir(root) if not d.startswith("_") and os.path.isdir(os.path.join(root, d)))
    except OSError:
        return
    for d in kept[:-keep]:
        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
    try:                                          # scratch left by a task that never finished (a crash)
        for d in os.listdir(root):
            p = os.path.join(root, d)
            if d.startswith("_current-") and time.time() - os.path.getmtime(p) > 3600:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def failures(root=None):
    """Kept replays, newest first: [{"folder", "task", "when", "why", "steps", "failed_step"}]."""
    root = root or ROOT
    out = []
    try:
        names = sorted((d for d in os.listdir(root) if not d.startswith("_")), reverse=True)
    except OSError:
        return out
    for d in names:
        try:
            with open(os.path.join(root, d, "trace.json"), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        bad = next((s for s in reversed(info.get("steps", [])) if not s.get("ok")), None)
        out.append({"folder": os.path.join(root, d), "task": info.get("task", ""), "when": info.get("when", ""),
                    "why": info.get("why", ""), "steps": len(info.get("steps", [])),
                    "failed_step": (f"step {bad['n']}: {bad['tool']} -- {bad.get('error') or 'failed'}"
                                    if bad else "")})
    return out


# ---- the page --------------------------------------------------------------------------
def _pretty_when(stamp):
    """'2026-10-11_144208' -> '2026-10-11 14:42:08'."""
    try:
        day, hms = str(stamp).split("_")
        return f"{day} {hms[0:2]}:{hms[2:4]}:{hms[4:6]}"
    except ValueError:
        return str(stamp)


def render(info):
    e = html.escape
    rows = []
    for s in info["steps"]:
        status = "ok" if s["ok"] else "failed"
        if s["ok"] and s.get("verified") is False:
            status = "not confirmed"
        detail = s.get("error") or s.get("note") or ""
        pic = (f'<a href="{e(s["shot"])}"><img src="{e(s["shot"])}" alt="screen after step {s["n"]}" '
               f'loading="lazy"></a>' if s.get("shot") else '<div class="nopic">no picture</div>')
        rows.append(f"""<li class="{ 'bad' if status != 'ok' else '' }">
<div class="head"><span class="n">{s['n']}</span><code>{e(s['tool'])}</code>
<span class="tag {status.replace(' ', '-')}">{status}</span><span class="ms">{s['ms']} ms</span></div>
<pre>{e(json.dumps(s['args'], ensure_ascii=False, default=str))}</pre>
{f'<p class="detail">{e(detail)}</p>' if detail else ''}{pic}</li>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Eva replay</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --ink:#1c1c1e; --muted:#5b5b60; --line:#d8d8dd; --bad:#b3261e; --ok:#1e7e34;
        --warn:#8a5a00; --accent:#0a6cdb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141416; --card:#1f1f22; --ink:#ececf0; --muted:#a6a6ad;
        --line:#38383d; --bad:#ff8a80; --ok:#7bd88f; --warn:#f2c46d; --accent:#6aa9ff; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 "Segoe UI",system-ui,sans-serif; }}
main {{ max-width:1000px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:20px; margin:0 0 4px; }} .sub {{ color:var(--muted); margin:0 0 18px; }}
.why {{ border-left:3px solid var(--bad); padding:8px 12px; background:var(--card); margin:0 0 18px; }}
ol {{ list-style:none; padding:0; margin:0; display:grid; gap:12px; }}
li {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }}
li.bad {{ border-color:var(--bad); }}
.head {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.n {{ font-weight:600; color:var(--muted); min-width:1.6em; }} code {{ font-weight:600; }}
.tag {{ font-size:12px; padding:1px 8px; border-radius:99px; border:1px solid currentColor; }}
.tag.ok {{ color:var(--ok); }} .tag.failed {{ color:var(--bad); }} .tag.not-confirmed {{ color:var(--warn); }}
.ms {{ color:var(--muted); font-size:12px; margin-left:auto; }}
pre {{ white-space:pre-wrap; word-break:break-word; font-size:12.5px; color:var(--muted); margin:8px 0; }}
.detail {{ margin:6px 0 8px; }} img {{ max-width:min(100%, 680px); border:1px solid var(--line); border-radius:6px; display:block; }}
.nopic {{ color:var(--muted); font-size:12px; }}
</style></head><body><main>
<h1>{e(info['task'])}</h1>
<p class="sub">{e(_pretty_when(info['when']))} &middot; {len(info['steps'])} steps &middot; screen pictures are taken just after each on-screen action</p>
{f'<p class="why">{e(info["why"])}</p>' if info.get('why') else ''}
{f'<p class="sub">Eva said: &ldquo;{e(info["reply"])}&rdquo;</p>' if info.get('reply') else ''}
<ol>{''.join(rows)}</ol></main></body></html>"""
