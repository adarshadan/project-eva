"""Eva -- classification of shell commands. Pure logic, no I/O.

run_command lets Eva reach the machine's back end (git, network checks,
package lists...). Handing an LLM a shell is the single most dangerous
capability in this project, especially one that also reads web pages, so
the policy is deliberately conservative and lives in one testable place:

  * read-only programs run without asking;
  * anything else is treated as potentially destructive and needs the
    person's confirmation (the framework gates it, see capabilities.py);
  * a short denylist is refused outright even with confirmation;
  * shell operators (pipes, redirection, chaining) are rejected, because
    the command is run WITHOUT a shell, one program at a time.

This is defence in depth, not a guarantee -- an allowlisted program can
still be pointed at a sensitive file. Output goes through the same
credential redaction as every other tool result.
"""
import os
import re
import shlex

# Programs whose arguments must all be flags (-x, /x). No positional
# targets, so they can only report, never be aimed at something.
_FLAGS_ONLY = {
    "whoami", "hostname", "ipconfig", "tasklist", "systeminfo", "netstat",
    "getmac", "uname", "uptime", "df", "free", "id", "date", "pwd",
}
# Read-only, but take a positional target (a host, a name, a folder).
_READ_WITH_TARGET = {"ping", "nslookup", "where", "which", "ls"}

_GIT_READ = {"status", "log", "diff", "branch", "rev-parse", "remote", "show",
             "ls-files", "describe", "blame", "shortlog", "tag"}
_PIP_READ = {"list", "show", "freeze", "check", "--version"}
_VERSION_ONLY = {"python", "python3", "py", "node"}
_VERSION_FLAGS = {"--version", "-v", "-V", "-VV"}

# Refused outright, whatever the person answers.
DENYLIST = {"format", "diskpart", "bcdedit", "vssadmin", "cipher", "sdelete",
            "mkfs", "dd", "shutdown", "reg", "wmic", "takeown", "icacls",
            "rd", "rmdir", "del", "erase"}

_SHELL_OPERATORS = re.compile(r"[|&;<>`]|\$\(")


def parse(command: str):
    """(argv, error). error is a human-readable string, or None."""
    if not command or not command.strip():
        return None, "empty command"
    if _SHELL_OPERATORS.search(command):
        return None, ("shell operators (| & ; < > `) are not supported -- commands run "
                      "one program at a time, without a shell")
    try:
        argv = shlex.split(command, posix=(os.name != "nt"))
    except ValueError as e:
        return None, f"could not parse the command: {e}"
    if not argv:
        return None, "empty command"
    return argv, None


def program_name(argv) -> str:
    base = os.path.basename(argv[0]).lower()
    return base[:-4] if base.endswith(".exe") else base


def is_denied(argv) -> bool:
    return program_name(argv) in DENYLIST


def is_read_only(argv) -> bool:
    """True only for commands that cannot change anything."""
    name, args = program_name(argv), argv[1:]
    if name in _FLAGS_ONLY:
        return all(a.startswith(("-", "/")) for a in args)
    if name in _READ_WITH_TARGET:
        return True
    if name == "git":
        # `git -c core.pager=...` and `--output=` can execute or write.
        if any(a.startswith(("-c", "--output", "--exec", "--upload-pack")) for a in args):
            return False
        return bool(args) and args[0] in _GIT_READ
    if name in ("pip", "pip3"):
        return bool(args) and args[0] in _PIP_READ
    if name in _VERSION_ONLY:
        return len(args) == 1 and args[0] in _VERSION_FLAGS
    if name == "npm":
        return bool(args) and args[0] in {"--version", "-v", "ls", "list"}
    return False
