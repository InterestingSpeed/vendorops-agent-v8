from __future__ import annotations

from .db import connect


def verify_gstin(gstin: str) -> dict:
    gstin = gstin.strip().upper()
    with connect() as conn:
        fault = conn.execute(
            "SELECT * FROM api_faults WHERE service='GST' AND lookup_key=?", (gstin,)
        ).fetchone()
        if fault and fault["remaining_failures"] > 0:
            conn.execute(
                "UPDATE api_faults SET remaining_failures = remaining_failures - 1 WHERE service='GST' AND lookup_key=?",
                (gstin,),
            )
            conn.commit()
            return {
                "ok": False,
                "error": fault["error_code"],
                "message": fault["message"],
                "retryable": True,
            }
        row = conn.execute("SELECT * FROM gst_registry WHERE gstin=?", (gstin,)).fetchone()
        if not row:
            return {"ok": False, "error": "GSTIN_NOT_FOUND", "message": "GSTIN was not found in the demo registry.", "retryable": False}
        return {"ok": True, "profile": dict(row), "retryable": False}
