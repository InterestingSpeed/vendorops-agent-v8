# Included scenarios

The sample invoices are meant to test different business states, not just different-looking files.

1. **Happy path** — approved vendor, active GST, matching open PO, no duplicate -> verified gate entry.
2. **New vendor** — GST is active but the supplier is not in the vendor master -> provisional vendor + human approval.
3. **Inactive GST** — cancelled registration -> blocked and escalated.
4. **PO/vendor mismatch** — invoice supplier differs from PO supplier -> blocked.
5. **Duplicate invoice** — same supplier + invoice number already exists -> blocked.
6. **PO amount exception** — invoice would exceed the allowed remaining PO value -> review.
7. **Transient GST failure** — first lookup fails temporarily, retry succeeds.
8. **OCR fallback** — no QR, invoice fields come from Tesseract when confidence is good enough.
9. **Low-quality OCR** — uncertain critical fields -> human document review instead of guessing.
10. **Closed PO** — blocked.
11. **Missing PO** — blocked/reviewed.
12. **Blocked vendor** — supplier state prevents autonomous processing.

The tests also call backend write tools directly to check that an LLM cannot bypass the important controls.
