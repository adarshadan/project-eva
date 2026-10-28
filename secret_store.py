"""Eva -- where the API key lives, so an update cannot wipe it and a log cannot leak it.

Two real problems this fixes (eva.log 2026-09-30):
  * the key was pasted into config.py, so replacing the Eva folder with a new
    zip silently reset it to the placeholder and every request failed with 401;
  * a key had already leaked once through a log.

Lookup order for get(): the environment variable, then the encrypted store.
The store is ~/.eva/secrets.bin, encrypted with Windows DPAPI
(CryptProtectData): only THIS Windows user on THIS machine can decrypt it, no
extra package is needed, and no key or password has to be managed. Idea taken
from Open Dots, which encrypts provider credentials at rest.

If DPAPI is unavailable (non-Windows, or it fails) set() REFUSES rather than
falling back to plaintext; the environment variable still works.

This module imports nothing from Eva (config imports it).

CLI:  python secret_store.py set NVIDIA_API_KEY | status | delete NVIDIA_API_KEY
"""
import json
import os
import sys

STORE_PATH = os.path.join(os.path.expanduser("~"), ".eva", "secrets.bin")
_ENTROPY = b"eva-avatar-secret-store-v1"       # binds the blob to this app, not a secret


class SecretStoreError(Exception):
    pass


# ---- DPAPI (Windows) --------------------------------------------------------
def _dpapi(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("encrypted storage needs Windows (DPAPI); use the environment variable")
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.POINTER(BLOB), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
    fn.restype = wintypes.BOOL

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    b_in, keep1 = blob(data)
    b_ent, keep2 = blob(_ENTROPY)
    b_out = BLOB()
    if not fn(ctypes.byref(b_in), None, ctypes.byref(b_ent), None, None, 0, ctypes.byref(b_out)):
        raise SecretStoreError(f"Windows could not {'encrypt' if protect else 'decrypt'} the secrets "
                               f"(error {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(b_out.pbData, b_out.cbData)
    finally:
        kernel32.LocalFree(b_out.pbData)
        del keep1, keep2


# overridable in tests
_protect = lambda b: _dpapi(b, True)      # noqa: E731
_unprotect = lambda b: _dpapi(b, False)   # noqa: E731


def _load() -> dict:
    try:
        with open(STORE_PATH, "rb") as f:
            blob = f.read()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise SecretStoreError(f"cannot read {STORE_PATH}: {e}")
    try:
        data = json.loads(_unprotect(blob).decode("utf-8"))
    except SecretStoreError:
        raise
    except Exception as e:
        raise SecretStoreError(f"{STORE_PATH} is unreadable (made by another Windows user/PC, or damaged): {e}")
    return data if isinstance(data, dict) else {}


def _save(data: dict):
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    blob = _protect(json.dumps(data).encode("utf-8"))
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, STORE_PATH)            # a crash mid-write cannot leave a half file


# ---- public API -------------------------------------------------------------
def get(name: str, default=None):
    """Environment variable first, then the encrypted store. Never raises."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        return _load().get(name) or default
    except Exception:
        return default


def set(name: str, value: str):
    value = (value or "").strip()
    if not value:
        raise SecretStoreError("empty value")
    data = _load()
    data[name] = value
    _save(data)


def delete(name: str) -> bool:
    data = _load()
    if name not in data:
        return False
    del data[name]
    _save(data)
    return True


def status(name: str) -> str:
    """Where the value comes from, never the value itself."""
    if os.environ.get(name):
        return "environment variable"
    try:
        return "encrypted store" if _load().get(name) else "not set"
    except Exception as e:
        return f"store unreadable: {e}"


def mask(value: str) -> str:
    return "" if not value else (value[:4] + "..." + value[-3:] if len(value) > 10 else "***")


def _cli(argv):
    import getpass
    cmd = argv[1] if len(argv) > 1 else "status"
    name = argv[2] if len(argv) > 2 else "NVIDIA_API_KEY"
    if cmd == "set":
        value = getpass.getpass(f"Paste the value for {name} (input is hidden), then Enter: ")
        set(name, value)
        print(f"Saved {name} to the encrypted store ({STORE_PATH}). Restart Eva.")
    elif cmd == "delete":
        print("Deleted." if delete(name) else "Nothing to delete.")
    else:
        print(f"{name}: {status(name)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_cli(sys.argv))
    except SecretStoreError as e:
        print(f"Failed: {e}")
        sys.exit(1)
