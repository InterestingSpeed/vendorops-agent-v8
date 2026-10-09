from __future__ import annotations
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import qrcode

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'invoices'; OUT.mkdir(parents=True,exist_ok=True)
try:
    FONT=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',28)
    BOLD=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',32)
except Exception:
    FONT=BOLD=None

SCENARIOS=[
('01_happy_path_qr.png', dict(supplier_gstin='09AAECS1234F1Z5',invoice_number='INV-2026-1001',invoice_date='2026-10-08',po_number='PO-1001',amount=15000.0,vehicle_number='UP16CD4321'), True, False),
('02_new_vendor_qr.png', dict(supplier_gstin='09AAACN7777P1Z8',invoice_number='NIM-2026-001',invoice_date='2026-10-08',po_number='PO-1005',amount=22000.0,vehicle_number='UP16EF2222'), True, False),
('03_inactive_gst_qr.png', dict(supplier_gstin='09AAACD9999Q1Z1',invoice_number='DOR-2026-041',invoice_date='2026-10-08',po_number='PO-DOR-01',amount=18000.0,vehicle_number='UP16AA1000'), True, False),
('04_po_vendor_mismatch_qr.png', dict(supplier_gstin='09AAECS1234F1Z5',invoice_number='INV-2026-MIS',invoice_date='2026-10-08',po_number='PO-1002',amount=10000.0,vehicle_number='UP16GH8765'), True, False),
('05_duplicate_invoice_qr.png', dict(supplier_gstin='09AAECS1234F1Z5',invoice_number='INV-DUP-001',invoice_date='2026-10-01',po_number='PO-1001',amount=12000.0,vehicle_number='UP16AB1234'), True, False),
('06_amount_exception_qr.png', dict(supplier_gstin='09ABCDE1234F1Z5',invoice_number='PRA-2026-019',invoice_date='2026-10-08',po_number='PO-1008',amount=70000.0,vehicle_number='UP14JK9911'), True, False),
('07_transient_gst_qr.png', dict(supplier_gstin='09AAACT8888R1Z6',invoice_number='TRI-2026-007',invoice_date='2026-10-08',po_number='PO-1006',amount=40000.0,vehicle_number='UP13LM1010'), True, False),
('08_ocr_no_qr.png', dict(supplier_gstin='09AABCA5678K1Z2',invoice_number='ARV-2026-088',invoice_date='2026-10-08',po_number='PO-1002',amount=25000.0,vehicle_number='UP16MN2020'), False, False),
('09_low_quality_ocr.png', dict(supplier_gstin='09AABCA5678K1Z2',invoice_number='ARV-2026-LOW',invoice_date='2026-10-08',po_number='PO-1002',amount=25000.0,vehicle_number='UP16MN2021'), False, True),
('10_closed_po_qr.png', dict(supplier_gstin='09AAECS1234F1Z5',invoice_number='INV-2026-CLOSED',invoice_date='2026-10-08',po_number='PO-1004',amount=5000.0,vehicle_number='UP16PQ3030'), True, False),
('11_missing_po_qr.png', dict(supplier_gstin='09AAECS1234F1Z5',invoice_number='INV-2026-NOPO',invoice_date='2026-10-08',po_number='PO-9999',amount=5000.0,vehicle_number='UP16RS4040'), True, False),
('12_blocked_vendor_qr.png', dict(supplier_gstin='09AACCO4321M1Z7',invoice_number='OLD-2026-012',invoice_date='2026-10-08',po_number='PO-1010',amount=15000.0,vehicle_number='UP16TU5050'), True, False),
]

def make(name,data,with_qr,low):
    img=Image.new('RGB',(1450,900),'white'); d=ImageDraw.Draw(img)
    d.text((70,45),'TAX INVOICE',font=BOLD,fill='black')
    lines=[
        ('Supplier GSTIN',data['supplier_gstin']),('Invoice No',data['invoice_number']),('Invoice Date',data['invoice_date']),
        ('PO No',data['po_number']),('Total Amount',f"{data['amount']:.2f}"),('Vehicle No',data['vehicle_number']),
        ('Ship To','Demo Manufacturing Plant, Greater Noida'),('Note','Synthetic invoice generated for VendorOps hiring prototype')]
    y=140
    for k,v in lines:
        d.text((80,y),f'{k}: {v}',font=FONT,fill='black'); y+=70
    if with_qr:
        qr=qrcode.QRCode(version=None,box_size=5,border=2); qr.add_data(json.dumps(data,separators=(',',':')));qr.make(fit=True)
        q=qr.make_image(fill_color='black',back_color='white').convert('RGB').resize((330,330))
        img.paste(q,(1030,100))
        d.text((1035,445),'Structured demo QR',font=FONT,fill='black')
    if low:
        img=img.resize((725,450)).resize((1450,900)).filter(ImageFilter.GaussianBlur(radius=2.2))
        overlay=Image.new('RGBA',img.size,(255,255,255,0)); od=ImageDraw.Draw(overlay)
        for y in range(30,900,45): od.line((0,y,1450,y+15),fill=(150,150,150,65),width=4)
        img=Image.alpha_composite(img.convert('RGBA'),overlay).convert('RGB')
    img.save(OUT/name,quality=92)

for s in SCENARIOS: make(*s)
print(f'generated {len(SCENARIOS)} invoices in {OUT}')
