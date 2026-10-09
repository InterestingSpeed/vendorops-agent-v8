from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_test_database(tmp_path, monkeypatch):
    """Run every test against its own temporary SQLite DB.

    This keeps pytest completely separate from the demo database, so the test
    suite can run even while the web app is open on Windows.
    """
    test_db = tmp_path / "vendorops_test.db"
    monkeypatch.setenv("VENDOROPS_DB_PATH", str(test_db))
    yield test_db
