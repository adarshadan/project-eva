@echo off
REM Eva Doctor: tests what really works on this PC and writes eva-doctor-report.txt.
REM Close Eva first. Do not use the mouse or keyboard while it runs (about a minute).
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
python eva_doctor.py %*
pause
