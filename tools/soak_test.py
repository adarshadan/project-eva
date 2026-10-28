"""Soak test: many turns through the real agent pipeline (scripted model, fake
desktop) while measuring what the process holds, to catch leaks before they
make a long session slow.

    python tools/soak_test.py              # 2,000 turns, a few minutes
    python tools/soak_test.py --turns 50000   # roughly the tool traffic of a long day

Each turn makes the calls a real task makes -- settings, status, a click that
fails every fifth turn (so a failed-task replay is written and old ones are
pruned) -- through permission gate, audit log, task trace and result
handling. Pass: memory settles (growth in the second half under 15 MB),
threads do not grow, and no more replays are kept than REPLAY_KEEP.
"""
import argparse
import gc
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import run_checks as rc  # noqa: E402  (stubs the Windows-only libraries)
from core import llm, replay, vitals  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=2000)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="eva-soak-")
    replay.ROOT = os.path.join(tmp, "replays")
    replay._grab = lambda path: open(path, "wb").write(b"\xff\xd8\xff\xd9")      # no screen here
    import audit as audit_mod
    log_ = audit_mod.AuditLog(os.path.join(tmp, "audit.jsonl"))
    n = {"turn": 0}

    def fake_click(x, y, **k):
        return {"ok": False, "error": "nothing there"} if n["turn"] % 5 == 0 else {"ok": True, "verified": True}
    eva = rc._eva(lambda req: "once", {"click_coords": fake_click}, audit_log=log_)
    eva.on_say = lambda line: None

    def script():
        steps = [("", [("list_settings", {}), ("activity_status", {})]),
                 ("", [("click_coords", {"x": 10, "y": 20})]),
                 ("Done.", [])]
        i = {"k": 0}

        def call(messages, system_prompt, tools, attempts=5):
            content, tcs = steps[i["k"] % len(steps)]
            i["k"] += 1
            return {"content": content, "reasoning_content": None, "reasoning_leak": False, "provider_error": False,
                    "tool_calls": [{"id": f"c{i['k']}_{j}", "name": nm, "args": ar} for j, (nm, ar) in enumerate(tcs)]}
        return call

    proc = vitals._proc()
    samples = []
    t0 = time.monotonic()
    for t in range(1, a.turns + 1):
        n["turn"] = t
        llm.call = script()
        eva.handle(f"task number {t}", mode="text")
        if len(eva.messages) > 60:
            del eva.messages[:-20]
        if t % max(1, a.turns // 20) == 0:
            gc.collect()
            samples.append(vitals.sample(proc))
            s = samples[-1]
            print(f"turn {t:6d}  rss {s.get('rss_mb', '?')} MB  threads {s['threads']}  gc {s['gc']}", flush=True)
    kept = len(replay.failures(replay.ROOT))
    half = samples[len(samples) // 2:]
    growth = (half[-1].get("rss_mb", 0) - half[0].get("rss_mb", 0)) if len(half) > 1 else 0
    threads = samples[-1]["threads"] - samples[0]["threads"]
    ok = growth < 15 and threads <= 2 and kept <= replay.KEEP
    print(f"\n{a.turns} turns in {time.monotonic() - t0:.0f} s; memory growth over the second half {growth:.1f} MB; "
          f"threads {threads:+d}; replays kept {kept} (max {replay.KEEP}); live threads: "
          f"{sorted({th.name.split('-')[0] + '-' + th.name.split('-')[1] if '-' in th.name else th.name for th in threading.enumerate()})}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
