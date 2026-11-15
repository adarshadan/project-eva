# WO-001 review -- APPROVED (Program Manager, 2026-11-15)

Verified against the files: scorecard 21/23 with 0 false "done"; motion tests pass; latency median 4.5 s,
p90 11.3 s; only docs/program changed since pre-wo-001. One correction: run_checks.txt has 187 PASS lines
(the report says 189) and 2 failures.

What the baseline tells us
- The 2 failing checks were introduced by the 2026-11-14/15 commits, not by WO-001 -> WO-001B repairs them.
- Every request carries ~46,500 characters (prompt + tool list) even for "what time is it". That is the
  cost behind the latency and the rate limits -> confirms A3 (reflexes) and A6 (prompt diet).
- 21/23 means the scorecard is too easy: it has single-app tasks, while the Director's complaints are
  about multi-step, cross-app requests. Stage A2 starts by adding those to the scorecard (WO-010).
- settings and calculator fail the same way: Windows store apps not opening/detected (13 calls, 47 s each).
