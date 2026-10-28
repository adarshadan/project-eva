@echo off
REM Eva flicker test: draws the avatar with each render setting and MEASURES
REM how often she goes missing on this screen (about 3 minutes). The best
REM setting is saved to %USERPROFILE%\.eva\render.json and Eva uses it from the
REM next start. Close Eva first; do not move windows over the avatar while it runs.
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
python tools\flicker_bench.py %*
pause
