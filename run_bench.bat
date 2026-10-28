@echo off
REM The task benchmark only (tools\gains_run.log, eva-scorecard.txt)
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
echo === scorecard %TIME% === > tools\gains_run.log
python tools\scorecard.py >> tools\gains_run.log 2>&1
echo === DONE %TIME% === >> tools\gains_run.log
