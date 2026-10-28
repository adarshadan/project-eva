@echo off
REM Read-only: Windows OCR, new engine per read vs the kept engine.
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
python tools\ocr_bench.py > tools\ocr_bench.log 2>&1
