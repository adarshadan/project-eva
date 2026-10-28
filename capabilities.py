"""Eva -- capability lookups, answered from the one tool registry.

This module used to BE the manifest, a sixth hand-kept list beside brain.py's
schemas, main.py's implementations and deadline.py's timeouts. The facts now
live in core/registry.py, declared once per tool in core/toolset/; this file
keeps the lookup functions the permission gate and the performer already use.
"""
from core.effects import (DESTRUCTIVE_HOTKEYS, Effect, Surface,  # noqa: F401  (re-exported)
                          confirm_effects)
from core import registry

__all__ = ["DESTRUCTIVE_HOTKEYS", "Effect", "Surface", "confirm_effects", "get", "all_names",
           "effect_of", "is_gated", "describe"]


def _ensure_loaded():
    # The declarations register themselves on import.
    import core.toolset
    return core.toolset


def get(name: str):
    _ensure_loaded()
    return registry.get(name)


def all_names():
    _ensure_loaded()
    return registry.names()


def effect_of(name: str, args: dict = None) -> Effect:
    spec = get(name)
    if spec is None:
        return Effect.DESTROY           # an unknown tool is assumed the worst
    eff = spec.effect
    return eff(args or {}) if callable(eff) else eff


def is_gated(name: str, args: dict = None) -> bool:
    """Whether this call needs the person's confirmation."""
    return effect_of(name, args) in confirm_effects()


def describe(name: str, args: dict = None):
    spec = get(name)
    if spec is None or spec.describe is None:
        return None
    try:
        return spec.describe(args or {})
    except Exception:
        return None
