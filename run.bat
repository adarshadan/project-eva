@echo off
REM Run this file from inside the eva-avatar folder (double-click it in
REM File Explorer, or run it from PowerShell/cmd). It only creates the
REM virtual environment once -- if venv\ already exists, it skips straight
REM to activating it. requirements.txt is installed/synced on EVERY run,
REM not just the first -- pip skips anything already installed at the
REM right version, so this stays fast, but it means a newly added
REM dependency (like when the TTS engine changed) gets picked up
REM automatically instead of silently missing until you remember to
REM reinstall by hand.

cd /d "%~dp0"

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    python -m pip install --upgrade pip
) else (
    call venv\Scripts\activate.bat
)

REM 2026-10-28: pip asked the package index twice on EVERY start (~3 s before Eva began). Now only when
REM requirements.txt or requirements-play.txt changed since the last good install (tools\deps_sync.py).
python tools\deps_sync.py check
if errorlevel 1 (
    echo Syncing dependencies...
    python -m pip install -r requirements.txt && python tools\deps_sync.py mark
    REM Optional speed-ups for games and repeats; a failure here is not fatal.
    python -m pip install -r requirements-play.txt || echo ^(optional speed-ups not installed -- Eva works without them^)
) else (
    echo Dependencies up to date.
)

REM Eva's "Restart" quits this process completely (so no old window is left
REM behind) and returns code 75; this loop then starts her again.
set EVA_LAUNCHER=run.bat
:start_eva
echo Starting Eva...
python main.py
if %ERRORLEVEL%==75 goto start_eva

pause
