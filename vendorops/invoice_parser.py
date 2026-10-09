from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
INVOICE_RE = re.compile(r"(?:INVOICE\s*(?:NO\.?|NUMBER)|INV\s*NO\.?)\s*[:#-]?\s*([A-Z0-9\-/]+)", re.I)
PO_RE = re.compile(r"(?:PO\s*(?:NO\.?|NUMBER)?)\s*[:#-]?\s*([A-Z0-9\-/]+)", re.I)
AMOUNT_RE = re.compile(r"(?:TOTAL\s*(?:AMOUNT)?|INVOICE\s*VALUE|AMOUNT)\s*[:₹Rs.]*\s*([0-9,]+(?:\.\d{1,2})?)", re.I)
DATE_RE = re.compile(r"(?:INVOICE\s*DATE|DATE)\s*[:#-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})", re.I)
VEHICLE_RE = re.compile(r"(?:VEHICLE\s*(?:NO\.?|NUMBER)?)\s*[:#-]?\s*([A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{3,4})", re.I)


def _decode_qr(path: Path) -> dict[str, Any] | None:
    image = cv2.imread(str(path))
    if image is None:
        return None
    detector = cv2.QRCodeDetector()
    data, _, _ = detector.detectAndDecode(image)
    if not data:
        return None
    try:
        payload = json.loads(data)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        return None
    return None


def _ocr(path: Path) -> tuple[str, float]:
    try:
        import pytesseract
    except Exception as exc:
        raise RuntimeError("pytesseract is not installed") from exc

    img = Image.open(path).convert("RGB")
    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    data = pytesseract.image_to_data(th, output_type=pytesseract.Output.DICT, config="--psm 6")
    tokens = []
    confidences = []
    for txt, conf in zip(data.get("text", []), data.get("conf", [])):
        txt = (txt or "").strip()
        try:
            c = float(conf)
        except (TypeError, ValueError):
            c = -1
        if txt:
            tokens.append(txt)
            if c >= 0:
                confidences.append(c)
    text = " ".join(tokens)
    confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
    return text, max(0.0, min(1.0, confidence))


def _first(regex: re.Pattern, text: str) -> str | None:
    m = regex.search(text)
    return m.group(1).strip() if m else None


def parse_invoice(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"ok": False, "error": "FILE_NOT_FOUND", "message": f"Invoice file not found: {path}"}

    qr = _decode_qr(path)
    if qr:
        result = {
            "supplier_gstin": qr.get("supplier_gstin"),
            "invoice_number": qr.get("invoice_number"),
            "invoice_date": qr.get("invoice_date"),
            "po_number": qr.get("po_number"),
            "amount": float(qr["amount"]) if qr.get("amount") is not None else None,
            "vehicle_number": qr.get("vehicle_number"),
            "extraction_method": "QR",
            "confidence": 1.0,
            "raw_text": None,
        }
        result["missing_fields"] = [k for k in ("supplier_gstin", "invoice_number", "po_number", "amount") if result.get(k) in (None, "")]
        result["ok"] = not result["missing_fields"]
        return result

    try:
        text, confidence = _ocr(path)
    except Exception as exc:
        return {"ok": False, "error": "OCR_UNAVAILABLE", "message": str(exc), "confidence": 0.0}

    upper = text.upper()
    gst_match = GSTIN_RE.search(upper)
    gstin = gst_match.group(0) if gst_match else None
    if not gstin:
        # OCR commonly confuses 0/O and can insert a stray character around the fixed "Z" position.
        labelled = re.search(r"GSTIN\s*[:#-]?\s*([A-Z0-9]{14,18})", upper)
        if labelled:
            token = labelled.group(1).replace("O", "0", 1)
            if len(token) == 16 and token[14] == "Z":
                token = token[:13] + token[14:]  # drop one OCR artefact before the fixed Z
            if GSTIN_RE.fullmatch(token):
                gstin = token
    amount_raw = _first(AMOUNT_RE, text)
    result = {
        "supplier_gstin": gstin,
        "invoice_number": _first(INVOICE_RE, text),
        "invoice_date": _first(DATE_RE, text),
        "po_number": _first(PO_RE, text),
        "amount": float(amount_raw.replace(",", "")) if amount_raw else None,
        "vehicle_number": (_first(VEHICLE_RE, text) or "").replace(" ", "") or None,
        "extraction_method": "OCR",
        "confidence": round(confidence, 3),
        "raw_text": text,
    }
    result["missing_fields"] = [k for k in ("supplier_gstin", "invoice_number", "po_number", "amount") if result.get(k) in (None, "")]
    result["ok"] = not result["missing_fields"]
    return result
