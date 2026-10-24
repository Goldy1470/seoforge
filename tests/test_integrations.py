import json

from seoforge import db, integrations, notify, scheduler


def setup_function(fn):
    conn = db.get_db()
    conn.execute("DELETE FROM opportunities")
    conn.execute("UPDATE schedule SET last_run=NULL, last_status=NULL")
    conn.commit()
    conn.close()


def test_recompute_persists_and_queue():
    from seoforge import backlinks

    backlinks.load_directories()  # self-seed so a fresh DB still produces DR-based work
    r = integrations.recompute(persist=True)
    assert r["count"] >= 1
    rows = integrations.queue(limit=5)
    assert rows, "queue should return persisted opportunities"
    first = rows[0]
    assert first["score"] >= first["impact"] / first["effort"] - 0.01
    assert set(["type", "target", "evidence", "sources"]) <= set(first)
    tt = f"{first['type']}|{first['target']}"
    assert integrations.mark_done(tt).endswith("done")
    left = [x for x in integrations.queue(limit=50) if x["target"] == first["target"]]
    assert not left


def test_scheduler_tick_graceful_without_keys():
    results = scheduler.tick(only_due=False)
    statuses = {r["name"]: r["status"] for r in results}
    assert "nightly_delta" in statuses and statuses["nightly_delta"] in {"done", "skipped", "failed"}
    for name, st in statuses.items():
        assert st in {"done", "skipped", "failed"}, (name, st)


def test_delta_formatter():
    fake = [
        {"query": "seo tools", "page": "https://x/p", "clicks_now": 10, "clicks_baseline": 2,
         "click_delta_pct": 400, "position_delta": 2.5, "signal": "gain"},
    ]
    lines = scheduler.notify_lines_from_delta(fake)
    assert "+400%" in lines[0] and "+2.5" in lines[0]


def test_watchlist_roundtrip():
    assert scheduler.watch_add("best tools for d2c", "competitor.com").startswith("watching")
    kws = scheduler.watch_list()
    assert any(k["keyword"] == "best tools for d2c" for k in kws)
    r = scheduler.serp_watch()
    assert r == {"error": "watchlist empty - add with watch_add"} or "results" in r


def test_digest_saves_report_without_webhook():
    msg = notify.send("unit", ["line1", "line2"])
    assert "saved report" in msg or "webhook" in msg
    with db._conn() as conn:
        rid = conn.execute(
            "SELECT id FROM reports WHERE kind='digest' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
    data = notify.flush(rid)
    assert data["title"] == "unit" and "line1" in data["lines"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all integration tests passed")
