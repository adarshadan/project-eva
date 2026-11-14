@echo off
REM Update this Eva folder to the merged main branch: stop Eva, fast-forward to origin/main, start Eva again. Log: tools\jobs\update_main.log
set ROOT=D:\eva-avatar\eva-avatar
set LOG=%ROOT%\tools\jobs\update_main.log
cd /d %ROOT%
echo === %DATE% %TIME% === > "%LOG%"
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*python*main.py*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >> "%LOG%" 2>&1
del /q .git\stale-HEAD-lock* 2>nul
del /q .git\*.lock 2>nul
git fetch origin main >> "%LOG%" 2>&1
git checkout main >> "%LOG%" 2>&1
git merge --ff-only FETCH_HEAD >> "%LOG%" 2>&1
git log --oneline -1 >> "%LOG%"
git status --short >> "%LOG%"
start "" /min /d %ROOT% %ROOT%\run.bat
echo === DONE %TIME% === >> "%LOG%"
exit
