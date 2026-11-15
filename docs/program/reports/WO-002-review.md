# WO-002 review -- CHANGES REQUESTED (Program Manager, 2026-11-15)

Good: the design was followed, the selftest catches the 2026-11-14 failure (step 3 output), it runs in 1.8 s,
and it is the first thing main() does. Three things must change before approval. Items 1-2 come from a gap in
the work order itself (the check was specified as "run() returns [] on the repository", which cannot be true
on a machine without the desktop packages or the model file) -- the fix is specified here.

Tag `pre-wo-002-fix` first. Files you may change: selftest.py, main.py, run_checks.py, docs/program/reports/WO-002.md.

1. selftest.py: DELETE the block that removes PySide6 stubs from sys.modules (the lines from the comment
   "If a test harness stubbed PySide6" down to the `except Exception: pass`). Product code must never know
   about test stand-ins, and deleting entries from sys.modules can break whatever runs next.
2. selftest.py: change the signature to
   `def run(modules=None, check_tools=True, assets=("web/avatar_3d.html", "web/assets/character.glb")) -> list[str]:`
   - part b (tool validation) runs only when check_tools is True;
   - part c loops over `assets` (an empty tuple means no asset check).
   main.py: `--selftest` keeps calling `selftest.run()` (everything on). If "--skip-assets" is also in
   sys.argv, call `selftest.run(assets=("web/avatar_3d.html",))` -- the model file is not in git, so the CI
   machine (WO-005) will use this flag.
3. run_checks.py:
   a. Rewrite selftest_proves_build_healthy so it does not depend on the machine:
      - `selftest.run(modules=["command_policy", "coder"], check_tools=False, assets=("web/avatar_3d.html",)) == []`
      - `selftest.run(modules=["fake_module_not_real"], check_tools=False, assets=())` -> one problem naming it
      - `selftest.run(modules=[], check_tools=False, assets=("web/does_not_exist.bin",))` -> one problem
        containing "missing asset"
      - `eva_app.startup.CORE_MODULES` contains "coder" (the module whose absence broke start-up).
   b. Line 127: replace the literal `BOUND_AT_STARTUP = {...}` with
      `from eva_app.startup import BOUND_AT_STARTUP` (one source of truth; the two sets are equal today).
4. Run: `venv\Scripts\python.exe main.py --selftest` (expect SELFTEST OK), then with `--skip-assets`
   (expect SELFTEST OK), then the full run_checks.py (expect All checks passed.). Paste all three outputs at
   the end of docs/program/reports/WO-002.md under a heading "Fix round 1". Commit as `WO-002: ...`, push,
   and report "WO-002 fix ready for review".
