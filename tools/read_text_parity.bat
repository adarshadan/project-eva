@echo off
cd /d "%~dp0.."
venv\Scripts\python.exe tools\read_text_parity.py > tools\read_text_parity.log 2>&1
