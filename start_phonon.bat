@echo off
REM Starts the local speech-recognition server (Phonon-2) used when
REM config.STT_BACKEND = "phonon". Keep this window open while using Eva.
REM First run installs into its OWN folder (phonon-venv) so it cannot disturb
REM Eva's packages; it downloads torch (CPU) and the 164 MB model once.
REM If "import torch" fails with WinError 126, install Microsoft's
REM vc_redist.x64.exe (https://aka.ms/vs/17/release/vc_redist.x64.exe) and retry.
cd /d "%~dp0"
if not exist phonon-venv\Scripts\python.exe (
  echo Creating phonon-venv and installing the speech runtime - one time, a few minutes...
  python -m venv phonon-venv || goto :fail
  phonon-venv\Scripts\python -m pip install --upgrade pip
  phonon-venv\Scripts\python -m pip install fermion-research torch safetensors soundfile scipy zstandard || goto :fail
)
echo Starting Phonon-2 on http://127.0.0.1:8000 ...
phonon-venv\Scripts\fermion serve --model phonon
goto :eof
:fail
echo.
echo Installation failed - send me the messages above.
pause
