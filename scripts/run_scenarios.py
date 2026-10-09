from __future__ import annotations
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vendorops.seed import reset_database
from vendorops.agent import VendorOpsAgent

FILES=[
'01_happy_path_qr.png','02_new_vendor_qr.png','03_inactive_gst_qr.png','04_po_vendor_mismatch_qr.png',
'05_duplicate_invoice_qr.png','06_amount_exception_qr.png','07_transient_gst_qr.png','08_ocr_no_qr.png',
'09_low_quality_ocr.png','10_closed_po_qr.png','11_missing_po_qr.png','12_blocked_vendor_qr.png']
for f in FILES:
    reset_database()
    result=VendorOpsAgent(ROOT/'data'/'invoices'/f).run('Process this invoice and complete whatever can safely be completed. Verify the result.')
    print(f'{f:32} {result.status:16} {result.summary}')
