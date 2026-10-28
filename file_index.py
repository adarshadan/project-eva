"""Eva -- an index of the person's own files, for instant search.

eva.log 2026-10-11: search_files over C:\\ took seconds per call and was
repeated with different words. This keeps a small SQLite index of file NAMES
(not contents) in the person's own folders -- Desktop, Documents, Downloads,
Pictures, Videos, Music, OneDrive -- built in the background at start-up and
refreshed every 30 minutes, so find_files answers in milliseconds.

It never reads file contents and never leaves the PC (~/.eva/files.db).
Skipped: hidden/system folders, AppData, node_modules, .git, virtualenvs.
"""
import os
import re
import sqlite3
import sys
import threading
import time

from logger import get_logger

log = get_logger(__name__)

SKIP_DIRS = {"appdata", "node_modules", ".git", "__pycache__", "venv", ".venv", "$recycle.bin", ".cache",
             "site-packages", "temp", "tmp"}
KINDS = {
    "document": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "program": {".exe", ".msi", ".lnk", ".bat", ".apk"},
    "code": {".py", ".js", ".ts", ".html", ".css", ".java", ".cs", ".cpp", ".c", ".json", ".ps1"},
}
REFRESH_SECONDS = 1800
MAX_FILES = 400_000


def default_roots():
    home = os.path.expanduser("~")
    names = ["Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"]
    roots = [os.path.join(home, n) for n in names]
    for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(k):
            roots.append(os.environ[k])
    public_desktop = os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")
    roots.append(public_desktop)
    seen, out = set(), []
    for r in roots:
        a = os.path.abspath(r)
        if os.path.isdir(a) and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def kind_of(ext: str) -> str:
    ext = ext.lower()
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    return "other"


class FileIndex:
    def __init__(self, path=None, roots=None):
        self.path = path or os.path.join(os.path.expanduser("~"), ".eva", "files.db")
        self.roots = roots
        self._lock = threading.Lock()
        self.built_at = None
        self.count = 0
        self.building = False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, name TEXT, ext TEXT, "
                         "kind TEXT, size INTEGER, mtime REAL)")
        self._db.execute("CREATE INDEX IF NOT EXISTS files_name ON files(name)")
        self._db.commit()
        with self._lock:
            self.count = self._db.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def close(self):
        """Releases the database file. On Windows an open SQLite file cannot be moved or
        deleted (run_checks on Windows, 2026-10-17: WinError 32 on files.db)."""
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

    def build(self):
        """Walks the roots and replaces the index (on its own thread)."""
        self.building = True
        started, rows = time.monotonic(), []
        try:
            for root in (self.roots or default_roots()):
                for base, dirs, names in os.walk(root):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS]
                    for n in names:
                        fp = os.path.join(base, n)
                        try:
                            st = os.stat(fp)
                        except OSError:
                            continue
                        ext = os.path.splitext(n)[1].lower()
                        rows.append((fp, n.lower(), ext, kind_of(ext), st.st_size, st.st_mtime))
                        if len(rows) >= MAX_FILES:
                            break
                    if len(rows) >= MAX_FILES:
                        break
            with self._lock:
                self._db.execute("DELETE FROM files")
                self._db.executemany("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?)", rows)
                self._db.commit()
                self.count = len(rows)
            self.built_at = time.time()
            log.info(f"File index: {len(rows)} files in {time.monotonic() - started:.1f} s.")
        finally:
            self.building = False

    def start(self):
        def run():
            while True:
                try:
                    self.build()
                except Exception:
                    log.exception("File index build failed")
                time.sleep(REFRESH_SECONDS)
        threading.Thread(target=run, name="eva-file-index", daemon=True).start()
        return self

    def find(self, query: str = "", kind: str = "", limit: int = 20, newest: bool = False):
        words = [w for w in re.findall(r"[a-z0-9]+", str(query or "").lower()) if len(w) > 1]
        kind = str(kind or "").lower().strip()
        if not words and not kind:
            return {"ok": False, "error": "give a few words of the file name, or a kind (document, image...)"}
        where, args = [], []
        for w in words:
            where.append("name LIKE ?")
            args.append(f"%{w}%")
        if kind:
            if kind not in KINDS and kind != "other":
                return {"ok": False, "error": f"kind must be one of {', '.join(list(KINDS) + ['other'])}"}
            where.append("kind = ?")
            args.append(kind)
        order = "mtime DESC" if newest or not words else "LENGTH(name) ASC, mtime DESC"
        limit = max(1, min(100, int(limit or 20)))
        with self._lock:
            rows = self._db.execute(f"SELECT path, size, mtime FROM files WHERE {' AND '.join(where)} "
                                    f"ORDER BY {order} LIMIT ?", (*args, limit)).fetchall()
        out = {"ok": True, "results": [{"path": p, "kb": round(s / 1024, 1),
                                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(m))}
                                       for p, s, m in rows],
               "indexed_files": self.count}
        if self.building and not self.built_at:
            out["note"] = "the index is still being built; results may be incomplete -- search_files covers everything"
        elif not rows:
            out["note"] = ("nothing in your folders matches; the index covers Desktop, Documents, Downloads, "
                           "Pictures, Videos, Music and OneDrive -- use search_files for anywhere else")
        return out


# ---- Windows Search (the index Windows already keeps) -----------------------------------
# Answers in milliseconds for every indexed location (the whole user profile by
# default, plus whatever the person added in Indexing Options) and can search
# INSIDE documents. Eva's own index above stays as the fallback: the Windows
# Search service may be disabled, or a folder not indexed.
WS = {"ok": None}            # None = not tried yet, False = unavailable this session

_WS_CONN = "Provider=Search.CollatorDSO;Extended Properties='Application=Windows';"


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) > 1]


def _ws_sql(words, kind, limit, newest, contents_words):
    cond = ["SCOPE='file:'", "System.FileAttributes <> ALL BITWISE 0x2"]       # not hidden
    for w in words:                                                             # alnum only: safe to inline
        cond.append(f"System.FileName LIKE '%{w}%'")
    if kind:
        exts = ",".join(f"'{e}'" for e in sorted(KINDS.get(kind, ())))
        if exts:
            cond.append(f"System.FileExtension IN ({exts})")
    if contents_words:
        phrase = " AND ".join(f'"{w}"' for w in contents_words)
        cond.append(f"CONTAINS(System.Search.Contents, '{phrase}')")
    order = "System.DateModified DESC" if newest or not words else "System.Search.Rank DESC"
    return (f"SELECT TOP {int(limit)} System.ItemPathDisplay, System.Size, System.DateModified "
            f"FROM SystemIndex WHERE {' AND '.join(cond)} ORDER BY {order}")


def _ws_query(sql):
    """Rows (path, size, modified as datetime/str) from the Windows Search index.
    A module function so the checks can replace it."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open(_WS_CONN)
        try:
            rs = win32com.client.Dispatch("ADODB.Recordset")
            rs.Open(sql, conn)
            rows = []
            try:
                while not rs.EOF:
                    f = rs.Fields
                    rows.append((f.Item(0).Value, f.Item(1).Value, f.Item(2).Value))
                    rs.MoveNext()
            finally:
                rs.Close()
            return rows
        finally:
            conn.Close()
    finally:
        pythoncom.CoUninitialize()


def _stamp(v):
    try:
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d %H:%M")
        return str(v)[:16]
    except Exception:
        return ""


def windows_search(query="", kind="", limit=20, newest=False, contents=""):
    """Results from Windows' own index, or None when it cannot be used."""
    if WS["ok"] is False or (sys.platform != "win32" and _ws_query is _WS_REAL):
        return None
    words, cwords = _words(query), _words(contents)
    if not words and not kind and not cwords:
        return None
    try:
        t0 = time.monotonic()
        rows = _ws_query(_ws_sql(words, kind, limit, newest, cwords))
        WS["ok"] = True
    except Exception as e:
        WS["ok"] = False
        log.info(f"Windows Search unavailable ({e}); using Eva's own file index")
        return None
    log.info(f"Windows Search: {len(rows)} results in {(time.monotonic() - t0) * 1000:.0f} ms")
    # ADODB hands back sizes as Decimal (eva.log 2026-10-11 22:58: "Object of type Decimal is not
    # JSON serializable" ended the turn): plain numbers and text only.
    return [{"path": str(p), "kb": round(float(s or 0) / 1024, 1), "modified": _stamp(m)} for p, s, m in rows if p]


_WS_REAL = _ws_query
INDEX = None


def find_files(query: str = "", kind: str = "", limit: int = 20, newest: bool = False, contents: str = ""):
    kind = str(kind or "").lower().strip()
    if kind and kind not in KINDS and kind != "other":
        return {"ok": False, "error": f"kind must be one of {', '.join(list(KINDS) + ['other'])}"}
    limit = max(1, min(100, int(limit or 20)))
    ws = windows_search(query, kind if kind != "other" else "", limit, newest, contents)
    if ws:
        return {"ok": True, "results": ws, "source": "windows search"}
    if contents and not _words(query) and not kind:
        return {"ok": False, "error": "searching inside files needs the Windows Search service (it is off or "
                                      "unavailable); give words from the file NAME instead"}
    if INDEX is None:
        if ws == []:
            return {"ok": True, "results": [], "source": "windows search",
                    "note": "nothing indexed matches; search_files can look in a specific folder"}
        return {"ok": False, "error": "the file index is not running; use search_files"}
    out = INDEX.find(query, kind, limit, newest)
    if out.get("ok"):
        out["source"] = "eva index"
        if contents:
            out["note"] = (out.get("note", "") + " (matched by file name only: Windows Search, which can "
                           "look inside files, is unavailable)").strip()
    return out
