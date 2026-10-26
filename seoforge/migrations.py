"""Versioned schema migrations for SEOForge.

Rule: migration files are immutable once applied — append new versions,
never edit an applied one. Every migration is idempotent so that opening
either a fresh DB or an upgraded legacy DB lands on the same schema.
"""
from __future__ import annotations

import re
import sqlite3

_SCHEMA = """
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
  headings TEXT,
  first_text TEXT,
  viewport INTEGER,
  render_blockers INTEGER,
  js_flag TEXT,
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

_CONSTRAINT_WORDS = {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"}


def _parse_columns(schema_sql: str) -> dict:
    """Extract (table -> [(column, type-decl)]) from _SCHEMA for legacy upgrades.

    Skips table-level constraints (UNIQUE(...), PRIMARY KEY(...)) and columns
    that cannot be added via ALTER TABLE (UNIQUE / PRIMARY KEY columns).
    """
    tables: dict = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);", schema_sql, re.S):
        table, body = m.group(1), m.group(2)
        cols = []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            first = line.split()[0]
            if first.upper().split("(")[0] in _CONSTRAINT_WORDS:
                continue
            name = first.strip('"')
            decl = line.split(None, 1)[1].strip()
            upper = " ".join(decl.upper().split())
            if "PRIMARY KEY" in upper or "UNIQUE" in upper:
                continue
            cols.append((name, decl))
        tables[table] = cols
    return tables


_PARSED_V1 = _parse_columns(_SCHEMA)


def _v2_ai_bot_ranges(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS ai_bot_ranges (
  url TEXT PRIMARY KEY,
  prefixes_json TEXT NOT NULL,
  fetched_at REAL NOT NULL
);
"""
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _v3_leads_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT,
  site_url TEXT,
  score INTEGER,
  details TEXT,
  created_at TEXT
);
"""
    )


def _v4_bot_tables(conn: sqlite3.Connection) -> None:
    """AI-bot log ingest (AT-010 style): connections with per-connection secrets
    (only their SHA-256 stored), one row per verified bot request, bot x page x
    day counters that always count (overflow folds onto '(other)' rows), the
    month's usage caps, and log-file hashes that keep a resend from double
    counting."""
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS bot_connections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('cloudflare_worker', 'cloudflare_logpush', 'vercel_drain')),
  secret_hash TEXT NOT NULL UNIQUE,
  domains TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  last_event_at TEXT,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS bot_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot TEXT NOT NULL,
  bot_type TEXT NOT NULL CHECK (bot_type IN ('training', 'search', 'user')),
  page TEXT NOT NULL,
  status INTEGER,
  country TEXT,
  occurred_at TEXT NOT NULL,
  received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS bot_events_time_idx ON bot_events (occurred_at);

CREATE TABLE IF NOT EXISTS bot_daily (
  day TEXT NOT NULL,
  bot TEXT NOT NULL,
  bot_type TEXT NOT NULL,
  page TEXT NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT NOT NULL,
  PRIMARY KEY (day, bot, page)
);

CREATE INDEX IF NOT EXISTS bot_daily_day_idx ON bot_daily (day);

CREATE TABLE IF NOT EXISTS bot_usage (
  month TEXT PRIMARY KEY,
  stored_events INTEGER NOT NULL DEFAULT 0,
  counted_events INTEGER NOT NULL DEFAULT 0,
  raw_records INTEGER NOT NULL DEFAULT 0,
  capped_at TEXT,
  raw_capped_at TEXT,
  alerted_at TEXT
);

CREATE TABLE IF NOT EXISTS bot_files (
  connection_id INTEGER NOT NULL REFERENCES bot_connections(id) ON DELETE CASCADE,
  body_hash TEXT NOT NULL,
  received_at TEXT NOT NULL,
  PRIMARY KEY (connection_id, body_hash)
);

CREATE INDEX IF NOT EXISTS bot_files_time_idx ON bot_files (received_at);
"""
    )


def _v1_initial_schema(conn: sqlite3.Connection) -> None:
    # Upgrade pre-existing tables BEFORE running the script: index creation
    # inside _SCHEMA references columns a legacy table may not have yet.
    for table, cols in _PARSED_V1.items():
        have = {h.lower() for h in _table_columns(conn, table)}
        if not have:
            continue  # table does not exist -> executescript creates it whole
        for name, decl in cols:
            if name.lower() not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.executescript(_SCHEMA)


def _v5_outcome_measurements(conn: sqlite3.Connection) -> None:
    """Outcome capture: when the work was closed, the measured verdict once
    its after-window has finished (the before/after numbers stored next to
    it as validation JSON), and an event trail so a measurement sits with
    the work that preceded it."""
    have = _table_columns(conn, "opportunities")
    for name, decl in (
        ("completed_at", "TEXT"),
        ("outcome", "TEXT"),
        ("validation", "TEXT"),
        ("validated_at", "TEXT"),
    ):
        if name.lower() not in have:
            conn.execute(f"ALTER TABLE opportunities ADD COLUMN {name} {decl}")
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS opportunity_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER,
  event TEXT,
  data TEXT,
  at TEXT
);
CREATE INDEX IF NOT EXISTS idx_opp_events_opp ON opportunity_events(opportunity_id);
"""
    )


def _v6_prompts(conn: sqlite3.Connection) -> None:
    """Citation tracking: prompts tracked in AI answers plus the per-run
    results — one row per answer, parsed at record time (deterministic,
    no LLM). Append-only: same argument as the outcome windows before it,
    every prompt's baseline is made of rows that never change."""
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS prompts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS prompts_text_idx ON prompts (text);

CREATE TABLE IF NOT EXISTS prompt_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt_id INTEGER NOT NULL REFERENCES prompts(id),
  platform TEXT NOT NULL,
  at TEXT NOT NULL,
  response TEXT NOT NULL DEFAULT '',
  citations TEXT NOT NULL DEFAULT '[]',
  mention_count INTEGER NOT NULL DEFAULT 0,
  citation_count INTEGER NOT NULL DEFAULT 0,
  sentiment TEXT NOT NULL DEFAULT 'neutral',
  visibility_score REAL,
  mention_position INTEGER,
  mentioned_entity_count INTEGER,
  competitor_mentions TEXT NOT NULL DEFAULT '[]',
  competitors TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS prompt_results_prompt_idx
  ON prompt_results (prompt_id, platform, at);
"""
    )


def _v7_serp_tracking(conn: sqlite3.Connection) -> None:
    """Position tracking: domains, their tracked keywords (device/country/
    city dimensions) and the failed-scrape retry queue. Keyword state is
    one row: current position plus the full daily history and last scraped
    result packed as JSON, exactly as the reference stores them."""
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS serp_domains (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL UNIQUE,
  keyword_count INTEGER NOT NULL DEFAULT 0,
  last_updated TEXT,
  added TEXT,
  tags TEXT NOT NULL DEFAULT '[]',
  notification INTEGER NOT NULL DEFAULT 1,
  notification_interval TEXT NOT NULL DEFAULT 'daily',
  notification_emails TEXT NOT NULL DEFAULT '',
  scrape_strategy TEXT NOT NULL DEFAULT '',
  scrape_pagination_limit INTEGER NOT NULL DEFAULT 0,
  scrape_smart_full_fallback INTEGER NOT NULL DEFAULT 0,
  subdomain_matching TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS serp_keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  device TEXT NOT NULL DEFAULT 'desktop',
  country TEXT NOT NULL DEFAULT 'US',
  city TEXT NOT NULL DEFAULT '',
  latlong TEXT NOT NULL DEFAULT '',
  domain TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0,
  url TEXT NOT NULL DEFAULT '',
  volume INTEGER NOT NULL DEFAULT 0,
  history TEXT NOT NULL DEFAULT '{}',
  last_result TEXT NOT NULL DEFAULT '[]',
  tags TEXT NOT NULL DEFAULT '[]',
  sticky INTEGER NOT NULL DEFAULT 0,
  updating INTEGER NOT NULL DEFAULT 0,
  last_updated TEXT,
  added TEXT,
  last_update_error TEXT NOT NULL DEFAULT 'false',
  settings TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS serp_keywords_domain_idx ON serp_keywords (domain);

CREATE TABLE IF NOT EXISTS serp_retry_queue (
  keyword_id INTEGER PRIMARY KEY REFERENCES serp_keywords(id),
  error TEXT NOT NULL DEFAULT '',
  queued_at TEXT NOT NULL
);
"""
    )


MIGRATIONS: list = [
    (1, "initial schema (pages + crawl engine tables)", _v1_initial_schema),
    (2, "ai_bot_ranges cache for AI-operator published IP ranges", _v2_ai_bot_ranges),
    (3, "leads table for public scan conversions", _v3_leads_table),
    (4, "bot ingest: connections, events, daily counters, usage caps, file claims", _v4_bot_tables),
    (5, "opportunity outcome capture: completion stamp, verdict, validation, event trail", _v5_outcome_measurements),
    (6, "prompts + prompt_results: tracked prompts, pasted-in AI answers with parsed citation metrics", _v6_prompts),
    (7, "serp tracking: domains, tracked keywords with device/country/city, retry queue", _v7_serp_tracking),
]


def apply(conn: sqlite3.Connection) -> list:
    """Apply pending migrations in order; returns the versions applied this call."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    ran = []
    for version, name, fn in MIGRATIONS:
        if version in applied:
            continue
        fn(conn)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,?)",
            (version, name, db_now()),
        )
        ran.append((version, name))
    if ran:
        conn.commit()
    return ran


def db_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
