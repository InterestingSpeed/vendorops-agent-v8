from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .db import audit, connect, utcnow
from .gst_service import verify_gstin as gst_verify
from .invoice_parser import parse_invoice


def _policy(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value_text FROM policies WHERE policy_key=?", (key,)).fetchone()
        return row[0] if row else None


def _bool_policy(key: str, default: bool = False) -> bool:
    raw = _policy(key)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y"}


@dataclass
class ToolContext:
    invoice_path: Path | None = None


class VendorTools:
    def __init__(self, context: ToolContext | None = None):
        self.context = context or ToolContext()

    def _logged(self, name: str, args: dict, fn: Callable[[], dict]) -> dict:
        try:
            result = fn()
            audit("TOOL", name, "OK" if result.get("ok", True) else "FAILED", {"args": args, "result": result})
            return result
        except Exception as exc:
            result = {"ok": False, "error": "INTERNAL_TOOL_ERROR", "message": str(exc), "retryable": False}
            audit("TOOL", name, "ERROR", {"args": args, "result": result})
            return result

    def inspect_uploaded_invoice(self) -> dict:
        return self._logged("inspect_uploaded_invoice", {}, lambda: (
            {"ok": False, "error": "NO_INVOICE", "message": "No invoice is attached."}
            if not self.context.invoice_path else parse_invoice(self.context.invoice_path)
        ))

    def search_vendor(self, gstin: str) -> dict:
        def run():
            with connect() as conn:
                row = conn.execute("SELECT * FROM vendors WHERE gstin=?", (gstin.upper(),)).fetchone()
                return {"ok": True, "found": bool(row), "vendor": dict(row) if row else None}
        return self._logged("search_vendor", {"gstin": gstin}, run)

    def verify_gstin(self, gstin: str) -> dict:
        return self._logged("verify_gstin", {"gstin": gstin}, lambda: gst_verify(gstin))

    def search_purchase_order(self, po_number: str) -> dict:
        def run():
            with connect() as conn:
                row = conn.execute("SELECT * FROM purchase_orders WHERE po_number=?", (po_number.upper(),)).fetchone()
                return {"ok": True, "found": bool(row), "purchase_order": dict(row) if row else None}
        return self._logged("search_purchase_order", {"po_number": po_number}, run)

    def find_duplicate_invoice(self, supplier_gstin: str, invoice_number: str) -> dict:
        def run():
            with connect() as conn:
                row = conn.execute(
                    "SELECT * FROM invoices WHERE supplier_gstin=? AND invoice_number=?",
                    (supplier_gstin.upper(), invoice_number.upper()),
                ).fetchone()
                return {"ok": True, "duplicate": bool(row), "invoice": dict(row) if row else None}
        return self._logged("find_duplicate_invoice", {"supplier_gstin": supplier_gstin, "invoice_number": invoice_number}, run)

    def get_policy(self, policy_key: str) -> dict:
        def run():
            with connect() as conn:
                row = conn.execute("SELECT * FROM policies WHERE policy_key=?", (policy_key.upper(),)).fetchone()
                return {"ok": True, "found": bool(row), "policy": dict(row) if row else None}
        return self._logged("get_policy", {"policy_key": policy_key}, run)

    def create_provisional_vendor(self, gstin: str) -> dict:
        def run():
            gstin_u = gstin.upper()
            with connect() as conn:
                existing = conn.execute("SELECT * FROM vendors WHERE gstin=?", (gstin_u,)).fetchone()
                if existing:
                    return {"ok": True, "created": False, "vendor": dict(existing), "message": "Vendor already exists."}
                profile = conn.execute("SELECT * FROM gst_registry WHERE gstin=?", (gstin_u,)).fetchone()
                if not profile:
                    return {"ok": False, "error": "GST_PROFILE_REQUIRED", "message": "Cannot create vendor without a verified GST profile."}
                if profile["registration_status"] != "ACTIVE":
                    return {"ok": False, "error": "GST_NOT_ACTIVE", "message": "Cannot create a vendor from an inactive GST registration."}
                count = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
                code = f"V{count + 1:04d}"
                now = utcnow()
                cur = conn.execute("""
                    INSERT INTO vendors(vendor_code,legal_name,trade_name,gstin,status,address,state_code,contact_name,contact_email,bank_verified,procurement_approved,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (code, profile["legal_name"], profile["trade_name"], gstin_u, "PROVISIONAL", profile["address"], profile["state_code"], None, None, 0, 0, now, now))
                conn.commit()
                vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (cur.lastrowid,)).fetchone()
                return {"ok": True, "created": True, "vendor": dict(vendor)}
        return self._logged("create_provisional_vendor", {"gstin": gstin}, run)

    def request_human_approval(self, approval_type: str, entity_type: str, entity_key: str, reason: str, payload: dict | None = None) -> dict:
        def run():
            with connect() as conn:
                existing = conn.execute(
                    "SELECT * FROM approvals WHERE approval_type=? AND entity_type=? AND entity_key=? AND status='PENDING'",
                    (approval_type, entity_type, entity_key),
                ).fetchone()
                if existing:
                    return {"ok": True, "created": False, "approval": dict(existing)}
                cur = conn.execute("""
                    INSERT INTO approvals(approval_type,entity_type,entity_key,reason,payload_json,status,requested_at)
                    VALUES(?,?,?,?,?,'PENDING',?)
                """, (approval_type, entity_type, entity_key, reason, json.dumps(payload or {}, ensure_ascii=False), utcnow()))
                conn.commit()
                row = conn.execute("SELECT * FROM approvals WHERE id=?", (cur.lastrowid,)).fetchone()
                return {"ok": True, "created": True, "approval": dict(row)}
        return self._logged("request_human_approval", {"approval_type": approval_type, "entity_type": entity_type, "entity_key": entity_key, "reason": reason, "payload": payload or {}}, run)

    def send_notification(self, recipient: str, subject: str, body: str) -> dict:
        def run():
            with connect() as conn:
                cur = conn.execute("""
                    INSERT INTO notifications(channel,recipient,subject,body,status,created_at)
                    VALUES('EMAIL',?,?,?,'SENT',?)
                """, (recipient, subject, body, utcnow()))
                conn.commit()
                return {"ok": True, "notification_id": cur.lastrowid, "status": "SENT"}
        return self._logged("send_notification", {"recipient": recipient, "subject": subject}, run)

    def create_gate_entry(self, supplier_gstin: str, invoice_number: str, invoice_date: str | None, po_number: str, amount: float, vehicle_number: str | None = None, extraction_method: str = "UNKNOWN", extraction_confidence: float = 1.0) -> dict:
        args = locals().copy(); args.pop("self")
        def run():
            gstin = supplier_gstin.upper(); inv = invoice_number.upper(); po_no = po_number.upper()

            # If this action is tied to an uploaded invoice, do not trust the
            # model-supplied fields blindly. Re-read the document and ensure
            # the write request matches deterministic extraction.
            if self.context.invoice_path:
                parsed = parse_invoice(self.context.invoice_path)
                if not parsed.get("ok"):
                    return {"ok": False, "error": "DOCUMENT_NOT_VERIFIED", "message": "The attached invoice could not be deterministically verified before the write."}
                expected = {
                    "supplier_gstin": str(parsed.get("supplier_gstin") or "").upper(),
                    "invoice_number": str(parsed.get("invoice_number") or "").upper(),
                    "po_number": str(parsed.get("po_number") or "").upper(),
                }
                supplied = {"supplier_gstin": gstin, "invoice_number": inv, "po_number": po_no}
                mismatches = [k for k in expected if expected[k] and expected[k] != supplied[k]]
                parsed_amount = float(parsed.get("amount") or 0)
                if abs(parsed_amount - float(amount)) > 0.01:
                    mismatches.append("amount")
                if mismatches:
                    return {
                        "ok": False,
                        "error": "DOCUMENT_ARGUMENT_MISMATCH",
                        "message": "Write arguments do not match the attached invoice.",
                        "mismatched_fields": mismatches,
                    }
                if parsed.get("extraction_method") == "OCR":
                    min_conf = float(_policy("OCR_MIN_CONFIDENCE") or "0.65")
                    if float(parsed.get("confidence", 0)) < min_conf:
                        return {
                            "ok": False,
                            "error": "LOW_OCR_CONFIDENCE",
                            "message": f"OCR confidence {float(parsed.get('confidence',0)):.2f} is below required threshold {min_conf:.2f}.",
                        }

            with connect() as conn:
                vendor = conn.execute("SELECT * FROM vendors WHERE gstin=?", (gstin,)).fetchone()
                if not vendor:
                    return {"ok": False, "error": "VENDOR_NOT_FOUND", "message": "Vendor does not exist in vendor master."}
                if _bool_policy("APPROVED_VENDOR_REQUIRED", True) and vendor["status"] != "APPROVED":
                    return {"ok": False, "error": "VENDOR_NOT_APPROVED", "message": f"Vendor status is {vendor['status']}."}

                gst = conn.execute("SELECT * FROM gst_registry WHERE gstin=?", (gstin,)).fetchone()
                if _bool_policy("GST_ACTIVE_REQUIRED", True) and (not gst or gst["registration_status"] != "ACTIVE"):
                    return {"ok": False, "error": "GST_NOT_ACTIVE", "message": "Supplier GST registration is not ACTIVE."}

                po = conn.execute("SELECT * FROM purchase_orders WHERE po_number=?", (po_no,)).fetchone()
                if not po:
                    return {"ok": False, "error": "PO_NOT_FOUND", "message": "Purchase order not found."}
                if po["status"] != "OPEN":
                    return {"ok": False, "error": "PO_NOT_OPEN", "message": f"Purchase order status is {po['status']}."}
                if po["supplier_gstin"] != gstin:
                    return {"ok": False, "error": "PO_VENDOR_MISMATCH", "message": "Invoice GSTIN does not match the supplier GSTIN on the PO."}

                duplicate = conn.execute("SELECT * FROM invoices WHERE supplier_gstin=? AND invoice_number=?", (gstin, inv)).fetchone()
                if duplicate:
                    return {"ok": False, "error": "DUPLICATE_INVOICE", "message": "An invoice with the same GSTIN and invoice number already exists.", "invoice_id": duplicate["id"]}

                tolerance_pct = float(_policy("PO_AMOUNT_TOLERANCE_PCT") or "0")
                remaining = float(po["total_amount"]) - float(po["amount_consumed"])
                permitted = remaining * (1 + tolerance_pct / 100.0)
                if float(amount) > permitted + 1e-9:
                    return {"ok": False, "error": "PO_AMOUNT_EXCEEDED", "message": f"Invoice amount {amount:.2f} exceeds permitted remaining PO value {permitted:.2f} (remaining {remaining:.2f}, tolerance {tolerance_pct:.1f}%).", "remaining": remaining, "permitted": permitted}

                now = utcnow()
                source = str(self.context.invoice_path.name) if self.context.invoice_path else None
                try:
                    cur = conn.execute("""
                        INSERT INTO invoices(invoice_number,supplier_gstin,vendor_id,po_number,invoice_date,amount,source_file,extraction_method,extraction_confidence,status,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,'GATE_CREATED',?)
                    """, (inv, gstin, vendor["id"], po_no, invoice_date, float(amount), source, extraction_method, float(extraction_confidence), now))
                except Exception as exc:
                    if "UNIQUE constraint failed" in str(exc):
                        return {"ok": False, "error": "DUPLICATE_INVOICE", "message": "Duplicate invoice blocked by database uniqueness constraint."}
                    raise
                invoice_id = cur.lastrowid
                seq = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM gate_entries").fetchone()[0]
                ge_no = f"GE-{seq:04d}"
                conn.execute("""
                    INSERT INTO gate_entries(gate_entry_number,invoice_id,po_number,vendor_id,vehicle_number,status,reason,created_at,verified_at)
                    VALUES(?,?,?,?,?,'CREATED',NULL,?,?)
                """, (ge_no, invoice_id, po_no, vendor["id"], vehicle_number, now, now))
                conn.execute("UPDATE purchase_orders SET amount_consumed = amount_consumed + ? WHERE id=?", (float(amount), po["id"]))
                conn.commit()
                verify = conn.execute("""
                    SELECT ge.*, i.invoice_number, v.vendor_code, v.legal_name
                    FROM gate_entries ge JOIN invoices i ON i.id=ge.invoice_id JOIN vendors v ON v.id=ge.vendor_id
                    WHERE ge.gate_entry_number=?
                """, (ge_no,)).fetchone()
                return {"ok": True, "gate_entry": dict(verify), "verified": bool(verify and verify["status"] == "CREATED")}
        return self._logged("create_gate_entry", args, run)

    def list_gate_entries(self, status: str | None = None) -> dict:
        def run():
            with connect() as conn:
                sql = """
                    SELECT ge.*, i.invoice_number, i.supplier_gstin, i.amount, v.vendor_code, v.trade_name
                    FROM gate_entries ge JOIN invoices i ON i.id=ge.invoice_id JOIN vendors v ON v.id=ge.vendor_id
                """
                params = []
                if status:
                    sql += " WHERE ge.status=?"; params.append(status.upper())
                sql += " ORDER BY ge.id DESC LIMIT 100"
                rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
                return {"ok": True, "count": len(rows), "gate_entries": rows}
        return self._logged("list_gate_entries", {"status": status}, run)

    def list_pending_approvals(self) -> dict:
        def run():
            with connect() as conn:
                rows = [dict(r) for r in conn.execute("SELECT * FROM approvals WHERE status='PENDING' ORDER BY id DESC").fetchall()]
                return {"ok": True, "count": len(rows), "approvals": rows}
        return self._logged("list_pending_approvals", {}, run)

    def tool_map(self) -> dict[str, Callable[..., dict]]:
        return {
            "inspect_uploaded_invoice": self.inspect_uploaded_invoice,
            "search_vendor": self.search_vendor,
            "verify_gstin": self.verify_gstin,
            "search_purchase_order": self.search_purchase_order,
            "find_duplicate_invoice": self.find_duplicate_invoice,
            "get_policy": self.get_policy,
            "create_provisional_vendor": self.create_provisional_vendor,
            "request_human_approval": self.request_human_approval,
            "send_notification": self.send_notification,
            "create_gate_entry": self.create_gate_entry,
            "list_gate_entries": self.list_gate_entries,
            "list_pending_approvals": self.list_pending_approvals,
        }
