@echo off
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
python tools\uia_profile.py > tools\uia_profile.log 2>&1
