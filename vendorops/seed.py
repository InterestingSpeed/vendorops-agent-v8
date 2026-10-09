from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import DATA_DIR, db_path
from .db import connect, utcnow
from .schema import SCHEMA

GST = {
    "ganesh": "09AAECS1234F1Z5",
    "arvind": "09AABCA5678K1Z2",
    "northstar": "07AAEFN9012L1Z4",
    "prakash": "09ABCDE1234F1Z5",
    "oldline": "09AACCO4321M1Z7",
    "nimbus": "09AAACN7777P1Z8",
    "trident": "09AAACT8888R1Z6",
    "dormant": "09AAACD9999Q1Z1",
}


def _clear_database_in_place(path: Path) -> None:
    """Reset SQLite without deleting the file.

    On Windows, the running FastAPI process (or SQLite WAL handles) can keep the
    database file open briefly. Deleting the file then raises WinError 32.
    Dropping/recreating the schema through SQLite itself is safe for the live
    demo and works cross-platform.
    """
    last_error: Exception | None = None
    for attempt in range(8):
        conn = None
        try:
            conn = connect(path)
            conn.execute("PRAGMA foreign_keys = OFF")
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for name in tables:
                safe_name = name.replace('"', '""')
                conn.execute(f'DROP TABLE IF EXISTS "{safe_name}"')
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            time.sleep(0.15 * (attempt + 1))
        finally:
            if conn is not None:
                conn.close()
    if last_error:
        raise last_error


def reset_database(path: Path | None = None) -> Path:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _clear_database_in_place(path)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        try:
            conn.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass
        now = utcnow()

        gst_rows = [
            (GST["ganesh"], "Shree Ganesh Packaging Private Limited", "Ganesh Packaging", "ACTIVE", "2018-07-01", "Private Limited Company", "Site IV, Greater Noida, Uttar Pradesh", "09"),
            (GST["arvind"], "Arvind Industrial Chemicals Private Limited", "Arvind Chemicals", "ACTIVE", "2017-09-15", "Private Limited Company", "Surajpur Industrial Area, Greater Noida, Uttar Pradesh", "09"),
            (GST["northstar"], "Northstar Logistics LLP", "Northstar Logistics", "ACTIVE", "2019-04-12", "Limited Liability Partnership", "Okhla Industrial Estate, New Delhi", "07"),
            (GST["prakash"], "Prakash Engineering Works", "Prakash Engineering", "ACTIVE", "2020-01-04", "Proprietorship", "Ghaziabad, Uttar Pradesh", "09"),
            (GST["oldline"], "Oldline Components Private Limited", "Oldline Components", "ACTIVE", "2018-10-10", "Private Limited Company", "Noida, Uttar Pradesh", "09"),
            (GST["nimbus"], "Nimbus Packaging Solutions Private Limited", "Nimbus Packaging", "ACTIVE", "2024-05-20", "Private Limited Company", "Kasna, Greater Noida, Uttar Pradesh", "09"),
            (GST["trident"], "Trident Metals Private Limited", "Trident Metals", "ACTIVE", "2021-03-11", "Private Limited Company", "Sikandrabad, Uttar Pradesh", "09"),
            (GST["dormant"], "Dormant Traders Private Limited", "Dormant Traders", "CANCELLED", "2017-07-01", "Private Limited Company", "Noida, Uttar Pradesh", "09"),
        ]
        conn.executemany("INSERT INTO gst_registry VALUES(?,?,?,?,?,?,?,?)", gst_rows)

        vendors = [
            ("V0001", "Shree Ganesh Packaging Private Limited", "Ganesh Packaging", GST["ganesh"], "APPROVED", "Site IV, Greater Noida, Uttar Pradesh", "09", "Rohit Mehra", "rohit@ganesh.example", 1, 1, now, now),
            ("V0002", "Arvind Industrial Chemicals Private Limited", "Arvind Chemicals", GST["arvind"], "APPROVED", "Surajpur Industrial Area, Greater Noida, Uttar Pradesh", "09", "Neha Arora", "neha@arvind.example", 1, 1, now, now),
            ("V0003", "Northstar Logistics LLP", "Northstar Logistics", GST["northstar"], "APPROVED", "Okhla Industrial Estate, New Delhi", "07", "Sahil Jain", "sahil@northstar.example", 1, 1, now, now),
            ("V0004", "Prakash Engineering Works", "Prakash Engineering", GST["prakash"], "APPROVED", "Ghaziabad, Uttar Pradesh", "09", "Prakash Verma", "prakash@example.test", 1, 1, now, now),
            ("V0005", "Oldline Components Private Limited", "Oldline Components", GST["oldline"], "BLOCKED", "Noida, Uttar Pradesh", "09", "Accounts Team", "accounts@oldline.example", 1, 0, now, now),
            ("V0006", "Trident Metals Private Limited", "Trident Metals", GST["trident"], "APPROVED", "Sikandrabad, Uttar Pradesh", "09", "Aditi Singh", "aditi@trident.example", 1, 1, now, now),
        ]
        conn.executemany("""
            INSERT INTO vendors(vendor_code,legal_name,trade_name,gstin,status,address,state_code,contact_name,contact_email,bank_verified,procurement_approved,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, vendors)

        # Additional realistic synthetic suppliers make the company database useful for browsing/query demos.
        extra_suppliers = [
            ("Apex Fasteners Private Limited", "Apex Fasteners", "09AACAA1001A1Z1", "Noida, Uttar Pradesh"),
            ("Bharat Polymer Industries", "Bharat Polymer", "09AACBB1002B1Z2", "Ghaziabad, Uttar Pradesh"),
            ("Crown Cables Private Limited", "Crown Cables", "09AACCC1003C1Z3", "Greater Noida, Uttar Pradesh"),
            ("Delta Bearings LLP", "Delta Bearings", "06AACDD1004D1Z4", "Faridabad, Haryana"),
            ("Everest Safety Products Private Limited", "Everest Safety", "07AACEE1005E1Z5", "New Delhi"),
            ("Falcon Plastics Private Limited", "Falcon Plastics", "09AACFF1006F1Z6", "Noida, Uttar Pradesh"),
            ("Greenline Industrial Supplies LLP", "Greenline Industrial", "09AACGG1007G1Z7", "Greater Noida, Uttar Pradesh"),
            ("Horizon Paper Products Private Limited", "Horizon Paper", "09AACHH1008H1Z8", "Sahibabad, Uttar Pradesh"),
            ("Indus Lubricants Private Limited", "Indus Lubricants", "06AACII1009I1Z9", "Gurugram, Haryana"),
            ("JKM Tools and Components", "JKM Tools", "09AACJJ1010J1ZA", "Ghaziabad, Uttar Pradesh"),
            ("Kaveri Labels Private Limited", "Kaveri Labels", "09AACKK1011K1ZB", "Noida, Uttar Pradesh"),
            ("Lotus Hardware Solutions Private Limited", "Lotus Hardware", "07AACLL1012L1ZC", "New Delhi"),
        ]
        for i, (legal, trade, gstin, address) in enumerate(extra_suppliers, start=7):
            state = gstin[:2]
            conn.execute("INSERT INTO gst_registry VALUES(?,?,?,?,?,?,?,?)",
                         (gstin, legal, trade, "ACTIVE", "2022-04-01", "Private/LLP/Proprietorship", address, state))
            conn.execute("""
                INSERT INTO vendors(vendor_code,legal_name,trade_name,gstin,status,address,state_code,contact_name,contact_email,bank_verified,procurement_approved,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (f"V{i:04d}", legal, trade, gstin, "APPROVED", address, state, f"Vendor Contact {i}", f"accounts{i}@supplier.example", 1, 1, now, now))

        vendor_ids = {r["gstin"]: r["id"] for r in conn.execute("SELECT id,gstin FROM vendors")}
        pos = [
            ("PO-1001", vendor_ids[GST["ganesh"]], GST["ganesh"], "5-ply corrugated cartons", 5000, 20.0, 100000.0, 0.0, "INR", "OPEN", "2026-10-15", now),
            ("PO-1002", vendor_ids[GST["arvind"]], GST["arvind"], "Industrial cleaning solvent drums", 100, 2500.0, 250000.0, 50000.0, "INR", "OPEN", "2026-10-12", now),
            ("PO-1003", vendor_ids[GST["northstar"]], GST["northstar"], "Monthly inbound logistics service", 1, 75000.0, 75000.0, 0.0, "INR", "OPEN", "2026-10-31", now),
            ("PO-1004", vendor_ids[GST["ganesh"]], GST["ganesh"], "Legacy carton order", 1000, 18.0, 18000.0, 18000.0, "INR", "CLOSED", "2026-09-01", now),
            ("PO-1005", None, GST["nimbus"], "Printed secondary packaging", 2000, 15.0, 30000.0, 0.0, "INR", "OPEN", "2026-10-20", now),
            ("PO-1006", vendor_ids[GST["trident"]], GST["trident"], "Stainless steel sheets", 50, 4000.0, 200000.0, 0.0, "INR", "OPEN", "2026-10-20", now),
            ("PO-1008", vendor_ids[GST["prakash"]], GST["prakash"], "Machined spares", 10, 5000.0, 50000.0, 0.0, "INR", "OPEN", "2026-10-18", now),
            ("PO-1010", vendor_ids[GST["oldline"]], GST["oldline"], "Electrical components", 20, 3000.0, 60000.0, 0.0, "INR", "OPEN", "2026-10-25", now),
        ]
        for j, (legal, trade, gstin, address) in enumerate(extra_suppliers, start=1):
            total = 80000.0 + (j * 5000.0)
            pos.append((f"PO-{2000+j}", vendor_ids[gstin], gstin, f"Recurring supply order - {trade}", 100, total/100.0, total, 0.0, "INR", "OPEN", "2026-11-15", now))

        conn.executemany("""
            INSERT INTO purchase_orders(po_number,vendor_id,supplier_gstin,description,ordered_qty,unit_price,total_amount,amount_consumed,currency,status,expected_by,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, pos)

        # Historical invoice + gate entry used to prove duplicate detection.
        cur = conn.execute("""
            INSERT INTO invoices(invoice_number,supplier_gstin,vendor_id,po_number,invoice_date,amount,source_file,extraction_method,extraction_confidence,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, ("INV-DUP-001", GST["ganesh"], vendor_ids[GST["ganesh"]], "PO-1001", "2026-10-01", 12000.0, "historical", "QR", 1.0, "GATE_CREATED", now))
        invoice_id = cur.lastrowid
        conn.execute("""
            INSERT INTO gate_entries(gate_entry_number,invoice_id,po_number,vendor_id,vehicle_number,status,reason,created_at,verified_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, ("GE-0001", invoice_id, "PO-1001", vendor_ids[GST["ganesh"]], "UP16AB1234", "CREATED", None, now, now))
        conn.execute("UPDATE purchase_orders SET amount_consumed = amount_consumed + 12000 WHERE po_number='PO-1001'")

        # A small history of successfully processed inward invoices gives the prototype realistic state.
        for j, (legal, trade, gstin, address) in enumerate(extra_suppliers, start=1):
            po_no = f"PO-{2000+j}"
            amount = 10000.0 + j * 750.0
            cur = conn.execute("""
                INSERT INTO invoices(invoice_number,supplier_gstin,vendor_id,po_number,invoice_date,amount,source_file,extraction_method,extraction_confidence,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,'GATE_CREATED',?)
            """, (f"HIST-{j:03d}", gstin, vendor_ids[gstin], po_no, f"2026-09-{min(j+5,28):02d}", amount, "historical", "QR", 1.0, now))
            inv_id = cur.lastrowid
            conn.execute("""
                INSERT INTO gate_entries(gate_entry_number,invoice_id,po_number,vendor_id,vehicle_number,status,reason,created_at,verified_at)
                VALUES(?,?,?,?,?,'CREATED',NULL,?,?)
            """, (f"GE-{j+1:04d}", inv_id, po_no, vendor_ids[gstin], f"UP16HX{j:04d}", now, now))
            conn.execute("UPDATE purchase_orders SET amount_consumed = amount_consumed + ? WHERE po_number=?", (amount, po_no))

        policies = [
            ("GST_ACTIVE_REQUIRED", "true", "Supplier GST registration must be ACTIVE before automated gate creation."),
            ("APPROVED_VENDOR_REQUIRED", "true", "Only APPROVED vendors can receive an automated gate entry."),
            ("NEW_VENDOR_REQUIRES_APPROVAL", "true", "New GSTINs may be pre-populated as PROVISIONAL but require procurement approval."),
            ("PO_AMOUNT_TOLERANCE_PCT", "2", "Invoice amount may not exceed the PO remaining value by more than 2% without human review."),
            ("OCR_MIN_CONFIDENCE", "0.65", "OCR-only extraction below this confidence must be confirmed by a human."),
            ("GST_TRANSIENT_MAX_RETRIES", "2", "Retry temporary GST service failures up to two times before escalating."),
        ]
        conn.executemany("INSERT INTO policies VALUES(?,?,?)", policies)

        # First GST lookup for Trident fails, second succeeds.
        conn.execute("INSERT INTO api_faults(service,lookup_key,remaining_failures,error_code,message) VALUES(?,?,?,?,?)",
                     ("GST", GST["trident"], 1, "TIMEOUT", "GST verification service temporarily timed out."))
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    print(reset_database())
