from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from vendorops.seed import reset_database

ROOT = Path(__file__).resolve().parents[1]
INV = ROOT / 'data' / 'invoices'


def test_provider_status_and_page_load():
    reset_database()
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        r = client.get('/api/providers')
        assert r.status_code == 200
        data = r.json()
        assert {'groq','deterministic','ollama'} <= set(data)
        assert data['deterministic']['configured'] is True


def test_multipart_invoice_upload_route_is_runnable():
    reset_database()
    with TestClient(app) as client:
        with (INV/'01_happy_path_qr.png').open('rb') as f:
            r = client.post(
                '/api/process',
                data={
                    'prompt':'Process this invoice and complete whatever can safely be completed. Verify the result.',
                    'mode':'deterministic',
                },
                files={'invoice':('01_happy_path_qr.png', f, 'image/png')},
            )
        assert r.status_code == 200
        payload = r.json()
        assert payload['ok'] is True
        assert payload['status'] == 'COMPLETE'
        assert any(step['tool']=='create_gate_entry' for step in payload['trace'])


def test_live_job_api_streams_progress_and_finishes():
    import time
    reset_database()
    with TestClient(app) as client:
        with (INV/'01_happy_path_qr.png').open('rb') as f:
            r = client.post(
                '/api/process/start',
                data={
                    'prompt':'Process this invoice and complete whatever can safely be completed. Verify the result.',
                    'mode':'deterministic',
                },
                files={'invoice':('01_happy_path_qr.png', f, 'image/png')},
            )
        assert r.status_code == 200
        job_id = r.json()['job_id']
        payload = None
        for _ in range(100):
            payload = client.get(f'/api/process/jobs/{job_id}').json()
            if payload['state'] in {'finished','failed'}:
                break
            time.sleep(0.03)
        assert payload['state'] == 'finished'
        types = [e['type'] for e in payload['events']]
        assert 'tool_started' in types
        assert 'tool_finished' in types
        assert payload['result']['status'] == 'COMPLETE'
        assert any(e.get('tool')=='create_gate_entry' for e in payload['events'])


def test_company_data_endpoints_are_browsable():
    reset_database()
    with TestClient(app) as client:
        assert len(client.get('/api/vendors').json()) >= 10
        assert len(client.get('/api/pos').json()) >= 10
        assert len(client.get('/api/invoices').json()) >= 1
        assert len(client.get('/api/gate_entries').json()) >= 1
        assert len(client.get('/api/policies').json()) >= 1
