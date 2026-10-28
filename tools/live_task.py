"""One real request through Eva's real agent, on the real screen -- for live tests (Wordle and the like).

    python tools/live_task.py "Open Wordle and play today's game"

Same agent, model, tools, operator and permission gate as Eva; no avatar (avatar-bound tools answer
"no avatar"). Approvals: on-screen work and opening pages are allowed; anything that deletes, spends money,
touches secrets or changes system settings is REFUSED, as if the person said no. Every approval asked is
printed. At the end: the reply, the steps with their results, the task card's verdict, time and calls.
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
os.environ.setdefault("EVA_LOG_PATH", os.path.join(HERE, "tools", "jobs", "live_task_eva.log"))

ALLOW = {"INTERACT", "EXTERNAL", "OBSERVE"}


def approver(req):
    ok = str(req.effect).upper() in ALLOW
    print(f"  [approval] {'ALLOW' if ok else 'REFUSE'} {req.tool} ({req.effect}): {req.title[:90]}", flush=True)
    return "once" if ok else "deny"


def main(argv):
    ask = " ".join(argv).strip() or "Open Wordle and play today's game."
    import audit as audit_mod
    import importlib
    import permissions
    importlib.import_module("core.toolset")
    from core import llm, registry
    from core.agent import Agent
    for name in registry.unbound():
        registry.bind(name, lambda **k: {"ok": True, "note": "no avatar in this test"})
    calls = {"n": 0}
    real = llm.call

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    llm.call = counting
    agent = Agent(permissions.PermissionGate(approver), audit=audit_mod.AuditLog(
        os.path.join(tempfile.gettempdir(), "eva-live-audit.jsonl")))
    card = {}
    agent.on_task_card = lambda d, final: card.update(d)
    agent.on_say = lambda line: print(f"  [said] {line}", flush=True)
    print(f"ASK: {ask}", flush=True)
    started = time.monotonic()
    try:
        reply = agent.handle(ask, mode="chat")
    except Exception as e:
        import traceback
        traceback.print_exc()
        reply = f"(crashed: {type(e).__name__}: {e})"
    secs = time.monotonic() - started
    print(f"\nREPLY: {reply}")
    print(f"\nSTEPS ({len(agent.turn.results)}):")
    for name, eff, r in agent.turn.results:
        brief = {k: r.get(k) for k in ("ok", "error", "verified", "steps", "facts", "why", "summary") if k in r} \
            if isinstance(r, dict) else r
        print(f"  {name} [{getattr(eff, 'name', eff)}] {json.dumps(brief, default=str)[:400]}")
    print(f"\nCARD: {card.get('status')} {card.get('why', '')} -- {card.get('summary', '')}")
    print(f"TIME {secs:.0f} s, {calls['n']} model calls")


if __name__ == "__main__":
    main(sys.argv[1:])
