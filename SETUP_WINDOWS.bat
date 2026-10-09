@echo off
setlocal
if not exist .venv (py -m venv .venv)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python scripts\generate_sample_invoices.py
python scripts\reset_demo.py
python scripts\preflight.py
pause
