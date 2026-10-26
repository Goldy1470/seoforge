"""The tenant directory: one small cross-dialect store where the SaaS
facts live - tenants, their API keys, plan gates and monthly usage.

SQLite is the offline/default backend (the suite, local dev, the
operator seat). Postgres is the production backend: same DDL, same
queries, `?` placeholders translated to `%s`, and a lazy driver import -
nothing here needs psycopg until a postgresql:// URL is actually
opened, so the suite never touches the network.

Writers' discipline: `?` never appears inside string literals in this
store's SQL, and primary keys are TEXT ids generated app-side - both
dialects answer identically."""
from __future__ import annotations

import re
import sqlite3
import threading

_POSTGRES_PREFIXES = ("postgres://", "postgresql://")
_PLACEHOLDER = re.compile(r"\?")

DDL = """
CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  plan TEXT NOT NULL DEFAULT 'owner',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  prefix TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS usage (
  tenant_id TEXT NOT NULL,
  month TEXT NOT NULL,
  metric TEXT NOT NULL,
  value INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, month, metric)
);
"""


def now() -> str:
    from . import db

    return db.now()


class Directory:
    """A tenant directory opened from one URL. A plain path (or
    `sqlite:///...`) is the stdlib backend; a postgresql:// URL is the
    production backend. The DDL runs on every open and is idempotent."""

    def __init__(self, url: str):
        url = str(url or "").strip()
        if not url:
            raise ValueError("a directory URL is required")
        self.url = url
        self.backend = "postgres" if url.startswith(_POSTGRES_PREFIXES) else "sqlite"
        self._conn = None

    @classmethod
    def open(cls, url: str | None = None) -> "Directory":
        from .config import settings

        d = cls(url or settings.directory_db)
        if d.backend == "sqlite":
            conn = sqlite3.connect(d._sqlite_path(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
        else:
            conn = d._connect_postgres()
        d._conn = conn
        d.migrate()
        return d

    # ----------------------------------------------------- connections

    def _sqlite_path(self) -> str:
        if self.url.startswith("sqlite:///"):
            return self.url[len("sqlite:///") :]
        return self.url

    def _connect_postgres(self):
        try:
            import psycopg
            from psycopg.rows import dict_row

            conn = psycopg.connect(self.url, row_factory=dict_row)
            conn.autocommit = True
            return conn
        except Exception:
            pass
        try:
            import psycopg2
        except Exception as exc:
            raise RuntimeError(
                "a postgres driver is required for a postgresql:// directory:"
                " pip install psycopg[binary]"
            ) from exc
        conn = psycopg2.connect(self.url)
        conn.autocommit = True
        return conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "Directory":
        if self._conn is None:
            return Directory.open(self.url)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----------------------------------------------------------- reads

    def migrate(self) -> None:
        if self._conn is None:
            raise ValueError("the directory is closed")
        if self.backend == "sqlite":
            self._conn.executescript(DDL)
            self._conn.commit()
        else:
            with self._conn.cursor() as cur:
                cur.execute(DDL)

    def execute(self, sql: str, params: tuple = ()) -> int:
        sql = self._translate(sql)
        if self.backend == "sqlite":
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        sql = self._translate(sql)
        if self.backend == "sqlite":
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------- internals

    def _translate(self, sql: str) -> str:
        if self.backend == "sqlite":
            return sql
        return _PLACEHOLDER.sub("%s", sql)


_local = threading.local()


def directory(url: str | None = None) -> Directory:
    """The thread's cached directory (one connection per thread, replaced
    wholesale when the URL changes). Every call migrates lazily and the
    mutation helpers never share a connection across threads."""
    from .config import settings

    resolved = url or settings.directory_db
    d = getattr(_local, "directory", None)
    if d is None or d.url != resolved:
        if d is not None:
            d.close()
        d = Directory.open(resolved)
        _local.directory = d
    return d
