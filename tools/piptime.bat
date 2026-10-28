@echo off
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
echo === %TIME% start > tools\jobs\piptime.log
python -m pip install -r requirements.txt >> tools\jobs\piptime.log 2>&1
echo === %TIME% after requirements >> tools\jobs\piptime.log
python -m pip install -r requirements-play.txt >> tools\jobs\piptime.log 2>&1
echo === %TIME% after play >> tools\jobs\piptime.log
python -c "import time;t=time.time();import main;print('import main', round(time.time()-t,2))" >> tools\jobs\piptime.log 2>&1
echo === %TIME% DONE >> tools\jobs\piptime.log
exit
