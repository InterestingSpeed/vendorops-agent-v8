from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import db_path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(path: Path | None = None):
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def one(sql: str, params: Iterable[Any] = (), path: Path | None = None) -> dict | None:
    with connect(path) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None


def all_rows(sql: str, params: Iterable[Any] = (), path: Path | None = None) -> list[dict]:
    with connect(path) as conn:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def execute(sql: str, params: Iterable[Any] = (), path: Path | None = None) -> int:
    with connect(path) as conn:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return int(cur.lastrowid)


def audit(event_type: str, action: str, status: str, details: dict | None = None, *, actor: str = "agent", path: Path | None = None) -> None:
    execute(
        "INSERT INTO audit_logs(created_at, actor, event_type, action, status, details_json) VALUES(?,?,?,?,?,?)",
        (utcnow(), actor, event_type, action, status, json.dumps(details or {}, ensure_ascii=False, default=str)),
        path,
    )
