"""Has requirements*.txt changed since the last successful install? (run.bat asks before running pip)

2026-10-28 "EVA boot time is very high": run.bat ran pip twice on every start, each time asking the package
index over the network, before Eva even began. Now pip runs only when a requirements file changed (or the
venv is new); its fingerprint is kept in venv\\.eva_deps.

    python tools/deps_sync.py check     exit 0: up to date, 1: install needed
    python tools/deps_sync.py mark      after a successful install
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ("requirements.txt", "requirements-play.txt")
STAMP = os.path.join(HERE, "venv", ".eva_deps")


def fingerprint(root=HERE):
    h = hashlib.sha256()
    for name in FILES:
        try:
            with open(os.path.join(root, name), "rb") as f:
                h.update(name.encode() + b"\0" + f.read())
        except OSError:
            h.update(name.encode() + b"\0missing")
    h.update(sys.version.encode())
    return h.hexdigest()


def up_to_date(stamp=STAMP, root=HERE):
    try:
        with open(stamp, encoding="utf-8") as f:
            return f.read().strip() == fingerprint(root)
    except OSError:
        return False


def mark(stamp=STAMP, root=HERE):
    os.makedirs(os.path.dirname(stamp), exist_ok=True)
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(fingerprint(root))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "mark":
        mark()
        sys.exit(0)
    sys.exit(0 if up_to_date() else 1)
