@echo off
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
python tools\uia_bench.py > tools\uia_bench.log 2>&1
