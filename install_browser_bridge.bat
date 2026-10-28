@echo off
REM Registers Eva's browser bridge for this Windows user (no administrator rights needed).
REM   install_browser_bridge.bat --uninstall   removes it again
cd /d "%~dp0"
call venv\Scripts\activate.bat
python browser_bridge\install.py %*
pause
