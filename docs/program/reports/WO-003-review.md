# WO-003 review, before step 4 -- NOT YET: four safety fixes first (Program Manager, 2026-11-15)

Steps 1-3 follow the design and the heartbeat works. The live test must wait: the script as written can harm
things outside Eva. Fix items 1-4, then do step 4.
Tag `pre-wo-003-fix`. Files you may change: tools/update.py, run_checks.py, docs/program/reports/WO-003.md.

1. STOP ONLY EVA (serious). `stop-eva` kills every process on the PC whose command line has an argument
   ending in main.py -- any other project the Director runs as `python main.py` would be killed.
   Change it: kill a process only if its `cwd()` equals REPO_ROOT (compare os.path.normcase(os.path.abspath))
   OR one of its arguments is the absolute path of REPO_ROOT\main.py. Use `p.terminate()`, wait up to 5 s
   (psutil.wait_procs), then `kill()` what is left. If psutil cannot be imported, print
   "cannot stop Eva: psutil missing" and make update() return 6 BEFORE fetching anything.
   Move the matching into a pure function `is_eva_process(cmdline, cwd, repo_root) -> bool` so it can be checked.
2. CHECK THE FETCH (serious). `git fetch` result is ignored; if it fails (no network), the next line merges
   whatever FETCH_HEAD was left from an earlier fetch -- after step 4 that would be the BROKEN branch.
   If fetch_res.returncode != 0: start Eva on the old commit, log "fetch failed", return 3. Do not merge.
3. EMPTY COMMIT PASSES. In wait_for_heartbeat, `expected_commit.startswith(hb_commit)` is True when the
   heartbeat's commit is "" . Require `hb_commit` non-empty and not "unknown" before comparing.
4. FIRST START AFTER A DEPENDENCY CHANGE. run.bat runs pip when requirements*.txt changed, which can take
   minutes; the 90 s wait would roll back a good update. After the merge, run
   `git diff --name-only <old> HEAD -- requirements.txt requirements-play.txt`; if it lists a file, wait
   600 s instead of the default (print that you are doing so).
5. Checks (add to the existing WO-003 check, same fake `run`): is_eva_process is True for cwd == repo and for
   an absolute repo main.py argument, False for `python main.py` in another folder and for the updater
   itself; fetch failure -> no merge call, Eva started, return 3; heartbeat with commit "" or "unknown" ->
   not accepted; requirements changed -> the long wait is used.
6. Run the full run_checks.py (expect All checks passed.), commit `WO-003: ...`, push. THEN do step 4 of the
   work order (the Director has agreed to the Eva restart), and finish the report as written.
