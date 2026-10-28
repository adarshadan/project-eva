@echo off
REM 2026-10-28 trials: benchmark per candidate model (fallbacks off), then the speech recogniser trial
cd /d "%~dp0\.."
call venv\Scripts\activate.bat
echo === start %TIME% === > tools\jobs\trials.log
python tools\scorecard.py --set=MODEL_ID="minimaxai/minimax-m3.1-flash" --set=MODEL_FALLBACKS=() > tools\jobs\bench_minimax.log 2>&1
echo minimax done %TIME% >> tools\jobs\trials.log
python tools\scorecard.py --set=MODEL_ID="nvidia/nemotron-3-ultra-550b-a55b" --set=MODEL_FALLBACKS=() > tools\jobs\bench_ultra.log 2>&1
echo ultra done %TIME% >> tools\jobs\trials.log
call tools\asr_trial.bat
echo === DONE %TIME% === >> tools\jobs\trials.log
exit
