@echo off
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
python tools\provider_smoke.py > tools\provider_smoke.log 2>&1
