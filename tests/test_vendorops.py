from pathlib import Path
import json
import shutil
import pytest

from vendorops.agent import VendorOpsAgent
from vendorops.invoice_parser import parse_invoice
from vendorops.seed import GST, reset_database
from vendorops.service import dashboard, decide_approval, list_approvals
from vendorops.tools import ToolContext, VendorTools

ROOT=Path(__file__).resolve().parents[1]
INV=ROOT/'data'/'invoices'

@pytest.fixture(autouse=True)
def clean_db(isolated_test_database):
    reset_database()


def run(name):
    return VendorOpsAgent(INV/name).run('Process this invoice and complete whatever can safely be completed. Verify the result.')


def test_qr_parser_happy():
    x=parse_invoice(INV/'01_happy_path_qr.png')
    assert x['ok'] and x['extraction_method']=='QR'
    assert x['invoice_number']=='INV-2026-1001'
    assert x['supplier_gstin']==GST['ganesh']


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is optional and not installed")
def test_ocr_parser_without_qr():
    x=parse_invoice(INV/'08_ocr_no_qr.png')
    assert x['extraction_method']=='OCR'
    assert x['supplier_gstin']==GST['arvind']
    assert x['invoice_number']=='ARV-2026-088'
    assert x['po_number']=='PO-1002'
    assert x['amount']==25000.0


def test_happy_path_creates_verified_gate_entry():
    before=dashboard()['counts']['gate_entries']
    r=run('01_happy_path_qr.png')
    assert r.status=='COMPLETE'
    assert r.final_data['verified'] is True
    assert dashboard()['counts']['gate_entries']==before+1


def test_processing_same_invoice_twice_is_idempotently_blocked_as_duplicate():
    r1=run('01_happy_path_qr.png'); assert r1.status=='COMPLETE'
    r2=VendorOpsAgent(INV/'01_happy_path_qr.png').run('Process this invoice')
    assert r2.status=='HUMAN_REQUIRED'
    assert 'duplicate' in r2.summary.lower()


def test_new_vendor_is_provisional_and_needs_approval():
    r=run('02_new_vendor_qr.png')
    assert r.status=='HUMAN_REQUIRED'
    tools=VendorTools(); v=tools.search_vendor(GST['nimbus'])['vendor']
    assert v['status']=='PROVISIONAL'
    assert list_approvals()[0]['approval_type']=='NEW_VENDOR'


def test_new_vendor_approval_updates_vendor_status():
    run('02_new_vendor_qr.png')
    a=list_approvals()[0]
    out=decide_approval(a['id'],'APPROVED')
    assert out['ok']
    v=VendorTools().search_vendor(GST['nimbus'])['vendor']
    assert v['status']=='APPROVED'


def test_inactive_gst_blocks():
    r=run('03_inactive_gst_qr.png')
    assert r.status=='HUMAN_REQUIRED'
    assert any(s['tool']=='verify_gstin' and s['result'].get('profile',{}).get('registration_status')=='CANCELLED' for s in r.trace)


def test_po_vendor_mismatch_blocks():
    r=run('04_po_vendor_mismatch_qr.png')
    assert r.status=='HUMAN_REQUIRED'
    assert 'does not match' in r.summary.lower()


def test_preexisting_duplicate_blocks():
    r=run('05_duplicate_invoice_qr.png')
    assert r.status=='HUMAN_REQUIRED'
    assert 'duplicate' in r.summary.lower()


def test_amount_exception_blocks():
    r=run('06_amount_exception_qr.png')
    assert r.status=='HUMAN_REQUIRED'
    assert 'exceeds' in r.summary.lower()


def test_transient_gst_error_is_retried_and_recovers():
    r=run('07_transient_gst_qr.png')
    calls=[s for s in r.trace if s['tool']=='verify_gstin']
    assert len(calls)==2
    assert calls[0]['result'].get('retryable') is True
    assert calls[1]['result'].get('ok') is True
    assert r.status=='COMPLETE'


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is optional and not installed")
def test_ocr_only_invoice_can_complete_when_confident():
    r=run('08_ocr_no_qr.png')
    assert r.status=='COMPLETE'


def test_low_quality_ocr_does_not_crash_and_never_unsafely_executes():
    r=run('09_low_quality_ocr.png')
    assert r.status in {'HUMAN_REQUIRED','COMPLETE'}
    if r.status=='COMPLETE':
        inspect=next(s for s in r.trace if s['tool']=='inspect_uploaded_invoice')['result']
        assert inspect['confidence'] >= 0.65


def test_closed_po_blocks():
    r=run('10_closed_po_qr.png')
    assert r.status=='HUMAN_REQUIRED'
    assert 'not open' in r.summary.lower()


def test_missing_po_blocks():
    r=run('11_missing_po_qr.png')
    assert r.status=='HUMAN_REQUIRED'
    assert 'not present' in r.summary.lower()


def test_blocked_vendor_blocks():
    r=run('12_blocked_vendor_qr.png')
    assert r.status=='HUMAN_REQUIRED'
    assert 'not approved' in r.summary.lower()


def test_backend_gate_tool_enforces_duplicate_even_if_agent_skips_check():
    t=VendorTools(ToolContext(INV/'05_duplicate_invoice_qr.png'))
    out=t.create_gate_entry(GST['ganesh'],'INV-DUP-001','2026-10-01','PO-1001',12000,'UP16AB1234','QR',1.0)
    assert out['ok'] is False and out['error']=='DUPLICATE_INVOICE'


def test_backend_gate_tool_enforces_po_vendor_mismatch():
    t=VendorTools(ToolContext())
    out=t.create_gate_entry(GST['ganesh'],'NEW-X','2026-10-08','PO-1002',1000,None,'MANUAL',1.0)
    assert out['ok'] is False and out['error']=='PO_VENDOR_MISMATCH'


def test_backend_gate_tool_enforces_blocked_vendor():
    t=VendorTools(ToolContext())
    out=t.create_gate_entry(GST['oldline'],'NEW-X','2026-10-08','PO-1010',1000,None,'MANUAL',1.0)
    assert out['ok'] is False and out['error']=='VENDOR_NOT_APPROVED'


def test_pending_approval_request_is_deduplicated():
    t=VendorTools()
    a=t.request_human_approval('TEST','INVOICE','INV-X','test',{})
    b=t.request_human_approval('TEST','INVOICE','INV-X','test',{})
    assert a['created'] is True and b['created'] is False


def test_list_pending_approvals_prompt():
    run('02_new_vendor_qr.png')
    r=VendorOpsAgent().run('List pending approvals')
    assert r.status=='COMPLETE' and r.final_data['count']>=1


def test_new_vendor_can_continue_after_human_approval():
    r1=run('02_new_vendor_qr.png')
    assert r1.status=='HUMAN_REQUIRED'
    a=list_approvals()[0]
    assert decide_approval(a['id'],'APPROVED')['ok']
    r2=VendorOpsAgent(INV/'02_new_vendor_qr.png').run('Continue processing this approved vendor invoice')
    assert r2.status=='COMPLETE'
    assert r2.final_data['verified'] is True


def test_groq_mode_requires_key(monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    r=VendorOpsAgent().run('List pending approvals', mode='groq')
    assert r.status=='CONFIG_ERROR'
    assert 'GROQ_API_KEY' in r.summary


def _install_fake_openai(monkeypatch, captured):
    import sys
    from types import SimpleNamespace

    class FakeCompletions:
        def __init__(self):
            self.calls=0
        def create(self, **kwargs):
            self.calls += 1
            captured.setdefault('requests', []).append(kwargs)
            if self.calls == 1:
                tc=SimpleNamespace(
                    id='call_1',
                    function=SimpleNamespace(name='list_pending_approvals', arguments='{}')
                )
                msg=SimpleNamespace(content='', tool_calls=[tc])
            else:
                msg=SimpleNamespace(content='Checked the approval queue successfully.', tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured['client_kwargs']=kwargs
            self.chat=SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=FakeOpenAI))


def test_groq_openai_compatible_tool_loop(monkeypatch):
    captured={}
    _install_fake_openai(monkeypatch, captured)
    monkeypatch.setenv('GROQ_API_KEY','gsk_test')
    monkeypatch.setenv('GROQ_MODEL','openai/gpt-oss-20b')
    r=VendorOpsAgent().run('List pending approvals', mode='groq')
    assert r.status=='COMPLETE'
    assert r.mode=='groq'
    assert any(s['tool']=='list_pending_approvals' for s in r.trace)
    assert captured['client_kwargs']['base_url']=='https://api.groq.com/openai/v1'
    assert captured['requests'][0]['model']=='openai/gpt-oss-20b'
    assert captured['requests'][0]['parallel_tool_calls'] is False


def test_ollama_openai_compatible_tool_loop(monkeypatch):
    captured={}
    _install_fake_openai(monkeypatch, captured)
    monkeypatch.setenv('OLLAMA_MODEL','qwen3:4b')
    monkeypatch.setenv('OLLAMA_BASE_URL','http://localhost:11434/v1')
    r=VendorOpsAgent().run('List pending approvals', mode='ollama')
    assert r.status=='COMPLETE'
    assert r.mode=='ollama'
    assert any(s['tool']=='list_pending_approvals' for s in r.trace)
    assert captured['client_kwargs']['base_url']=='http://localhost:11434/v1'
    assert captured['requests'][0]['model']=='qwen3:4b'
    assert 'parallel_tool_calls' not in captured['requests'][0]


def test_unknown_agent_mode_is_rejected():
    r=VendorOpsAgent().run('hello', mode='not-a-provider')
    assert r.status=='CONFIG_ERROR'


def test_backend_rejects_llm_arguments_that_do_not_match_attached_invoice():
    t=VendorTools(ToolContext(INV/'01_happy_path_qr.png'))
    out=t.create_gate_entry(GST['ganesh'],'HALLUCINATED-INV','2026-10-08','PO-1001',18000,'UP16AB1234','QR',1.0)
    assert out['ok'] is False
    assert out['error']=='DOCUMENT_ARGUMENT_MISMATCH'
    assert 'invoice_number' in out['mismatched_fields']

def test_groq_completion_guard_recovers_from_premature_finish(monkeypatch):
    """Regression test for a real observed GPT-OSS behavior: it stopped after GST verification."""
    import sys
    from types import SimpleNamespace

    scripted = [
        ('tool', 'inspect_uploaded_invoice', {}),
        ('tool', 'search_vendor', {'gstin': GST['ganesh']}),
        ('tool', 'verify_gstin', {'gstin': GST['ganesh']}),
        ('text', 'Task finished.'),  # must be rejected by the completion guard
        ('tool', 'search_purchase_order', {'po_number': 'PO-1001'}),
        ('tool', 'find_duplicate_invoice', {'supplier_gstin': GST['ganesh'], 'invoice_number': 'INV-2026-1001'}),
        ('tool', 'create_gate_entry', {
            'supplier_gstin': GST['ganesh'],
            'invoice_number': 'INV-2026-1001',
            'invoice_date': '2026-10-08',
            'po_number': 'PO-1001',
            'amount': 15000,
            'vehicle_number': 'UP16CD4321',
            'extraction_method': 'QR',
            'extraction_confidence': 1.0,
        }),
        ('text', 'Gate entry created and verified.'),
    ]

    class FakeCompletions:
        def __init__(self):
            self.i = 0
        def create(self, **kwargs):
            kind, *rest = scripted[self.i]
            self.i += 1
            if kind == 'tool':
                name, args = rest
                tc = SimpleNamespace(
                    id=f'call_{self.i}',
                    function=SimpleNamespace(name=name, arguments=json.dumps(args)),
                )
                msg = SimpleNamespace(content='', tool_calls=[tc])
            else:
                msg = SimpleNamespace(content=rest[0], tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv('GROQ_API_KEY', 'gsk_test')

    before = dashboard()['counts']['gate_entries']
    r = VendorOpsAgent(INV/'01_happy_path_qr.png').run(
        'Process this invoice and complete whatever can safely be completed. Verify the result.',
        mode='groq',
    )
    assert r.status == 'COMPLETE'
    assert any(s['tool'] == 'search_purchase_order' for s in r.trace)
    assert any(s['tool'] == 'find_duplicate_invoice' for s in r.trace)
    assert any(s['tool'] == 'create_gate_entry' and s['result'].get('verified') for s in r.trace)
    assert dashboard()['counts']['gate_entries'] == before + 1

def test_groq_rate_limit_is_retried(monkeypatch):
    import sys
    from types import SimpleNamespace

    class FakeCompletions:
        def __init__(self):
            self.calls = 0
        def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise Exception("Error code: 429 - rate limit reached. Please try again in 0.01s")
            if self.calls == 2:
                tc = SimpleNamespace(id='call_1', function=SimpleNamespace(name='list_pending_approvals', arguments='{}'))
                msg = SimpleNamespace(content='', tool_calls=[tc])
            else:
                msg = SimpleNamespace(content='Checked approvals.', tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv('GROQ_API_KEY', 'gsk_test')
    monkeypatch.setenv('GROQ_RATE_LIMIT_RETRIES', '2')
    monkeypatch.setenv('GROQ_RATE_LIMIT_MAX_WAIT_SECONDS', '1')
    monkeypatch.setattr('vendorops.agent.time.sleep', lambda _: None)

    r = VendorOpsAgent().run('List pending approvals', mode='groq')
    assert r.status == 'COMPLETE'
    assert any(s['tool'] == 'list_pending_approvals' for s in r.trace)

def test_groq_fast_hybrid_uses_one_planner_call_then_safe_executor(monkeypatch):
    import sys
    from types import SimpleNamespace
    captured={'calls':0}

    class FakeCompletions:
        def create(self, **kwargs):
            captured['calls'] += 1
            msg=SimpleNamespace(content='{"intent":"PROCESS_INVOICE","goal":"process the attached invoice","reason":"The user asked for safe invoice processing."}', tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat=SimpleNamespace(completions=FakeCompletions())
    monkeypatch.setitem(sys.modules,'openai',SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv('GROQ_API_KEY','gsk_test')

    before=dashboard()['counts']['gate_entries']
    r=VendorOpsAgent(INV/'01_happy_path_qr.png').run('Process this invoice and complete whatever can safely be completed. Verify the result.',mode='groq_fast')
    assert r.status=='COMPLETE'
    assert r.mode=='groq_fast'
    assert captured['calls']==1
    assert r.trace[0]['tool']=='llm_plan'
    assert r.trace[0]['result']['intent']=='PROCESS_INVOICE'
    assert any(s['tool']=='create_gate_entry' and s['result'].get('verified') for s in r.trace)
    assert dashboard()['counts']['gate_entries']==before+1


def test_groq_fast_hybrid_falls_back_without_key_and_still_runs_safely(monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    r=VendorOpsAgent(INV/'04_po_vendor_mismatch_qr.png').run('Process this invoice safely',mode='groq_fast')
    assert r.mode=='groq_fast'
    assert r.status=='HUMAN_REQUIRED'
    assert r.trace[0]['tool']=='llm_plan'
    assert 'fallback' in r.trace[0]['result']['source']
    assert not any(s['tool']=='create_gate_entry' and s['result'].get('ok') for s in r.trace)


def test_fast_hybrid_read_only_intent_does_not_write(monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    before=dashboard()['counts']['gate_entries']
    r=VendorOpsAgent(INV/'01_happy_path_qr.png').run('Just inspect this invoice and tell me its fields',mode='groq_fast')
    assert r.status=='COMPLETE'
    assert r.trace[0]['result']['intent']=='INSPECT_INVOICE'
    assert any(s['tool']=='inspect_uploaded_invoice' for s in r.trace)
    assert not any(s['tool']=='create_gate_entry' for s in r.trace)
    assert dashboard()['counts']['gate_entries']==before


def test_reset_database_works_with_live_reader():
    """Regression: Windows can lock an open SQLite file, so reset must not unlink it."""
    from vendorops.db import connect
    from vendorops.seed import reset_database
    from vendorops.service import dashboard

    reset_database()
    conn = connect()
    try:
        conn.execute("SELECT * FROM vendors LIMIT 1").fetchone()
        reset_database()
        counts = dashboard()["counts"]
        assert counts["approved_vendors"] == 17
        assert counts["gate_entries"] == 13
        assert counts["pending_approvals"] == 0
    finally:
        conn.close()
