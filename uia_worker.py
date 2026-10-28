"""Eva -- UI Automation reads, run in a CHILD process.

eva.log 2026-09-30 20:18 ended in the middle of ui_tree on the Windows
Settings window: no result, no traceback, no shutdown line. The run before it
also ended right after UI Automation work on a browser window. Enumerating a
large or packaged-app (UWP) tree is native COM code; when it faults it takes
the whole interpreter down, and with it the avatar, the microphone and every
in-flight task.

Read-only enumeration therefore runs here, in its own process. If it crashes
or hangs the parent sees a non-zero exit or a timeout, kills it, and reports
an ordinary tool failure; Eva stays up. Cost: ~0.5-1s of start-up per call.

Usage (by desktop_control._isolated only):
    python uia_worker.py <function> <json-args>     one call, one JSON document on stdout
    python uia_worker.py --serve                    long-lived: one JSON request per stdin line,
                                                    one JSON reply per stdout line

2026-10-17 (plan Phase 1): a new process per read cost 0.5-1.4 s -- and every blind click now also
reads the control under the cursor (system_guard). --serve keeps ONE worker alive: same isolation
(a crash or hang still only kills the worker; desktop_control restarts it), without the start-up
cost. Requests: {"id": n, "fn": name, "args": {...}}; replies: {"id": n, "result": ...} or
{"id": n, "error": "..."}.
"""
import json
import sys


def _functions():
    import desktop_control as dc
    import structure
    return {"ui_tree": dc._ui_tree_inproc,
            "find_elements_by_text": dc._find_by_text_inproc,
            "structure_snapshot": structure.snapshot_inproc,
            "name_at_point": dc._name_at_point_inproc}


def _dpi_aware():
    try:                                      # rectangles in real screen pixels on scaled displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def serve():
    """Long-lived mode. stdout carries ONLY replies; anything a library prints goes to stderr."""
    _dpi_aware()
    out = sys.stdout
    sys.stdout = sys.stderr
    fns = _functions()
    out.write(json.dumps({"ready": True}) + "\n")
    out.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            fn = fns.get(req.get("fn"))
            if fn is None:
                reply = {"id": rid, "error": f"unknown function {req.get('fn')}"}
            else:
                reply = {"id": rid, "result": fn(**(req.get("args") or {}))}
        except Exception as e:                # an ordinary Python error: report it, keep serving
            reply = {"id": rid, "error": f"{type(e).__name__}: {e}"}
        out.write(json.dumps(reply, default=str) + "\n")
        out.flush()
    return 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--serve":
        return serve()
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: uia_worker.py <function> <json-args>"}))
        return 2
    name, args = sys.argv[1], json.loads(sys.argv[2])
    _dpi_aware()
    fn = _functions().get(name)
    if fn is None:
        print(json.dumps({"error": f"unknown function {name}"}))
        return 2
    print(json.dumps(fn(**args), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
