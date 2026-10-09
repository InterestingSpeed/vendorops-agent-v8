from __future__ import annotations

import shutil
import uuid
import threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel

from vendorops.agent import VendorOpsAgent
from vendorops.config import BASE_DIR, UPLOAD_DIR, db_path, groq_api_key, groq_model, ollama_base_url, ollama_model
from vendorops.seed import reset_database
from vendorops.service import dashboard, decide_approval, list_approvals, list_audit, list_notifications, list_pos, list_vendors, list_invoices, list_gate_entries, list_policies

@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not db_path().exists():
        reset_database()
    yield


app = FastAPI(title="VendorOps Agent", version="8.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _save_invoice_upload(invoice: UploadFile | None) -> tuple[Path | None, dict | None]:
    if not invoice or not invoice.filename:
        return None, None
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(invoice.filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".pdf"}:
        return None, {"ok":False,"error":"UNSUPPORTED_FILE","message":"Use PNG/JPG/JPEG/WEBP/PDF."}
    if suffix == ".pdf":
        return None, {"ok":False,"error":"PDF_NOT_IMPLEMENTED","message":"For this runnable prototype, use an invoice image. PDF extraction is documented as the next extension."}
    invoice_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{Path(invoice.filename).name}"
    with invoice_path.open("wb") as f:
        shutil.copyfileobj(invoice.file, f)
    return invoice_path, None

def _append_job_event(job_id: str, event: dict) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        seq = len(job["events"]) + 1
        job["events"].append({"seq": seq, "at": _now_iso(), **event})
        job["updated_at"] = _now_iso()

def _run_job(job_id: str, prompt: str, mode: str, invoice_path: Path | None) -> None:
    try:
        def progress(event: dict) -> None:
            _append_job_event(job_id, event)
        result = VendorOpsAgent(invoice_path=invoice_path, progress=progress).run(prompt, mode=mode)
        with JOBS_LOCK:
            job = JOBS[job_id]
            job["state"] = "finished"
            job["result"] = {
                "ok": True, "mode": result.mode, "status": result.status,
                "summary": result.summary, "trace": result.trace, "final_data": result.final_data,
            }
            job["updated_at"] = _now_iso()
        _append_job_event(job_id, {"type":"run_finished","status":result.status,"summary":result.summary})
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS[job_id]
            job["state"] = "failed"
            job["error"] = {"type": type(exc).__name__, "message": str(exc)}
            job["updated_at"] = _now_iso()
        _append_job_event(job_id, {"type":"run_failed","error":type(exc).__name__,"message":str(exc)})


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/process")
async def process_task(prompt: str = Form(...), mode: str = Form("deterministic"), invoice: UploadFile | None = File(None)):
    invoice_path, upload_error = _save_invoice_upload(invoice)
    if upload_error:
        return upload_error
    result = VendorOpsAgent(invoice_path=invoice_path).run(prompt, mode=mode)
    return {"ok":True,"mode":result.mode,"status":result.status,"summary":result.summary,"trace":result.trace,"final_data":result.final_data}


@app.post("/api/process/start")
async def process_task_start(prompt: str = Form(...), mode: str = Form("deterministic"), invoice: UploadFile | None = File(None)):
    invoice_path, upload_error = _save_invoice_upload(invoice)
    if upload_error:
        return upload_error
    job_id = uuid.uuid4().hex
    now = _now_iso()
    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id, "state": "running", "created_at": now, "updated_at": now,
            "events": [], "result": None, "error": None, "mode": mode, "prompt": prompt,
        }
    thread = threading.Thread(target=_run_job, args=(job_id, prompt, mode, invoice_path), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id, "state": "running"}


@app.get("/api/process/jobs/{job_id}")
def process_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return {"ok": False, "error": "JOB_NOT_FOUND"}
        return {"ok": True, **job}


@app.get("/api/providers")
def provider_status():
    ollama_openai_url = ollama_base_url()
    ollama_native_root = ollama_openai_url[:-3] if ollama_openai_url.endswith("/v1") else ollama_openai_url
    ollama_reachable = False
    ollama_error = None
    installed_models: list[str] = []
    try:
        response = httpx.get(f"{ollama_native_root}/api/tags", timeout=0.6)
        response.raise_for_status()
        payload = response.json()
        installed_models = [m.get("name", "") for m in payload.get("models", []) if m.get("name")]
        ollama_reachable = True
    except Exception as exc:
        ollama_error = type(exc).__name__
    return {
        "groq": {
            "configured": bool(groq_api_key()),
            "model": groq_model(),
            "needs_key": True,
        },
        "deterministic": {
            "configured": True,
            "model": "rule-based safety planner",
            "needs_key": False,
        },
        "ollama": {
            "configured": ollama_reachable,
            "reachable": ollama_reachable,
            "model": ollama_model(),
            "model_installed": any(x == ollama_model() or x.startswith(ollama_model()+":") for x in installed_models),
            "installed_models": installed_models,
            "error": ollama_error,
            "needs_key": False,
        },
    }


@app.post("/api/reset")
def reset():
    path = reset_database()
    return {"ok":True,"db":str(path)}


@app.get("/api/dashboard")
def get_dashboard():
    return dashboard()


@app.get("/api/vendors")
def get_vendors(): return list_vendors()

@app.get("/api/pos")
def get_pos(): return list_pos()

@app.get("/api/invoices")
def get_invoices(): return list_invoices()

@app.get("/api/gate_entries")
def get_gate_entries(): return list_gate_entries()

@app.get("/api/policies")
def get_policies(): return list_policies()

@app.get("/api/approvals")
def get_approvals(): return list_approvals()

@app.get("/api/notifications")
def get_notifications(): return list_notifications()

@app.get("/api/audit")
def get_audit(limit: int = 200): return list_audit(limit)


class Decision(BaseModel):
    decision: str


@app.post("/api/approvals/{approval_id}/decision")
def approval_decision(approval_id: int, body: Decision):
    return decide_approval(approval_id, body.decision)
