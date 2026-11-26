@echo off
rem AXAR ONE POS - local demo only (simulated payments, no authentication).
rem Listens on 127.0.0.1 only. Close this window to stop the server.
cd /d "%~dp0"

set AXAR_POS_DEMO_IDENTITY=1
set AXAR_POS_SEED_DEMO=1
set AXAR_POS_INPROCESS_WORKER=1
set AXAR_POS_SECRET_KEY=

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment missing - creating it...
  py -3 -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

rem Open the till in the browser a few seconds after the server starts.
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start "" http://127.0.0.1:8000/one/"

echo Starting AXAR ONE POS demo at http://127.0.0.1:8000/one/
".venv\Scripts\python.exe" -m uvicorn axar_pos.main:app --host 127.0.0.1 --port 8000
goto :eof

:error
echo Setup failed.
pause
