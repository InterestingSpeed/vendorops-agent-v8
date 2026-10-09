@echo off
setlocal
if not exist .venv\Scripts\python.exe (
  echo Virtual environment missing. Run SETUP_WINDOWS.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python scripts\reset_demo.py
start "" http://127.0.0.1:8000
python -m uvicorn app:app --host 127.0.0.1 --port 8000
