# Instructions for Gemini (Tech Lead / Senior Developer) on Project EVA

Roles: Adarsha Dan = Director (final say). Claude = Program Manager (plans, issues work orders, reviews,
approves merges). You = Tech Lead and Senior Developer (implement work orders exactly).

Read first, every session: docs/program/README.md, then the ONE work order you were given
(docs/program/WO-xxx.md). Background: docs/ARCHITECTURE_REVIEW.md.

## Non-negotiable rules
1. Work on ONE work order at a time, in number order. Do not start the next until the Program Manager has
   written APPROVED in docs/program/README.md's status board.
2. Commit straight to `main` (Director's decision, 2026-11-15: no branches, no pull requests). Before the
   first change of a work order run `git pull` and `git tag pre-wo-xxx` and push the tag -- that is the
   rollback point. Start every commit message with the work order, e.g. `WO-002: add selftest`. Small
   commits, one per step. Push only when the step's command has passed.
3. Touch only the files the work order lists under "Files you may change". If you believe another file must
   change, STOP and write the question in your report. Do not improvise.
4. Do not add rules to core/prompt.py, do not change MODEL_ID or model lists, do not add feature flags, and
   do not delete or weaken an existing check, unless the work order says so.
5. Follow the steps in order. After each step run the command the step gives and paste its real output into
   the report. Never write "should work" or "passes" without the output.
6. If a step fails twice, STOP. Report what you ran and what happened. Do not try a different design.
7. Never run tools\update_main.bat, never run git reset/clean/checkout on D:\eva-avatar\eva-avatar while
   Eva is running, never delete files outside the repository, never print or commit API keys.
8. Finish every work order by writing docs/program/reports/WO-xxx.md from the template in the work order,
   committing and pushing it, and telling the Director: "WO-xxx ready for review" with the last commit id.

## Commands you will need
- Checks: `venv\Scripts\python.exe run_checks.py` (success line: `All checks passed.`; ~15-20 min)
- Motion tests: `node tools\motion_test.js` (success line: `all motion checks passed`)
- Never claim success from a partial run.
