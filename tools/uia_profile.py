"""Read-only: where the cached ui_tree read spends its time."""
import os, sys, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pygetwindow as gw
import desktop_control as dc
from pywinauto.uia_defines import IUIA
for w in [w for w in gw.getAllWindows() if "File Explorer" in w.title][:1]:
    h = w._hWnd; iu = IUIA()
    root = iu.iuia.ElementFromHandle(h)
    cr = iu.iuia.CreateCacheRequest()
    for pid in dc._UIA_P.values(): cr.AddProperty(pid)
    t0 = time.perf_counter(); arr = root.FindAllBuildCache(4, iu.true_condition, cr); t1 = time.perf_counter()
    n = arr.Length; els = [arr.GetElement(i) for i in range(n)]; t2 = time.perf_counter()
    names = [e.CachedName for e in els]; t3 = time.perf_counter()
    texts = 0
    for e in els:
        if (e.CachedClassName or "") and e.GetCachedPropertyValue(30040): texts += 1
    t4 = time.perf_counter()
    for i, e in enumerate(els[:500]): dc._cached_text(e)
    t5 = time.perf_counter()
    for i, e in enumerate(els[:500]): dc._cached_id(e, i)
    t6 = time.perf_counter()
    for e in els[:500]: dc._friendly(e.CachedControlType)
    t7 = time.perf_counter()
    print(f"{w.title!r}: {n} elements total, {texts} with Text pattern")
    print(f"FindAllBuildCache {1000*(t1-t0):.0f} ms | GetElement x{n} {1000*(t2-t1):.0f} ms | names {1000*(t3-t2):.0f} ms | "
          f"text-check {1000*(t4-t3):.0f} ms | _cached_text x500 {1000*(t5-t4):.0f} ms | _cached_id x500 {1000*(t6-t5):.0f} ms | friendly x500 {1000*(t7-t6):.0f} ms")
