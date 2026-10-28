@echo off
REM 2026-10-28: Windows checks, then the task benchmark; results in tools\gains_run.log and eva-scorecard.txt
cd /d "%~dp0"
call tools\windows_checks.bat
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
echo === scorecard %TIME% === > tools\gains_run.log
python tools\scorecard.py >> tools\gains_run.log 2>&1
echo === DONE %TIME% === >> tools\gains_run.log
