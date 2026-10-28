"""Eva -- everyday desktop helpers: documents, PC care, email and calendar
drafts, window layouts, focus sessions.

Rules kept throughout:
  * Nothing here overwrites or deletes the person's files: every output is a
    NEW file next to its source (or in Documents\\Eva), with a free name.
  * Email and calendar never send anything: they open a ready draft in the
    person's own mail or calendar app, and the person presses Send / Save.
  * PC care only reports; clean-up is the person's call (or Windows' own
    Disk Cleanup, which asks them itself).
Windows-only parts degrade to a clear message elsewhere.
"""
import hashlib
import json
import os
import re
import shutil
import time
import urllib.parse
from datetime import datetime, timedelta

from logger import get_logger

log = get_logger(__name__)

EVA_DOCS = os.path.join(os.path.expanduser("~"), "Documents", "Eva")


def _p(path):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(str(path or "").strip().strip('"'))))


def _free_name(path):
    """path, or 'name (2).ext', 'name (3).ext'... -- never an existing file."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


def _open(path):
    try:
        os.startfile(path)                      # noqa: S606 -- Windows: open with its app
        return True
    except Exception:
        return False


# ---- documents -------------------------------------------------------------------------
def merge_pdfs(paths, output_name: str = ""):
    """Joins PDFs in the order given into a new file beside the first one."""
    from pypdf import PdfReader, PdfWriter
    files = [_p(x) for x in (paths or [])]
    missing = [f for f in files if not os.path.isfile(f)]
    if len(files) < 2:
        return {"ok": False, "error": "give at least two PDF files"}
    if missing:
        return {"ok": False, "error": f"not found: {missing[0]}"}
    writer = PdfWriter()
    pages = 0
    for f in files:
        for page in PdfReader(f).pages:
            writer.add_page(page)
            pages += 1
    name = (output_name or f"{os.path.splitext(os.path.basename(files[0]))[0]} - merged").removesuffix(".pdf")
    out = _free_name(os.path.join(os.path.dirname(files[0]), name + ".pdf"))
    with open(out, "wb") as fh:
        writer.write(fh)
    return {"ok": True, "verified": os.path.isfile(out), "path": out, "pages": pages}


def split_pdf(path, pages: str):
    """Copies the pages asked for ('1-3, 7') into a new PDF beside the original."""
    from pypdf import PdfReader, PdfWriter
    src = _p(path)
    if not os.path.isfile(src):
        return {"ok": False, "error": f"not found: {src}"}
    reader = PdfReader(src)
    total = len(reader.pages)
    wanted = []
    for part in re.split(r"[,\s]+", str(pages or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            return {"ok": False, "error": f"cannot read the page range {part!r} (use e.g. 1-3, 7)"}
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        wanted += [i for i in range(a, b + 1) if 1 <= i <= total]
    if not wanted:
        return {"ok": False, "error": f"no pages in range (the file has {total})"}
    writer = PdfWriter()
    for i in wanted:
        writer.add_page(reader.pages[i - 1])
    out = _free_name(os.path.splitext(src)[0] + f" - pages {pages.replace(' ', '')}.pdf")
    with open(out, "wb") as fh:
        writer.write(fh)
    return {"ok": True, "verified": os.path.isfile(out), "path": out, "pages": len(wanted)}


def word_to_pdf(path):
    """Saves a .docx/.doc as PDF beside it, using Microsoft Word if installed."""
    src = _p(path)
    if not os.path.isfile(src):
        return {"ok": False, "error": f"not found: {src}"}
    out = _free_name(os.path.splitext(src)[0] + ".pdf")
    try:
        import comtypes.client
        word = comtypes.client.CreateObject("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(src, ReadOnly=True)
            doc.SaveAs(out, FileFormat=17)          # wdFormatPDF
            doc.Close(False)
        finally:
            word.Quit()
    except Exception as e:
        return {"ok": False, "error": f"Microsoft Word is needed for this and could not be used ({e}). "
                                      "LibreOffice or Word's own 'Save as PDF' can do it."}
    return {"ok": True, "verified": os.path.isfile(out), "path": out}


def pdf_tables_to_csv(path):
    """Writes the text of each page as rows of a CSV beside the PDF -- a
    starting point for tables (columns split on runs of spaces)."""
    from pypdf import PdfReader
    import csv
    src = _p(path)
    if not os.path.isfile(src):
        return {"ok": False, "error": f"not found: {src}"}
    rows = []
    for n, page in enumerate(PdfReader(src).pages, 1):
        for line in (page.extract_text() or "").splitlines():
            cells = [c for c in re.split(r"\s{2,}|\t", line.strip()) if c]
            if len(cells) >= 2:
                rows.append([n] + cells)
    if not rows:
        return {"ok": False, "error": "no table-like lines found (a scanned PDF has no text to read)"}
    out = _free_name(os.path.splitext(src)[0] + " - tables.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerows(rows)
    return {"ok": True, "verified": True, "path": out, "rows": len(rows),
            "note": "opens in Excel; column splits are a best guess from spacing"}


# ---- PC care (report only) ---------------------------------------------------------------
def disk_space():
    out = []
    drives = [f"{c}:\\" for c in "CDEFGHIJ"] if os.name == "nt" else ["/"]
    for d in drives:
        if os.path.exists(d):
            try:
                u = shutil.disk_usage(d)
                out.append({"drive": d, "free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1),
                            "used_percent": round(100 * u.used / u.total)})
            except OSError:
                pass
    low = [x["drive"] for x in out if x["used_percent"] >= 90]
    return {"ok": True, "drives": out, "low": low}


def largest_files(folder: str = "~", limit: int = 15):
    root = _p(folder)
    if not os.path.isdir(root):
        return {"ok": False, "error": f"not a folder: {root}"}
    found, scanned, deadline = [], 0, time.monotonic() + 20
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("AppData", "node_modules", "$Recycle.Bin")]
        for n in names:
            fp = os.path.join(base, n)
            try:
                found.append((os.path.getsize(fp), fp))
            except OSError:
                pass
            scanned += 1
        if time.monotonic() > deadline:
            break
    found.sort(reverse=True)
    limit = max(1, min(50, int(limit or 15)))
    return {"ok": True, "folder": root, "scanned": scanned, "complete": time.monotonic() <= deadline,
            "files": [{"path": p, "mb": round(s / 1e6, 1)} for s, p in found[:limit]]}


def duplicate_files(folder: str, limit: int = 20):
    """Groups of identical files (same size and contents)."""
    root = _p(folder)
    if not os.path.isdir(root):
        return {"ok": False, "error": f"not a folder: {root}"}
    by_size = {}
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            fp = os.path.join(base, n)
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            if size > 1024:
                by_size.setdefault(size, []).append(fp)
    groups = []
    for size, files in by_size.items():
        if len(files) < 2:
            continue
        by_hash = {}
        for fp in files:
            try:
                h = hashlib.sha1()
                with open(fp, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                by_hash.setdefault(h.hexdigest(), []).append(fp)
            except OSError:
                pass
        for same in by_hash.values():
            if len(same) > 1:
                groups.append({"mb_each": round(size / 1e6, 2), "files": same})
    groups.sort(key=lambda g: -g["mb_each"] * (len(g["files"]) - 1))
    wasted = sum(g["mb_each"] * (len(g["files"]) - 1) for g in groups)
    return {"ok": True, "groups": groups[:max(1, min(50, int(limit or 20)))], "wasted_mb": round(wasted, 1),
            "note": "nothing was deleted; the person decides which copies to remove"}


def startup_apps():
    """Programs that start with Windows (registry Run keys and Startup folders)."""
    out = []
    if os.name == "nt":
        import winreg
        for hive, hname in ((winreg.HKEY_CURRENT_USER, "you"), (winreg.HKEY_LOCAL_MACHINE, "all users")):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        out.append({"name": name, "command": str(value)[:200], "for": hname})
                        i += 1
            except OSError:
                pass
        for folder in (os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),):
            if os.path.isdir(folder):
                for n in os.listdir(folder):
                    out.append({"name": os.path.splitext(n)[0], "command": os.path.join(folder, n), "for": "you"})
    return {"ok": True, "apps": out,
            "note": "to turn one off, the person can use Task Manager > Startup apps (launch_app('taskmgr'))"}


def battery_status():
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if b is None:
        return {"ok": True, "battery": None, "note": "this PC has no battery"}
    mins = None if b.secsleft in (None, -1, -2) or b.secsleft < 0 else int(b.secsleft / 60)
    return {"ok": True, "percent": round(b.percent), "plugged_in": bool(b.power_plugged), "minutes_left": mins}


# ---- email and calendar: drafts the person sends themselves ------------------------------
def draft_email(to: str = "", subject: str = "", body: str = "", cc: str = ""):
    """Opens a new message in the person's mail app, filled in. Nothing is sent."""
    q = {k: v for k, v in (("subject", subject), ("body", body), ("cc", cc)) if v}
    url = f"mailto:{urllib.parse.quote(to or '', safe='@,')}"
    if q:
        url += "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)
    if len(url) > 1900:                         # mail apps cut long mailto links
        path = _free_name(os.path.join(EVA_DOCS, f"Email draft - {re.sub(r'[^A-Za-z0-9 ]', '', subject)[:40] or 'untitled'}.txt"))
        os.makedirs(EVA_DOCS, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"To: {to}\nCc: {cc}\nSubject: {subject}\n\n{body}\n")
        _open(path)
        url = f"mailto:{urllib.parse.quote(to or '', safe='@,')}?" + urllib.parse.urlencode(
            {"subject": subject, "body": f"(The full text is in {path} -- paste it here.)"}, quote_via=urllib.parse.quote)
    ok = _open(url)
    return {"ok": ok, "verified": False, "opened": "the mail app with a draft" if ok else None,
            "note": "the person reviews and presses Send; nothing was sent" if ok else
                    "no mail app answered the mailto link -- set a default mail app in Windows settings"}


def add_calendar_event(title: str, start: str, minutes: int = 60, location: str = "", notes: str = ""):
    """Makes the event as an .ics file and opens it in the person's calendar
    app, which shows it for them to save. start: 'YYYY-MM-DD HH:MM' local time."""
    try:
        when = datetime.strptime(str(start).strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
    except ValueError:
        return {"ok": False, "error": "start must be 'YYYY-MM-DD HH:MM' in the person's local time"}
    end = when + timedelta(minutes=max(5, int(minutes or 60)))
    fmt = "%Y%m%dT%H%M%S"
    esc = lambda s: str(s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")  # noqa: E731
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Eva//Desktop//EN", "BEGIN:VEVENT",
        f"UID:{int(time.time() * 1000)}@eva", f"DTSTAMP:{datetime.utcnow().strftime(fmt)}Z",
        f"DTSTART:{when.strftime(fmt)}", f"DTEND:{end.strftime(fmt)}", f"SUMMARY:{esc(title)}",
        f"LOCATION:{esc(location)}", f"DESCRIPTION:{esc(notes)}", "END:VEVENT", "END:VCALENDAR", ""])
    folder = os.path.join(EVA_DOCS, "events")
    os.makedirs(folder, exist_ok=True)
    path = _free_name(os.path.join(folder, f"{when.strftime('%Y-%m-%d %H%M')} - {re.sub(r'[^A-Za-z0-9 ]', '', title)[:40]}.ics"))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(ics)
    ok = _open(path)
    return {"ok": True, "verified": False, "path": path, "opened": ok,
            "note": "the calendar app shows the event; the person saves it" if ok else
                    "the .ics file is ready; double-clicking it adds it to the calendar"}


# ---- window layouts ----------------------------------------------------------------------
def _layouts_path():
    return os.path.join(os.path.expanduser("~"), ".eva", "layouts.json")


def _load_layouts():
    try:
        with open(_layouts_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _exe_of(hwnd):
    try:
        import ctypes
        import psutil
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return psutil.Process(pid.value).exe()
    except Exception:
        return ""


def _window_state(hwnd):
    import ctypes
    u = ctypes.windll.user32
    if u.IsIconic(ctypes.c_void_p(hwnd)):
        return "minimized"
    if u.IsZoomed(ctypes.c_void_p(hwnd)):
        return "maximized"
    return "normal"


def save_layout(name: str):
    """Remembers where every open window is (app, position, size, state)."""
    import desktop_control as dc
    name = re.sub(r"[^a-z0-9 -]", "", str(name or "").lower()).strip()
    if not name:
        return {"ok": False, "error": "give the layout a name, like 'coding' or 'writing'"}
    wins = []
    for w in dc.list_windows().get("windows", []):
        rect = dc.get_window_rect(w["handle"])
        if not rect:
            continue
        state = _window_state(w["handle"])
        if state == "minimized" or rect["right"] - rect["left"] < 50:
            continue                                  # minimised windows report no useful place
        wins.append({"title": w["title"], "exe": _exe_of(w["handle"]), "state": state,
                     "x": rect["left"], "y": rect["top"], "width": rect["right"] - rect["left"],
                     "height": rect["bottom"] - rect["top"]})
    if not wins:
        return {"ok": False, "error": "no windows are open to remember"}
    layouts = _load_layouts()
    layouts[name] = {"saved": time.strftime("%Y-%m-%d %H:%M"), "windows": wins}
    os.makedirs(os.path.dirname(_layouts_path()), exist_ok=True)
    with open(_layouts_path(), "w", encoding="utf-8") as fh:
        json.dump(layouts, fh, indent=2)
    return {"ok": True, "name": name, "windows": len(wins), "apps": sorted({os.path.basename(w["exe"]) for w in wins if w["exe"]})}


def list_layouts():
    return {"ok": True, "layouts": {k: {"saved": v.get("saved"), "windows": len(v.get("windows", []))}
                                    for k, v in _load_layouts().items()}}


def restore_layout(name: str):
    """Puts windows back where a saved layout had them, starting apps that are
    not running. Returns what it placed, started and could not find."""
    import desktop_control as dc
    layout = _load_layouts().get(re.sub(r"[^a-z0-9 -]", "", str(name or "").lower()).strip())
    if not layout:
        return {"ok": False, "error": f"no layout called {name!r}", "layouts": list(_load_layouts())}
    placed, started, missing, used = [], [], [], set()

    def open_windows():
        return [(w["handle"], w["title"], _exe_of(w["handle"])) for w in dc.list_windows().get("windows", [])]
    current = open_windows()
    for want in layout["windows"]:
        match = next((h for h, t, e in current if h not in used and e and e == want["exe"] and t == want["title"]), None) \
            or next((h for h, t, e in current if h not in used and e and e == want["exe"]), None)
        if match is None and want["exe"] and os.path.isfile(want["exe"]):
            dc.launch_app(want["exe"])
            started.append(os.path.basename(want["exe"]))
            time.sleep(1.5)
            current = open_windows()
            match = next((h for h, t, e in current if h not in used and e == want["exe"]), None)
        if match is None:
            missing.append(want["title"][:60])
            continue
        used.add(match)
        dc.restore_window(match)
        dc.move_window(match, want["x"], want["y"])
        dc.resize_window(match, want["width"], want["height"])
        if want["state"] == "maximized":
            dc.maximize_window(match)
        elif want["state"] == "minimized":
            dc.minimize_window(match)
        placed.append(want["title"][:60])
    return {"ok": bool(placed), "placed": placed, "started": started, "not_found": missing}


# ---- focus sessions ---------------------------------------------------------------------
_focus = {"handles": [], "until": None}


def focus_start(minutes: int = 25, keep: str = ""):
    """Minimises every window except the ones named in keep (comma-separated
    words from their titles, e.g. 'word, notes'), remembers them, and opens
    Windows' notification settings so the person can turn on Do not disturb.
    focus_end puts everything back."""
    import desktop_control as dc
    words = [w.strip().lower() for w in str(keep or "").split(",") if w.strip()]
    hidden = []
    for w in dc.list_windows().get("windows", []):
        title = (w["title"] or "").lower()
        if words and any(k in title for k in words):
            continue
        if _window_state(w["handle"]) == "minimized":
            continue
        r = dc.minimize_window(w["handle"]) or {}
        if r.get("ok", True):
            hidden.append(w["handle"])
    _focus.update(handles=hidden, until=time.time() + 60 * max(1, int(minutes or 25)))
    _open("ms-settings:notifications")
    return {"ok": True, "minimized": len(hidden), "minutes": int(minutes or 25),
            "note": "schedule_reminder can say when the time is up; focus_end brings the windows back. "
                    "Do not disturb is the person's switch in the settings page just opened."}


def focus_end():
    import desktop_control as dc
    back = 0
    for h in _focus["handles"]:
        r = dc.restore_window(h) or {}
        if r.get("ok", True):
            back += 1
    _focus.update(handles=[], until=None)
    return {"ok": True, "restored": back}
