@echo off
REM tools\job.bat NAME command args...  -- runs the command in the app folder with the venv; output in tools\jobs\NAME.log, ending "=== DONE"
cd /d "%~dp0\.."
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
set NAME=%1
for /f "tokens=1,*" %%a in ("%*") do set CMD=%%b
echo === %DATE% %TIME% %CMD% === > tools\jobs\%NAME%.log
%CMD% >> tools\jobs\%NAME%.log 2>&1
echo === DONE %TIME% exit %ERRORLEVEL% === >> tools\jobs\%NAME%.log
exit
