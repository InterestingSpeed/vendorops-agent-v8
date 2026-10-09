from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import all_rows, connect, utcnow, audit


def dashboard() -> dict[str, Any]:
    with connect() as conn:
        counts = {
            "approved_vendors": conn.execute("SELECT COUNT(*) FROM vendors WHERE status='APPROVED'").fetchone()[0],
            "provisional_vendors": conn.execute("SELECT COUNT(*) FROM vendors WHERE status='PROVISIONAL'").fetchone()[0],
            "open_pos": conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status='OPEN'").fetchone()[0],
            "gate_entries": conn.execute("SELECT COUNT(*) FROM gate_entries WHERE status='CREATED'").fetchone()[0],
            "pending_approvals": conn.execute("SELECT COUNT(*) FROM approvals WHERE status='PENDING'").fetchone()[0],
        }
        latest_gate = [dict(r) for r in conn.execute("""
            SELECT ge.gate_entry_number, ge.created_at, ge.vehicle_number, i.invoice_number, i.amount, v.trade_name
            FROM gate_entries ge JOIN invoices i ON ge.invoice_id=i.id JOIN vendors v ON ge.vendor_id=v.id
            ORDER BY ge.id DESC LIMIT 10
        """).fetchall()]
        return {"counts": counts, "latest_gate_entries": latest_gate}


def list_vendors() -> list[dict]:
    return all_rows("SELECT vendor_code,legal_name,trade_name,gstin,status,created_at,bank_verified,procurement_approved FROM vendors ORDER BY id DESC")


def list_pos() -> list[dict]:
    return all_rows("SELECT po_number,supplier_gstin,description,total_amount,amount_consumed,status,expected_by FROM purchase_orders ORDER BY id DESC")


def list_approvals() -> list[dict]:
    rows = all_rows("SELECT * FROM approvals ORDER BY id DESC")
    for r in rows:
        raw = r.pop("payload_json", "{}")
        try:
            r["review_context"] = json.loads(raw or "{}")
        except Exception:
            r["review_context"] = {}
    return rows


def list_notifications() -> list[dict]:
    return all_rows("SELECT * FROM notifications ORDER BY id DESC LIMIT 100")


def list_audit(limit: int = 200) -> list[dict]:
    rows = all_rows("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        try:
            r["details"] = json.loads(r.pop("details_json"))
        except Exception:
            pass
    return rows


def decide_approval(approval_id: int, decision: str, decided_by: str = "demo.manager") -> dict:
    decision = decision.upper()
    if decision not in {"APPROVED", "REJECTED"}:
        return {"ok":False,"error":"INVALID_DECISION"}
    with connect() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            return {"ok":False,"error":"NOT_FOUND"}
        if row["status"] != "PENDING":
            return {"ok":False,"error":"ALREADY_DECIDED","status":row["status"]}
        conn.execute("UPDATE approvals SET status=?,decided_at=?,decided_by=? WHERE id=?", (decision, utcnow(), decided_by, approval_id))
        # If this is a NEW_VENDOR approval, approving it changes vendor status.
        if decision == "APPROVED" and row["approval_type"] == "NEW_VENDOR":
            try:
                payload = json.loads(row["payload_json"])
                vendor = payload.get("provisional_vendor") or {}
                gstin = vendor.get("gstin")
                if gstin:
                    conn.execute("UPDATE vendors SET status='APPROVED', procurement_approved=1, updated_at=? WHERE gstin=?", (utcnow(), gstin))
                    vendor_id = conn.execute("SELECT id FROM vendors WHERE gstin=?", (gstin,)).fetchone()[0]
                    conn.execute("UPDATE purchase_orders SET vendor_id=? WHERE supplier_gstin=? AND vendor_id IS NULL", (vendor_id, gstin))
            except Exception:
                pass
        conn.commit()
        effect_summary = "Approval recorded."
        if row["approval_type"] == "NEW_VENDOR" and decision == "APPROVED":
            effect_summary = "Vendor onboarding approved. The provisional vendor is now APPROVED in the Vendor Master."
        elif row["approval_type"] == "NEW_VENDOR" and decision == "REJECTED":
            effect_summary = "Vendor onboarding rejected. No autonomous gate entry will be created for this request."
        elif decision == "APPROVED":
            effect_summary = "Human review approved. The exception has been acknowledged by an authorized person."
        elif decision == "REJECTED":
            effect_summary = "Human review rejected. The blocked request remains closed until corrected data is provided."
        audit("APPROVAL", "approval_decision", "OK", {"approval_id": approval_id, "decision": decision, "decided_by": decided_by, "approval_type": row["approval_type"], "entity_key": row["entity_key"]}, actor=decided_by)
        return {
            "ok":True,
            "approval_id":approval_id,
            "status":decision,
            "approval_type":row["approval_type"],
            "reason":row["reason"],
            "entity_key":row["entity_key"],
            "effect_summary":effect_summary,
            "decided_by":decided_by,
            "decided_at":utcnow(),
        }


def list_invoices() -> list[dict]:
    return all_rows("""
        SELECT i.invoice_number,
               COALESCE(v.vendor_code, '-') AS vendor_code,
               COALESCE(v.trade_name, i.supplier_gstin) AS vendor,
               i.supplier_gstin,
               i.po_number,
               i.invoice_date,
               i.amount,
               i.extraction_method,
               ROUND(i.extraction_confidence * 100, 1) AS confidence_pct,
               i.status,
               i.created_at
        FROM invoices i
        LEFT JOIN vendors v ON v.id=i.vendor_id
        ORDER BY i.id DESC
    """)


def list_gate_entries() -> list[dict]:
    return all_rows("""
        SELECT ge.gate_entry_number,
               i.invoice_number,
               v.vendor_code,
               v.trade_name AS vendor,
               ge.po_number,
               i.amount,
               ge.vehicle_number,
               ge.status,
               ge.created_at,
               ge.verified_at
        FROM gate_entries ge
        JOIN invoices i ON i.id=ge.invoice_id
        JOIN vendors v ON v.id=ge.vendor_id
        ORDER BY ge.id DESC
    """)


def list_policies() -> list[dict]:
    return all_rows("SELECT policy_key,value_text,description FROM policies ORDER BY policy_key")
