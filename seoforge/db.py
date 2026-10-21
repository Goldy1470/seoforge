from __future__ import annotations

import datetime as _dt
import json
import sqlite3

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  url TEXT UNIQUE,
  status INTEGER,
  depth INTEGER,
  title TEXT,
  meta_desc TEXT,
  h1 TEXT,
  canonical TEXT,
  meta_robots TEXT,
  word_count INTEGER,
  links_out INTEGER,
  response_ms INTEGER,
  size_bytes INTEGER,
  jsonld TEXT,
  images_missing_alt INTEGER,
  harvested_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pages_job ON pages(job_id);

CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  source TEXT,
  target TEXT,
  anchor TEXT,
  rel TEXT
);
CREATE INDEX IF NOT EXISTS idx_links_job ON links(job_id);

CREATE TABLE IF NOT EXISTS paa_questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT UNIQUE,
  seed TEXT,
  parent TEXT,
  depth INTEGER,
  cluster TEXT,
  intent TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS directories (
  name TEXT PRIMARY KEY,
  dr INTEGER,
  category TEXT,
  url TEXT,
  status TEXT DEFAULT 'todo',
  notes TEXT,
  submitted_at TEXT
);

CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  title TEXT,
  description TEXT,
  link TEXT,
  image_link TEXT,
  price TEXT,
  brand TEXT,
  availability TEXT,
  gtin TEXT,
  condition TEXT DEFAULT 'new',
  reviews_json TEXT
);

CREATE TABLE IF NOT EXISTS rank_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT,
  domain TEXT,
  position INTEGER,
  url TEXT,
  searched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rank_kw ON rank_snapshots(keyword);

CREATE TABLE IF NOT EXISTS gsc_cache (
  query TEXT,
  page TEXT,
  clicks REAL,
  impressions REAL,
  ctr REAL,
  position REAL,
  pulled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gsc_cache_query ON gsc_cache(query);

CREATE TABLE IF NOT EXISTS watchlist (
  keyword TEXT PRIMARY KEY,
  competitor TEXT,
  added_at TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT,
  target TEXT,
  impact REAL,
  effort REAL,
  score REAL,
  evidence TEXT,
  sources TEXT,
  status TEXT DEFAULT 'open',
  created_at TEXT,
  updated_at TEXT,
  UNIQUE(type, target)
);
CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status);

CREATE TABLE IF NOT EXISTS schedule (
  name TEXT PRIMARY KEY,
  kind TEXT,
  interval_hours REAL,
  enabled INTEGER DEFAULT 1,
  params TEXT DEFAULT '{}',
  last_run TEXT,
  last_status TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT,
  params TEXT,
  status TEXT,
  started_at TEXT,
  finished_at TEXT,
  summary TEXT
);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,
  site TEXT,
  payload TEXT,
  created_at TEXT
);
"""


def get_db() -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
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
