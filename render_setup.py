"""Eva -- how Qt composes her windows (set before the first window exists).

Shared by main.py and tools/flicker_bench.py. Qt-free.
"""
import os


def apply_qt_graphics(cfg, environ=os.environ):
    """Sets how Qt composes Eva's windows, before the first window exists.
    Returns a description for the log. "software" is Qt Quick's software
    scene graph (QT_QUICK_BACKEND) -- it is NOT an RHI backend, so passing it
    as QSG_RHI_BACKEND, as before, silently did nothing."""
    api = (getattr(cfg, "QT_GRAPHICS_API", "") or "").strip().lower()
    comp = (getattr(cfg, "QT_COMPOSITION", "gpu") or "gpu").strip().lower()
    mode = (getattr(cfg, "RENDER_MODE", "") or "").strip().lower()
    if (api == "software" or comp == "software") and mode != "software":
        # eva.log 2026-10-11 16:59: Qt's software scene graph switches
        # Chromium's GPU off, so WebGL cannot start in a GPU render mode and she
        # was invisible. Only honoured with the software (SwiftShader) mode.
        environ.pop("QT_QUICK_BACKEND", None)
        if api in ("opengl", "d3d11", "d3d12", "vulkan"):
            environ["QSG_RHI_BACKEND"] = api
        return f"GPU composition (software composition ignored: it needs RENDER_MODE software, not {mode or 'default'})"
    if api == "software" or comp == "software":
        environ["QT_QUICK_BACKEND"] = "software"
        environ.pop("QSG_RHI_BACKEND", None)
        return "software composition (no Direct3D device in Eva's process)"
    if api in ("opengl", "d3d11", "d3d12", "vulkan"):
        environ["QSG_RHI_BACKEND"] = api           # read by Qt when the first window is made
        return f"GPU composition via {api}"
    return "GPU composition (Qt default, Direct3D 11)"
