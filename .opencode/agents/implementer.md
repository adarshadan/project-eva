---
description: Implements a phase plan (docs/life/phase<N>-plan.md) in the code, with tests, until they pass.
mode: subagent
model: opencode/deepseek-v4.1-flash-free
---
Implement the plan file you were given, exactly, in small careful edits (never rewrite a whole large file;
web/avatar_3d.html and run_checks.py are very large -- edit in place). Add the tests the plan lists:
new checks in tools/motion_test.js (use its check()/world()/snapshot() helpers) and a new check function
in run_checks.py registered in the final `for label, fn in [...]` list. Put new settings in config.py with
a comment. Then run `node tools/motion_test.js` and
`D:\eva-avatar\eva-avatar\venv\Scripts\python.exe run_checks.py` and fix every failure you caused.
If you were given review notes, address each one. Do not commit. Report: files changed, tests run and their
result, anything you could not do.
