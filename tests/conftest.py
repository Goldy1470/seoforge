import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own empty SQLite DB + exports dir.

    db.get_db() reads settings.db_path at call time, so patching the
    attribute on the settings object is enough — no data/seoforge.db is
    ever touched by the suite.
    """
    from seoforge.config import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "tests.db"))
    monkeypatch.setattr(settings, "exports_dir", str(tmp_path / "exports"))
    yield settings


def crawl_fixtures(job_id: str = "tj1") -> None:
    """Minimal graph that internal_links.build_report() can run against.

    Home -> /free-guides -> /paa -> back to home plus a dangling /pricing.
    """
    from seoforge import db

    stamp = db.now()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO jobs(id, kind, params, status, started_at, finished_at) values (?,?,?,?,?,?)",
            (job_id, "crawl", "{}", "done", stamp, stamp),
        )
        conn.executemany(
            """INSERT INTO pages(job_id,url,status,depth,title,meta_desc,h1,word_count) values (?,?,?,?,?,?,?,?)""",
            [
                (job_id, "https://example.com/", 200, 0, "Homepage", "All about us", "Homepage", 800),
                (job_id, "https://example.com/free-guides", 200, 1, "Free Guides", "Guides", "Free Guides", 900),
                (job_id, "https://example.com/paa", 200, 2, "PAA", "People also ask", "PAA", 400),
                (job_id, "https://example.com/pricing", 200, 3, "Pricing", "Costs", "Pricing", 300),
            ],
        )
        conn.executemany(
            "INSERT INTO links(job_id, source, target, anchor, rel) values (?,?,?,?,?)",
            [
                (job_id, "https://example.com/", "https://example.com/free-guides", "Free Guides", ""),
                (job_id, "https://example.com/free-guides", "https://example.com/paa", "PAA deep dive", ""),
                (job_id, "https://example.com/paa", "https://example.com/", "Homepage", ""),
            ],
        )
