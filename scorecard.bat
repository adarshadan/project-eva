@echo off
REM Eva task scorecard: real everyday requests through the real Eva, scored by
REM what actually happened on the PC. Works only inside Documents\Eva-scorecard.
REM About 3-5 minutes; please do not use the PC meanwhile. Writes eva-scorecard.txt.
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
python tools\scorecard.py %*
pause
