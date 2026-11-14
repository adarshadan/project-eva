"""Phase 3 (a body that moves like a person): the flags the page reads.

Pure stdlib so run_checks can test it without Qt. avatar_ui sends `page_flags_js()` to the page whenever
it (re)loads or the settings change; the page (web/avatar_3d.html) reads exactly these window names.
"""
import json

# window names the page reads (the checks verify each one appears in avatar_3d.html)
PAGE_FLAGS = ("EVA_BODY_LIFE", "EVA_CLOTH_PHYSICS", "EVA_CLOTH_TUNE")


def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                                   # NaN
        return default
    return max(lo, min(hi, v))


def page_flags_js(cfg=None):
    """JavaScript that sets the Phase 3 flags and cloth tuning from config (defaults: on)."""
    if cfg is None:
        import config as cfg
    body = "true" if getattr(cfg, "EVA_BODY_LIFE", True) else "false"
    cloth = "true" if getattr(cfg, "EVA_CLOTH_PHYSICS", True) else "false"
    tune = {
        "stiffness": _clamp(getattr(cfg, "EVA_CLOTH_STIFFNESS", 0.85), 0.1, 2.0, 0.85),
        "drag": _clamp(getattr(cfg, "EVA_CLOTH_DRAG", 0.9), 0.1, 2.0, 0.9),
        "breeze": _clamp(getattr(cfg, "EVA_CLOTH_BREEZE", 1.0), 0.0, 3.0, 1.0),
    }
    return (f"window.EVA_BODY_LIFE = {body}; window.EVA_CLOTH_PHYSICS = {cloth}; "
            f"window.EVA_CLOTH_TUNE = {json.dumps(tune)};")
