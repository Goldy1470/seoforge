import sqlite3

from seoforge import db
from seoforge.migrations import apply


def _expected_pages_columns():
    return {
        "id", "job_id", "url", "status", "depth", "title", "meta_desc", "h1",
        "canonical", "meta_robots", "word_count", "links_out", "response_ms",
        "size_bytes", "jsonld", "images_missing_alt", "headings", "first_text",
        "viewport", "render_blockers", "js_flag", "harvested_at",
    }


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_db_reaches_full_schema():
    with db._conn() as conn:
        assert _columns(conn, "pages") == _expected_pages_columns()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert tables >= {
            "schema_migrations", "pages", "links", "paa_questions", "directories",
            "products", "rank_snapshots", "gsc_cache", "watchlist", "opportunities",
            "schedule", "jobs", "reports", "leads",
            "bot_connections", "bot_events", "bot_daily", "bot_usage", "bot_files",
        }
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        assert versions == [1, 2, 3, 4, 5]


def test_legacy_db_upgrades_to_current_schema():
    legacy = sqlite3.connect(db.settings.db_path)
    legacy.row_factory = sqlite3.Row
    legacy.execute("CREATE TABLE pages (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    legacy.commit()
    legacy.close()
    with db._conn() as conn:
        assert _columns(conn, "pages") == _expected_pages_columns()
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        assert versions == [1, 2, 3, 4, 5]
        cache_cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_bot_ranges)")}
        assert cache_cols == {"url", "prefixes_json", "fetched_at"}
        bot_cols = {r[1] for r in conn.execute("PRAGMA table_info(bot_events)")}
        assert bot_cols == {
            "id", "bot", "bot_type", "page", "status", "country",
            "occurred_at", "received_at",
        }
        opp_cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)")}
        assert opp_cols >= {"completed_at", "outcome", "validation", "validated_at"}
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "opportunity_events" in tables


def test_apply_is_idempotent_on_reopen():
    with db._conn() as conn:
        first = apply(conn)
    assert first == []
    with db._conn() as conn:
        rows = conn.execute("SELECT count(*) c FROM schema_migrations").fetchone()
        assert rows["c"] == 5
