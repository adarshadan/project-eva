"""Read-only: read_window_text the cached way vs the old way, on every open window; timings.
Run tools\\read_text_parity.bat; the result is in tools\\read_text_parity.txt."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402
import desktop_control as dc  # noqa: E402

out = []
for w in dc.list_windows().get("windows", [])[:8]:
    h = w["handle"]
    config.UIA_CACHED_READS = True
    t = time.time(); a = dc.read_window_text(h); ta = time.time() - t
    config.UIA_CACHED_READS = False
    t = time.time(); b = dc.read_window_text(h); tb = time.time() - t
    same = a.get("text") == b.get("text")
    out.append(f"{'SAME' if same else 'DIFF'}  {w['title'][:50]!r}: cached {ta*1000:.0f} ms, old {tb*1000:.0f} ms, "
               f"source {a.get('source')}/{b.get('source')}, len {a.get('length')}/{b.get('length')}")
    if not same:
        out.append(f"      cached: {str(a.get('text'))[:160]!r}")
        out.append(f"      old:    {str(b.get('text'))[:160]!r}")
text = "\n".join(out)
print(text)
with open(os.path.join(os.path.dirname(__file__), "read_text_parity.txt"), "w", encoding="utf-8") as f:
    f.write(text + "\n")
