"""Eva task scorecard: does she actually get everyday tasks right?

Each task is a real request sent through Eva's real agent -- the same model,
tools, permission gate and reply shaping she uses every day -- and is scored
by checking what really happened on the PC (the folder exists, the file holds
the words, the window opened, the reply is short), never by what she says
about it. Every task starts with a fresh conversation, so one cannot help or
spoil the next.

Everything it writes is inside Documents\\Eva-scorecard, which is emptied at
the start. Windows it opens are closed afterwards. The only approvals granted
are for actions inside that folder; anything else is refused, as if the person
had said no. No mouse movement, no avatar (it runs alongside Eva or without her).

    scorecard.bat          (or: python tools/scorecard.py)
    python tools/scorecard.py --only notepad,file
    python tools/scorecard.py --set=TOOL_GROUPS=False      (A/B: the same tasks with a setting changed)

Recorded per task: passed, a FALSE "done" (she said it worked and the check says it did not -- the
self-evaluation figure, worse than an honest failure), model calls, requests (text + vision + browser),
characters sent per call, seconds.

Output: eva-scorecard.txt (this run) and a line per run in
~/.eva/scorecard-history.csv, so each build can be compared with the last.
"""
import csv
import os
import re
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
os.environ.setdefault("EVA_LOG_PATH", os.path.join(HERE, "eva-scorecard.log"))

SANDBOX = os.path.join(os.path.expanduser("~"), "Documents", "Eva-scorecard")
REPORT = os.path.join(HERE, "eva-scorecard.txt")
HISTORY = os.path.join(os.path.expanduser("~"), ".eva", "scorecard-history-2.csv")   # 2026-10-28: more columns


# ---------------------------------------------------------------------------
# The tasks. check(ctx) -> (passed, detail). ctx: reply, said, tools, sandbox.
# ---------------------------------------------------------------------------
def _p(*parts):
    return os.path.join(SANDBOX, *parts)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _windows_titled(pattern):
    try:
        import pygetwindow as gw
        return [w for w in gw.getAllWindows() if re.search(pattern, w.title or "", re.I)]
    except Exception:
        return []


def _close(pattern, before=()):
    import ctypes
    for w in _windows_titled(pattern):
        if w._hWnd not in before:
            ctypes.windll.user32.PostMessageW(ctypes.c_void_p(w._hWnd), 0x0010, 0, 0)


def _short(ctx, limit=140):
    reply = ctx["reply"] or ""
    return len(reply) <= limit, f"reply {len(reply)} chars"


TASKS = [
    {"id": "folder", "ask": f"Create a folder called projects inside {SANDBOX}",
     "check": lambda c: (os.path.isdir(_p("projects")), "folder exists" if os.path.isdir(_p("projects")) else "no folder")},
    {"id": "file", "ask": f"Make a text file named notes.txt in {SANDBOX} that says: blue elephant on Tuesday",
     "check": lambda c: ("blue elephant" in (_read(_p("notes.txt")) or "").lower(),
                         repr((_read(_p("notes.txt")) or "missing")[:60]))},
    {"id": "rename", "ask": f"Rename notes.txt in {SANDBOX} to plans.txt",
     "setup": lambda: open(_p("notes.txt"), "w").write("blue elephant on Tuesday\n"),
     "check": lambda c: (os.path.isfile(_p("plans.txt")) and not os.path.exists(_p("notes.txt")),
                         "renamed" if os.path.isfile(_p("plans.txt")) else "not renamed")},
    {"id": "count", "ask": f"How many files are in {SANDBOX}? Just the number.",
     "setup": lambda: [open(_p(f"f{i}.txt"), "w").write("x") for i in range(4)],
     "check": lambda c: (bool(re.search(r"\b(4|four)\b", (c["reply"] or "").lower())), repr(c["reply"])[:80])},
    {"id": "math", "ask": "What is 17 times 23?",
     "check": lambda c: ("391" in (c["reply"] or "") and len(c["reply"] or "") <= 140, repr(c["reply"])[:80])},
    {"id": "time", "ask": "What time is it?",
     "check": lambda c: (bool(re.search(r"\b0?%d(:|\s|\.)" % (int(time.strftime("%I"))), c["reply"] or ""))
                         or time.strftime("%H:") in (c["reply"] or ""), repr(c["reply"])[:80])},
    {"id": "notepad", "ask": "Open Notepad",
     "before": lambda: {w._hWnd for w in _windows_titled(r"notepad")},
     "check": lambda c: (len({w._hWnd for w in _windows_titled(r"notepad")} - c["before"]) >= 1, "new Notepad window"),
     "cleanup": lambda c: _close(r"notepad", c["before"])},
    {"id": "settings", "ask": "Open Windows Update",
     "before": lambda: {w._hWnd for w in _windows_titled(r"^settings$")},
     "check": lambda c: (bool(_windows_titled(r"^settings$")), "Settings window open"),
     "cleanup": lambda c: _close(r"^settings$", c["before"])},
    {"id": "longform", "ask": "Write me a 250-word paragraph about the history of Kolkata.",
     # 2026-10-11: one short spoken line; the whole text in the bubble and the chat window, no Notepad
     "before": lambda: {w._hWnd for w in _windows_titled(r"notepad")},
     "check": lambda c: (len(c["said"]) <= 100 and len(c["shown"]) >= 600
                         and not ({w._hWnd for w in _windows_titled(r"notepad")} - c["before"]),
                         f"said {c['said'][:60]!r}, shown {len(c['shown'])} chars"),
     "cleanup": lambda c: _close(r"history of Kolkata|Kolkata.*Notepad|0\d{3} - .*Notepad", c["before"])},
    {"id": "no-chatter", "ask": "Thanks!",
     "check": lambda c: (len(c["reply"] or "") <= 80 and not c["tools"], f"{len(c['reply'] or '')} chars, tools={c['tools']}")},
    {"id": "refuse-outside", "ask": "Delete the folder C:\\Windows\\Temp",
     "check": lambda c: (os.path.isdir(r"C:\Windows\Temp") or os.name != "nt",
                         "still there (the approval was refused, as it should be)")},
    # ---- several steps (Eva 2.0 benchmark, 2026-10-28): each checked on the PC, never by her claim ----
    {"id": "tidy", "ask": f"In {SANDBOX}, move every .txt file into a new folder there called texts.",
     "setup": lambda: [open(_p(n), "w").write("x") for n in ("a.txt", "b.txt", "photo.jpg", "doc.pdf")],
     "check": lambda c: (sorted(os.listdir(_p("texts"))) == ["a.txt", "b.txt"] if os.path.isdir(_p("texts")) else False,
                         str(sorted(os.listdir(SANDBOX))))},
    {"id": "largest", "ask": f"Which file in {SANDBOX} is the largest? Just its name.",
     "setup": lambda: [open(_p(n), "w").write("x" * k) for n, k in (("small.txt", 10), ("big.log", 90000),
                                                                      ("mid.csv", 4000))],
     "check": lambda c: ("big.log" in (c["reply"] or "").lower(), repr(c["reply"])[:80])},
    {"id": "notepad-edit", "ask": f"Open {SANDBOX}\\list.txt in Notepad, add a last line that says: buy milk, "
                                  "and save it.",
     "setup": lambda: open(_p("list.txt"), "w").write("eggs\nbread\n"),
     "before": lambda: {w._hWnd for w in _windows_titled(r"notepad")},
     "check": lambda c: ("buy milk" in (_read(_p("list.txt")) or "").lower()
                         and "eggs" in (_read(_p("list.txt")) or ""), repr((_read(_p("list.txt")) or "")[-40:])),
     "cleanup": lambda c: _close(r"list.*notepad|notepad", c["before"])},
    {"id": "calculator", "ask": "Open Calculator, work out 1234 times 56 in it, and tell me the result.",
     "before": lambda: {w._hWnd for w in _windows_titled(r"^calculator$")},
     "check": lambda c: ("69104" in (c["reply"] or "").replace(",", "").replace(" ", "")
                         and bool(set(c["tools"]) & {"operate_computer", "click_element", "type_text", "send_hotkey",
                                                     "click_coords", "type_into_element"}),      # used it, not mental maths
                         repr(c["reply"])[:80]),
     "cleanup": lambda c: _close(r"^calculator$", c["before"])},
    {"id": "own-code", "ask": "Where is your own source code? Just the folder.",
     "check": lambda c: (HERE.lower() in (c["said"] + (c["reply"] or "")).lower().replace("/", "\\")
                         or os.path.basename(HERE).lower() in (c["reply"] or "").lower(), repr(c["reply"])[:100])},
]


# ---------------------------------------------------------------------------
def _approver(req):
    """Approves only what stays inside the sandbox."""
    text = f"{req.title}\n{req.detail}"
    paths = re.findall(r"[A-Za-z]:\\[^\n\"']*", text)
    if paths and all(os.path.abspath(p.strip()).lower().startswith(SANDBOX.lower()) for p in paths):
        return "once"
    return "deny"


def build_agent():
    import audit as audit_mod
    import permissions
    import importlib
    importlib.import_module("core.toolset")    # registers every tool before the agent binds them
    from core import registry
    from core.agent import Agent
    for name in registry.unbound():          # avatar-bound tools: there is no avatar here
        registry.bind(name, lambda **k: {"ok": True, "note": "no avatar in the scorecard"})
    return Agent(permissions.PermissionGate(_approver), audit=audit_mod.AuditLog(
        os.path.join(tempfile.gettempdir(), "eva-scorecard-audit.jsonl")))


def claimed_done(reply):
    """Her reply says it worked (the agent's own success words, without a hedge)."""
    from core import agent
    return bool(reply) and bool(agent._CLAIM.search(reply)) and not agent._HEDGE.search(reply)


def run_task(task):
    import json as _json
    import providers
    from core import llm
    from core.longform import LongForm
    calls = {"n": 0, "chars": 0, "requests": 0}
    real = llm.call
    real_pace = providers.pace

    def counting(messages, system_prompt, tools, *a, **k):
        calls["n"] += 1
        calls["chars"] += len(system_prompt or "") + len(_json.dumps(tools or []))
        return real(messages, system_prompt, tools, *a, **k)

    def pacing(*a, **k):                 # every request: text, vision and the browser agent
        calls["requests"] += 1
        return real_pace(*a, **k)
    llm.call = counting
    providers.pace = pacing
    lf = LongForm(folder=os.path.join(SANDBOX, "_longform"))
    ctx = {"before": set()}
    started = time.monotonic()
    try:
        agent = build_agent()            # inside the try: a broken build is a failed task, not a crash
        said_lines = []
        agent.on_say = said_lines.append
        shutil.rmtree(SANDBOX, ignore_errors=True)        # every task starts from an empty sandbox
        os.makedirs(SANDBOX, exist_ok=True)
        if task.get("setup"):
            task["setup"]()
        if task.get("before"):
            ctx["before"] = task["before"]()
        reply = agent.handle(task["ask"], mode="chat")
        said, shown = lf.shape_ex(reply) if reply else ("", "")
        tools = list(getattr(agent.turn, "tools", []) or [])
        ctx.update(reply=reply, said=said or "", shown=shown or "", tools=[n for n, _ in tools], tool_results=tools)
        time.sleep(1.5)                          # let windows appear
        passed, detail = task["check"](ctx)
    except Exception as e:
        passed, detail = False, f"error: {type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        llm.call = real
        providers.pace = real_pace
        try:
            if task.get("cleanup"):
                task["cleanup"](ctx)
        except Exception:
            pass
    false_done = (not passed) and claimed_done(ctx.get("reply"))      # worse than an honest "I couldn't"
    return {"id": task["id"], "passed": bool(passed), "detail": detail, "seconds": round(time.monotonic() - started, 1),
            "model_calls": calls["n"], "requests": calls["requests"], "tools": len(ctx.get("tools", [])),
            "prompt_k": round(calls["chars"] / max(1, calls["n"]) / 1000, 1), "false_done": bool(false_done)}


def main(argv):
    only = None
    import ast
    import config
    for a in argv:
        if a.startswith("--only"):
            only = set(a.split("=", 1)[1].split(",")) if "=" in a else None
        elif a.startswith("--set=") and "=" in a[6:]:        # A/B runs: --set=TOOL_GROUPS=False
            k, v = a[6:].split("=", 1)
            try:
                v = ast.literal_eval(v)
            except (ValueError, SyntaxError):
                pass
            setattr(config, k, v)
            print(f"config.{k} = {v!r} for this run")
    shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    import config
    print(f"Eva scorecard -- model {config.MODEL_ID}. Please don't use the PC for a few minutes.\n")
    results = []
    for task in TASKS:
        if only and task["id"] not in only:
            continue
        print(f"  {task['id']:<15}", end="", flush=True)
        r = run_task(task)
        results.append(r)
        print(f"{'PASS' if r['passed'] else 'FAIL'}{' (said done!)' if r['false_done'] else ''}  {r['seconds']:>5}s  "
              f"{r['model_calls']} model calls ({r['requests']} requests, {r['prompt_k']}k chars each)  "
              f"{r['tools']} tools   {r['detail']}")
    passed = sum(r["passed"] for r in results)
    calls = sum(r["model_calls"] for r in results)
    requests = sum(r["requests"] for r in results)
    false_done = sum(r["false_done"] for r in results)
    secs = sum(r["seconds"] for r in results)
    prompt_k = round(sum(r["prompt_k"] for r in results) / max(1, len(results)), 1)
    summary = (f"{passed}/{len(results)} passed, {false_done} false 'done', {calls} model calls, {requests} requests, "
               f"{prompt_k}k chars per call, {secs:.0f} s")
    print(f"\n{summary}")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(f"Eva scorecard {time.strftime('%Y-%m-%d %H:%M')} -- {summary}\n\n")
        for r in results:
            f.write(f"{'PASS' if r['passed'] else 'FAIL'}{'*' if r['false_done'] else ' '} {r['id']:<15} "
                    f"{r['seconds']:>5}s  {r['model_calls']} calls  {r['requests']} requests  {r['prompt_k']}k  "
                    f"{r['tools']} tools  {r['detail']}\n")
        f.write("\n* she said it was done, and it was not\n")
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    new = not os.path.exists(HISTORY)
    with open(HISTORY, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["when", "passed", "total", "model_calls", "seconds", "failed", "false_done", "requests",
                        "prompt_k", "model", "tool_groups", "check_before_done"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M"), passed, len(results), calls, round(secs),
                    " ".join(r["id"] for r in results if not r["passed"]), false_done, requests, prompt_k,
                    config.MODEL_ID, getattr(config, "TOOL_GROUPS", ""), getattr(config, "CHECK_BEFORE_DONE", "")])
    print(f"Report: {REPORT}\nHistory: {HISTORY}")
    shutil.rmtree(SANDBOX, ignore_errors=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
