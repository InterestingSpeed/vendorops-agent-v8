from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import (
    groq_api_key,
    groq_base_url,
    groq_model,
    groq_rate_limit_max_wait,
    groq_rate_limit_retries,
    llm_max_output_tokens,
    llm_max_steps,
    llm_timeout_seconds,
    ollama_base_url,
    ollama_model,
)
from .tools import ToolContext, VendorTools

SYSTEM_INSTRUCTIONS = """You are VendorOps Agent, an autonomous but safety-constrained vendor inward operations employee.
Your job is to complete the user's operational goal using only the provided tools.
Rules:
- Never invent company database facts or claim an action succeeded without tool evidence.
- Prefer deterministic company data/tools over guesses.
- For an uploaded invoice: inspect it, validate GST, check vendor, PO, duplicate status, then execute only if safe.
- Retry retryable/transient tool failures. Do not treat temporary service failure as invalid business data.
- A new vendor may be pre-populated as PROVISIONAL from a verified ACTIVE GST profile, but must not be treated as APPROVED.
- Do not override blocked vendors, inactive GST registrations, PO/vendor mismatches, closed/missing POs, duplicates, low-confidence critical fields, or material amount exceptions. Escalate with a human approval request instead.
- Keep an auditable sequence of actions. Verify final state after writes.
- Do not invent capabilities. If the user's goal exceeds the available tools, explain the limitation.
- Use tools for company facts and actions; do not merely describe what a human should do when a permitted tool can actually do it.
- For broad invoice-processing requests, DO NOT stop after merely identifying or verifying the vendor. The task is terminal only when either (a) a gate entry has been created and verified after the required checks, or (b) a human approval/review request has been created for a genuine blocker.
- If you create a human approval request, stop unsafe execution and clearly tell the user what needs review.
"""

FAST_PLANNER_INSTRUCTIONS = """You are the lightweight intent/planning layer for VendorOps.
Return ONLY one compact JSON object; no markdown and no prose outside JSON.
Choose exactly one intent from:
- PROCESS_INVOICE: execute the attached invoice through the safe inward/gate workflow.
- INSPECT_INVOICE: only read/extract the attached invoice; do not create or update records.
- LIST_PENDING_APPROVALS: inspect the human approval queue.
- LIST_GATE_ENTRIES: inspect existing gate entries.
- CANNOT_DO: the request is outside the available VendorOps capabilities.

Schema:
{"intent":"...","goal":"short restatement","reason":"one short sentence"}
Do not invent invoice values or company facts. This call only interprets the user's goal; deterministic business tools perform and verify the actual work.
"""

# Canonical function definitions. They are converted into OpenAI-compatible
# Chat Completions tool schemas, which both Groq and Ollama support.
FUNCTION_DEFINITIONS = [
    {"name":"inspect_uploaded_invoice","description":"Extract supplier GSTIN, invoice number/date, PO, amount and vehicle number from the currently uploaded invoice. Uses QR first and OCR fallback.","parameters":{"type":"object","properties":{},"additionalProperties":False}},
    {"name":"search_vendor","description":"Search the vendor master by GSTIN.","parameters":{"type":"object","properties":{"gstin":{"type":"string"}},"required":["gstin"],"additionalProperties":False}},
    {"name":"verify_gstin","description":"Verify a GSTIN against the demo GST registry. May return a retryable transient error.","parameters":{"type":"object","properties":{"gstin":{"type":"string"}},"required":["gstin"],"additionalProperties":False}},
    {"name":"search_purchase_order","description":"Fetch a purchase order by PO number.","parameters":{"type":"object","properties":{"po_number":{"type":"string"}},"required":["po_number"],"additionalProperties":False}},
    {"name":"find_duplicate_invoice","description":"Check whether this supplier GSTIN and invoice number already exist.","parameters":{"type":"object","properties":{"supplier_gstin":{"type":"string"},"invoice_number":{"type":"string"}},"required":["supplier_gstin","invoice_number"],"additionalProperties":False}},
    {"name":"get_policy","description":"Read a company policy by policy key.","parameters":{"type":"object","properties":{"policy_key":{"type":"string"}},"required":["policy_key"],"additionalProperties":False}},
    {"name":"create_provisional_vendor","description":"Create a PROVISIONAL vendor profile from an already-verifiable ACTIVE GST profile.","parameters":{"type":"object","properties":{"gstin":{"type":"string"}},"required":["gstin"],"additionalProperties":False}},
    {"name":"request_human_approval","description":"Create a human approval/review request for an unsafe or ambiguous case.","parameters":{"type":"object","properties":{"approval_type":{"type":"string"},"entity_type":{"type":"string"},"entity_key":{"type":"string"},"reason":{"type":"string"},"payload":{"type":"object"}},"required":["approval_type","entity_type","entity_key","reason"],"additionalProperties":False}},
    {"name":"send_notification","description":"Send a simulated email notification. It is persisted in the outbox and can be inspected.","parameters":{"type":"object","properties":{"recipient":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["recipient","subject","body"],"additionalProperties":False}},
    {"name":"create_gate_entry","description":"Create and verify a gate entry. The backend independently enforces approved-vendor, active-GST, open-PO, PO-vendor match, duplicate, and PO-value rules.","parameters":{"type":"object","properties":{"supplier_gstin":{"type":"string"},"invoice_number":{"type":"string"},"invoice_date":{"type":"string"},"po_number":{"type":"string"},"amount":{"type":"number"},"vehicle_number":{"type":"string"},"extraction_method":{"type":"string"},"extraction_confidence":{"type":"number"}},"required":["supplier_gstin","invoice_number","invoice_date","po_number","amount"],"additionalProperties":False}},
    {"name":"list_gate_entries","description":"List gate entries, optionally filtered by status.","parameters":{"type":"object","properties":{"status":{"type":"string"}},"additionalProperties":False}},
    {"name":"list_pending_approvals","description":"List current pending human approvals.","parameters":{"type":"object","properties":{},"additionalProperties":False}},
]

CHAT_TOOLS = [{"type": "function", "function": item} for item in FUNCTION_DEFINITIONS]


def _compact_tool_result_for_llm(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Keep the model context small while preserving the facts needed for the next decision.

    The full result is still retained in the audit trace; only the copy sent back to
    the LLM is compacted. This matters on free-tier TPM limits because every tool
    result is resent on subsequent model turns.
    """
    if not isinstance(result, dict):
        return {"ok": False, "message": str(result)}

    if name == "inspect_uploaded_invoice":
        keys = ("ok","supplier_gstin","invoice_number","invoice_date","po_number","amount","vehicle_number","extraction_method","confidence","missing_fields","message")
        return {k: result.get(k) for k in keys if k in result}
    if name == "verify_gstin":
        out = {k: result.get(k) for k in ("ok","retryable","message","error") if k in result}
        p = result.get("profile") or {}
        if p:
            out["profile"] = {k:p.get(k) for k in ("gstin","legal_name","trade_name","registration_status") if k in p}
        return out
    if name == "search_vendor":
        out = {k: result.get(k) for k in ("ok","found","message") if k in result}
        v = result.get("vendor") or {}
        if v:
            out["vendor"] = {k:v.get(k) for k in ("vendor_code","legal_name","trade_name","gstin","status","bank_verified","procurement_approved") if k in v}
        return out
    if name == "search_purchase_order":
        out = {k: result.get(k) for k in ("ok","found","message") if k in result}
        po = result.get("purchase_order") or result.get("po") or {}
        if po:
            out["purchase_order"] = {k:po.get(k) for k in ("po_number","supplier_gstin","total_amount","amount_consumed","status") if k in po}
        return out
    if name == "find_duplicate_invoice":
        out = {k: result.get(k) for k in ("ok","duplicate","found","message") if k in result}
        inv = result.get("invoice") or {}
        if inv:
            out["invoice"] = {k:inv.get(k) for k in ("invoice_number","supplier_gstin","status") if k in inv}
        return out
    if name == "create_gate_entry":
        out = {k: result.get(k) for k in ("ok","verified","error","message","mismatched_fields") if k in result}
        ge = result.get("gate_entry") or {}
        if ge:
            out["gate_entry"] = {k:ge.get(k) for k in ("gate_entry_number","invoice_number","po_number","status","vendor_code") if k in ge}
        return out
    if name == "create_provisional_vendor":
        out = {k: result.get(k) for k in ("ok","created","message","error") if k in result}
        v = result.get("vendor") or {}
        if v:
            out["vendor"] = {k:v.get(k) for k in ("vendor_code","legal_name","trade_name","gstin","status") if k in v}
        return out
    if name == "request_human_approval":
        out = {k: result.get(k) for k in ("ok","created","message","error") if k in result}
        a = result.get("approval") or {}
        if a:
            out["approval"] = {k:a.get(k) for k in ("id","approval_type","entity_type","entity_key","status") if k in a}
        return out
    if name == "send_notification":
        return {k: result.get(k) for k in ("ok","notification_id","status","message","error") if k in result}
    if name in {"list_gate_entries", "list_pending_approvals"}:
        return {k: result.get(k) for k in ("ok","count","message","error") if k in result}
    if name == "get_policy":
        out = {k: result.get(k) for k in ("ok","message","error") if k in result}
        pol = result.get("policy") or {}
        if pol:
            out["policy"] = {k:pol.get(k) for k in ("policy_key","value_text") if k in pol}
        return out
    return result


def _is_rate_limit_error(exc: Exception) -> bool:
    low = str(exc).lower()
    return "429" in low or "rate limit" in low or "rate_limit" in low


def _retry_delay_seconds(exc: Exception) -> float:
    text = str(exc)
    match = re.search(r"try again in\s*([0-9]+(?:\.[0-9]+)?)s", text, re.I)
    if match:
        return min(groq_rate_limit_max_wait(), float(match.group(1)) + 0.75)
    return min(groq_rate_limit_max_wait(), 8.0)



def _fallback_fast_intent(prompt: str, has_invoice: bool) -> dict[str, str]:
    p = (prompt or "").lower()
    if "pending approval" in p or "approval queue" in p:
        intent = "LIST_PENDING_APPROVALS"
    elif ("gate entr" in p and any(x in p for x in ("list", "show", "find", "existing"))) and not has_invoice:
        intent = "LIST_GATE_ENTRIES"
    elif has_invoice and any(x in p for x in ("only inspect", "just inspect", "only read", "just read", "only extract", "just extract", "what is the", "tell me the")):
        intent = "INSPECT_INVOICE"
    elif has_invoice and any(x in p for x in ("process", "complete", "execute", "handle", "gate entry", "continue", "finish", "safely")):
        intent = "PROCESS_INVOICE"
    elif has_invoice:
        intent = "INSPECT_INVOICE"
    else:
        intent = "CANNOT_DO"
    return {"intent": intent, "goal": (prompt or "").strip()[:180], "reason": "Local fallback intent parser used."}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.I | re.S).strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # Be tolerant of models that wrap the object in a sentence/code fence.
    starts = [m.start() for m in re.finditer(r"\{", cleaned)]
    for start in starts:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(cleaned[start:i+1])
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        break
    return None


@dataclass
class AgentResult:
    mode: str
    summary: str
    status: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    final_data: dict[str, Any] = field(default_factory=dict)


class VendorOpsAgent:
    def __init__(self, invoice_path: Path | None = None, progress: Callable[[dict[str, Any]], None] | None = None):
        self.tools = VendorTools(ToolContext(invoice_path=invoice_path))
        self.trace: list[dict[str, Any]] = []
        self.progress = progress

    def _emit(self, event_type: str, **payload: Any) -> None:
        if not self.progress:
            return
        try:
            self.progress({"type": event_type, **payload})
        except Exception:
            # UI progress reporting must never be able to break business execution.
            pass

    def _call(self, name: str, **kwargs) -> dict:
        fn = self.tools.tool_map()[name]
        next_step = len(self.trace) + 1
        self._emit("tool_started", step=next_step, tool=name, input=kwargs)
        result = fn(**kwargs)
        item = {"step": next_step, "tool": name, "input": kwargs, "result": result}
        self.trace.append(item)
        self._emit("tool_finished", **item)
        return result

    def run(self, prompt: str, mode: str = "deterministic") -> AgentResult:
        normalized = (mode or "deterministic").strip().lower()
        self._emit("run_started", mode=normalized, task=prompt, invoice_attached=bool(self.tools.context.invoice_path))
        if normalized == "groq_fast":
            return self._run_fast_hybrid(prompt, provider="groq")
        if normalized == "ollama_fast":
            return self._run_fast_hybrid(prompt, provider="ollama")
        if normalized == "groq":
            return self._run_groq(prompt)
        if normalized == "ollama":
            return self._run_ollama(prompt)
        if normalized == "deterministic":
            return self._run_deterministic(prompt)
        return AgentResult(normalized, f"Unknown agent mode: {mode}", "CONFIG_ERROR", self.trace)

    def _approval(self, approval_type: str, entity_key: str, reason: str, payload: dict, summary: str) -> AgentResult:
        self._call("request_human_approval", approval_type=approval_type, entity_type="INVOICE", entity_key=entity_key, reason=reason, payload=payload)
        self._call("send_notification", recipient="procurement@company.example", subject=f"VendorOps review required: {entity_key}", body=reason)
        return AgentResult("deterministic", summary, "HUMAN_REQUIRED", self.trace, payload)

    def _run_deterministic(self, prompt: str) -> AgentResult:
        p = prompt.lower().strip()
        if "pending approval" in p:
            res = self._call("list_pending_approvals")
            return AgentResult("deterministic", f"There are {res.get('count',0)} pending approval(s).", "COMPLETE", self.trace, res)
        if "gate entr" in p and "list" in p:
            res = self._call("list_gate_entries")
            return AgentResult("deterministic", f"Found {res.get('count',0)} gate entries.", "COMPLETE", self.trace, res)
        if not self.tools.context.invoice_path:
            return AgentResult("deterministic", "Attach an invoice for invoice-processing tasks, or ask to list gate entries/pending approvals.", "NEEDS_INPUT", self.trace)

        inv = self._call("inspect_uploaded_invoice")
        key = inv.get("invoice_number") or self.tools.context.invoice_path.name
        if not inv.get("ok"):
            return self._approval("DOCUMENT_REVIEW", key, f"Invoice extraction is incomplete: {inv.get('missing_fields') or inv.get('message')}", inv, "I could not safely extract all critical invoice fields, so I created a human document-review request.")

        min_conf = float((self._call("get_policy", policy_key="OCR_MIN_CONFIDENCE").get("policy") or {}).get("value_text", "0.65"))
        if inv.get("extraction_method") == "OCR" and float(inv.get("confidence",0)) < min_conf:
            return self._approval("OCR_CONFIRMATION", key, f"OCR confidence {inv.get('confidence'):.2f} is below policy threshold {min_conf:.2f}.", inv, "OCR confidence is too low for autonomous execution; I requested human confirmation.")

        gstin = inv["supplier_gstin"].upper()
        gst_attempts = 0
        gst = None
        while gst_attempts <= 2:
            gst_attempts += 1
            gst = self._call("verify_gstin", gstin=gstin)
            if gst.get("ok") or not gst.get("retryable"):
                break
        if not gst or not gst.get("ok"):
            return self._approval("GST_REVIEW", key, gst.get("message","GST verification failed."), {"invoice":inv,"gst_result":gst}, "GST verification could not be completed safely; I escalated it.")
        profile = gst["profile"]
        if profile["registration_status"] != "ACTIVE":
            return self._approval("GST_INACTIVE", key, f"GST registration is {profile['registration_status']}.", {"invoice":inv,"gst_profile":profile}, "The supplier GST registration is not active, so I blocked autonomous gate creation and requested review.")

        vendor_search = self._call("search_vendor", gstin=gstin)
        if not vendor_search.get("found"):
            created = self._call("create_provisional_vendor", gstin=gstin)
            reason = "New supplier detected. GST is ACTIVE and a provisional profile was pre-populated, but procurement approval is required before gate entry."
            return self._approval("NEW_VENDOR", key, reason, {"invoice":inv,"provisional_vendor":created.get("vendor")}, "A new supplier was detected. I verified GST, created a PROVISIONAL vendor profile, and sent it for human approval; no gate entry was created.")

        vendor = vendor_search["vendor"]
        if vendor["status"] != "APPROVED":
            return self._approval("VENDOR_STATUS", key, f"Vendor {vendor['vendor_code']} is {vendor['status']}, not APPROVED.", {"invoice":inv,"vendor":vendor}, "The vendor is not approved, so gate creation was stopped and a review was requested.")

        po_res = self._call("search_purchase_order", po_number=inv["po_number"])
        if not po_res.get("found"):
            return self._approval("PO_NOT_FOUND", key, f"PO {inv['po_number']} was not found.", inv, "The invoice references a PO that is not present in the company system, so I requested review.")
        po = po_res["purchase_order"]
        if po["status"] != "OPEN":
            return self._approval("PO_NOT_OPEN", key, f"PO {po['po_number']} is {po['status']}.", {"invoice":inv,"po":po}, "The purchase order is not open, so autonomous gate creation was blocked.")
        if po["supplier_gstin"] != gstin:
            return self._approval("PO_VENDOR_MISMATCH", key, f"Invoice GSTIN {gstin} does not match PO supplier GSTIN {po['supplier_gstin']}.", {"invoice":inv,"po":po}, "The invoice supplier does not match the PO supplier. I blocked the transaction and requested review.")

        dup = self._call("find_duplicate_invoice", supplier_gstin=gstin, invoice_number=inv["invoice_number"])
        if dup.get("duplicate"):
            return self._approval("DUPLICATE_INVOICE", key, "Same GSTIN + invoice number already exists.", {"invoice":inv,"existing_invoice":dup.get("invoice")}, "A probable duplicate invoice was detected, so no new gate entry was created.")

        tolerance = float((self._call("get_policy", policy_key="PO_AMOUNT_TOLERANCE_PCT").get("policy") or {}).get("value_text", "0"))
        remaining = float(po["total_amount"]) - float(po["amount_consumed"])
        permitted = remaining * (1 + tolerance / 100.0)
        if float(inv["amount"]) > permitted + 1e-9:
            return self._approval("PO_AMOUNT_EXCEPTION", key, f"Invoice amount {inv['amount']:.2f} exceeds allowed PO remaining value {permitted:.2f}.", {"invoice":inv,"po":po,"remaining":remaining,"permitted":permitted}, "The invoice exceeds the permitted remaining PO value, so I requested procurement review.")

        created = self._call(
            "create_gate_entry",
            supplier_gstin=gstin,
            invoice_number=inv["invoice_number"],
            invoice_date=inv.get("invoice_date"),
            po_number=inv["po_number"],
            amount=float(inv["amount"]),
            vehicle_number=inv.get("vehicle_number"),
            extraction_method=inv.get("extraction_method","UNKNOWN"),
            extraction_confidence=float(inv.get("confidence",1.0)),
        )
        if not created.get("ok"):
            return self._approval("GATE_ENTRY_FAILURE", key, created.get("message","Gate creation failed."), {"invoice":inv,"error":created}, "The final write was rejected by backend safety checks, so I escalated it instead of bypassing the controls.")
        ge = created["gate_entry"]
        return AgentResult("deterministic", f"Gate entry {ge['gate_entry_number']} was created and verified for invoice {ge['invoice_number']} ({ge['legal_name']}).", "COMPLETE", self.trace, created)

    def _is_broad_invoice_processing_task(self, prompt: str) -> bool:
        """Return True only for broad execute/process requests, not read-only invoice questions."""
        if not self.tools.context.invoice_path:
            return False
        p = (prompt or "").lower()
        read_only_markers = (
            "only inspect", "just inspect", "only read", "just read", "only extract",
            "just extract", "what is the", "tell me the", "show me the", "summarize",
        )
        if any(x in p for x in read_only_markers):
            return False
        broad_markers = (
            "process", "complete", "execute", "handle", "do whatever",
            "gate entry", "continue processing", "finish", "safely be completed",
        )
        return any(x in p for x in broad_markers)

    def _last_tool_result(self, tool_name: str) -> dict | None:
        for step in reversed(self.trace):
            if step.get("tool") == tool_name:
                result = step.get("result")
                return result if isinstance(result, dict) else {}
        return None

    def _completion_guard(self, prompt: str) -> tuple[bool, str]:
        """
        Application-level terminal-state check.

        LLMs are planners, not the authority on whether an enterprise workflow is complete.
        For broad invoice processing we only accept a terminal answer after a verified write
        or a persisted human-review request. The returned message tells the model what is
        still missing without prescribing one giant hard-coded workflow.
        """
        if not self._is_broad_invoice_processing_task(prompt):
            return True, ""

        approval = self._last_tool_result("request_human_approval")
        if approval and approval.get("ok"):
            return True, ""

        created = self._last_tool_result("create_gate_entry")
        if created and created.get("ok") and created.get("verified"):
            required = {
                "inspect_uploaded_invoice", "search_vendor", "verify_gstin",
                "search_purchase_order", "find_duplicate_invoice", "create_gate_entry",
            }
            seen = {s.get("tool") for s in self.trace}
            missing = sorted(required - seen)
            if not missing:
                return True, ""
            return False, (
                "A write was attempted before the full audit checks were explicitly performed. "
                f"Complete these missing checks before finishing: {', '.join(missing)}."
            )

        inspect = self._last_tool_result("inspect_uploaded_invoice")
        if not inspect:
            return False, "Inspect the uploaded invoice before finishing."
        if not inspect.get("ok"):
            return False, "Invoice extraction is incomplete. Create a human document-review request rather than finishing."

        gst = self._last_tool_result("verify_gstin")
        if not gst:
            return False, "GST verification is still required before this invoice-processing task can finish."
        if not gst.get("ok"):
            if gst.get("retryable"):
                return False, "GST verification failed transiently. Retry verify_gstin before finishing."
            return False, "GST verification failed non-transiently. Persist a human review request before finishing."
        profile = gst.get("profile") or {}
        if profile.get("registration_status") != "ACTIVE":
            return False, "The GST registration is not ACTIVE. Persist a human review request before finishing."

        vendor = self._last_tool_result("search_vendor")
        if not vendor:
            return False, "The vendor master has not yet been checked."
        if not vendor.get("found"):
            provisional = self._last_tool_result("create_provisional_vendor")
            if not (provisional and provisional.get("ok")):
                return False, "This is a new supplier. Create a provisional vendor profile from the verified ACTIVE GST record."
            return False, "The new supplier is provisional. Create a human procurement-approval request before finishing."
        vendor_row = vendor.get("vendor") or {}
        if vendor_row.get("status") != "APPROVED":
            return False, f"Vendor status is {vendor_row.get('status')}. Create a human review request before finishing."

        po = self._last_tool_result("search_purchase_order")
        if not po:
            return False, "The purchase order has not yet been checked."
        if not po.get("found"):
            return False, "The purchase order was not found. Create a human review request before finishing."
        po_row = po.get("purchase_order") or po.get("po") or {}
        if po_row.get("status") != "OPEN":
            return False, f"The purchase order is {po_row.get('status')}. Create a human review request before finishing."
        inv_gstin = str(inspect.get("supplier_gstin") or "").upper()
        po_gstin = str(po_row.get("supplier_gstin") or "").upper()
        if inv_gstin and po_gstin and inv_gstin != po_gstin:
            return False, "The PO supplier does not match the invoice GSTIN. Create a human review request before finishing."

        duplicate = self._last_tool_result("find_duplicate_invoice")
        if not duplicate:
            return False, "Duplicate-invoice checking is still required."
        if duplicate.get("duplicate") or duplicate.get("found"):
            return False, "A duplicate invoice was detected. Create a human review request before finishing."

        if created and not created.get("ok"):
            return False, f"Gate-entry creation was rejected ({created.get('error','unknown error')}). Create a human review request before finishing."

        return False, "All prerequisite checks are complete. Create the gate entry, verify the returned state, and only then finish."

    def _fast_planner_call(self, prompt: str, provider: str) -> tuple[dict[str, Any], str]:
        """Use exactly one small LLM call to interpret the goal.

        Business execution is intentionally deterministic and safety-constrained afterwards.
        This keeps the demo fast/reliable and avoids free-tier multi-turn TPM bursts while
        preserving a genuine natural-language planning layer.
        """
        has_invoice = bool(self.tools.context.invoice_path)
        fallback = _fallback_fast_intent(prompt, has_invoice)
        if provider == "groq":
            key = groq_api_key()
            if not key:
                return fallback, "planner_fallback_no_groq_key"
            api_key, base_url, model = key, groq_base_url(), groq_model()
        else:
            api_key, base_url, model = "ollama", ollama_base_url(), ollama_model()

        try:
            self._emit("planner_started", provider=provider, model=model)
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=min(llm_timeout_seconds(), 60.0))
            messages = [
                {"role": "system", "content": FAST_PLANNER_INSTRUCTIONS},
                {"role": "user", "content": json.dumps({"task": prompt, "invoice_attached": has_invoice}, ensure_ascii=False)},
            ]
            kwargs = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 180}
            attempts = groq_rate_limit_retries() + 1 if provider == "groq" else 1
            response = None
            for attempt in range(attempts):
                try:
                    response = client.chat.completions.create(**kwargs)
                    break
                except Exception as exc:
                    if provider == "groq" and _is_rate_limit_error(exc) and attempt < attempts - 1:
                        delay = _retry_delay_seconds(exc)
                        self._emit("provider_wait", provider="groq", reason="rate_limit", seconds=round(delay, 2))
                        time.sleep(delay)
                        continue
                    raise
            raw = (response.choices[0].message.content or "") if response else ""
            plan = _extract_json_object(raw) or fallback
            allowed = {"PROCESS_INVOICE", "INSPECT_INVOICE", "LIST_PENDING_APPROVALS", "LIST_GATE_ENTRIES", "CANNOT_DO"}
            if plan.get("intent") not in allowed:
                plan = fallback
                source = "planner_fallback_invalid_intent"
            else:
                plan = {
                    "intent": plan.get("intent"),
                    "goal": str(plan.get("goal") or prompt)[:220],
                    "reason": str(plan.get("reason") or "Goal interpreted by LLM planner.")[:260],
                }
                source = f"{provider}_llm_single_call"
            self._emit("planner_finished", plan=plan, source=source)
            return plan, source
        except Exception as exc:
            # A demo should never die merely because the optional planner provider is slow/offline.
            # The execution layer still uses the exact same guarded business tools.
            fallback["reason"] = f"Planner unavailable; safe local intent fallback used ({type(exc).__name__})."
            source = f"planner_fallback_{type(exc).__name__}"
            self._emit("planner_finished", plan=fallback, source=source)
            return fallback, source

    def _run_fast_hybrid(self, prompt: str, provider: str) -> AgentResult:
        mode = f"{provider}_fast"
        plan, source = self._fast_planner_call(prompt, provider)
        plan_step = {
            "step": 1,
            "tool": "llm_plan",
            "input": {"task": prompt, "invoice_attached": bool(self.tools.context.invoice_path)},
            "result": {**plan, "source": source},
        }
        self.trace.append(plan_step)
        self._emit("tool_finished", **plan_step)
        intent = plan.get("intent")

        if intent == "LIST_PENDING_APPROVALS":
            res = self._call("list_pending_approvals")
            return AgentResult(mode, f"There are {res.get('count',0)} pending approval(s).", "COMPLETE", self.trace, res)
        if intent == "LIST_GATE_ENTRIES":
            res = self._call("list_gate_entries")
            return AgentResult(mode, f"Found {res.get('count',0)} gate entries.", "COMPLETE", self.trace, res)
        if intent == "INSPECT_INVOICE":
            if not self.tools.context.invoice_path:
                return AgentResult(mode, "Attach an invoice image first.", "NEEDS_INPUT", self.trace)
            inv = self._call("inspect_uploaded_invoice")
            status = "COMPLETE" if inv.get("ok") else "HUMAN_REQUIRED"
            summary = (
                f"Invoice inspected: {inv.get('invoice_number','unknown')} from GSTIN {inv.get('supplier_gstin','unknown')}. No business records were changed."
                if inv.get("ok") else
                "The invoice could not be extracted safely; no records were changed."
            )
            return AgentResult(mode, summary, status, self.trace, inv)
        if intent == "CANNOT_DO":
            return AgentResult(mode, "That request is outside this prototype's authorized VendorOps capabilities.", "CANNOT_DO", self.trace, plan)

        # PROCESS_INVOICE: LLM interpreted the goal; the deterministic executor now adapts
        # to real observations (GST status, vendor state, PO state, duplicates, retries,
        # approvals) and verifies any write. This separation is deliberate for ERP safety.
        result = self._run_deterministic(prompt)
        result.mode = mode
        return result

    def _run_groq(self, prompt: str) -> AgentResult:
        key = groq_api_key()
        if not key:
            return AgentResult(
                "groq",
                "GROQ_API_KEY is not set. Copy .env.example to .env and add a Groq API key, or use Deterministic/Ollama mode.",
                "CONFIG_ERROR",
                self.trace,
            )
        return self._run_openai_compatible(
            prompt=prompt,
            mode="groq",
            api_key=key,
            base_url=groq_base_url(),
            model=groq_model(),
        )

    def _run_ollama(self, prompt: str) -> AgentResult:
        # Ollama's OpenAI-compatible local endpoint accepts any non-empty key.
        return self._run_openai_compatible(
            prompt=prompt,
            mode="ollama",
            api_key="ollama",
            base_url=ollama_base_url(),
            model=ollama_model(),
        )

    def _run_openai_compatible(self, prompt: str, mode: str, api_key: str, base_url: str, model: str) -> AgentResult:
        try:
            from openai import OpenAI
        except Exception as exc:
            return AgentResult(mode, f"OpenAI-compatible Python client is unavailable: {exc}", "CONFIG_ERROR", self.trace)

        # This SDK is only being used as an OpenAI-compatible protocol client.
        # In Groq mode requests go to Groq; in Ollama mode they stay on localhost.
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=llm_timeout_seconds())
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": prompt},
        ]

        try:
            for _ in range(llm_max_steps()):
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "tools": CHAT_TOOLS,
                    "tool_choice": "auto",
                }
                # Sequential actions are easier to audit and safer for state-changing
                # business tools. Groq documents this flag; Ollama compatibility varies.
                if mode == "groq":
                    kwargs["parallel_tool_calls"] = False

                kwargs["max_tokens"] = llm_max_output_tokens()

                # Groq's free tier has a modest tokens-per-minute allowance. A multi-step
                # tool run can briefly hit that limit even though nothing is wrong.
                # Retry the same model turn after the provider's suggested cooldown.
                attempts = groq_rate_limit_retries() + 1 if mode == "groq" else 1
                for attempt in range(attempts):
                    try:
                        response = client.chat.completions.create(**kwargs)
                        break
                    except Exception as exc:
                        if mode == "groq" and _is_rate_limit_error(exc) and attempt < attempts - 1:
                            delay = _retry_delay_seconds(exc)
                            self._emit("provider_wait", provider="groq", reason="rate_limit", seconds=round(delay, 2))
                            time.sleep(delay)
                            continue
                        raise
                message = response.choices[0].message
                tool_calls = list(message.tool_calls or [])

                if not tool_calls:
                    can_finish, guard_message = self._completion_guard(prompt)
                    if not can_finish:
                        # Do not let the model self-declare a business workflow complete.
                        # Feed the missing terminal condition back and continue the loop.
                        messages.append({"role": "assistant", "content": message.content or ""})
                        messages.append({
                            "role": "system",
                            "content": (
                                "COMPLETION GUARD: You attempted to finish too early. "
                                + guard_message
                                + " Continue the task with the available tools. Do not give a final answer yet."
                            ),
                        })
                        continue
                    text = (message.content or "Task finished.").strip()
                    status = "HUMAN_REQUIRED" if any(s["tool"] == "request_human_approval" for s in self.trace) else "COMPLETE"
                    final_data = self.trace[-1]["result"] if self.trace else {}
                    return AgentResult(mode, text, status, self.trace, final_data)

                assistant_tool_calls = []
                for call in tool_calls:
                    assistant_tool_calls.append({
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments or "{}",
                        },
                    })
                messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": assistant_tool_calls,
                })

                for call in tool_calls:
                    name = call.function.name
                    try:
                        args = json.loads(call.function.arguments or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("Tool arguments must be a JSON object")
                    except Exception as exc:
                        result = {"ok": False, "error": "INVALID_TOOL_ARGUMENTS", "message": str(exc)}
                    else:
                        fn = self.tools.tool_map().get(name)
                        if not fn:
                            result = {"ok": False, "error": "UNKNOWN_TOOL", "message": name}
                        else:
                            try:
                                result = fn(**args)
                            except TypeError as exc:
                                result = {"ok": False, "error": "INVALID_TOOL_ARGUMENTS", "message": str(exc)}
                            except Exception as exc:
                                result = {"ok": False, "error": "TOOL_EXECUTION_ERROR", "message": f"{type(exc).__name__}: {exc}"}
                            self.trace.append({"step":len(self.trace)+1,"tool":name,"input":args,"result":result})

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(_compact_tool_result_for_llm(name, result), ensure_ascii=False, default=str, separators=(",", ":")),
                    })

            return AgentResult(mode, f"Stopped after {llm_max_steps()} model turns to prevent runaway execution.", "MAX_STEPS", self.trace)
        except Exception as exc:
            message = str(exc)
            low = message.lower()
            if mode == "ollama" and any(x in low for x in ("connection", "connect", "refused", "failed to establish")):
                summary = f"Could not reach local Ollama at {base_url}. Start Ollama and run `ollama pull {model}` first. ({type(exc).__name__}: {exc})"
                status = "CONFIG_ERROR"
            elif mode == "groq" and ("401" in low or "authentication" in low or "api key" in low):
                summary = f"Groq rejected the API key. Check GROQ_API_KEY in .env. ({type(exc).__name__}: {exc})"
                status = "CONFIG_ERROR"
            elif "429" in low or "rate limit" in low:
                summary = f"The model provider rate limit was reached. Wait briefly or use another mode. ({type(exc).__name__}: {exc})"
                status = "RATE_LIMIT"
            else:
                summary = f"{mode.title()} agent call failed: {type(exc).__name__}: {exc}"
                status = "LLM_ERROR"
            return AgentResult(mode, summary, status, self.trace)
