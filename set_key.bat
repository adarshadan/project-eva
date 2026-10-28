@echo off
REM Stores an API key encrypted (Windows DPAPI, this user only), so replacing the Eva folder
REM never loses it. With no argument: the NVIDIA key. For another provider, name its key:
REM   set_key.bat OPENROUTER_API_KEY     (see MODEL_FALLBACKS in config.py)
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
set KEYNAME=%1
if "%KEYNAME%"=="" set KEYNAME=NVIDIA_API_KEY
python secret_store.py set %KEYNAME%
python secret_store.py status %KEYNAME%
pause
