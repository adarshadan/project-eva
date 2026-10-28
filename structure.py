"""Eva -- finding things by the STRUCTURE windows publish, before reading pixels.

Every kind of window on Windows describes itself through UI Automation: classic
Win32 programs, WPF, WinUI/UWP, Qt, Office, Electron apps (VS Code, Teams,
Slack, Discord, Spotify) and every browser's page (Chrome, Edge, Opera, Brave,
Firefox publish the web page's accessibility tree: links, buttons, headings,
text, form fields -- the same names and roles the page's DOM gives them). That
is exact: the control's own name, role and rectangle, no OCR guess, no model.

Why it was not used for finding things (eva.log 2026-10-11 23:14, Wordle in
Opera): ui_tree walked controls one by one with several cross-process COM calls
EACH and stopped at 500 or 8 s -- on a browser it ran out inside the tab strip
and a hidden "Speed Dial" page, long before the real page's Play button.

Here one UI Automation call (FindAllBuildCache) returns every ON-SCREEN, NAMED
element of a window with its properties cached, typically a few thousand in
well under a second, and the description is matched against the names with
the same forgiving matcher as on-screen text (screen_text.py: spelling and
sound, several readings of the description, labels before running text) plus
what the structure adds: interactive roles (button, link, menu item, tab, list
item, check box, field) rank first, disabled controls last.

Why not the browser's debugging protocol (CDP): Chrome 136+ refuses remote
debugging on the person's own profile (a security change: it would expose
their cookies); it only works on a separate, signed-out profile -- not the
browser they are using. UI Automation reads the page they are looking at.

snapshot_inproc runs in the isolated worker (uia_worker.py): native COM faults
must not take Eva down. locate() is called by vision.locate_on_screen first.
"""
import re
import sys
import time

from logger import get_logger

log = get_logger(__name__)

# UI Automation ids
P_RECT, P_TYPE, P_NAME, P_FOCUSABLE, P_ENABLED, P_AID, P_CLASS, P_HELP, P_OFFSCREEN = (
    30001, 30003, 30005, 30009, 30010, 30011, 30012, 30013, 30022)
TREESCOPE_DESCENDANTS = 4
ROLES = {50000: "button", 50001: "calendar", 50002: "check box", 50003: "combo box", 50004: "field",
         50005: "link", 50006: "image", 50007: "list item", 50008: "list", 50009: "menu", 50010: "menu bar",
         50011: "menu item", 50012: "progress bar", 50013: "radio button", 50014: "scroll bar", 50015: "slider",
         50016: "spinner", 50017: "status bar", 50018: "tab", 50019: "tab item", 50020: "text", 50021: "tool bar",
         50022: "tooltip", 50023: "tree", 50024: "tree item", 50025: "custom", 50026: "group", 50027: "thumb",
         50028: "data grid", 50029: "data item", 50030: "document", 50031: "split button", 50032: "window",
         50033: "pane", 50034: "header", 50035: "header item", 50036: "table", 50037: "title bar",
         50038: "separator"}
INTERACTIVE = {"button", "check box", "combo box", "field", "link", "list item", "menu item", "radio button",
               "tab item", "tree item", "split button", "data item", "header item", "slider"}
CONTAINERS = {"window", "pane", "document", "group", "list", "tree", "tab", "tool bar", "menu bar", "menu",
              "table", "data grid", "status bar", "title bar", "scroll bar", "custom"}


# ---- in the worker process -----------------------------------------------------------------
def _dpi_aware():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)          # rectangles in real screen pixels
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def snapshot_inproc(window_handle, max_elements=6000, retry_sparse=True):
    """Every on-screen, named element of a window: [{name, role, rect, aid, enabled, focusable, help}]."""
    _dpi_aware()
    from pywinauto.uia_defines import IUIA
    iuia = IUIA().iuia
    t0 = time.time()
    root = iuia.ElementFromHandle(int(window_handle))

    def grab():
        cr = iuia.CreateCacheRequest()
        for pid in (P_RECT, P_TYPE, P_NAME, P_FOCUSABLE, P_ENABLED, P_AID, P_CLASS, P_HELP):
            cr.AddProperty(pid)
        cond = iuia.CreateAndCondition(iuia.CreatePropertyCondition(P_OFFSCREEN, False),
                                       iuia.CreateNotCondition(iuia.CreatePropertyCondition(P_NAME, "")))
        arr = root.FindAllBuildCache(TREESCOPE_DESCENDANTS, cond, cr)
        out = []
        n = arr.Length if arr is not None else 0
        for i in range(min(n, max_elements)):
            try:
                e = arr.GetElement(i)
                r = e.CachedBoundingRectangle
                rect = [int(r.left), int(r.top), int(r.right), int(r.bottom)]
                if rect[2] - rect[0] < 2 or rect[3] - rect[1] < 2:
                    continue
                out.append({"name": " ".join(str(e.CachedName or "").split())[:300],
                            "role": ROLES.get(int(e.CachedControlType or 0), "other"),
                            "rect": rect, "aid": str(e.CachedAutomationId or "")[:80],
                            "enabled": bool(e.CachedIsEnabled), "focusable": bool(e.CachedIsKeyboardFocusable),
                            "help": " ".join(str(e.CachedHelpText or "").split())[:120]})
            except Exception:
                continue
        return out, n
    els, total = grab()
    # Chromium and Electron build their page tree the first time a reader asks for it:
    # the first answer can be only the browser's frame. Ask once more.
    if retry_sparse and len(els) < 60:
        try:
            cls = str(root.CurrentClassName or "")
        except Exception:
            cls = ""
        if cls.startswith("Chrome_WidgetWin") or "Mozilla" in cls:
            time.sleep(0.6)
            els, total = grab()
    return {"ok": True, "elements": els, "total": total, "ms": int((time.time() - t0) * 1000)}


# ---- in Eva ----------------------------------------------------------------------------------
_SNAP_CACHE = {}


def snapshot(window_handle, key=None):
    """The window's elements via the isolated worker (cached per unchanged screen)."""
    ck = (int(window_handle), key) if key else None
    if ck and ck in _SNAP_CACHE:
        return _SNAP_CACHE[ck]
    import desktop_control as dc
    payload, err = dc._isolated("structure_snapshot", {"window_handle": int(window_handle)}, timeout=8.0)
    if err or not isinstance(payload, dict):
        log.info(f"Structure of window {window_handle} unavailable: {err}")
        return []
    els = payload.get("elements") or []
    log.info(f"Structure of window {window_handle}: {len(els)} on-screen elements in {payload.get('ms')} ms")
    if ck:
        if len(_SNAP_CACHE) > 12:
            _SNAP_CACHE.clear()
        _SNAP_CACHE[ck] = els
    return els


def _tokens(s):
    import screen_text
    return screen_text._tokens(s)


def match(description, elements, region=None, exclude=()):
    """The element that best matches `description`, as a locate result, or None."""
    import screen_text as st
    variants = st.query_variants(description)
    if not variants or not st.query_words(description):
        return None
    control = st.is_control(description)
    # In a browser the page is a "document"; its own tabs, address bar and toolbar sit outside it
    # and often repeat the page's words ("Wordle - The New York Times" tab). Page content first,
    # unless the person asked for a tab, the address bar or a bookmark.
    docs = [e["rect"] for e in elements if e.get("role") == "document"]
    page = max(docs, key=lambda r: (r[2] - r[0]) * (r[3] - r[1])) if docs else None
    chrome_wanted = bool(re.search(r"\b(tab|tabs|address|url|bookmark|toolbar|extension)\b", str(description).lower()))
    cands = []
    for e in elements:
        l, t, r, b = e["rect"]
        cx, cy = (l + r) // 2, (t + b) // 2
        if region and not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
            continue
        if any(x0 <= cx <= x1 and y0 <= cy <= y1 for (x0, y0, x1, y1) in exclude):
            continue                                        # Eva's own windows
        role = e.get("role", "")
        if role in CONTAINERS and (r - l) * (b - t) > 600 * 400:
            continue                                        # a whole page or panel is not "the thing"
        for field, weight in (("name", 1.0), ("help", 0.9), ("aid", 0.85)):
            toks = [x for x in _tokens(e.get(field, "")) if re.search(r"[a-z0-9]", x)]
            if not toks:
                continue
            for q in variants:
                n = len(q)
                for size in range(max(1, n - 1), n + 3):
                    for i in range(0, max(1, len(toks) - size + 1)):
                        sc = st.phrase_score(q, toks[i:i + size]) * weight
                        if sc <= 0:
                            continue
                        standalone = size >= len(toks)
                        rank = sc + (0.05 if standalone else 0.0) + (0.06 if role in INTERACTIVE else 0.0)
                        if control and not standalone and role not in INTERACTIVE and len(toks) > size + 1:
                            rank -= 0.12                    # a word inside a paragraph is not the control
                        if not e.get("enabled", True):
                            rank -= 0.1
                        if page and not chrome_wanted and page[0] <= cx <= page[2] and page[1] <= cy <= page[3]:
                            rank += 0.08
                        cands.append((rank, sc, len(q), e, q))
    if not cands:
        return None
    # best rank; then the one covering more of what was said; then the LARGER element -- the Play
    # button, not a small "Play" link in a footer (big panels and rows are already left out)
    cands.sort(key=lambda c: (-round(c[0], 2), -c[2],
                              -(c[3]["rect"][2] - c[3]["rect"][0]) * (c[3]["rect"][3] - c[3]["rect"][1])))
    rank, sc, _, best, q = cands[0]
    need = st.SHORT_THRESHOLD if (len(q) == 1 and len(q[0]) <= 5) else st.THRESHOLD
    if min(rank, sc, 1.0) < need:
        return None
    l, t, r, b = best["rect"]
    out = {"found": True, "x1": l, "y1": t, "x2": r, "y2": b, "center_x": (l + r) // 2, "center_y": (t + b) // 2,
           "text": best["name"][:120] or best.get("aid", ""), "role": best.get("role", ""), "score": round(min(sc, 1), 3),
           "rank": rank, "enabled": best.get("enabled", True)}
    others, seen = [], {id(best)}
    for c in cands[1:]:
        e = c[3]
        if id(e) in seen or c[0] < rank - 0.02:
            continue
        seen.add(id(e))
        ex, ey = (e["rect"][0] + e["rect"][2]) // 2, (e["rect"][1] + e["rect"][3]) // 2
        inside = l <= ex <= r and t <= ey <= b or e["rect"][0] <= out["center_x"] <= e["rect"][2] and \
            e["rect"][1] <= out["center_y"] <= e["rect"][3]
        if inside:
            continue                                        # the label inside the same button
        if all(abs(ex - o["x"]) > 30 or abs(ey - o["y"]) > 20 for o in others):
            others.append({"x": ex, "y": ey, "text": e["name"][:60], "role": e.get("role", "")})
    if others:
        out["also_at"] = others[:4]
    return out


# ---- which windows to read -------------------------------------------------------------------
def _find_window(cls):
    try:
        import ctypes
        h = ctypes.windll.user32.FindWindowW(cls, None)
        return int(h) if h else None
    except Exception:
        return None


def desktop_window():
    """The window holding the desktop icons (Progman, or the WorkerW it moves to)."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(h, _):
            if u.FindWindowExW(h, None, "SHELLDLL_DefView", None):
                found.append(int(h))
            return True
        u.EnumWindows(each, 0)
        return found[0] if found else _find_window("Progman")
    except Exception:
        return _find_window("Progman")


def windows_for(description, region=None):
    """Windows to read, most likely first: the one in front; the desktop when it is
    named or in front; the taskbar when named; with an area, the windows under it."""
    import desktop_control as dc
    out = []

    def add(h):
        try:
            if h and int(h) not in out and not dc.is_own_window(int(h)):
                from core import elevation
                if not (not elevation.self_elevated() and elevation.window_elevated(int(h))):
                    out.append(int(h))
        except Exception:
            pass
    d = str(description or "").lower()
    if region:
        try:
            for w in dc.list_windows().get("windows", [])[:12]:
                r = dc.get_window_rect(w["handle"])
                if r and r.get("ok", True) and r["left"] < region[2] and r["right"] > region[0] \
                        and r["top"] < region[3] and r["bottom"] > region[1]:
                    add(w["handle"])
                    if len(out) >= 3:
                        break
        except Exception:
            pass
    fg = dc.get_foreground_app()
    if "desktop" in d:
        add(desktop_window())
    add(fg.get("handle"))
    if "taskbar" in d or "system tray" in d:
        add(_find_window("Shell_TrayWnd"))
    title = (fg.get("title") or "").lower()
    if not fg.get("handle") or title in ("", "program manager"):
        add(desktop_window())
    return out


def locate(description, region=None, exclude=(), key=None):
    """Find `description` in the structure of the likely windows. None when not
    found (or not on Windows): then the screen's text, then vision, look."""
    if _TESTING["on"]:
        pass
    elif sys.platform != "win32" or _TESTING["off"]:
        return None                              # off in the checks: never read the person's real windows
    t0 = time.monotonic()
    for h in windows_for(description, region):
        els = snapshot(h, key)
        hit = match(description, els, region, exclude) if els else None
        if hit:
            hit["window_handle"] = h
            log.info(f"Located {description!r} in the window's structure: {hit['role']} {hit['text'][:60]!r} at "
                     f"{hit['center_x']},{hit['center_y']} ({(time.monotonic() - t0) * 1000:.0f} ms)")
            return hit
        if time.monotonic() - t0 > 10:
            break
    return None


def describe_screen(window_handle=None, limit=30):
    """What is on screen now, in words, from the window's structure: the window, any
    dialog in it, and the controls on offer -- for the stuck check (core/agent.py).
    Fast (no vision model). '' when there is nothing to say."""
    import desktop_control as dc
    fg = dc.get_foreground_app()
    h = window_handle or fg.get("handle")
    if not h:
        return ""
    els = snapshot(h)
    title = fg.get("title") or ""
    if not els:
        return f"In front: {title!r} (its controls cannot be read; look at the screen)." if title else ""
    dialogs = [e["name"] for e in els if e["role"] == "window" and e["name"] and e["name"] != title][:3]
    seen, ctrls = set(), []
    order = {"button": 0, "link": 1, "check box": 2, "radio button": 2, "field": 3, "combo box": 3,
             "menu item": 4, "tab item": 5, "list item": 6}
    for e in sorted(els, key=lambda e: (order.get(e["role"], 9), e["rect"][1], e["rect"][0])):
        if e["role"] not in order:
            continue
        label = e["name"][:40] + ("" if e.get("enabled", True) else " (disabled)")
        key = (e["role"], label.lower())
        if key in seen:
            continue
        seen.add(key)
        ctrls.append(f"{e['role']} '{label}'")
        if len(ctrls) >= limit:
            break
    heads = [e["name"][:60] for e in els if e["role"] in ("text", "header") and 3 <= len(e["name"]) <= 60][:6]
    parts = [f"In front: {title!r}."]
    if dialogs:
        parts.append("Dialogs / panels: " + "; ".join(repr(d) for d in dialogs) + ".")
    if heads:
        parts.append("Text: " + " | ".join(heads) + ".")
    if ctrls:
        parts.append("Controls: " + ", ".join(ctrls) + ".")
    return " ".join(parts)[:1800]


def read_elements(window_handle=None, contains="", max_rows=60):
    """Every labelled element of a window, ROW BY ROW (top to bottom, left to right):
    a web game's tiles and keys, a form, a table, a list. For reading a state exactly
    ("1st letter, S, absent | 2nd letter, L, present in another position ...")."""
    import desktop_control as dc
    fg = dc.get_foreground_app()
    h = window_handle or fg.get("handle")
    if not h:
        return {"ok": False, "error": "no window in front"}
    els = snapshot(int(h))
    if not els:
        return {"ok": False, "error": "this window does not publish its elements; use look_at_screen"}
    want = str(contains or "").lower().strip()
    keep = []
    for e in els:
        l, t, r, b = e["rect"]
        if e["role"] in CONTAINERS and (r - l) * (b - t) > 400 * 300:
            continue
        label = e["name"]
        if want and want not in label.lower() and want not in e.get("role", ""):
            continue
        keep.append(e)
    keep.sort(key=lambda e: ((e["rect"][1] + e["rect"][3]) / 2, e["rect"][0]))
    rows, cur, cur_y, cur_h = [], [], None, 0
    for e in keep:
        cy, hh = (e["rect"][1] + e["rect"][3]) / 2, max(4, e["rect"][3] - e["rect"][1])
        if cur and abs(cy - cur_y) > max(6, min(hh, cur_h) * 0.5):
            rows.append((cur_y, cur))
            cur = []
        if not cur:
            cur_y, cur_h = cy, hh
        cur.append(e)
    if cur:
        rows.append((cur_y, cur))
    lines = []
    for y, row in rows[:max_rows]:
        row.sort(key=lambda e: e["rect"][0])
        cells = []
        for e in row:
            tag = "" if e["role"] in ("text", "other") else f" [{e['role']}]"
            dis = "" if e.get("enabled", True) else " (disabled)"
            cells.append(f"{e['name'][:60]}{tag}{dis}")
        lines.append(f"y={int(y)}: " + " | ".join(cells))
    text = "\n".join(lines)
    return {"ok": True, "window": fg.get("title") if not window_handle else window_handle, "rows": len(rows),
            "elements": text[:6000] + ("\n..." if len(text) > 6000 else "")}


_TESTING = {"on": False, "off": False}
