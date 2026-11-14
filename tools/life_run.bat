@echo off
REM "Eva feels alive", run by OpenCode agents with GLM as the gatekeeper:
REM   lead (plans/builds one phase) -> reviewer on GLM 5.3 (APPROVED or CHANGES) -> fix -> ... -> commit.
REM A phase is committed only when GLM approves it (at most 3 review rounds, then it stops for a human).
REM Releases to the real Eva (merge/push) are done by the manager (Claude) after its own review.
REM Works in a separate clone, tools\jobs\eva-life. Log: tools\jobs\life_run.log
setlocal EnableDelayedExpansion
set ROOT=D:\eva-avatar\eva-avatar
set WORK=%ROOT%\tools\jobs\eva-life
set OC="%LOCALAPPDATA%\Programs\@opencodedesktop\resources\opencode-cli.exe"
set LOG=%ROOT%\tools\jobs\life_run.log
set BUILDER=opencode/deepseek-v4.1-flash-free
set REVIEWER=opencode/glm-5.3-flash-free
echo === start %DATE% %TIME% === >> "%LOG%"
if not exist "%WORK%\.git" git clone -q https://github.com/adarshadan/project-eva "%WORK%" >> "%LOG%" 2>&1
cd /d "%WORK%"
git rev-parse --verify -q life >nul || git checkout -q -b life origin/next-level-wip >> "%LOG%" 2>&1
git checkout -q life >> "%LOG%" 2>&1
git config user.name "Adarsha Dan"
git config user.email "adarshadan@gmail.com"
for /L %%P in (1,1,5) do (
  findstr /c:"Phase 5 approved" docs\life\STATUS.md >nul 2>&1 && goto :finished
  set APPROVED=0
  for /L %%R in (1,1,4) do (
    if !APPROVED!==0 (
      echo ===== build round %%R %TIME% ===== >> "%LOG%"
      %OC% run --auto --agent lead --model %BUILDER% --title "Eva alive: build" "Continue the programme in docs/life/LIFE_DESIGN.md: do your one step for the current phase, then stop." >> "%LOG%" 2>&1
      set /p N=<docs\life\CURRENT_PHASE.txt
      echo ===== GLM review of phase !N! round %%R %TIME% ===== >> "%LOG%"
      %OC% run --auto --agent reviewer --model %REVIEWER% --title "Eva alive: review" "Review the uncommitted changes for phase !N! against docs/life/phase!N!-plan.md." > docs\life\review.md 2>&1
      type docs\life\review.md >> "%LOG%"
      findstr /r /c:"^APPROVED" docs\life\review.md >nul && set APPROVED=1
      if !APPROVED!==1 (
        copy /y docs\life\review.md docs\life\phase!N!-review-approved.md >nul
        del docs\life\review.md
        echo - Phase !N! approved by GLM -- %DATE% -- awaiting manager review before release >> docs\life\STATUS.md
        git add -A
        git commit -q -m "Life phase !N!: approved by GLM review"
        echo ===== phase !N! committed %TIME% ===== >> "%LOG%"
      )
    )
  )
  if !APPROVED!==0 (
    echo ===== phase !N! NOT approved after 4 rounds: stopping for the manager ===== >> "%LOG%"
    goto :finished
  )
)
:finished
echo === DONE %DATE% %TIME% === >> "%LOG%"
exit
