@echo off
REM The "Eva feels alive" programme, run by OpenCode's own agents (lead -> planner / implementer / reviewer)
REM in a separate clone so the Eva you use is untouched. Log: tools\jobs\life_run.log
set ROOT=D:\eva-avatar\eva-avatar
set WORK=%ROOT%\tools\jobs\eva-life
set OC="%LOCALAPPDATA%\Programs\@opencodedesktop\resources\opencode-cli.exe"
set LOG=%ROOT%\tools\jobs\life_run.log
echo === start %DATE% %TIME% === > "%LOG%"
if not exist "%WORK%\.git" git clone -q https://github.com/adarshadan/project-eva "%WORK%" >> "%LOG%" 2>&1
cd /d "%WORK%"
git fetch -q origin >> "%LOG%" 2>&1
git checkout -q life >> "%LOG%" 2>&1 || git checkout -q -b life origin/next-level-wip >> "%LOG%" 2>&1
git config user.name "Adarsha Dan"
git config user.email "adarshadan@gmail.com"
git log --oneline -1 >> "%LOG%"
%OC% run --auto --agent lead --title "Eva feels alive" "Start the programme in docs/life/LIFE_DESIGN.md. Do every phase in order with the planner, implementer and reviewer subagents, committing each approved phase, until all five are done or one is blocked." >> "%LOG%" 2>&1
echo === DONE %DATE% %TIME% exit %ERRORLEVEL% === >> "%LOG%"
exit
