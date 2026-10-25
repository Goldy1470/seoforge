"""Bot ingest (connections, batch verify+cap write, log-file claim, prune,
report). The reference's fixtures are mirrored: GPTBot verifies with an IP
inside the operator's published ranges, ClaudeBot outside, an unknown agent
and an unverified IP are skipped, and nothing is written when nothing
verifies. Tests never touch the default verifier (offline rule).
"""
import asyncio
import datetime as dt

import pytest

from seoforge import bot_ingest, db

NOW_MS = int(
    dt.datetime(2026, 10, 19, 10, 0, tzinfo=dt.timezone.utc).timestamp() * 1000
)
AT = "2026-10-19T09:58:00Z"
DAY_MS = 24 * 60 * 60 * 1000


def iso(ms):
    return (
        dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def gpt(**over):
    req = {
        "userAgent": "GPTBot/1.0 (+https://openai.com/gptbot)",
        "ip": "20.171.206.9",
        "url": "https://acme.example/menu?x=1",
        "status": 200,
        "country": "US",
        "at": AT,
    }
    req.update(over)
    return req


def verifier(request):
    """Offline double for ai_bots.verify_bot_request (same verdict shape)."""
    ua = request["userAgent"].lower()
    if "gptbot" in ua:
        if request["ip"] == "20.171.206.9":
            return {"bot": "gptbot", "bot_type": "training", "verified": True}
        return {"reason": "unverified_ip", "bot": "gptbot", "bot_type": "training"}
    if "claudebot" in ua:
        return {"bot": "claudebot", "bot_type": "training", "verified": True}
    return {"reason": "unknown_agent"}


def new_connection(kind="cloudflare_worker", domains=("acme.example",)):
    return bot_ingest.create_connection(kind, None if domains is None else list(domains))


def ingest(requests, connection=None):
    return asyncio.run(
        bot_ingest.ingest_bot_requests(
            connection or new_connection(), requests, verifier=verifier
        )
    )


# ------------------------------------------------------------- page paths


def test_normalize_page_path_names_the_page():
    cases = [
        ("https://acme.example/gifts?ref=x#top", "/gifts"),
        ("/Menu/", "/Menu"),
        ("", "/"),
        ("https://acme.example", "/"),
        ("https://acme.example/", "/"),
        ("/a%20b", "/a b"),
        ("/a%zz", "/a%zz"),  # a raw % that isn't an escape survives
        ("/p?a=1/", "/p"),
        ("https://acme.example/x/y/z/", "/x/y/z"),
    ]
    for raw, want in cases:
        assert bot_ingest.normalize_page_path(raw) == want, raw
    # case is preserved for display; callers lower() it for keys
    assert bot_ingest.normalize_page_path("/Menu") != bot_ingest.normalize_page_path("/menu")


def test_on_brand_site_cases():
    f = bot_ingest.on_brand_site
    assert f("https://acme.example.evil.com/", ["acme.example"]) is False
    assert f("https://ACME.example:8443/pricing", ["acme.example"]) is True
    assert f("/gifts", ["acme.example"]) is True  # a worker may send a path
    assert f("https://other.example/x", ["acme.example"]) is False
    assert f("https://shop.acme.example/x", ["acme.example"]) is True
    assert f("https://www.acme.example/", ["acme.example"]) is True
    assert f("https://anything.example/", []) is True  # no domains: everything passes
    assert f(None, ["acme.example"]) is True


# ------------------------------------------------------------ connections


def test_connection_secret_lifecycle_and_hash_only_storage():
    row = new_connection()
    assert row["secret"].startswith("sfb_")
    got = bot_ingest.connection_for_secret(row["secret"])
    assert got["kind"] == "cloudflare_worker"
    assert got["domains"] == ["acme.example"]
    with db._conn() as conn:
        stored = conn.execute(
            "SELECT secret_hash FROM bot_connections WHERE id=?", (row["id"],)
        ).fetchone()[0]
        assert stored == bot_ingest.hash_secret(row["secret"])
        assert row["secret"] not in stored
    assert bot_ingest.revoke_connection(row["id"]) is True
    assert bot_ingest.revoke_connection(row["id"]) is False  # already revoked
    assert bot_ingest.connection_for_secret(row["secret"]) is None
    assert bot_ingest.connection_for_secret("nope") is None
    assert bot_ingest.connection_for_secret("sfb_" + "x" * 300) is None
    with db._conn() as conn:
        listing = bot_ingest.list_connections(conn)
        assert listing[0]["revoked"] is True
        assert listing[0]["kind"] == "cloudflare_worker"


def test_create_connection_normalizes_and_validates():
    row = new_connection(domains=("ACME.example:8443", "https://Shop.Acme.Example/", ""))
    assert row["domains"] == ["acme.example", "shop.acme.example"]
    row2 = new_connection(domains=None)
    assert row2["domains"] == []
    with pytest.raises(ValueError):
        new_connection(kind="cloudflare_flume")


# ------------------------------------------------------------------ ingest


def test_read_request_time_shapes_and_facts():
    def r(at):
        return bot_ingest._read_request(gpt(at=at), NOW_MS)

    assert r(NOW_MS)["at"].startswith("2026-10-19T10:00")
    assert r(NOW_MS * 1e6) is not None  # Logpush nanoseconds
    assert r(NOW_MS / 1000) is not None  # Logpush unix seconds
    assert r(str(NOW_MS)) is not None  # epoch ms as a string
    assert r(NOW_MS + 2 * 3600 * 1000) is None  # more than an hour ahead
    assert r(NOW_MS - 8 * DAY_MS) is None  # older than a week
    assert r("not-a-time") is None
    missing = gpt()
    missing.pop("at")
    assert bot_ingest._read_request(missing, NOW_MS) is not None  # missing at is now

    weird = bot_ingest._read_request(gpt(status=999, country="usa"), NOW_MS)
    assert weird["status"] is None  # 999 isn't a status
    assert weird["country"] == "US"


def test_verify_requests_skips_and_accepts_sync_and_async_verifiers():
    raws = [
        gpt(),
        {"userAgent": "ClaudeBot/1.0", "ip": "1.2.3.4", "url": "/c", "at": AT},
        {"userAgent": "ImNotABot", "ip": "20.171.206.9", "url": "/x", "at": AT},
        gpt(ip="1.1.1.1"),
    ]
    events, skipped = asyncio.run(
        bot_ingest.verify_requests(raws, [], verifier=verifier, now=NOW_MS)
    )
    assert [e["bot"] for e in events] == ["gptbot", "claudebot"]
    assert events[0]["page"].endswith("menu?x=1")
    assert skipped == {
        "invalid": 0,
        "other_host": 0,
        "unknown_agent": 1,
        "unverified_ip": 1,
        "no_ranges": 0,
    }

    async def async_verify(request):  # the real verifier's shape
        return verifier(request)

    events, _ = asyncio.run(
        bot_ingest.verify_requests(raws[:1], [], verifier=async_verify, now=NOW_MS)
    )
    assert events[0]["bot"] == "gptbot"


def test_batch_ingest_mixed_verdicts_writes_only_verified():
    connection = new_connection()
    reqs = [
        gpt(),  # verified
        gpt(userAgent="ClaudeBot/1.0", url="https://acme.example/gifts"),  # verified
        gpt(userAgent="ImNotABot"),  # unknown_agent
        gpt(ip="1.1.1.1"),  # unverified_ip
        {"userAgent": "GPTBot/1.0", "ip": "20.171.206.9"},  # no URL -> invalid
        gpt(at=iso(NOW_MS - 8 * DAY_MS)),  # too old -> invalid
        gpt(url="https://other.example/menu"),  # another site -> other_host
    ]
    result = asyncio.run(
        bot_ingest.ingest_bot_requests(connection, reqs, verifier=verifier)
    )
    assert result["received"] == 7
    assert result["verified"] == 2
    assert result["stored"] == 2
    assert result["skipped"] == {
        "invalid": 2,
        "other_host": 1,
        "unknown_agent": 1,
        "unverified_ip": 1,
        "no_ranges": 0,
    }
    with db._conn() as conn:
        events = conn.execute(
            "SELECT bot, page, country FROM bot_events ORDER BY bot"
        ).fetchall()
        assert [(e["bot"], e["page"], e["country"]) for e in events] == [
            ("claudebot", "/gifts", "US"),
            ("gptbot", "/menu", "US"),
        ]
        daily = {
            (r["day"], r["bot"], r["page"]): (r["bot_type"], r["hits"])
            for r in conn.execute("SELECT * FROM bot_daily")
        }
        assert daily[("2026-10-19", "gptbot", "/menu")] == ("training", 1)
        assert daily[("2026-10-19", "claudebot", "/gifts")] == ("training", 1)
        last_at = conn.execute(
            "SELECT last_event_at FROM bot_connections WHERE id=?", (connection["id"],)
        ).fetchone()[0]
        assert last_at is not None


def test_batch_ingest_counts_same_page_per_bot():
    connection = new_connection()
    reqs = [
        gpt(),
        gpt(),
        gpt(),
        gpt(userAgent="ClaudeBot/1.0", url="https://acme.example/menu?x=1"),
    ]
    result = asyncio.run(bot_ingest.ingest_bot_requests(connection, reqs, verifier=verifier))
    assert result["verified"] == 4 and result["stored"] == 4
    with db._conn() as conn:
        daily = {
            r["bot"]: r["hits"]
            for r in conn.execute("SELECT bot, hits FROM bot_daily WHERE page='/menu'")
        }
        assert daily == {"gptbot": 3, "claudebot": 1}  # one counter row per bot
        events = {
            r["page"]: r["hits"]
            for r in conn.execute(
                "SELECT page, count(*) hits FROM bot_events GROUP BY page"
            )
        }
        assert set(events) == {"/menu"}


def test_nothing_written_when_no_request_verifies():
    result = ingest([gpt(userAgent="ImNotABot"), {"userAgent": "GPTBot"}])
    assert result["received"] == 2
    assert result["verified"] == 0 and result["stored"] == 0
    assert result["skipped"]["invalid"] == 1 and result["skipped"]["unknown_agent"] == 1
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM bot_daily").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM bot_usage").fetchone()[0] == 0


def test_batch_is_kept_to_its_limit():
    connection = new_connection()
    many = [gpt(url=f"https://acme.example/p{i}") for i in range(bot_ingest.MAX_BATCH + 50)]
    result = asyncio.run(bot_ingest.ingest_bot_requests(connection, many, verifier=verifier))
    assert result["received"] == bot_ingest.MAX_BATCH
    assert result["stored"] == bot_ingest.MAX_BATCH  # no cap set: all stored
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_events").fetchone()[0] == bot_ingest.MAX_BATCH


def test_non_list_batch_reads_as_empty():
    result = ingest("junk")
    assert result["received"] == 0 and result["stored"] == 0


def test_event_cap_zero_locks_the_report(monkeypatch):
    monkeypatch.setenv("SEOFORGE_BOT_EVENT_CAP", "0")
    connection = new_connection()
    result = ingest([gpt(), gpt()], connection=connection)
    assert result["locked"] is True and result["stored"] == 0
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM bot_daily").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM bot_usage").fetchone()[0] == 0


def test_cap_overflow_folds_onto_other_and_counts_all(monkeypatch):
    monkeypatch.setenv("SEOFORGE_BOT_EVENT_CAP", "3")
    connection = new_connection()
    reqs = [gpt(url=f"https://acme.example/p{i}") for i in range(5)]
    result = asyncio.run(bot_ingest.ingest_bot_requests(connection, reqs, verifier=verifier))
    assert result["stored"] == 3
    assert result["used"] == 3 and result["cap"] == 3
    assert result["crossed80"] is True  # 3 >= 0.8 * 3
    with db._conn() as conn:
        usage = conn.execute("SELECT * FROM bot_usage").fetchone()
        assert usage["stored_events"] == 3 and usage["counted_events"] == 5
        assert usage["capped_at"] is not None and usage["alerted_at"] is not None
        daily = {r["page"]: r["hits"] for r in conn.execute("SELECT page, hits FROM bot_daily")}
        assert daily["/p0"] == 1 and daily["/p1"] == 1 and daily["/p2"] == 1
        assert daily["(other)"] == 2  # the overflow keeps the totals honest


def test_crossed80_alerts_only_monthly(monkeypatch):
    monkeypatch.setenv("SEOFORGE_BOT_EVENT_CAP", "10")
    connection = new_connection()
    first = ingest([gpt()] * 8, connection=connection)
    assert first["crossed80"] is True
    second = ingest([gpt()] * 2, connection=connection)
    assert second["stored"] == 2 and second["crossed80"] is False
    with db._conn() as conn:
        usage = conn.execute("SELECT * FROM bot_usage").fetchone()
        assert usage["stored_events"] == 10 and usage["counted_events"] == 10
        assert usage["alerted_at"] is not None


# ------------------------------------------------------------- log files


def _parsed(n=3, page_base="/f"):
    return {
        "records": n,
        "requests": [gpt(url=f"https://acme.example/{page_base}{i}") for i in range(n)],
    }


def test_log_file_claims_counts_and_writes_in_one_step():
    connection = new_connection()
    h = bot_ingest.log_file_hash('{"requests":"acme"}')
    result = asyncio.run(
        bot_ingest.ingest_log_file(connection, h, _parsed(), verifier=verifier)
    )
    assert result["received"] == 3 and result["verified"] == 3 and result["stored"] == 3
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_events").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 1
    # a sender that times out and sends the same body again is not re-read
    again = asyncio.run(
        bot_ingest.ingest_log_file(connection, h, _parsed(), verifier=verifier)
    )
    assert again["duplicate"] is True and again["stored"] == 0
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_events").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 1


def test_log_file_claim_rolls_back_on_failure():
    import seoforge.bot_ingest as bi

    connection = new_connection()
    h = bot_ingest.log_file_hash("file-1")
    calls = {"n": 0}

    def broken_apply(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("disk went away mid-write")

    old = bi._apply_verified
    bi._apply_verified = broken_apply
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(bi.ingest_log_file(connection, h, _parsed(), verifier=verifier))
    finally:
        bi._apply_verified = old
    assert calls["n"] == 1
    # a failure leaves nothing behind, so the sender's retry reads as new
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM bot_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM bot_usage").fetchone()[0] == 0
    retry = asyncio.run(
        bi.ingest_log_file(connection, h, _parsed(), verifier=verifier)
    )
    assert retry["stored"] == 3 and not retry.get("duplicate")


def test_raw_cap_counts_then_blocks(monkeypatch):
    monkeypatch.setenv("SEOFORGE_BOT_RAW_CAP", "4")
    connection = new_connection()
    first = bi_ingest_file(connection, "a", _parsed(3))
    assert first["stored"] == 3 and first["raw"] == 3
    # this batch crossed the cap while the month was still under it: counted, allowed
    second = bi_ingest_file(connection, "b", _parsed(2, page_base="/g"))
    assert second["raw"] == 5 and "rawCapped" not in second
    with db._conn() as conn:
        usage = conn.execute("SELECT * FROM bot_usage").fetchone()
        assert usage["raw_records"] == 5 and usage["raw_capped_at"] is not None
    # past it, a file's records are only counted
    third = bi_ingest_file(connection, "c", _parsed(2, page_base="/h"))
    assert third["rawCapped"] is True and third["stored"] == 0
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_events").fetchone()[0] == 5


def bi_ingest_file(connection, label, parsed):
    return asyncio.run(
        bot_ingest.ingest_log_file(
            connection, bot_ingest.log_file_hash(label), parsed, verifier=verifier
        )
    )


def test_log_file_locked_when_report_off(monkeypatch):
    monkeypatch.setenv("SEOFORGE_BOT_RAW_CAP", "0")
    connection = new_connection()
    result = bi_ingest_file(connection, "a", _parsed())
    assert result["locked"] is True and result["stored"] == 0
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 0


def test_log_file_empty_read_is_claimed_and_counted():
    connection = new_connection()
    body = '{"content":"tests"}'  # a Logpush job's upload test
    result = asyncio.run(
        bot_ingest.ingest_log_file(
            connection,
            bot_ingest.log_file_hash(body),
            {"records": 0, "requests": []},
            verifier=verifier,
        )
    )
    assert result["stored"] == 0 and result["received"] == 0
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 1


# ------------------------------------------------------------------ prune


def _old_rows(conn_row):
    old = iso(NOW_MS - 100 * DAY_MS)
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO bot_events(bot, bot_type, page, status, country,"
            " occurred_at, received_at) VALUES (?,?,?,?,?,?,?)",
            ("gptbot", "training", "/keep", 200, "US", AT, old),
        )
        conn.executemany(
            "INSERT INTO bot_events(bot, bot_type, page, status, country,"
            " occurred_at, received_at) VALUES (?,?,?,?,?,?,?)",
            [("gptbot", "training", f"/old{i}", 200, "US", old, old) for i in range(12)],
        )
        conn.executemany(
            "INSERT INTO bot_files(connection_id, body_hash, received_at) VALUES (?,?,?)",
            [(conn_row["id"], f"h{i}", iso(NOW_MS - 10 * DAY_MS)) for i in range(2)],
        )
        conn.executemany(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
            [("2025-01-01", "gptbot", "training", f"/d{i}", 1, "2025-01-01") for i in range(3)],
        )


def test_prune_deletes_old_rows_in_batches(monkeypatch):
    connection = new_connection()
    _old_rows(connection)
    monkeypatch.setattr(bot_ingest, "PRUNE_BATCH", 5)
    monkeypatch.setattr(bot_ingest, "MAX_PRUNE_CALLS", 10)
    totals = bot_ingest.prune_bot_events(now=NOW_MS)
    assert totals == {"events": 12, "daily": 3, "files": 2}
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bot_events").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM bot_daily").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 0


def test_prune_waits_a_day_when_calls_run_out(monkeypatch):
    connection = new_connection()
    _old_rows(connection)
    monkeypatch.setattr(bot_ingest, "PRUNE_BATCH", 5)
    monkeypatch.setattr(bot_ingest, "MAX_PRUNE_CALLS", 1)
    totals = bot_ingest.prune_bot_events(now=NOW_MS)
    assert totals == {"events": 5, "daily": 3, "files": 2}  # the rest waits a day


def test_prune_no_op_when_all_fresh():
    new_connection()
    assert bot_ingest.prune_bot_events(now=NOW_MS) == {"events": 0, "daily": 0, "files": 0}


# ----------------------------------------------------------------- report


def _day(days_ago):
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


def _seed_report(rows, last_event_at=None):
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO bot_connections(id, kind, secret_hash, domains, created_at,"
            " last_event_at, revoked_at) VALUES (1,'cloudflare_worker','h','[]',?,?,NULL)",
            (last_event_at or _day(10), last_event_at),
        )
        for row in rows:
            conn.execute(
                "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
                row,
            )


def test_bot_report_buckets_and_windows():
    stamp = lambda d: f"{d}T09:00:00+00:00"
    _seed_report(
        [
            (_day(31), "gptbot", "training", "/pricing", 10, stamp(_day(31))),
            (_day(3), "gptbot", "training", "/pricing", 5, stamp(_day(3))),
            (_day(3), "oai-searchbot", "search", "/blog", 3, stamp(_day(3))),
            (_day(3), "chatgpt-user", "user", "/docs", 2, stamp(_day(3))),
        ],
        last_event_at=db.now(),
    )
    with db._conn() as conn:
        report = bot_ingest.bot_report(conn)
    assert report["alert"] == "on"
    assert report["first_day"] == _day(31)
    types = {t["bot_type"]: t for t in report["types"]}
    assert types["training"]["hits"] == 15 and types["training"]["pages"] == 1
    assert types["training"]["bots"] == ["gptbot"]
    assert types["search"]["pages"] == 1 and types["search"]["bots"] == ["oai-searchbot"]
    bots = {b["bot"]: b for b in report["bots"]}
    assert bots["gptbot"]["hits"] == 15 and bots["gptbot"]["bot_type"] == "training"
    assert report["pages"][0]["page"] == "/pricing" and report["pages"][0]["hits"] == 15
    assert report["pages"][0]["training_last"] == stamp(_day(3))
    assert report["uncrawled"] is None
    # a narrower window drops the old day
    with db._conn() as conn:
        recent = bot_ingest.bot_report(conn, since=_day(10))
    assert [t["hits"] for t in recent["types"] if t["bot_type"] == "training"] == [5]


def test_bot_report_alarms_waiting_and_stale():
    with db._conn() as conn:
        report = bot_ingest.bot_report(conn)  # nothing at all
    assert report["alert"] == "waiting"

    with db._conn() as conn:
        conn.execute(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,'1',?)",
            (_day(1), "gptbot", "training", "/x", _day(1)),
        )
        fresh = bot_ingest.bot_report(conn)
    assert fresh["alert"] == "waiting"  # the first day of data is younger than 30 days

    # first day 31 days old, no connection has reported within 2 days
    _seed_report(
        [
            (_day(31), "gptbot", "training", "/pricing", 4, _day(31)),
            (_day(2), "gptbot", "training", "/pricing", 4, _day(2)),
        ],
        last_event_at=f"{_day(10)}T00:00:00+00:00",
    )
    with db._conn() as conn:
        stale = bot_ingest.bot_report(conn)
    assert stale["alert"] == "stale"


def test_bot_report_stale_when_search_reads_folded():
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO bot_connections(id, kind, secret_hash, domains, created_at,"
            " last_event_at, revoked_at) VALUES (9,'cloudflare_worker','h2','[]',?,?,NULL)",
            (db.now(), db.now()),
        )
        conn.execute(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
            (_day(31), "oai-searchbot", "search", "/blog", 1, _day(3)),
        )
        # search reads folding onto '(other)' in the last 30 days: pages past
        # the cap aren't known, so the report can't be trusted to be whole
        conn.execute(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
            (_day(1), "oai-searchbot", "search", "(other)", 99, db.now()),
        )
        folded = bot_ingest.bot_report(conn)
    assert folded["alert"] == "stale"
