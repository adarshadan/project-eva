@echo off
rem Eva -- plays her continuous-activity controllers against five local test
rem games (tic-tac-toe, Connect Four, 2048, a Flappy Bird clone, a runner) and
rem a long page to scroll, with real screen capture and real keys and clicks.
rem Close Eva first (she would be in the screenshots). Do not touch the mouse
rem or keyboard while it plays. Usage:  play_lab.bat   or   play_lab.bat flappy runner
cd /d "%~dp0"
call venv\Scripts\activate.bat
python tools\play_lab\lab.py %*
pause
