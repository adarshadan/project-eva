"""Safe update with health check and automatic rollback."""
import argparse
import json
import os
import subprocess
import sys
import time
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_eva_process(cmdline, cwd, repo_root) -> bool:
    """Pure function: True if process is Eva, False for others or the updater itself."""
    if not cmdline:
        return False
    # Never match the updater itself
    if any(arg.endswith("update.py") or "update.py" in arg for arg in cmdline):
        return False

    norm_repo = os.path.normcase(os.path.abspath(repo_root))
    norm_main = os.path.normcase(os.path.abspath(os.path.join(repo_root, "main.py")))

    for arg in cmdline:
        if os.path.isabs(arg):
            try:
                if os.path.normcase(os.path.abspath(arg)) == norm_main:
                    return True
            except Exception:
                pass

    if cwd:
        try:
            norm_cwd = os.path.normcase(os.path.abspath(cwd))
            if norm_cwd == norm_repo:
                if any(arg.endswith("main.py") or arg == "main.py" for arg in cmdline):
                    return True
        except Exception:
            pass

    return False


def default_run(cmd, cwd=None, **kwargs):
    if cmd == ["stop-eva"]:
        try:
            import psutil
        except ImportError:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="psutil missing")

        my_pid = os.getpid()
        eva_procs = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if p.pid != my_pid:
                    cmdline = p.info.get("cmdline") or []
                    if not any("main.py" in arg for arg in cmdline):
                        continue
                    try:
                        proc_cwd = p.cwd()
                    except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                        proc_cwd = None
                    if is_eva_process(cmdline, proc_cwd, REPO_ROOT):
                        eva_procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        for p in eva_procs:
            try:
                p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        gone, alive = psutil.wait_procs(eva_procs, timeout=5)
        for p in alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    if len(cmd) >= 3 and cmd[0] == "cmd.exe" and cmd[1] == "/c" and "start" in cmd:
        return subprocess.run(
            cmd,
            cwd=cwd or REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs
        )

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
                hb_commit = str(data.get("commit", "")).strip()
                hb_time = float(data.get("time", 0))
                if hb_commit and hb_commit != "unknown":
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

    # If psutil cannot be imported, refuse before fetching anything
    try:
        import psutil  # noqa: F401
    except ImportError:
        print("cannot stop Eva: psutil missing")
        return 6

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

    # 3. git fetch origin <ref>; if that fails -> restart Eva on old, log "fetch failed", exit 3
    fetch_res = run(["git", "fetch", "origin", ref])
    if fetch_res.returncode != 0:
        err = fetch_res.stderr or fetch_res.stdout
        print(f"Fetch failed: {err}")
        run(["cmd.exe", "/c", "start", "", "/min", "run.bat"])
        log_update(eva_dir, old_commit, ref, f"fetch failed, restarted on {old_commit}")
        return 3

    merge_res = run(["git", "merge", "--ff-only", "FETCH_HEAD"])
    if merge_res.returncode != 0:
        err = merge_res.stderr or merge_res.stdout
        print(f"Merge failed: {err}")
        run(["cmd.exe", "/c", "start", "", "/min", "run.bat"])
        log_update(eva_dir, old_commit, ref, f"merge failed, restarted on {old_commit}")
        return 3

    # Check if dependencies changed; if so, wait 600s instead of default for pip sync
    diff_res = run(["git", "diff", "--name-only", old_commit, "HEAD", "--", "requirements.txt", "requirements-play.txt"])
    diff_files = [f.strip() for f in diff_res.stdout.splitlines() if f.strip()]
    heartbeat_timeout = wait_heartbeat
    if diff_files:
        heartbeat_timeout = 600
        print(f"Dependencies changed ({', '.join(diff_files)}); increasing heartbeat timeout to 600s.")

    # 4. run python main.py --selftest; if it fails -> git reset --hard <old>, start Eva, exit 4
    new_rev = run(["git", "rev-parse", "--short", "HEAD"])
    new_commit = new_rev.stdout.strip() or ref
    py_exec = os.path.join(REPO_ROOT, "venv", "Scripts", "python.exe")
    if not os.path.exists(py_exec):
        py_exec = sys.executable
    selftest_res = run([py_exec, "main.py", "--selftest"])
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
    hb_ok = wait_for_heartbeat(eva_dir, new_commit, start_time, timeout=heartbeat_timeout, sleep_fn=sleep_fn)
    if not hb_ok:
        print(f"Heartbeat timeout for {new_commit} after {heartbeat_timeout}s; rolling back to {old_commit}.")
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
