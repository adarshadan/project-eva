@echo off
REM Which free models are served now (tools\models_now.txt)
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
python tools\models_now.py
