@echo off
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
python tools\read_probe.py > tools\read_probe.log 2>&1
