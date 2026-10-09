# VendorOps Agent v8

VendorOps Agent is a small AI-assisted vendor-inward operations prototype.

The idea came from a real vendor-portal workflow I had seen: invoices reach the gate, people manually identify the supplier, check the PO, enter data, and sometimes discover that the vendor is new or something does not match. I wanted to see how much of that work an AI agent could safely handle without turning the LLM into the source of truth.

All company data in this repo is synthetic.

## What it does

Give the system a natural-language request and an invoice image. Depending on what it finds, it can:

- read invoice data (QR first, OCR fallback)
- verify the supplier GST status
- search the vendor master
- validate the purchase order
- check for duplicate invoices
- create a provisional profile for a new supplier
- stop and ask for human approval when policy requires it
- create and verify a gate entry when the checks pass
- keep an audit trail and simulated notification outbox

The dashboard also exposes Vendors, Purchase Orders, Invoices, Gate Entries, Approvals, Outbox, Audit Log and Policies directly. You do not have to ask the LLM just to see company data.

## Main design choice

I do not let the LLM directly own business truth or database writes.

The model interprets the user's goal. Normal backend tools then fetch company facts, apply policy checks and perform writes. State-changing tools validate again before writing, and successful gate entries are read back before the task is marked complete.

For the recorded demo, the default mode is **Groq Fast Hybrid with `openai/gpt-oss-20b`**. It uses one small LLM planning call and then executes through the guarded business workflow. I also kept full iterative tool-calling modes for experimentation and discussion.

## Quick setup (Windows)

From the project folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts\generate_sample_invoices.py
python scripts\reset_demo.py
python scripts\preflight.py
```

Copy `.env.example` to `.env` and add your Groq key:

```env
GROQ_API_KEY=gsk_your_key_here
```

Start the app:

```powershell
python -m uvicorn app:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

You can also run `SETUP_WINDOWS.bat` once and `START_DEMO.bat` later.

## Recommended demo files

The included sample invoices cover 12 different paths. The three I use for a short demo are:

- `data/invoices/01_happy_path_qr.png` — normal invoice, creates and verifies a gate entry
- `data/invoices/02_new_vendor_qr.png` — valid GST but new supplier, creates a provisional vendor and asks for approval
- `data/invoices/04_po_vendor_mismatch_qr.png` — invoice supplier and PO supplier differ, so the write is blocked

Reset the demo data between scenarios if you want the same starting state each time:

```powershell
python scripts\reset_demo.py
```

## Execution modes

- **Groq Fast Hybrid** — recommended demo mode; hosted GPT-OSS planning + guarded local execution
- **Ollama Fast Hybrid** — local backup; defaults to `qwen3:4b`
- **Deterministic fallback** — no model or internet required
- **Groq/Ollama Full Agent** — repeated tool-calling loop for experimentation

For Ollama backup:

```powershell
ollama pull qwen3:4b
```

## Sandbox data

The SQLite database contains vendor master records, GST profiles, POs, historical invoices/gate entries, policies, approvals, notifications, simulated API faults and audit logs.

The sample set includes normal processing, a new vendor, inactive GST, PO/vendor mismatch, duplicate invoice, PO amount exception, transient GST API failure, OCR fallback, poor OCR, closed PO, missing PO and blocked vendor.

## Tests

```powershell
python -m pytest -q
python scripts\run_scenarios.py
```

Current build: **36 automated tests passing**, plus the 12 scenario runner.

## Current limitations

This is still a prototype, not a production ERP integration.

- GST is a sandbox adapter, not a live GST/GSP connection.
- Email is a simulated outbox.
- Company state is stored in SQLite instead of a real ERP/vendor portal.
- The document flow is strongest on the included image/QR cases; multi-page PDF handling is not implemented yet.
- There is no production RBAC, secrets manager or distributed task queue.
- The default fast-hybrid path deliberately keeps critical execution deterministic; the full tool-calling mode is included separately.

## If I continued it

The first production changes would be real vendor/ERP APIs, an authorized GST/GSP integration, proper email/notification connectors, Postgres/object storage, RBAC and durable background jobs. After that I would extend the inward workflow into GRN and PO-GRN-invoice matching and test on a much larger set of real invoice layouts.

## Confidentiality

No real company invoices, credentials, GST data or internal portal code are included. All records are synthetic and the external systems are simulated behind adapters.

More detail is in `docs/ARCHITECTURE.md` and `docs/SCENARIOS.md`.

## Optional public demo

The repository includes a Dockerfile, so the fastest public demo is to connect the GitHub repo to a small web-service host such as Render and set `GROQ_API_KEY` as a secret environment variable. The app binds to the host-provided `PORT` automatically.

The deployed SQLite database is only demo state. On a free/ephemeral host it can reset after a restart, which is acceptable for this prototype but not for production persistence.

### Running tests on Windows

The test suite uses a temporary SQLite database and does **not** touch the demo database used by the running web app:

```powershell
python -m pytest -q
```

Tesseract is optional. If it is not installed, the two OCR-only tests are skipped automatically; the QR-based demo and the rest of the test suite still run normally.


## Windows live reset

`Reset demo data` rebuilds the SQLite schema in place instead of deleting the database file. This avoids Windows `WinError 32` file-lock failures while the FastAPI demo is running.
