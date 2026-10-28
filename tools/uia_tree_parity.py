"""Read-only: the cached ui_tree read against the original element-by-element walk.

    python tools\\uia_tree_parity.py      (with Notepad and/or File Explorer open)

For each such window: both reads, timed; every element compared (id, name, role, rect, patterns);
then a sample of ids resolved both ways (_find_cached vs the old walk) and checked to be the SAME
control. Nothing is clicked or typed. Writes tools\\uia_tree_parity.txt.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import pygetwindow as gw  # noqa: E402
import desktop_control as dc  # noqa: E402


def slow_find(win, eid):
    for idx, ctrl in enumerate(win.descendants()):
        try:
            if dc._element_id_for(ctrl, idx) == eid:
                return ctrl
        except Exception:
            continue
    return None


def main():
    lines, bad = [], 0
    wins = [w for w in gw.getAllWindows() if w.title.strip() and not dc.is_own_window(w._hWnd)
            and ("Notepad" in w.title or "File Explorer" in w.title)][:4]
    if not wins:
        print("Open a Notepad or File Explorer window first.")
        return 1
    for w in wins:
        h = w._hWnd
        t0 = time.perf_counter(); slow = dc._ui_tree_slow(h, 3000)["elements"]; ts = time.perf_counter() - t0
        t0 = time.perf_counter(); fast = [{k: v for k, v in e.items() if k != "offscreen"} for e in dc._ui_tree_inproc(h)["elements"]]; tf = time.perf_counter() - t0
        t0 = time.perf_counter(); dc._ui_tree_inproc(h); tf2 = time.perf_counter() - t0
        diffs = [(i, a, b) for i, (a, b) in enumerate(zip(slow, fast)) if a != b]
        same = len(slow) == len(fast) and not diffs
        bad += 0 if same else 1
        lines.append(f"{w.title[:50]!r}: {len(slow)} elements; walk {ts*1000:.0f} ms, cached {tf*1000:.0f} ms "
                     f"(again {tf2*1000:.0f} ms); identical: {same}")
        for i, a, b in diffs[:5]:
            lines.append(f"   #{i}\n     walk:   {a}\n     cached: {b}")
        if len(slow) != len(fast):
            lines.append(f"   counts differ: walk {len(slow)} vs cached {len(fast)}")
        win = dc._connect(h).window(handle=h)
        ids = [e["element_id"] for e in slow]
        sample = ids[:: max(1, len(ids) // 12)][:12]
        mism = 0
        for eid in sample:
            a, b = dc._find_cached(win, eid), slow_find(win, eid)
            ra = a.element_info.runtime_id if a is not None else None
            rb = b.element_info.runtime_id if b is not None else None
            if ra != rb:
                mism += 1
                lines.append(f"   id {eid[:40]!r}: cached -> {ra}, walk -> {rb}")
        bad += 1 if mism else 0
        lines.append(f"   ids resolved to the same control: {len(sample) - mism}/{len(sample)}")
    lines.append("RESULT: " + ("PASS" if not bad else f"FAIL ({bad})"))
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(HERE, "uia_tree_parity.txt"), "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M") + "\n" + text + "\n")
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
