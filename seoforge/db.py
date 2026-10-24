from __future__ import annotations

import datetime as _dt
import json
import sqlite3

from .config import settings


def get_db() -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    from .migrations import apply

    apply(conn)
    return conn


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def start_job(kind: str, params: dict) -> str:
    import uuid

    job_id = uuid.uuid4().hex[:12]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO jobs(id, kind, params, status, started_at) VALUES (?,?,?,?,?)",
            (job_id, kind, json.dumps(params), "running", now()),
        )
    return job_id


def finish_job(job_id: str, summary: dict, status: str = "done") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, finished_at=?, summary=? WHERE id=?",
            (status, now(), json.dumps(summary), job_id),
        )


def save_report(kind: str, payload: dict) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO reports(kind, site, payload, created_at) VALUES (?,?,?,?)",
            (kind, settings.site_url, json.dumps(payload), now()),
        )
        return cur.lastrowid


class _DBCtx:
    def __init__(self):
        self._conn = None

    def __enter__(self) -> sqlite3.Connection:
        self._conn = get_db()
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self._conn.close()
        return False


def _conn() -> _DBCtx:
    return _DBCtx()
