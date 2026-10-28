@echo off
REM Speech recogniser trial on the kept phrases (tools\jobs\asr.log, tools\asr_trial.txt)
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
echo === %TIME% === > tools\jobs\asr.log
python -m pip install -q nvidia-riva-client >> tools\jobs\asr.log 2>&1
python tools\asr_trial.py >> tools\jobs\asr.log 2>&1
echo === DONE %TIME% === >> tools\jobs\asr.log
