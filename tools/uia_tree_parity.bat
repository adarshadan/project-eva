@echo off
REM Read-only: checks the fast ui_tree read against the original walk. Open Notepad and File Explorer first.
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
python tools\uia_tree_parity.py > tools\uia_tree_parity.log 2>&1
