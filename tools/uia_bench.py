"""Read-only benchmark: UI Automation reads through one-shot workers vs the long-lived worker.

    python tools\\uia_bench.py        (open a Notepad or File Explorer window first)

Prints the median time per read for ui_tree, structure_snapshot and name_at_point in each mode,
and whether both modes returned the same thing. Writes tools\\uia_bench_result.txt.
"""
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import pygetwindow as gw  # noqa: E402
import desktop_control as dc  # noqa: E402


def timed(fn, n=5):
    times, last = [], None
    for _ in range(n):
        t0 = time.perf_counter()
        last = fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times), min(times), last


def main():
    wins = [w for w in gw.getAllWindows() if w.title.strip() and not dc.is_own_window(w._hWnd)
            and ("Notepad" in w.title or "File Explorer" in w.title)]
    if not wins:
        print("Open a Notepad or File Explorer window first.")
        return 1
    w = wins[0]
    cx, cy = w.left + w.width // 2, w.top + w.height // 2
    reads = {"ui_tree": {"window_handle": w._hWnd},
             "structure_snapshot": {"window_handle": w._hWnd},
             "name_at_point": {"x": cx, "y": cy}}
    lines = [f"window: {w.title!r}"]
    t0 = time.perf_counter()
    dc.UIA_SERVER.call("name_at_point", {"x": 1, "y": 1}, 20)          # start-up, counted once
    lines.append(f"long-lived worker start-up: {time.perf_counter() - t0:.2f} s (once per session)")
    for name, args in reads.items():
        once_med, once_min, once_res = timed(lambda: dc._isolated_once(name, args, 25))
        srv_med, srv_min, srv_res = timed(lambda: dc.UIA_SERVER.call(name, args, 25))
        size = lambda r: len(str(r[0])) if r and r[0] is not None else f"error {r and r[1]}"
        lines.append(f"{name:20s} one-shot {once_med*1000:7.0f} ms   long-lived {srv_med*1000:6.0f} ms   "
                     f"(min {srv_min*1000:.0f} ms)   same result size: {size(once_res) == size(srv_res)} "
                     f"[{size(srv_res)}]")
    lines.append(f"restarts: {dc.UIA_SERVER.restarts}")
    dc.UIA_SERVER.stop()
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(HERE, "uia_bench_result.txt"), "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M") + "\n" + text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
