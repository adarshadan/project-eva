"""Safe update with health check and automatic rollback."""
import argparse
import json
import os
import subprocess
import sys
import time
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_run(cmd, cwd=None, **kwargs):
    if cmd == ["stop-eva"]:
        my_pid = os.getpid()
        try:
            import psutil
            for p in psutil.process_iter(["pid", "cmdline"]):
                try:
                    if p.pid != my_pid:
                        c = p.info.get("cmdline") or []
                        if any(arg.endswith("main.py") or arg == "main.py" for arg in c) and "-c" not in c:
                            p.kill()
                except Exception:
                    pass
        except Exception:
            pass
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    return subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        **kwargs
    )


def wait_for_heartbeat(eva_dir, expected_commit, start_time, timeout=90, sleep_fn=time.sleep):
    heartbeat_file = os.path.join(eva_dir, "heartbeat.json")
    deadline = time.time() + timeout
    while True:
        if os.path.exists(heartbeat_file):
            try:
                with open(heartbeat_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                hb_commit = str(data.get("commit", ""))
                hb_time = float(data.get("time", 0))
                if (hb_commit.startswith(expected_commit) or expected_commit.startswith(hb_commit)) and hb_time >= start_time:
                    return True
            except Exception:
                pass
        if time.time() >= deadline:
            break
        sleep_fn(min(1.0, max(0.01, deadline - time.time())))
    return False


def log_update(eva_dir, old_commit, target, result):
    eva_dir = eva_dir or os.path.expanduser("~/.eva")
    os.makedirs(eva_dir, exist_ok=True)
    log_file = os.path.join(eva_dir, "updates.log")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} {old_commit} -> {target}: {result}"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)
    return line


def update(run=default_run, eva_dir=None, wait_heartbeat=90, ref="main", sleep_fn=time.sleep) -> int:
    eva_dir = eva_dir or os.path.expanduser("~/.eva")

    # 1. refuse if git status --porcelain shows changes to tracked files (print them; exit 2)
    status_res = run(["git", "status", "--porcelain"])
    tracked_changes = [line for line in status_res.stdout.splitlines() if line and not line.startswith("?? ")]
    if tracked_changes:
        print("Refusing update: tracked files have changes:")
        for line in tracked_changes:
            print(f"  {line}")
        return 2

    # 2. old = current commit; stop Eva (the process whose command line contains main.py, never itself)
    rev_res = run(["git", "rev-parse", "--short", "HEAD"])
    old_commit = rev_res.stdout.strip()
    run(["stop-eva"])

    # 3. git fetch origin <ref>, git merge --ff-only FETCH_HEAD; if that fails -> restart Eva on old, exit 3
    fetch_res = run(["git", "fetch", "origin", ref])
    merge_res = run(["git", "merge", "--ff-only", "FETCH_HEAD"])
    if merge_res.returncode != 0:
        err = merge_res.stderr or merge_res.stdout
        print(f"Merge failed: {err}")
        run(["cmd.exe", "/c", "start", "", "/min", "run.bat"])
        log_update(eva_dir, old_commit, ref, f"merge failed, restarted on {old_commit}")
        return 3

    # 4. run python main.py --selftest; if it fails -> git reset --hard <old>, start Eva, exit 4
    new_rev = run(["git", "rev-parse", "--short", "HEAD"])
    new_commit = new_rev.stdout.strip() or ref
    selftest_res = run([sys.executable, "main.py", "--selftest"])
    if selftest_res.returncode != 0:
        err = selftest_res.stdout or selftest_res.stderr
        print(f"Selftest failed:\n{err}")
        run(["git", "reset", "--hard", old_commit])
        run(["cmd.exe", "/c", "start", "", "/min", "run.bat"])
        log_update(eva_dir, old_commit, ref, f"selftest failed, rolled back to {old_commit}")
        return 4

    # 5. start Eva (run.bat, minimised); wait up to 90 s for heartbeat.json with the NEW commit and a time newer than the start;
    # if it does not come -> stop Eva, git reset --hard <old>, start Eva, exit 5
    start_time = time.time()
    run(["cmd.exe", "/c", "start", "", "/min", "run.bat"])
    hb_ok = wait_for_heartbeat(eva_dir, new_commit, start_time, timeout=wait_heartbeat, sleep_fn=sleep_fn)
    if not hb_ok:
        print(f"Heartbeat timeout for {new_commit} after {wait_heartbeat}s; rolling back to {old_commit}.")
        run(["stop-eva"])
        run(["git", "reset", "--hard", old_commit])
        run(["cmd.exe", "/c", "start", "", "/min", "run.bat"])
        log_update(eva_dir, old_commit, ref, f"heartbeat timeout, rolled back to {old_commit}")
        return 5

    # 6. append one line to ~/.eva/updates.log: time, old -> new, result. Print the same line.
    log_update(eva_dir, old_commit, new_commit, "OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Update Eva with health check and rollback.")
    parser.add_argument("--ref", default="main", help="Git branch or ref to update to (default: main)")
    parser.add_argument("--timeout", type=int, default=90, help="Heartbeat wait timeout in seconds (default: 90)")
    args = parser.parse_args()
    code = update(ref=args.ref, wait_heartbeat=args.timeout)
    sys.exit(code)


if __name__ == "__main__":
    main()
