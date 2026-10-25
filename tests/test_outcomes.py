"""Outcome capture (did the work help?): window math, the verdict rules
(the count ratios and the direction-aware position rule), the measurable
reads over the site's own time series, and the one-shot validation of a
closed opportunity. Everything offline: GSC window reads are faked, rank
snapshots and bot counters are seeded by hand.
"""
import datetime as dt
import json

import pytest

from seoforge import db, integrations, outcomes

TODAY = dt.date(2026, 10, 25)
SITE = "https://acme.example"


@pytest.fixture(autouse=True)
def fixed_site(monkeypatch):
    from seoforge.config import settings

    monkeypatch.setattr(settings, "site_url", SITE)
    return settings


@pytest.fixture()
def at_today(monkeypatch):
    """so `now` never drifts: every test pins the measurement clock."""
    monkeypatch.setattr(outcomes, "today", lambda: TODAY)
    return TODAY


def day(days_ago: int) -> str:
    return (TODAY - dt.timedelta(days=days_ago)).isoformat()


# ------------------------------------------------------- windows + rules

def test_windows_are_like_for_like_seven_day_periods():
    assert outcomes.window_ending_before(dt.date(2026, 10, 10)) == ("2026-10-03", "2026-10-09")
    assert outcomes.window_starting_after(dt.date(2026, 10, 10)) == ("2026-10-11", "2026-10-17")
    assert outcomes.window_ending_before(dt.date(2026, 1, 1)) == ("2025-12-25", "2025-12-31")


def test_derive_outcome_only_pasts_the_noise_floor():
    def pair(before, after):
        return [{"metric": "gsc_clicks", "before": before, "after": after}]

    assert outcomes.derive_outcome(pair(100, 120)) == "improved"
    assert outcomes.derive_outcome(pair(100, 80)) == "declined"
    # exactly at the 5% floor it is a wobble, in both directions
    assert outcomes.derive_outcome(pair(100, 105)) == "no_meaningful_change"
    assert outcomes.derive_outcome(pair(100, 95)) == "no_meaningful_change"
    # a zero before has no ratio to scale by; any real arrival is movement
    assert outcomes.derive_outcome(pair(0, 0.001)) == "improved"
    assert outcomes.derive_outcome(pair(0, 0)) == "no_meaningful_change"
    # unmeasured vs arrived reads as zero-before movement, not as nothing
    # (the metric pair itself decides; the caller filters unmeasured)
    assert outcomes.derive_outcome([]) == "not_measurable"


def test_derive_outcome_mixed_movement_reads_improved():
    metrics = [
        {"before": 100, "after": 80},   # declined
        {"before": 10, "after": 30},    # improved
    ]
    assert outcomes.derive_outcome(metrics) == "improved"


def test_position_verdicts_read_lower_is_better():
    v = outcomes.position_verdict
    assert v(8.2, 7.4) == "improved"
    assert v(7.4, 8.2) == "declined"
    assert v(7.4, 7.6) == "unchanged"  # 0.2 of wobble
    assert v(7.0, 7.5) == "declined"   # exactly at the 0.5 floor counts
    assert v(7.5, 7.0) == "improved"
    assert v(None, 3.0) == "not_enough_data"
    assert v(3.0, None) == "not_enough_data"
    assert v(None, None) == "not_enough_data"


def test_position_verdicts_fold_into_the_outcome_vocabulary():
    assert outcomes.outcome_from_verdicts([]) is None
    assert outcomes.outcome_from_verdicts(["improved", "unchanged"]) == "improved"
    assert outcomes.outcome_from_verdicts(["declined", "unchanged"]) == "declined"
    assert outcomes.outcome_from_verdicts(["unchanged", "not_enough_data"]) == "no_meaningful_change"
    assert outcomes.outcome_from_verdicts(["not_enough_data"]) == "not_measurable"


# ------------------------------------------------------------- targets

def test_target_parts_read_page_and_query():
    parts = outcomes.target_parts(
        "promote_to_top5", "/menu [best gifts]", {"query": "best gifts"}
    )
    assert parts["page"].startswith(SITE)
    assert parts["page"].endswith("/menu")
    assert parts["page_key"] == "/menu"
    assert parts["query"] == "best gifts"

    url_target = outcomes.target_parts("fix_title_meta", "https://www.acme.example/gifts/")
    assert url_target["page"] == "https://www.acme.example/gifts/"
    assert url_target["page_key"] == "/gifts"
    assert url_target["query"] is None

    # a query-only target (cannibalization amounts on the query itself)
    q_only = outcomes.target_parts("repair_cannibalization", "best gifts")
    assert q_only["page"] is None and q_only["query"] == "best gifts"

    # a target with no measurable object at all
    none_target = outcomes.target_parts("backlink_submission", "ProductHunt (DR 90)")
    assert none_target["page"] is None and none_target["query"] is None

    # the evidence's named query counts even on a plain path target
    ev = outcomes.target_parts("build_question_page", "/free-guides", {})
    assert ev["page_key"] == "/free-guides"


# ------------------------------------------------------- the GSC reads

def gsc_row(query, page, clicks, impressions, position):
    return {
        "keys": [query, page],
        "clicks": clicks,
        "impressions": impressions,
        "position": position,
    }


def test_gsc_metrics_bucket_by_page_and_sum_queries():
    rows = [
        gsc_row("best gifts", "https://acme.example/menu?x=1", 3, 40, 9.2),
        gsc_row("gift guide", "https://acme.example/menu", 2, 10, 11.0),
        gsc_row("other", "https://other.example/", 5, 50, 3.0),
    ]
    g = outcomes.gsc_metrics(rows, "https://acme.example/menu")
    # both rows belong to the page's bucket (www/query/trailing slash gone)
    assert g["gsc_clicks"] == 5
    assert g["gsc_impressions"] == 50
    assert g["gsc_position"] == 10.1  # the mean of the two rows' positions

    # with a named query, only that query's row for the page counts
    g = outcomes.gsc_metrics(rows, "https://acme.example/menu", "best gifts")
    assert g["gsc_clicks"] == 3 and g["gsc_position"] == 9.2

    # a query-only target matches every page the query lands on
    g = outcomes.gsc_metrics(rows, None, "best gifts")
    assert g["gsc_clicks"] == 3 and g["gsc_impressions"] == 40

    # no match, or a target naming nothing: unmeasured, never zero
    assert outcomes.gsc_metrics(rows, "https://acme.example/never")["gsc_clicks"] is None
    none_target = outcomes.gsc_metrics(rows, None)
    assert (
        none_target["gsc_clicks"] is None
        and none_target["gsc_impressions"] is None
        and none_target["gsc_position"] is None
    )
    empty = outcomes.gsc_metrics([], None)
    assert empty["gsc_clicks"] is None and empty["gsc_impressions"] is None


def test_gsc_window_rows_pass_an_explicit_window(monkeypatch):
    calls = {}

    def fake_query(dims, days=None, **kw):
        calls["dims"] = dims
        calls["kw"] = kw
        return []

    import seoforge.gsc as gsc

    monkeypatch.setattr(gsc, "query", fake_query)
    rows = outcomes.gsc_window_rows(["query", "page"], ("2026-10-01", "2026-10-08"))
    assert rows == []
    assert calls["dims"] == ["query", "page"]
    assert calls["kw"]["extra"] == {"startDate": "2026-10-01", "endDate": "2026-10-08"}


def test_gsc_window_rows_say_unmeasured_when_the_property_is_off(monkeypatch):
    import seoforge.gsc as gsc

    def no_property(dims, **kw):  # gsc raises SystemExit when GSC_PROPERTY is unset
        raise SystemExit("Set GSC_PROPERTY ...")

    monkeypatch.setattr(gsc, "query", no_property)
    assert outcomes.gsc_window_rows(["query"], ("2026-10-01", "2026-10-08")) == []


# ------------------------------------------------- the site's own series

def test_rank_and_bot_reads_come_from_their_tables():
    ours = outcomes.site_domain()
    assert ours  # settings.site_url is set by the fixture
    with db._conn() as conn:
        conn.executemany(
            "INSERT INTO rank_snapshots(keyword,domain,position,url,searched_at) VALUES (?,?,?,?,?)",
            [
                ("best gifts", ours, 9, "/menu", f"{day(20)}T09:00:00+00:00"),
                ("best gifts", ours, None, "/menu", f"{day(19)}T09:00:00+00:00"),  # row w/o position
                ("best gifts", "other.example", 2, "/x", f"{day(19)}T09:00:00+00:00"),
            ],
        )
        conn.execute(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
            (day(19), "gptbot", "training", "/menu", 4, db.now()),
        )
        before = (day(21), day(15))
        after = (day(14), day(8))
        assert outcomes.rank_position(conn, "best gifts", before) == 9.0
        assert outcomes.rank_position(conn, "best gifts", after) is None
        assert outcomes.rank_position(conn, "unheard keyword", before) is None
        assert outcomes.bot_reads(conn, "/menu", before) == 4
        assert outcomes.bot_reads(conn, "/menu", after) == 0  # the window's real zero
        assert outcomes.bot_reads(conn, None, before) is None


# ------------------------------------------------------------- validate

def seeded_opportunity(target="/menu [best gifts]", type_="promote_to_top5", **over):
    row = {
        "type": type_,
        "target": target,
        "impact": 4,
        "effort": 1,
        "score": 4,
        "evidence": json.dumps({"query": "best gifts", "page": "/menu"}),
        "status": "open",
        "created_at": f"{day(30)}T06:00:00+00:00",
    }
    row.update(over)
    with db._conn() as conn:
        cols = ",".join(row)
        marks = ",".join("?" for _ in row)
        cur = conn.execute(f"INSERT INTO opportunities({cols}) VALUES ({marks})", tuple(row.values()))
        return cur.lastrowid


def close(opp_id, when=12):
    with db._conn() as conn:
        conn.execute(
            "UPDATE opportunities SET status='done', completed_at=?,"
            " outcome='pending_measurement' WHERE id=?",
            (f"{day(when)}T06:00:00+00:00", opp_id),
        )


GSC_ROWS = {
    "before": [
        gsc_row("best gifts", "https://acme.example/menu?x=1", 2, 30, 9.0),
        gsc_row("gift guide", "https://acme.example/menu", 1, 10, 9.0),
    ],
    "after": [
        gsc_row("best gifts", "https://acme.example/menu?x=1", 6, 60, 7.0),
        gsc_row("gift guide", "https://acme.example/menu", 1, 10, 7.0),
    ],
}


@pytest.fixture()
def gsc_by_window(monkeypatch):
    """The fake GSC window read: rows per window, keyed on which window the
    request falls in (before: raised-8..raised-2, after: closed+1..closed+7)."""

    def fake(dims, window):
        which = "before" if window[1] <= day(23) else "after"
        return list(GSC_ROWS[which])

    monkeypatch.setattr(outcomes, "gsc_window_rows", fake)
    return GSC_ROWS


def seed_series(bot_before=2, bot_after=4, rank_before=9.0, rank_after=6.0, domain=None):
    ours = domain or outcomes.site_domain()
    # created_at = day(30): the before window runs day(37)..day(31), the
    # after window day(11)..day(5) — every seeded row has to land inside.
    with db._conn() as conn:
        if bot_before is not None:
            for d in (day(36), day(33)):
                conn.execute(
                    "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
                    (d, "gptbot", "training", "/menu", bot_before, d),
                )
        if bot_after is not None:
            for d in (day(10), day(9)):
                conn.execute(
                    "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
                    (d, "gptbot", "training", "/menu", bot_after, d),
                )
        if rank_before is not None:
            conn.execute(
                "INSERT INTO rank_snapshots(keyword,domain,position,url,searched_at) VALUES (?,?,?,?,?)",
                ("best gifts", ours, rank_before, "/menu", f"{day(35)}T09:00:00+00:00"),
            )
        if rank_after is not None:
            conn.execute(
                "INSERT INTO rank_snapshots(keyword,domain,position,url,searched_at) VALUES (?,?,?,?,?)",
                ("best gifts", ours, rank_after, "/menu", f"{day(10)}T09:00:00+00:00"),
            )


def test_full_validation_records_the_verdict_once(at_today, gsc_by_window):
    opp_id = seeded_opportunity()
    seed_series()

    close(opp_id, when=12)
    outcome = outcomes.validate_opportunity(opp_id, now=TODAY)
    assert outcome == "improved"
    rows = outcomes.list_outcomes(limit=5)
    assert rows[0]["id"] == opp_id and rows[0]["outcome"] == "improved"

    with db._conn() as conn:
        stored = conn.execute(
            "SELECT outcome, validation, validated_at FROM opportunities WHERE id=?",
            (opp_id,),
        ).fetchone()
        validation = json.loads(stored["validation"])
        assert validation["beforeWindow"]["to"] == day(31)
        assert validation["afterWindow"]["from"] == day(11)
        metrics = {m["metric"]: m for m in validation["metrics"]}
        # the target names the page AND its query: only that query's row counts
        assert metrics["gsc_clicks"]["before"] == 2 and metrics["gsc_clicks"]["after"] == 6
        assert metrics["gsc_impressions"]["before"] == 30 and metrics["gsc_impressions"]["after"] == 60
        assert metrics["gsc_position"]["before"] == 9.0 and metrics["gsc_position"]["after"] == 7.0
        assert metrics["rank_position"]["before"] == 9.0 and metrics["rank_position"]["after"] == 6.0
        assert metrics["bot_reads"]["before"] == 4 and metrics["bot_reads"]["after"] == 8
        # position verdicts live next to the metrics
        assert validation["positionVerdicts"]["rank_position"] == "improved"
        assert validation["positionVerdicts"]["gsc_position"] == "improved"
        events = conn.execute(
            "SELECT event FROM opportunity_events WHERE opportunity_id=? ORDER BY id",
            (opp_id,),
        ).fetchall()
        assert [r["event"] for r in events] == ["outcome_measured"]

    # a re-measure of the same pair does not change the recorded verdict
    assert outcomes.validate_opportunity(opp_id, now=TODAY) == "improved"
    with db._conn() as conn:
        assert conn.execute(
            "SELECT count(*) c FROM opportunity_events WHERE opportunity_id=?", (opp_id,)
        ).fetchone()["c"] == 1


def test_only_position_metrics_speak_when_nothing_countable_exists(at_today, monkeypatch):
    # A query-only target (the cannibalization rule's bare query) with GSC
    # delivering no rows at all and no page to key bot counters on: nothing
    # countable measures anywhere, so the folded position verdict speaks.
    monkeypatch.setattr(outcomes, "gsc_window_rows", lambda dims, window: [])
    ours = outcomes.site_domain()
    with db._conn() as conn:
        conn.executemany(
            "INSERT INTO rank_snapshots(keyword,domain,position,url,searched_at) VALUES (?,?,?,?,?)",
            [
                ("best gifts", ours, 9.0, "", f"{day(35)}T09:00:00+00:00"),
                ("best gifts", ours, 6.0, "", f"{day(10)}T09:00:00+00:00"),
            ],
        )
    opp_id = seeded_opportunity(
        type_="repair_cannibalization", target="best gifts", evidence="{}"
    )
    close(opp_id, when=12)
    outcome = outcomes.validate_opportunity(opp_id, now=TODAY)
    assert outcome == "improved"

    with db._conn() as conn:
        validation = json.loads(
            conn.execute(
                "SELECT validation FROM opportunities WHERE id=?", (opp_id,)
            ).fetchone()["validation"]
        )
    metrics = {m["metric"]: m for m in validation["metrics"]}
    assert metrics["gsc_clicks"]["before"] is None and metrics["gsc_clicks"]["after"] is None
    assert metrics["bot_reads"]["before"] is None and metrics["bot_reads"]["after"] is None
    assert metrics["rank_position"]["before"] == 9.0 and metrics["rank_position"]["after"] == 6.0
    assert validation["positionVerdicts"]["rank_position"] == "improved"
    assert validation["positionVerdicts"]["gsc_position"] == "not_enough_data"


def test_counts_flat_but_a_position_moved_reads_the_movement(at_today, monkeypatch):
    # The count derivation says no meaningful change — clicks, GSC position
    # and bot reads flat on both sides — but the rank moved. The
    # mixed-movement rule does not hide it under "no change".
    def flat(dims, window):
        return [gsc_row("best gifts", "https://acme.example/menu", 2, 30, 9.5)]

    monkeypatch.setattr(outcomes, "gsc_window_rows", flat)
    opp_id = seeded_opportunity()  # '/menu [best gifts]'
    seed_series(bot_before=2, bot_after=2, rank_before=9.0, rank_after=6.0)
    close(opp_id, when=12)
    assert outcomes.validate_opportunity(opp_id, now=TODAY) == "improved"


def test_an_open_opportunity_is_not_measured_yet(at_today, gsc_by_window):
    opp_id = seeded_opportunity()
    assert outcomes.validate_opportunity(opp_id, now=TODAY) is None
    with db._conn() as conn:
        stored = conn.execute("SELECT outcome FROM opportunities WHERE id=?", (opp_id,)).fetchone()
        assert stored["outcome"] is None


def test_a_settling_window_still_in_future_waits(at_today, gsc_by_window):
    opp_id = seeded_opportunity()
    close(opp_id, when=4)  # its after window ends 6 days from today
    assert outcomes.validate_opportunity(opp_id, now=TODAY) is None
    with db._conn() as conn:
        stored = conn.execute("SELECT outcome FROM opportunities WHERE id=?", (opp_id,)).fetchone()
        # still armed, never measured: reading the half-window would read low
        assert stored["outcome"] == outcomes.PENDING


def test_a_legacy_done_row_without_a_close_date_is_not_measurable(at_today, gsc_by_window):
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO opportunities(type,target,impact,effort,score,status,created_at)"
            " VALUES ('fix_title_meta','https://acme.example/menu',4,1,4,'done',?)",
            (f"{day(30)}T06:00:00+00:00",),
        )
        opp_id = conn.execute("SELECT id FROM opportunities").fetchone()["id"]
    assert outcomes.validate_opportunity(opp_id, now=TODAY) == "not_measurable"
    with db._conn() as conn:
        stored = conn.execute(
            "SELECT outcome, validation FROM opportunities WHERE id=?", (opp_id,)
        ).fetchone()
        assert stored["outcome"] == "not_measurable" and stored["validation"] is None


def test_a_target_with_no_measurable_object_says_so_once(at_today, gsc_by_window):
    opp_id = seeded_opportunity(
        type_="backlink_submission", target="ProductHunt (DR 90)", evidence="{}"
    )
    close(opp_id, when=12)
    outcome = outcomes.validate_opportunity(opp_id, now=TODAY)
    assert outcome == "not_measurable"
    with db._conn() as conn:
        stored = conn.execute(
            "SELECT validation FROM opportunities WHERE id=?", (opp_id,)
        ).fetchone()
        assert stored["validation"] is None  # no invented metric


def test_sweep_measures_settled_done_rows_only(at_today, gsc_by_window, monkeypatch):
    ready = seeded_opportunity()
    close(ready, when=12)
    fresh = seeded_opportunity(target="/other [{q}]")
    close(fresh, when=2)  # still settling
    open_opp = seeded_opportunity(target="/never [{q}]")  # still open

    seed_series()
    result = outcomes.sweep(now=TODAY)
    assert result["measured"] == 1 and result["failed"] == 0
    with db._conn() as conn:
        assert conn.execute(
            "SELECT outcome FROM opportunities WHERE id=?", (ready,)
        ).fetchone()["outcome"] == "improved"
        assert conn.execute(
            "SELECT outcome FROM opportunities WHERE id=?", (fresh,)
        ).fetchone()["outcome"] == "pending_measurement"

    # a second sweep does not re-measure what is answered
    assert outcomes.sweep(now=TODAY) == {"measured": 0, "failed": 0}


def test_sweep_is_best_effort_per_row(at_today, gsc_by_window, monkeypatch):
    first = seeded_opportunity()
    close(first, when=12)
    second = seeded_opportunity(target="/other [broken]")
    close(second, when=12)

    def broken(opp_id, *, now=None):
        if opp_id == second:
            raise RuntimeError("the window read went away")
        return "improved"

    monkeypatch.setattr(outcomes, "validate_opportunity", broken)
    counts = outcomes.sweep(now=TODAY)
    assert counts["failed"] == 1 and counts["measured"] == 1


# --------------------------------------------------------------- mark_done

def test_mark_done_stamps_the_close_and_arms_the_measurement():
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO opportunities(type,target,impact,effort,score,status,created_at)"
            " VALUES ('fix_title_meta','https://acme.example/menu',4,1,4,'open',?)",
            (f"{day(30)}T06:00:00+00:00",),
        )
    assert integrations.mark_done("fix_title_meta|https://acme.example/menu") == (
        "fix_title_meta https://acme.example/menu -> done"
    )
    with db._conn() as conn:
        row = conn.execute(
            "SELECT status, completed_at, outcome FROM opportunities"
        ).fetchone()
        assert row["status"] == "done"
        assert row["completed_at"] is not None
        assert row["outcome"] == outcomes.PENDING
        events = [
            r["event"]
            for r in conn.execute(
                "SELECT event FROM opportunity_events ORDER BY id"
            ).fetchall()
        ]
        assert events == ["opportunity_done"]

    # a re-close does not move the close date (the windows are built on it)
    completions = row["completed_at"]
    integrations.mark_done("fix_title_meta|https://acme.example/menu")
    with db._conn() as conn:
        again = conn.execute("SELECT completed_at, outcome FROM opportunities").fetchone()
        assert again["completed_at"] == completions
        assert again["outcome"] == outcomes.PENDING
        assert conn.execute("SELECT count(*) c FROM opportunity_events").fetchone()["c"] == 1
