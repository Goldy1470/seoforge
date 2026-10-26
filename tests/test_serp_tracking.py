"""The position-tracking kernel offline: the migration's three tables,
the reference's history-key and record semantics (stamp on success, keep
it and queue on failure), the domain/keyword CRUD round trips and the
derived digest helpers."""
import datetime as dt
import json

import pytest

from seoforge import db, serp_tracking as st


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(st, "_today", lambda: dt.date(2026, 10, 26))


def _tables(conn):
    return {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_migration_seven_creates_the_tracking_tables():
    with db._conn() as conn:
        assert {"serp_domains", "serp_keywords", "serp_retry_queue"} <= _tables(conn)


def test_history_key_is_calendar_day_unpadded():
    assert st.history_key(dt.date(2026, 10, 2)) == "2026-10-2"
    assert st.history_key(dt.date(2026, 10, 26)) == "2026-10-26"


def test_slugify_flattens_a_domain():
    assert st.slugify("https://www.Example.com/blog") == "example-com-blog"
    assert st.slugify("  Example.COM ") == "example-com"
    assert st.slugify("") == "domain"


def test_add_domain_round_trip_and_vocabulary():
    made = st.add_domain("example.com", notification_emails="a@b.com", scrape_strategy="smart")
    assert made["created"] is True
    assert made["slug"] == "example-com"
    assert made["notification"] is True and made["scrape_strategy"] == "smart"

    assert st.get_domain("example.com")["id"] == made["id"]
    assert st.get_domain("Example-COM")["id"] == made["id"]  # slug answers too
    assert [d["id"] for d in st.list_domains()] == [made["id"]]

    again = st.add_domain("https://www.example.com/about")
    assert again["created"] is False and again["id"] == made["id"]  # same seat

    with pytest.raises(ValueError):
        st.add_domain("interval.com", notification_interval="fortnightly")
    with pytest.raises(ValueError):
        st.add_domain("strategy.com", scrape_strategy="aggressive")


def test_domain_scrape_overrides_are_settable():
    st.add_domain("strategy.com")
    row = st.set_domain_scrape(
        "strategy.com",
        scrape_strategy="custom",
        scrape_pagination_limit=3,
        scrape_smart_full_fallback=True,
        subdomain_matching="mail,blog",
    )
    assert row["scrape_strategy"] == "custom"
    assert row["scrape_pagination_limit"] == 3
    assert row["scrape_smart_full_fallback"] is True
    assert row["subdomain_matching"] == "mail,blog"

    with pytest.raises(ValueError):
        st.set_domain_scrape("strategy.com", scrape_strategy="aggressive")
    with pytest.raises(ValueError):
        st.set_domain_scrape("strategy.com", nonsense=1)
    assert st.set_domain_scrape("missing.com", scrape_strategy="basic") is None


def test_add_keyword_defaults_validation_and_dedupe_learning():
    made = st.add_keyword("best running shoes", "shoes.com", tags="gear, shoes")
    assert made["device"] == "desktop" and made["country"] == "US"
    assert made["position"] == 0 and made["volume"] == 0
    assert made["tags"] == ["gear", "shoes"]
    assert made["history"] == {} and made["last_result"] == []
    assert made["last_update_error"] is False and made["sticky"] is False
    assert made["added"] and made["last_updated"]
    # the domain row came along for free
    assert st.get_domain("shoes.com")["keyword_count"] == 1
    # exact duplicates are allowed on purpose (reference parity)
    twin = st.add_keyword("best running shoes", "shoes.com")
    assert st.get_domain("shoes.com")["keyword_count"] == 2
    assert st.get_domain("shoes.com")["keyword_count"] == 2  # stable re-read

    with pytest.raises(ValueError):
        st.add_keyword("", "shoes.com")
    with pytest.raises(ValueError):
        st.add_keyword("k", "")
    with pytest.raises(ValueError):
        st.add_keyword("k", "shoes.com", device="tablet")
    with pytest.raises(ValueError):
        st.add_keyword("k", "shoes.com", country="USA")
    # 2-letter only, but case is normalized like the domain is
    upgraded = st.add_keyword("k", "shoes.com", country="us")
    assert upgraded["country"] == "US"


def test_list_filters_and_flag_updates():
    a = st.add_keyword("alpha", "list.com")
    st.add_keyword("beta", "list.com", device="mobile", country="DE")
    st.add_keyword("alphabet soup", "list.com", tags="tagged")

    assert len(st.list_keywords("list.com")) == 3
    assert [k["id"] for k in st.list_keywords("list.com", device="mobile")] == [a["id"] + 1]
    assert [k["id"] for k in st.list_keywords("list.com", country="DE")] == [a["id"] + 1]
    assert [k["id"] for k in st.list_keywords("list.com", keyword="alpha")] == [a["id"], a["id"] + 2]

    st.set_keyword_flags(a["id"], sticky=True, tags="pinned")
    updated = st.get_keyword(a["id"])
    assert updated["sticky"] is True and updated["tags"] == ["pinned"]
    assert st.set_keyword_flags(999999, sticky=True) is None


def test_delete_keywords_dequeues_and_recounts():
    st.add_domain("counts.com")
    k1 = st.add_keyword("one", "counts.com")
    k2 = st.add_keyword("two", "counts.com")
    st.queue_retry(k1["id"], "boom")
    st.queue_retry(k2["id"], "boom")

    assert st.delete_keywords(f"{k2['id']},{k1['id']}") == 2
    assert st.get_domain("counts.com")["keyword_count"] == 0
    assert st.pending_retries() == []

    # garbage ids are ignored honestly: nothing removed
    assert st.delete_keywords("missing") == 0
    assert st.delete_keywords("  ") == 0
    assert st.pending_retries() == []


def test_record_success_stamps_and_clears_error(monkeypatch):
    k = st.add_keyword("track me", "record.com")
    old_stamp = k["last_updated"]
    st.queue_retry(k["id"], "old failure")
    monkeypatch.setattr(st, "now", lambda: "2026-10-26T09:00:00+00:00")

    updated = st.record_scrape_result(
        k["id"], position=4, url="https://record.com/shoes", last_result=[{"position": 4}], scraper="serpapi"
    )
    assert updated["position"] == 4 and updated["url"] == "https://record.com/shoes"
    assert updated["history"] == {"2026-10-26": 4}
    assert updated["last_updated"] == "2026-10-26T09:00:00+00:00" > old_stamp  # success stamps
    assert updated["last_update_error"] is False and updated["updating"] is False
    assert updated["last_result"] == [{"position": 4}]
    assert st.pending_retries() == []  # success dequeues even without retry=True


def test_record_failure_keeps_stamp_records_and_queues(monkeypatch):
    k = st.add_keyword("track me", "record.com")
    old_stamp = k["last_updated"]
    monkeypatch.setattr(st, "now", lambda: "2026-10-26T09:10:00+00:00")

    updated = st.record_scrape_result(
        k["id"], position=11, error="rate limited", scraper="serper", retry=True
    )
    assert updated["position"] == 0  # a failed attempt never moves the rank
    assert updated["history"] == {"2026-10-26": 0}  # the day is logged: checked, not found (rank 0 carried through)
    assert updated["last_updated"] == old_stamp  # the stamp keeps its truth
    err = updated["last_update_error"]
    assert err["error"] == "rate limited" and err["scraper"] == "serper"
    assert err["date"] == "2026-10-26T09:10:00+00:00"
    assert updated["updating"] is False

    queued = st.pending_retries()
    assert [q["keyword_id"] for q in queued] == [k["id"]]
    assert queued[0]["error"] == "rate limited"

    st.queue_retry(k["id"], "again")  # idempotent
    assert len(st.pending_retries()) == 1

    # same-day recovery: the day key flips to the new position, the error clears
    fixed = st.record_scrape_result(k["id"], position=2)
    assert fixed["history"] == {"2026-10-26": 2}
    assert fixed["last_update_error"] is False
    assert st.pending_retries() == []


def test_record_failure_after_history_uses_previous_position():
    k = st.add_keyword("seen", "record.com")
    st.record_scrape_result(k["id"], position=6)
    failed = st.record_scrape_result(k["id"], position=99, error="blocked")
    # the failed day carries the keyword's previous rank, not a fake climb
    assert failed["history"]["2026-10-26"] == 6
    assert failed["position"] == 6


def test_a_missing_keyword_records_nothing():
    assert st.record_scrape_result(424242, position=1) is None


def test_derived_digest_helpers_match_the_reference():
    week = {"2026-1-3": 9, "2026-1-4": 5, "2026-1-5": 7, "2026-1-6": 0, "2025-12-30": 12}
    assert st.best_position(week) == 5
    assert st.best_position({"2026-1-3": 0}) is None
    # the digest's calling convention: history already holds today's entry,
    # so "previous" is the second-newest day in the dict
    same_day = {"2025-12-30": 12, "2026-1-4": 5, "2026-1-5": 7, "2026-1-6": 3}
    assert st.position_change(same_day, 3) == 4  # 7 -> 3 improved
    assert st.position_change({"2026-1-4": 5, "2026-1-5": 7, "2026-1-6": 8}, 8) == -1  # declined
    assert st.position_change({"2026-1-4": 5, "2026-1-5": 7, "2026-1-6": 0}, 0) == -93  # dropped out
    assert st.position_change({"2026-1-3": 4}, 1) == 0  # baseline day
    # the week slice keeps calendar order across the year end; 5 days fit whole
    assert st.last_week_history(week) == week
    nine = {f"2026-1-{d}": d for d in range(1, 10)}
    assert st.last_week_history(nine) == {f"2026-1-{d}": d for d in range(3, 10)}


def test_domain_delete_cascades_keywords_and_retries():
    st.add_domain("cascade.com")
    k1 = st.add_keyword("one", "cascade.com")
    k2 = st.add_keyword("two", "cascade.com")
    st.queue_retry(k1["id"], "boom")

    gone = st.delete_domain("cascade.com")
    assert gone == {"domain": "cascade.com", "keywordsRemoved": 2}
    assert st.get_domain("cascade.com") is None
    assert st.get_keyword(k1["id"]) is None
    assert st.get_keyword(k2["id"]) is None
    assert st.pending_retries() == []
    assert st.delete_domain("cascade.com") is None


def test_json_state_survives_the_row_round_trip():
    k = st.add_keyword("json me", "jsonrow.com")
    st.record_scrape_result(
        k["id"], position=3, url="https://jsonrow.com/x", last_result=[{"position": 3, "url": "u"}]
    )
    row = st.get_keyword(k["id"])
    with db._conn() as conn:
        raw = conn.execute("SELECT history, last_result FROM serp_keywords WHERE id = ?", (k["id"],)).fetchone()
    # stored as JSON strings, decoded by the parser
    assert json.loads(raw[0]) == {"2026-10-26": 3}
    assert json.loads(raw[1]) == [{"position": 3, "url": "u"}]
    assert row["history"] == {"2026-10-26": 3}
