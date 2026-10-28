"""Eva core -- what a tool call can DO, which decides whether to ask.

The person's rule: act freely; ask only for DESTRUCTIVE actions and for
anything that SPENDS MONEY (config.CONFIRM_EFFECTS). Some effects depend on
the arguments -- overwriting a file is destructive, creating one is not -- so
an effect may be a function of the call's args. Money is detected separately,
from what a click or keystroke actually touches (permissions/finance_guard).
"""
import enum
import os

import command_policy


class Effect(enum.IntEnum):
    OBSERVE = 0      # reads only
    INTERACT = 1     # reversible UI interaction: click, type, scroll, focus
    MODIFY = 2       # changes data or system state, recoverably
    DESTROY = 3      # overwrites, deletes, or stops something with possible loss
    SENSITIVE = 4    # handles credentials or personal data
    EXTERNAL = 5     # leaves the machine (a message, an upload); not gated by default
    FINANCIAL = 6    # spends money, or is a step in a payment


class Surface(enum.Enum):
    SCREEN = "screen"
    WINDOW = "window"
    BROWSER = "browser"
    FILES = "files"
    SYSTEM = "system"
    TIME = "time"
    SELF = "self"


# Not ctrl+a: selecting loses nothing (eva.log 2026-10-17 12:06: two needless approval prompts for it).
DESTRUCTIVE_HOTKEYS = {"delete", "ctrl+x", "ctrl+shift+delete",
                       "alt+f4", "ctrl+w", "ctrl+q", "shift+delete"}


def hotkey_effect(args):
    keys = str(args.get("keys", "")).lower().replace(" ", "")
    return Effect.DESTROY if keys in DESTRUCTIVE_HOTKEYS else Effect.INTERACT


def write_effect(args):
    """Overwriting an existing file loses its old contents; creating or
    appending does not."""
    if str(args.get("mode", "create")) != "overwrite":
        return Effect.MODIFY
    raw = str(args.get("path", ""))
    try:
        from system_tools import _resolve      # the SAME path rules write_file uses
        target = _resolve(raw)
    except Exception:
        target = os.path.expanduser(raw)
    return Effect.DESTROY if os.path.exists(target) else Effect.MODIFY


def command_effect(args):
    argv, err = command_policy.parse(str(args.get("command", "")))
    if err or command_policy.is_denied(argv):
        return Effect.DESTROY            # refused by the tool itself; never quietly allowed
    return Effect.OBSERVE if command_policy.is_read_only(argv) else Effect.DESTROY


def element_effect(args):
    return Effect.OBSERVE if str(args.get("action", "auto")) == "get_state" else Effect.INTERACT


def confirm_effects():
    try:
        import config
        names = getattr(config, "CONFIRM_EFFECTS", ("DESTROY", "FINANCIAL"))
    except Exception:
        names = ("DESTROY", "FINANCIAL")
    return {Effect[n] for n in names}
