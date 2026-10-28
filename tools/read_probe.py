"""Read-only: how each route reads the document of open Notepad windows, first and second time."""
import os, sys, traceback
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pygetwindow as gw
import desktop_control as dc
def walk(h, tag):
    win = dc._connect(h).window(handle=h)
    for c in [win] + list(win.descendants()):
        kind = dc._control_type(c)
        if kind not in ("Document", "TitleBar", "Edit"):
            continue
        out = {}
        for name, fn in (("iface_value", lambda: c.iface_value.CurrentValue),
                         ("window_text", lambda: c.window_text()),
                         ("iface_text", lambda: c.iface_text.DocumentRange.GetText(-1))):
            try:
                out[name] = repr(fn())[:50]
            except Exception as e:
                out[name] = f"ERR {type(e).__name__}: {str(e)[:60]}"
        print(f"  {tag} {kind}: {out}")
for w in gw.getAllWindows():
    if "Notepad" in (w.title or ""):
        print("WINDOW", w._hWnd, repr(w.title))
        r = dc.read_window_text(w._hWnd); print("  rwt#1 values:", r.get("values"), "source:", r.get("source"))
        walk(w._hWnd, "walk")
        r = dc.read_window_text(w._hWnd); print("  rwt#2 values:", r.get("values"), "source:", r.get("source"))
