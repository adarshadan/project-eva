"""Eva -- back-end capabilities: the parts of the machine with no window.

Until now Eva could only reach a file by driving Notepad with a virtual
keyboard (a 235-character passage once "typed" into one character) and
could not see what the machine was running at all. These tools give her
direct, verifiable access -- through the same capability manifest as
everything else, so each one is permission-gated by what it does and
performed (status line, working pose, reaction) without extra wiring.
"""
import os
import subprocess
import tempfile

import command_policy
import config
from logger import get_logger

log = get_logger(__name__)

# Writing here needs a person at an administrator prompt, not an assistant.
_PROTECTED_WINDOWS = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
_PROTECTED_POSIX = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc", "/dev", "/lib")
_CRITICAL_PROCESSES = {"system", "system idle process", "registry", "smss.exe", "csrss.exe",
                       "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
                       "init", "systemd", "launchd", "kernel_task"}


def _resolve(path: str) -> str:
    from system_tools import _resolve as resolve
    return resolve(path)


def _is_protected(target: str) -> bool:
    norm = os.path.normcase(os.path.abspath(target))
    roots = ([os.environ.get(v) for v in _PROTECTED_WINDOWS] if os.name == "nt"
             else list(_PROTECTED_POSIX))
    return any(r and norm.startswith(os.path.normcase(os.path.abspath(r)) + os.sep)
               or (r and norm == os.path.normcase(os.path.abspath(r))) for r in roots)


def write_file(path: str, content: str, mode: str = "create"):
    """Create, append to, or overwrite a text file, then read it back.

    mode: create (fails if it exists), append, overwrite. Overwriting an
    existing file is confirmation-gated by the manifest, because it loses
    what was there."""
    if mode not in ("create", "append", "overwrite"):
        return {"ok": False, "error": f"unknown mode {mode!r} (create, append or overwrite)"}
    target = _resolve(path)
    if _is_protected(target):
        return {"ok": False, "error": f"refusing to write inside a protected system folder: {target}"}
    data = str(content)
    if len(data.encode("utf-8")) > config.WRITE_FILE_MAX_BYTES:
        return {"ok": False, "error": f"content is over the {config.WRITE_FILE_MAX_BYTES} byte limit"}
    exists = os.path.exists(target)
    if os.path.isdir(target):
        return {"ok": False, "error": f"{target} is a folder"}
    if mode == "create" and exists:
        return {"ok": False, "error": f"{target} already exists -- use mode='append', or "
                                      "mode='overwrite' if replacing it is intended"}
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        if mode == "append":
            with open(target, "a", encoding="utf-8") as f:
                f.write(data)
        else:
            # Write beside the target and swap it in, so a failure halfway
            # through never leaves a half-written file where a good one was.
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target) or ".", suffix=".eva-tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(data)
                os.replace(tmp, target)
            except Exception:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
    except OSError as e:
        return {"ok": False, "error": f"could not write {target}: {e}"}

    # Verify by reading back, not by trusting that write() returned.
    try:
        with open(target, "r", encoding="utf-8") as f:
            on_disk = f.read()
    except OSError as e:
        return {"ok": True, "verified": False, "path": target,
                "warning": f"written, but could not be read back to confirm: {e}"}
    norm = lambda t: t.replace("\r\n", "\n")
    landed = norm(on_disk).endswith(norm(data)) if mode == "append" else norm(on_disk) == norm(data)
    if not landed:
        return {"ok": False, "verified": False, "path": target,
                "error": "the file on disk does not match what was written"}
    return {"ok": True, "verified": True, "path": target, "mode": mode,
            "bytes": len(data.encode("utf-8")), "replaced_existing": exists and mode == "overwrite"}


def run_command(command: str, cwd: str = None, timeout: int = None):
    """Run ONE program, without a shell. Read-only programs run freely;
    anything else is confirmation-gated by the manifest (see
    command_policy for exactly what counts as read-only)."""
    argv, err = command_policy.parse(command)
    if err:
        return {"ok": False, "error": err}
    if command_policy.is_denied(argv):
        return {"ok": False, "error": f"{command_policy.program_name(argv)!r} is on the "
                                      "never-run list and is refused even with confirmation"}
    workdir = None
    if cwd:
        workdir = _resolve(cwd)
        if not os.path.isdir(workdir):
            return {"ok": False, "error": f"working folder not found: {workdir}"}
    limit = max(1, min(int(timeout or config.RUN_COMMAND_TIMEOUT), 120))
    # The child must not inherit Eva's own API key.
    env = {k: v for k, v in os.environ.items() if k != "NVIDIA_API_KEY"}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=limit, cwd=workdir,
                              shell=False, env=env, errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return {"ok": False, "error": f"program not found: {argv[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {limit}s and was stopped"}
    except OSError as e:
        return {"ok": False, "error": f"could not run it: {e}"}
    cap = config.RUN_COMMAND_MAX_OUTPUT
    out = {"ok": proc.returncode == 0, "verified": True, "exit_code": proc.returncode,
           "stdout": proc.stdout[:cap], "stderr": proc.stderr[:cap]}
    if len(proc.stdout) > cap or len(proc.stderr) > cap:
        out["truncated"] = True
    if proc.returncode != 0:
        first = (proc.stderr.strip().splitlines() or [""])[0][:120]
        out["error"] = f"exited with code {proc.returncode}" + (f": {first}" if first else "")
    return out


def _psutil():
    try:
        import psutil
        return psutil, None
    except ImportError:
        return None, {"ok": False, "error": "psutil is not installed (pip install psutil)"}


def list_processes(name_filter: str = None, limit: int = 40):
    """Running programs, biggest memory users first."""
    psutil, err = _psutil()
    if err:
        return err
    rows, needle = [], (name_filter or "").lower()
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            name = p.info["name"] or ""
            if needle and needle not in name.lower():
                continue
            mem = p.info["memory_info"]
            rows.append({"pid": p.info["pid"], "name": name,
                         "memory_mb": round((mem.rss if mem else 0) / 1048576, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    rows.sort(key=lambda r: r["memory_mb"], reverse=True)
    limit = max(1, min(int(limit), 200))
    return {"ok": True, "count": len(rows), "processes": rows[:limit],
            "truncated": len(rows) > limit}


def system_info():
    """CPU, memory, disk, battery and uptime, in one call."""
    psutil, err = _psutil()
    if err:
        return err
    import platform
    import time
    mem = psutil.virtual_memory()
    home = os.path.expanduser("~")
    disk = psutil.disk_usage(home)
    info = {"ok": True, "os": platform.platform(), "cpu_cores": psutil.cpu_count(),
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "memory_total_gb": round(mem.total / 1073741824, 1),
            "memory_available_gb": round(mem.available / 1073741824, 1),
            "memory_percent": mem.percent,
            "disk_free_gb": round(disk.free / 1073741824, 1),
            "disk_percent_used": disk.percent,
            "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1)}
    try:
        battery = psutil.sensors_battery()
        if battery:
            info["battery_percent"] = round(battery.percent)
            info["battery_plugged_in"] = bool(battery.power_plugged)
    except Exception:
        pass
    return info


def end_process(pid: int, force: bool = False):
    """Ask a process to exit (or kill it, with force). Confirmation-gated."""
    psutil, err = _psutil()
    if err:
        return err
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"pid must be a number, got {pid!r}"}
    if pid <= 4 or pid in (os.getpid(), os.getppid()):
        return {"ok": False, "error": "refusing: that is Eva herself, her launcher, or a core system process"}
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        if name.lower() in _CRITICAL_PROCESSES:
            return {"ok": False, "error": f"refusing to stop {name}: it is a core system process"}
        proc.kill() if force else proc.terminate()
        gone, alive = psutil.wait_procs([proc], timeout=3)
    except psutil.NoSuchProcess:
        return {"ok": False, "error": f"no process with id {pid}"}
    except psutil.AccessDenied:
        return {"ok": False, "error": f"not allowed to stop process {pid} (it may need administrator rights)"}
    if gone:
        return {"ok": True, "verified": True, "pid": pid, "name": name}
    return {"ok": False, "verified": False, "pid": pid, "name": name,
            "error": "it was asked to exit but is still running; call again with force=true if that is intended"}


__all__ = ["write_file", "run_command", "list_processes", "system_info", "end_process"]
