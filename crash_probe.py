"""Eva -- after a crash, ask Windows what crashed.

A native crash (eva.log 2026-10-11 15:34: over a playing video, no Python
error, the log just stops) leaves faulthandler's stacks in ~/.eva/crash.log,
which say what PYTHON was doing -- not which DLL faulted. Windows records that
in its Application log (event 1000, "Application Error": faulting module,
exception code). On the start after an unclean exit Eva reads the last few of
those for python and writes them into eva.log, so the cause is in the log the
person sends: a GPU driver module (nvwgf2um*, igd*, aticfx*, amdxx*), Qt,
Chromium, or something else.

parse_events() is pure and tested; recent_crashes() runs wevtutil (Windows
only, read-only, 5 s limit).
"""
import re
import subprocess
import sys

from logger import get_logger

log = get_logger(__name__)

GPU_DRIVER_MODULES = re.compile(r"^(nvwgf2um|nvd3dum|nvoglv|nvldumd|nvumdshim|nvcuda|igd10|igd12|igdumd|igc|igxe|"
                                r"aticfx|atidxx|atiumd|amdxx|amdvlk|d3d11|d3d12|dxgi|dxcore|d3d10warp)", re.I)
OURS = re.compile(r"^(python|pythonw|eva)[^\s,]*\.exe$", re.I)


def pe_exports(path):
    """[(rva, name)] sorted, from a PE (DLL) file's export table. Pure Python:
    enough to turn "Qt6Core.dll + fault offset" into the nearest exported
    function, which names what crashed without symbols or a debugger."""
    import struct
    with open(path, "rb") as f:
        data = f.read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        return []
    nsec, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (112 if magic == 0x20B else 96)          # data directories (PE32+ / PE32)
    exp_rva, exp_size = struct.unpack_from("<II", data, dd)
    secs = []
    sec = opt + opt_size
    for i in range(nsec):
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, sec + 40 * i + 8)
        secs.append((va, max(vsize, rsize), raw))

    def off(rva):
        for va, size, raw in secs:
            if va <= rva < va + size:
                return raw + rva - va
        raise ValueError("rva outside sections")
    if not exp_rva:
        return []
    e = off(exp_rva)
    nfuncs, nnames, a_funcs, a_names, a_ords = struct.unpack_from("<IIIII", data, e + 20)
    funcs = [struct.unpack_from("<I", data, off(a_funcs) + 4 * i)[0] for i in range(nfuncs)]
    out = []
    for i in range(nnames):
        name_rva = struct.unpack_from("<I", data, off(a_names) + 4 * i)[0]
        ordinal = struct.unpack_from("<H", data, off(a_ords) + 2 * i)[0]
        o = off(name_rva)
        name = data[o:data.index(b"\0", o)].decode("ascii", "replace")
        rva = funcs[ordinal]
        if not (exp_rva <= rva < exp_rva + exp_size):    # skip forwarders
            out.append((rva, name))
    return sorted(out)


def undecorate(name):
    """MSVC C++ name -> readable, with dbghelp on Windows; as is elsewhere."""
    if sys.platform != "win32" or not name.startswith("?"):
        return name
    try:
        import ctypes
        buf = ctypes.create_string_buffer(1024)
        n = ctypes.windll.dbghelp.UnDecorateSymbolName(name.encode(), buf, 1024, 0x1000)   # UNDNAME_NAME_ONLY
        return buf.value.decode() if n else name
    except Exception:
        return name


def nearest_symbol(path, offset):
    """"Qt6Core.dll+0x1234" -> "QObject::killTimer + 0x2c" (nearest export at
    or below the offset), or None. A large distance means the fault is in an
    internal function after that export -- said so."""
    try:
        exports = pe_exports(path)
    except Exception:
        return None
    best = None
    for rva, name in exports:
        if rva <= offset:
            best = (rva, name)
        else:
            break
    if not best:
        return None
    d = offset - best[0]
    return f"{undecorate(best[1])} + 0x{d:x}" + (" (far from it: an internal function after it)" if d > 0x4000 else "")


def parse_events(text: str):
    """Faulting-application events for Eva's python, newest first."""
    out = []
    for block in re.split(r"\n(?=Event\[\d+\])", text or ""):
        app = re.search(r"Faulting application name:\s*([^,\r\n]+)", block)
        mod = re.search(r"Faulting module name:\s*([^,\r\n]+)", block)
        if not app or not mod or not OURS.match(app.group(1).strip()):
            continue
        code = re.search(r"Exception code:\s*(0x[0-9a-fA-F]+)", block)
        date = re.search(r"Date:\s*(\S+)", block)
        offset = re.search(r"Fault offset:\s*(0x[0-9a-fA-F]+)", block)
        mpath = re.search(r"Faulting module path:\s*([^\r\n]+)", block)
        mver = re.search(r"Faulting module name:[^,\r\n]+,\s*version:\s*([^,\r\n]+)", block)
        module = mod.group(1).strip()
        out.append({"app": app.group(1).strip(), "module": module, "code": code.group(1) if code else "",
                    "when": date.group(1) if date else "", "gpu_driver": bool(GPU_DRIVER_MODULES.match(module)),
                    "offset": int(offset.group(1), 16) if offset else None,
                    "path": mpath.group(1).strip() if mpath else "", "version": mver.group(1).strip() if mver else ""})
    return out


def recent_crashes(limit=3):
    if sys.platform != "win32":
        return []
    query = "*[System[Provider[@Name='Application Error'] and (EventID=1000) and TimeCreated[timediff(@SystemTime) <= 172800000]]]"
    try:
        p = subprocess.run(["wevtutil", "qe", "Application", f"/q:{query}", f"/c:{limit * 4}", "/rd:true", "/f:text"],
                           capture_output=True, text=True, timeout=5, creationflags=0x08000000)   # CREATE_NO_WINDOW
        return parse_events(p.stdout)[:limit]
    except Exception as e:
        log.info(f"Could not read the Windows error log: {e}")
        return []


def report_after_unclean_exit(limit=6):
    """Logs what Windows recorded about recent crashes -- module, offset, and
    the function at that offset. Run in a thread."""
    found = recent_crashes(limit)
    if not found:
        log.info("Windows' error log has no recent crash entry for Eva's python.")
        return found
    for f in found:
        where = ""
        if f.get("offset") is not None:
            sym = nearest_symbol(f["path"], f["offset"]) if f.get("path") else None
            where = f" at +0x{f['offset']:x}" + (f" = {sym}" if sym else "") + (f" (v{f['version']})" if f.get("version") else "")
        log.warning(f"Windows recorded a crash of {f['app']} at {f['when']}: faulting module {f['module']}{where} "
                    f"(exception {f['code'] or '?'})"
                    + (" -- a GPU DRIVER module: the graphics driver failed" if f["gpu_driver"] else "")
                    + (" -- Qt itself: usually a Qt object used or destroyed off the GUI thread"
                       if f["module"].lower().startswith("qt6") else ""))
    return found


def report_new_crashes(state_path=None, limit=8):
    """On every start: the crashes Windows recorded that have not been
    reported in eva.log yet (newest first), each with its function."""
    import json
    import os
    state_path = state_path or os.path.join(os.path.expanduser("~"), ".eva", "crash_probe.json")
    try:
        last = json.load(open(state_path, encoding="utf-8")).get("last", "")
    except (OSError, ValueError):
        last = ""
    found = [f for f in recent_crashes(limit) if f.get("when", "") > last]
    if not found:
        return []
    for f in found:
        where = ""
        if f.get("offset") is not None:
            sym = nearest_symbol(f["path"], f["offset"]) if f.get("path") else None
            where = f" at +0x{f['offset']:x}" + (f" = {sym}" if sym else "") + (f" (v{f['version']})" if f.get("version") else "")
        log.warning(f"Windows recorded a crash of {f['app']} at {f['when']}: faulting module {f['module']}{where} "
                    f"(exception {f['code'] or '?'})")
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        json.dump({"last": max(f.get("when", "") for f in found)}, open(state_path, "w", encoding="utf-8"))
    except OSError:
        pass
    return found
