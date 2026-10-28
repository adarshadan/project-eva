@echo off
REM Started by the browser (Chrome, Edge, Brave, Opera) for the Eva extension. Must print nothing itself.
"%~dp0..\venv\Scripts\python.exe" "%~dp0native_host.py" %*
