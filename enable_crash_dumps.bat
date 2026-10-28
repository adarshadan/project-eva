@echo off
rem Eva -- let Windows save a crash dump of python.exe, so a native crash can be
rem traced to the exact function (eva.log alone shows only the Python side).
rem Writes HKLM\...\Windows Error Reporting\LocalDumps\python.exe (needs admin;
rem Windows will ask). Dumps go to %USERPROFILE%\.eva\dumps, at most 5 kept.
rem To undo: run this file with the word  off  (enable_crash_dumps.bat off).
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%1' -Verb RunAs"
  exit /b
)
set KEY=HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\python.exe
if /i "%1"=="off" (
  reg delete "%KEY%" /f
  echo Crash dumps for python.exe are OFF.
  pause
  exit /b
)
if not exist "%USERPROFILE%\.eva\dumps" mkdir "%USERPROFILE%\.eva\dumps"
reg add "%KEY%" /v DumpFolder /t REG_EXPAND_SZ /d "%USERPROFILE%\.eva\dumps" /f
reg add "%KEY%" /v DumpType /t REG_DWORD /d 1 /f
reg add "%KEY%" /v DumpCount /t REG_DWORD /d 5 /f
echo.
echo Crash dumps for python.exe are ON. After the next crash, send the newest
echo file in %USERPROFILE%\.eva\dumps  (a .dmp file, a few MB).
pause
