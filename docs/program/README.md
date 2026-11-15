# Project EVA -- programme board

Operating model: Director (Adarsha) decides priorities. Program Manager (Claude) writes work orders (WO),
reviews each work order's commits on `main` against its acceptance section, and records the verdict here. Tech Lead (Gemini)
implements one WO at a time. Rules for the Tech Lead: /GEMINI.md. Target design: docs/ARCHITECTURE_REVIEW.md.

How a work order moves: ISSUED -> (Gemini) IN PROGRESS -> READY FOR REVIEW (commits on main + report) -> (Claude)
APPROVED, or CHANGES REQUESTED (list in docs/program/reports/WO-xxx-review.md) -> ... Only APPROVED unlocks the next WO.
The Director relays "WO-xxx ready" to the Program Manager; nothing else is needed.

## Status board
| WO | Title | Stage | Status |
|---|---|---|---|
| WO-001 | Baseline: measure Eva today (scorecard, checks, latency) | A1 | APPROVED 2026-11-15: scorecard 21/23, median 4.5 s, p90 11.3 s |
| WO-001B | Repair the two checks broken on 11-14/15 | A1 | ISSUED |
| WO-002 | `--selftest`: prove a build can start before it is used | A1 | ISSUED (after 001B) |
| WO-003 | Safe update with health check and automatic rollback | A1 | ISSUED (after 002) |
| WO-004 | Fast unit tests (pytest) beside run_checks.py | A1 | ISSUED (after 003) |
| WO-005 | CI run on every push to main | A1 | ISSUED (after 004) |
| WO-010.. | Task engine (A2): issued when A1 is approved | A2 | not issued |
| WO-020.. | Reflexes and request scheduler (A3) | A3 | not issued |
| WO-030.. | Nervous system + world model, eyes (B0, B1) | B | not issued |

## Roadmap (stages; each stage ends with a scorecard run that must not be lower than the last)
A1 safety net -> A2 task engine -> A3 reflexes + scheduler -> B0 bus + world model -> B1 eyes -> B3 hands
-> B2 ears + A5 conversation -> B4 self-sense -> A4/B6 one body and expression -> B5 memory -> B7 initiative
-> B8 growth -> A6 prompt diet.

## Decisions log
- 2026-11-15: updates are done in place with selftest + rollback (WO-003), not with release folders: the
  model files and venv are outside git, and in-place with rollback is simpler to get right.
- 2026-11-15: no branches or pull requests (Director). Work goes straight to `main`; each work order starts
  with a tag `pre-wo-xxx` as the rollback point (`git reset --hard pre-wo-xxx` by the Director if needed).
