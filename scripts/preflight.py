from __future__ import annotations
import importlib, shutil, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

checks=[]
def check(label, ok, detail=''):
    checks.append(bool(ok)); print(('OK  ' if ok else 'FAIL')+f' {label}'+(f' — {detail}' if detail else ''))

print('VendorOps preflight')
print('Python:', sys.version.split()[0], sys.executable)
for mod in ['fastapi','uvicorn','jinja2','multipart','pydantic','PIL','cv2','pytesseract','qrcode','dotenv','openai','httpx']:
    try: importlib.import_module(mod); check(f'import {mod}',True)
    except Exception as e: check(f'import {mod}',False,str(e))

inv=ROOT/'data'/'invoices'/'01_happy_path_qr.png'
check('sample invoices',inv.exists(),str(inv))
db=ROOT/'data'/'vendorops.db'
if db.exists():
    con=sqlite3.connect(db); integrity=con.execute('PRAGMA integrity_check').fetchone()[0]; fk=con.execute('PRAGMA foreign_key_check').fetchall(); con.close()
    check('SQLite integrity',integrity=='ok',integrity); check('SQLite foreign keys',not fk,f'{len(fk)} violation(s)')
else: check('database exists',False,str(db))
check('Tesseract (only needed for OCR-only demos)',shutil.which('tesseract') is not None,shutil.which('tesseract') or 'not on PATH; QR demos still work')
print('\nREADY FOR QR DEMO' if all(checks[:-1]) else '\nFix FAIL items above before recording.')
