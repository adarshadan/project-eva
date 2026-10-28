"""
Eva -- resolving "open Brave" / "open Spotify" / "open Word" to something
that can actually be started.

eva.log 2026-09-30 17:44: launch_app('brave') failed with WinError 2 because
Popen() only finds programs on PATH. Brave, Chrome, Spotify, Word and most
installed programs are NOT on PATH, so anything not in the short list the
model happened to know failed -- which is what "she only does what she was
pre-configured with" looked like from outside. The model then guessed a full
path and got lucky.

resolve() tries, in order, what a person would try:
  1. the name as a path; PATH lookup (shutil.which)
  2. the Windows "App Paths" registry keys (how Start/Run find installed apps)
  3. Start Menu shortcuts, best fuzzy name match
  4. common install folders (Program Files, LocalAppData) one/two levels deep
  5. packaged (Store) apps by display name, via Get-StartApps
Every step is best-effort and isolated: one failing never hides the next.

Pure helpers (_norm, _score, best_match) are platform-independent so they can
be tested anywhere; the Windows-only readers are guarded.
"""
import glob
import os
import re
import shutil
import subprocess
import sys

from logger import get_logger

log = get_logger(__name__)

# Names a person says vs the executable/shortcut name. Only for names that
# differ; everything else is found by search, not by this table.
_ALIASES = {
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "outlook": "outlook", "edge": "msedge", "chrome": "chrome",
    "google chrome": "chrome", "brave": "brave", "firefox": "firefox",
    "vs code": "code", "vscode": "code", "visual studio code": "code",
    "notepad": "notepad", "calculator": "calc", "paint": "mspaint",
    "command prompt": "cmd", "task manager": "taskmgr",
}


# Windows' own apps and Settings pages have no Start-menu shortcut to find, so
# a fuzzy search found the wrong thing ("settings" -> "WSL Settings", eva.log
# 2026-10-10). These names open the real thing directly.
SYSTEM_TARGETS = {
    "settings": "ms-settings:", "windows settings": "ms-settings:", "pc settings": "ms-settings:",
    "windows update": "ms-settings:windowsupdate", "check for updates": "ms-settings:windowsupdate",
    "update settings": "ms-settings:windowsupdate", "updates": "ms-settings:windowsupdate",
    "bluetooth": "ms-settings:bluetooth", "bluetooth settings": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi", "wi-fi": "ms-settings:network-wifi", "network settings": "ms-settings:network",
    "display settings": "ms-settings:display", "sound settings": "ms-settings:sound",
    "apps settings": "ms-settings:appsfeatures", "installed apps": "ms-settings:appsfeatures",
    "default apps": "ms-settings:defaultapps", "notifications": "ms-settings:notifications",
    "power settings": "ms-settings:powersleep", "battery settings": "ms-settings:batterysaver",
    "storage settings": "ms-settings:storagesense", "privacy settings": "ms-settings:privacy",
    "date and time": "ms-settings:dateandtime", "language settings": "ms-settings:regionlanguage",
    "personalization": "ms-settings:personalization", "background settings": "ms-settings:personalization-background",
    "mouse settings": "ms-settings:mousetouchpad", "keyboard settings": "ms-settings:typing",
    "microsoft store": "ms-windows-store:", "store": "ms-windows-store:",
    "snipping tool": "ms-screenclip:", "screen snip": "ms-screenclip:",
    "control panel": "control.exe", "file explorer": "explorer.exe", "explorer": "explorer.exe",
    "downloads folder": "shell:Downloads", "documents folder": "shell:Personal", "desktop folder": "shell:Desktop",
}
# Address schemes launch_app may open directly (anything else is refused).
URI_SCHEMES = ("ms-settings", "ms-windows-store", "ms-screenclip", "ms-photos", "microsoft.windows.camera",
               "mailto", "http", "https", "shell", "calculator")


def uri_target(raw: str):
    """The raw text as an allowed address (ms-settings:windowsupdate,
    mailto:...), or None. A drive path such as C:\\x is never one."""
    m = re.match(r"^([a-z][a-z0-9+.-]+):", (raw or "").strip(), re.I)
    if not m or len(m.group(1)) < 2:
        return None
    return raw.strip() if m.group(1).lower() in URI_SCHEMES else None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _score(query: str, candidate: str) -> int:
    """Higher is better; 0 means unrelated. Exact > prefix > word > substring."""
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return 0
    if q == c:
        return 100
    if c.startswith(q):
        return 80
    if q in c.split():
        return 70
    if q in c:
        return 50
    q_words = set(q.split())
    if q_words and q_words <= set(c.split()):
        return 40
    return 0


def best_match(query: str, candidates):
    """candidates: iterable of (display_name, payload). Returns
    (payload, display_name) of the best match, preferring shorter names on a
    tie (so 'Brave' beats 'Brave Uninstall'), or None."""
    best, best_key = None, None
    for name, payload in candidates:
        s = _score(query, name)
        if s <= 0:
            continue
        if "uninstall" in _norm(name):
            s -= 30
            if s <= 0:
                continue
        key = (s, -len(name))
        if best_key is None or key > best_key:
            best, best_key = (payload, name), key
    return best


def _app_paths_registry(exe: str):
    if sys.platform != "win32":
        return None
    try:
        import winreg
        sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}.exe"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    val, _ = winreg.QueryValueEx(key, None)
                    val = os.path.expandvars(str(val).strip('"'))
                    if os.path.exists(val):
                        return val
            except OSError:
                continue
    except Exception as e:
        log.debug(f"App Paths lookup failed: {e}")
    return None


def _start_menu_shortcuts():
    out = []
    roots = [os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
             os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".lnk"):
                    out.append((os.path.splitext(f)[0], os.path.join(dirpath, f)))
    return out


def _install_dir_hits(exe: str):
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", "")]
    pats = []
    for r in filter(None, roots):
        pats += [os.path.join(r, "*", f"{exe}.exe"),
                 os.path.join(r, "*", "*", f"{exe}.exe"),
                 os.path.join(r, "*", "*", "Application", f"{exe}.exe"),
                 os.path.join(r, "Programs", "*", f"{exe}.exe")]
    hits = []
    for p in pats:
        hits += glob.glob(p)
    return hits


def _store_apps():
    """(display name, AppID) for packaged apps. Slow (~1s), so last."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        apps = []
        for line in out.splitlines():
            if "|" in line:
                name, appid = line.rsplit("|", 1)
                apps.append((name.strip(), appid.strip()))
        return apps
    except Exception as e:
        log.debug(f"Get-StartApps failed: {e}")
        return []


def resolve(name: str):
    """Returns {"kind": "path"|"lnk"|"appid", "target": str, "via": str} or
    {"kind": None, "candidates": [names]} when nothing matched."""
    raw = (name or "").strip().strip('"')
    if not raw:
        return {"kind": None, "candidates": []}
    uri = uri_target(raw)
    if uri:
        return {"kind": "uri", "target": uri, "via": "address"}
    system = SYSTEM_TARGETS.get(_norm(raw))
    if system:
        kind = "uri" if uri_target(system) else "path"
        target = system if kind == "uri" else (shutil.which(system) or system)
        return {"kind": kind, "target": target, "via": "Windows"}
    expanded = os.path.expandvars(os.path.expanduser(raw))
    if os.path.exists(expanded):
        return {"kind": "path", "target": expanded, "via": "given path"}
    key = _ALIASES.get(_norm(raw), _norm(raw)).replace(" ", "")
    on_path = shutil.which(raw) or shutil.which(key)
    if on_path:
        return {"kind": "path", "target": on_path, "via": "PATH"}
    reg = _app_paths_registry(key)
    if reg:
        return {"kind": "path", "target": reg, "via": "App Paths"}
    shortcuts = _start_menu_shortcuts()
    hit = best_match(raw, shortcuts) or best_match(key, shortcuts)
    hit_score = max(_score(raw, hit[1]), _score(key, hit[1])) if hit else 0
    if hit and hit_score >= 100:
        return {"kind": "lnk", "target": hit[0], "via": f"Start Menu ({hit[1]})"}
    for p in _install_dir_hits(key):
        return {"kind": "path", "target": p, "via": "install folder"}
    # A partial Start-menu match must not beat an exact installed app.
    store = _store_apps()
    shit = best_match(raw, store)
    if shit and _score(raw, shit[1]) > hit_score:
        return {"kind": "appid", "target": shit[0], "via": f"installed app ({shit[1]})"}
    if hit:
        return {"kind": "lnk", "target": hit[0], "via": f"Start Menu ({hit[1]})"}
    pool = [n for n, _ in shortcuts] + [n for n, _ in store]
    close = sorted({n for n in pool if _score(raw[:3], n) > 0})[:8]
    return {"kind": None, "candidates": close}


def start(resolved: dict, argument: str = ""):
    """Starts a resolved target. Raises on failure."""
    kind, target = resolved["kind"], resolved["target"]
    flags = 0
    if kind == "uri":
        os.startfile(target)  # noqa -- Windows only: opens Settings pages, mailto:, shell: folders
        return
    if kind == "appid":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
    elif kind == "lnk" and not argument:
        os.startfile(target)  # noqa -- Windows only; resolve() yields lnk only there
    elif kind == "lnk":
        subprocess.Popen(["cmd", "/c", "start", "", target, argument], shell=False)
    else:
        if argument:
            subprocess.Popen([target, argument], shell=False, creationflags=flags)
        elif target.lower().endswith(".exe"):
            subprocess.Popen([target], shell=False, creationflags=flags)
        else:
            os.startfile(target)  # documents: the file's own default program
