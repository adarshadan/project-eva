@echo off
REM Runs Eva's checks on this PC and writes tools\windows_checks.log.
REM   windows_checks.bat         checks + live tests
REM   windows_checks.bat probe   ... plus the model probe (a few minutes)
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
set LOG=tools\windows_checks.log
echo === %DATE% %TIME% === > %LOG%
python -m pip install -q -r requirements.txt >> %LOG% 2>&1
python -c "import edge_tts;print('edge-tts', edge_tts.__version__)" >> %LOG% 2>&1
echo === voice test === >> %LOG%
python -c "import voice,os;p=voice.synthesize('Testing Eva voice.');print('voice OK', voice.voice_source(), os.path.getsize(p), 'bytes');voice.discard(p)" >> %LOG% 2>&1
echo === live gate test === >> %LOG%
python -c "import permissions as pm,pyautogui;w,h=pyautogui.size();p=pm.DesktopProbe();print('screen',w,h);print('under Start button:',repr(p.control_at(30,h-25)));a=pm.assess('click_coords',{'x':30,'y':h-25,'reason_no_element':'t'});print('click on it gated:',a.gated)" >> %LOG% 2>&1
echo === run_checks === >> %LOG%
python run_checks.py >> %LOG% 2>&1
if /i "%1"=="probe" (
  echo === model_probe === >> %LOG%
  python tools\model_probe.py >> %LOG% 2>&1
)
echo === DONE %TIME% === >> %LOG%
