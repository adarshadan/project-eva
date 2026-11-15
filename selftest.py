"""Build verification: proves Eva can start without opening a window or using the network."""
import importlib
import os
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))


def run(modules=None) -> list[str]:
    """Run self-test checks. Returns a list of problem strings ([] = healthy)."""
    problems = []

    # If a test harness stubbed PySide6 or its submodules without real package files,
    # remove the stubs so real PySide6 modules can load cleanly.
    stubs = [k for k, m in list(sys.modules.items())
             if (k == "PySide6" or k.startswith("PySide6.")) and not hasattr(m, "__file__")]
    if stubs:
        for k in stubs:
            del sys.modules[k]
        try:
            import PySide6  # noqa: F401
        except Exception:
            pass

    # a. Import every module in eva_app.startup's module list (reuse that list)
    if modules is None:
        from eva_app import startup
        module_list = startup.CORE_MODULES
    else:
        module_list = modules

    for name in module_list:
        try:
            importlib.import_module(name)
        except Exception as e:
            problems.append(f"cannot import {name}: {type(e).__name__}: {e}")

    # b. Import core.toolset and check every registered tool has an implementation
    # or is in the bound-at-startup set
    try:
        import core.toolset
        from core import registry
        from eva_app.startup import BOUND_AT_STARTUP
        missing = set(registry.unbound()) - BOUND_AT_STARTUP
        for n in sorted(missing):
            problems.append(f"tool {n!r} has no implementation bound")
        for p in registry.validate():
            if "has no implementation bound" in p:
                continue
            problems.append(p)
    except Exception as e:
        problems.append(f"tool validation: {type(e).__name__}: {e}")

    # c. Check web/avatar_3d.html and web/assets/character.glb exist and are non-empty
    for rel in ("web/avatar_3d.html", "web/assets/character.glb"):
        path = os.path.join(HERE, *rel.split("/"))
        if not os.path.exists(path):
            problems.append(f"missing asset: {rel}")
        elif os.path.getsize(path) == 0:
            problems.append(f"empty asset: {rel}")

    # d. Check config.MODEL_ID is a non-empty string and providers.parse does not raise
    try:
        import config
        import providers
        mid = getattr(config, "MODEL_ID", None)
        if not mid or not isinstance(mid, str) or not mid.strip():
            problems.append(f"config.MODEL_ID is empty or invalid: {mid!r}")
        else:
            providers.parse(mid)
    except Exception as e:
        problems.append(f"config.MODEL_ID validation: {type(e).__name__}: {e}")

    # e. Check ~/.eva is writable (create and delete a temp file)
    try:
        eva_dir = os.path.expanduser("~/.eva")
        os.makedirs(eva_dir, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(dir=eva_dir, prefix="selftest_", delete=False)
        try:
            tmp.write(b"ok")
            tmp.flush()
        finally:
            tmp.close()
            try:
                os.remove(tmp.name)
            except OSError:
                pass
    except Exception as e:
        problems.append(f"~/.eva directory is not writable: {e}")

    return problems
