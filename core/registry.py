"""Eva core -- the one tool registry.

Every capability Eva has is declared exactly once, with everything any part
of the system needs to know about it:

    what the model sees      name, description, parameters
    what runs                fn (bound at startup for tools that need the avatar)
    whether to ask first     effect (fixed, or computed from the arguments)
    how long it may take     deadline (seconds; None = no watchdog thread)
    how she performs it      surface, verb, locus, approach, embodied, describe
    what it feeds            recordable (teach-and-repeat), learnable (procedure
                             memory), web (its result carries third-party text)

Before this, the same facts lived in six hand-kept lists in five files, and a
tool missing from one of them failed silently -- it ran without a deadline, or
without being recorded, or motionless. Now the permission gate, the deadline
watchdog, the performer, the recorder, the audit log and the model's tool list
all read from here, and validate() reports any tool with no implementation.
"""
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from core.effects import Effect, Surface


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    required: list
    surface: Surface
    effect: Union[Effect, Callable[[dict], Effect]]
    fn: Optional[Callable] = None
    deadline: Optional[float] = None
    verb: str = "none"
    locus: str = "none"            # element | coords | window | foreground | none
    approach: str = "none"         # walk | gaze | none
    embodied: bool = True          # False: the tool IS the embodiment (movement, gestures)
    recordable: bool = False
    learnable: bool = False
    web: bool = False
    describe: Optional[Callable[[dict], Optional[str]]] = None
    record_step: Optional[Callable[[dict], dict]] = None   # recorder step shape
    tags: set = field(default_factory=set)
    checkpoint: bool = False       # snapshot what it changes first, so it can be undone
    # A connected service's tool brings its own JSON schema, used verbatim.
    parameters_override: Optional[dict] = None
    dynamic: bool = False          # added at runtime (connections), not declared in code

    def schema(self) -> dict:
        parameters = self.parameters_override or {"type": "object", "properties": self.params,
                                                  "required": list(self.required)}
        fn = {"name": self.name, "description": self.description, "parameters": parameters}
        return {"type": "function", "function": fn}


_TOOLS: dict = {}
# Connections add and remove tools from their own thread while the agent reads
# the list; every access goes through this lock.
_LOCK = threading.RLock()


def register(name, description, params=None, required=(), *, surface, effect, fn=None, **meta) -> Tool:
    with _LOCK:
        if name in _TOOLS:
            raise ValueError(f"tool {name!r} is declared twice")
        t = Tool(name=name, description=" ".join(description.split()), params=params or {},
                 required=list(required), surface=surface, effect=effect, fn=fn, **meta)
        _TOOLS[name] = t
        return t


def replace_dynamic(prefix: str, tools: list):
    """Swaps in the full set of runtime tools whose names start with prefix
    (one connection's tools), removing any it no longer offers."""
    with _LOCK:
        for name in [n for n, t in _TOOLS.items() if t.dynamic and n.startswith(prefix)]:
            del _TOOLS[name]
        for t in tools:
            if t.name in _TOOLS:
                raise ValueError(f"tool {t.name!r} clashes with an existing tool")
            t.dynamic = True
            _TOOLS[t.name] = t


def bind(name: str, fn: Callable):
    """Supplies the implementation for a tool declared without one (tools that
    need live objects such as the avatar widget, created at startup)."""
    with _LOCK:
        if name not in _TOOLS:
            raise KeyError(f"cannot bind unknown tool {name!r}")
        _TOOLS[name].fn = fn


def get(name: str) -> Optional[Tool]:
    with _LOCK:
        return _TOOLS.get(name)


def names() -> set:
    with _LOCK:
        return set(_TOOLS)


def all_tools():
    with _LOCK:
        return list(_TOOLS.values())


def schemas(exclude=()) -> list:
    return [t.schema() for t in all_tools() if t.name not in exclude and t.fn is not None]


def unbound() -> list:
    return sorted(t.name for t in all_tools() if t.fn is None)


def validate() -> list:
    """Problems that would otherwise surface as a puzzling failure mid-task."""
    problems = [f"tool {n!r} has no implementation bound" for n in unbound()]
    for t in all_tools():
        if t.parameters_override is not None:
            continue
        for r in t.required:
            if r not in t.params:
                problems.append(f"tool {t.name!r} requires {r!r} but does not declare it")
        for p, s in t.params.items():
            if "type" not in s and "enum" not in s:
                problems.append(f"tool {t.name!r} parameter {p!r} has no type")
    return problems


# ---- parameter helpers ------------------------------------------------------
def INT(desc=""):
    return {"type": "integer", **({"description": desc} if desc else {})}


def STR(desc="", enum=None, max_len=None):
    s = {"type": "string"}
    if desc:
        s["description"] = desc
    if enum:
        s["enum"] = list(enum)
    if max_len:
        s["maxLength"] = max_len
    return s


def NUM(desc=""):
    return {"type": "number", **({"description": desc} if desc else {})}


def BOOL(desc=""):
    return {"type": "boolean", **({"description": desc} if desc else {})}


def OBJ(desc=""):
    return {"type": "object", **({"description": desc} if desc else {})}


def ARR(item_type="string", desc=""):
    return {"type": "array", "items": {"type": item_type}, **({"description": desc} if desc else {})}


HANDLE = INT("The window's handle, from list_windows or get_foreground_app.")
