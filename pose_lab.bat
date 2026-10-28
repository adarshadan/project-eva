@echo off
REM Pose lab: see and tune Eva's poses, blinking and mouth in a normal browser.
REM Needs internet (the 3D libraries load from a CDN, same as Eva herself).
cd /d "%~dp0"
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
echo Starting the pose lab at http://localhost:8765/avatar_3d.html?lab=1
echo Close this window to stop it.
start "" "http://localhost:8765/avatar_3d.html?lab=1"
python -m http.server 8765 --bind 127.0.0.1 --directory web
