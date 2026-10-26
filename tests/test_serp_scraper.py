"""The rank-scrape engine offline: injected fetch, strategy pages,
domain matching, the honest failure rules, refresh/retry recording and
the insight/CSV artifacts. No provider is ever contacted."""
import datetime as dt
import sqlite3

import pytest

from seoforge import serp_scraper as ss
from seoforge import serp_tracking as st
from seoforge import db
from seoforge.config import settings

SERPER_PAGE = (
    '[{"title":"First","link":"https://www.example.com/first","position":1},'
    '{"title":"Second","link":"https://other.example","position":2}]'
)
SERPAPI_PAGE = (
    '[{"position":1,"title":"Rank One","link":"https://other.example"},'
    '{"position":5,"title":"Rank Five","link":"https://example.com/deep/path"}]'
)


@pytest.fixture(autouse=True)
def today_control(monkeypatch):
    """Pins the history day; tests change today with `state["day"] = ...`."""
    state = {"day": dt.date(2026, 10, 26)}
    monkeypatch.setattr(st, "_today", lambda: state["day"])
    return state


@pytest.fixture
def serper_ready(monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    monkeypatch.setattr(settings, "serp_tracker_api_key", "SK")
    monkeypatch.setattr(settings, "serp_tracker_delay", 0)
    monkeypatch.setattr(settings, "serp_tracker_strategy", "basic")
    monkeypatch.setattr(settings, "serp_tracker_pages", 5)
    monkeypatch.setattr(settings, "serp_tracker_smart_fallback", False)
    monkeypatch.setattr(settings, "serp_tracker_retry", True)


def _serper_fetch(request):
    return SERPER_PAGE


def _failer(request):
    raise RuntimeError("blocked")


# ------------------------------------------------------------------ matching


def test_get_serp_finds_deep_paths_wwws_and_subdomains():
    items = [
        {"position": 1, "url": "https://a.example"},
        {"position": 2, "url": "https://other.example/x"},
        {"position": 3, "url": "https://www.example.com/deep/path"},
    ]
    # the returned URL is the raw item URL, exactly as the reference keeps it
    assert ss.get_serp("example.com", items) == {"position": 3, "url": "https://www.example.com/deep/path"}
    # a subdomain match only when listed
    subbed = [{"position": 1, "url": "https://shop.example.com/p"}]
    assert ss.get_serp("example.com", subbed, "")["position"] == 0
    assert ss.get_serp("example.com", subbed, "blog,shop") == {"position": 1, "url": "https://shop.example.com/p"}
    assert ss.get_serp("example.com", subbed, "*") == {"position": 1, "url": "https://shop.example.com/p"}
    # exact path match wins before host scanning; the raw URL comes back
    assert ss.get_serp("example.com/deep/path", items) == {"position": 3, "url": "https://www.example.com/deep/path"}
    assert ss.get_serp("example.com", items[:2]) == {"position": 0, "url": ""}
    assert ss.get_serp("", items) == {"position": 0, "url": ""}
    assert ss.get_serp("example.com", [{"position": 2, "url": "not a url"}])["position"] == 0


def test_full_results_array_fills_with_skipped_slots():
    full = ss.build_full_results([{"position": 1, "url": "https://a", "title": "A"}, {"position": 97, "url": "https://x", "title": "X"}])
    assert len(full) == 100
    assert full[0] == {"position": 1, "url": "https://a", "title": "A"}
    assert full[1]["skipped"] is True and full[1]["position"] == 2
    assert full[2]["skipped"] is True
    # a found rank keeps its own slot, never marked skipped
    assert full[96] == {"position": 97, "url": "https://x", "title": "X"}
    assert full[-1] == {"position": 100, "url": "", "title": "", "skipped": True}


# ------------------------------------------------------------------ strategy


def test_resolve_strategy_domain_overrides_fall_back_to_global():
    global_plan = ss.resolve_strategy(
        {"scrape_strategy": "basic", "scrape_pagination_limit": 5, "scrape_smart_full_fallback": False},
        None,
    )
    assert global_plan == {"strategy": "basic", "pagination_limit": 5, "smart_full_fallback": False}

    domain = {"scrape_strategy": "smart", "scrape_pagination_limit": 0, "scrape_smart_full_fallback": False}
    plan = ss.resolve_strategy(
        {"scrape_strategy": "basic", "scrape_pagination_limit": 4, "scrape_smart_full_fallback": True},
        domain,
    )
    assert plan == {"strategy": "smart", "pagination_limit": 4, "smart_full_fallback": True}


def test_pages_plans_for_each_strategy():
    plan = {"strategy": "basic", "pagination_limit": 5, "smart_full_fallback": False}
    assert ss._pages_for_strategy({"position": 0}, plan) == [1]
    custom = {**plan, "strategy": "custom", "pagination_limit": 3}
    assert ss._pages_for_strategy({"position": 5}, custom) == [1, 2, 3]
    smart = {**plan, "strategy": "smart"}
    assert ss._pages_for_strategy({"position": 23}, smart) == [2, 3, 4]
    assert ss._pages_for_strategy({"position": 0}, smart) == [1]
    assert ss._pages_for_strategy({"position": 99}, smart) == [9, 10]  # clamped at 10


# ------------------------------------------------------------------- scrape


def test_native_provider_keeps_the_providers_own_positions(monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "serpapi")
    k = st.add_keyword("coffee", "example.com", city="", country="US")
    st.record_scrape_result(k["id"], position=1)  # history day: page-2 anchor for smart plans

    row = st.get_keyword(k["id"])
    domain_row = st.get_domain("example.com")
    scraper = {"scraper_type": "serpapi", "scraping_api": "SK"}
    calls = []

    def fake_fetch(provider, parsed):
        calls.append(parsed["url"])
        return SERPAPI_PAGE

    result = ss.scrape_keyword(row, scraper, domain_row, fetch=fake_fetch)
    assert len(calls) == 1 and "num=100" in calls[0]
    assert result["error"] is False
    assert result["position"] == 5  # deep path hit on the tracked domain
    assert result["result"][0]["position"] == 1  # provider positions stand unrenumbered
    assert result["result"][-1]["position"] == 5


def test_basic_strategy_asks_page_one_and_renumbers(monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    k = st.add_keyword("shoes", "example.com")
    row = st.get_keyword(k["id"])
    scraper = {"scraper_type": "serper", "scraping_api": "SK", "scrape_strategy": "basic",
               "scrape_pagination_limit": 5, "scrape_smart_full_fallback": False}
    seen_pages = []

    def fake_fetch(provider, parsed):
        seen_pages.append(parsed["url"])
        return SERPER_PAGE

    result = ss.scrape_keyword(row, scraper, None, fetch=fake_fetch)
    assert len(seen_pages) == 1 and "page=1" in seen_pages[0]
    assert result["error"] is False
    assert result["position"] == 1  # www.example.com matches example.com
    assert result["result"][0] == {"position": 1, "url": "https://www.example.com/first", "title": "First"}
    assert result["result"][1] == {"position": 2, "url": "https://other.example", "title": "Second"}
    assert len(result["result"]) == 100 and result["result"][10]["skipped"] is True


def test_custom_strategy_walks_n_pages_and_smart_walks_neighbours(monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    k = st.add_keyword("shoes", "example.com")
    st.record_scrape_result(k["id"], position=23)  # history implies page 3
    row = st.get_keyword(k["id"])

    pages = []

    def fake_fetch(provider, parsed):
        pages.append(parsed["url"])
        return SERPER_PAGE  # domain first: stop conditions skip extra pages

    custom_scraper = {"scraper_type": "serper", "scraping_api": "SK", "scrape_strategy": "custom",
                      "scrape_pagination_limit": 2, "scrape_smart_full_fallback": False}
    result = ss.scrape_keyword(row, custom_scraper, None, fetch=fake_fetch)
    assert result["error"] is False
    assert len(pages) == 2  # pages 1 and 2 of the custom walk
    assert "page=2" in pages[1]

    pages.clear()
    smart_scraper = {**custom_scraper, "scrape_strategy": "smart"}
    result = ss.scrape_keyword(row, smart_scraper, None, fetch=fake_fetch)
    assert result["error"] is False
    assert len(pages) == 3  # the neighbours of page 3
    assert "page=2" in pages[0] and "page=4" in pages[-1]


def test_smart_full_fallback_walks_until_the_domain_appears(monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    k = st.add_keyword("rare", "example.com")
    row = st.get_keyword(k["id"])
    scraper = {"scraper_type": "serper", "scraping_api": "SK", "scrape_strategy": "smart",
               "scrape_pagination_limit": 5, "scrape_smart_full_fallback": True}
    other_pages = lambda i: (
        f'[{{"title":"R","link":"https://r{i}.example","position":{i}}}]'
    )
    state = {"page": 0}

    def fake_fetch(provider, parsed):
        state["page"] += 1
        if "page=5" in parsed["url"]:  # page 5: the keyword finally shows up
            return '[{"title":"Mine","link":"https://www.example.com/rare","position":1}]'
        return other_pages(state["page"])

    result = ss.scrape_keyword(row, scraper, None, fetch=fake_fetch)
    assert result["error"] is False and result["position"] == 41  # page 5, slot 1 renumbered


def test_failed_pages_never_invent_a_rank_loss(monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    k = st.add_keyword("blocked", "example.com")
    st.record_scrape_result(k["id"], position=8)
    row = st.get_keyword(k["id"])

    def fake_fetch(provider, parsed):
        if "page=1" in parsed["url"]:
            return '[{"title":"R","link":"https://r.example","position":1}]'  # answers, domain absent
        raise RuntimeError("880 gone")  # pages 2 and 3 fail

    scraper = {"scraper_type": "serper", "scraping_api": "SK", "scrape_strategy": "custom",
               "scrape_pagination_limit": 3, "scrape_smart_full_fallback": False}
    result = ss.scrape_keyword(row, scraper, None, fetch=fake_fetch)
    assert result["error"] == "2/3 pages failed - scraper too unreliable to determine position"
    assert result["position"] == 8  # carried through, never a fake 0


def test_an_honest_rank_loss_when_all_pages_answer(monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    k = st.add_keyword("dropped", "example.com")
    st.record_scrape_result(k["id"], position=8)
    row = st.get_keyword(k["id"])

    def fake_fetch(provider, parsed):
        return '[{"title":"R","link":"https://r.example","position":1}]'  # elsewhere

    scraper = {"scraper_type": "serper", "scraping_api": "SK", "scrape_strategy": "basic",
               "scrape_pagination_limit": 5, "scrape_smart_full_fallback": False}
    result = ss.scrape_keyword(row, scraper, None, fetch=fake_fetch)
    assert result["error"] is False and result["position"] == 0  # a real loss, recorded


def test_everything_failing_answers_an_error_message(monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    k = st.add_keyword("dark", "example.com")
    row = st.get_keyword(k["id"])
    scraper = {"scraper_type": "serper", "scraping_api": "SK", "scrape_strategy": "basic",
               "scrape_pagination_limit": 5, "scrape_smart_full_fallback": False}
    result = ss.scrape_keyword(row, scraper, None, fetch=_failer)
    assert result["error"] == "Scraper failed on all 1 pages for dark"
    assert result["position"] == 0  # seed row never had one


# ------------------------------------------------------------------- refresh


def test_refresh_records_positions_and_clear_flags(serper_ready, monkeypatch):
    st.add_domain("bench.com", scrape_strategy="basic")
    a = st.add_keyword("alpha", "bench.com")
    b = st.add_keyword("beta", "bench.com", device="mobile")
    st.record_scrape_result(a["id"], position=6)
    st.set_keyword_updating(a["id"], True)

    # fresh results one per keyword
    seq = {"n": 0}

    def fake_fetch(provider, parsed):
        seq["n"] += 1
        return (
            '[{"title":"M","link":"https://www.bench.com/a","position":3}]'
            if seq["n"] % 2
            else '[{"title":"M","link":"https://www.bench.com/b","position":9}]'
        )

    out = ss.refresh_domain("bench.com", fetch=fake_fetch)
    assert out == {"domain": "bench.com", "keywords": 2, "refreshed": 2, "failed": 0}
    row_a = st.get_keyword(a["id"])
    row_b = st.get_keyword(b["id"])
    # provider positions are replaced by the page offset: slot 0 on page 1 is rank 1
    assert row_a["position"] == 1 and row_a["updating"] is False
    assert row_b["position"] == 1
    assert row_a["last_updated"] and row_a["last_update_error"] is False
    assert len(row_a["last_result"]) == 100


def test_refresh_queues_failures_when_retry_is_on(serper_ready):
    st.add_domain("flaky.com")
    k = st.add_keyword("flaky kw", "flaky.com")
    out = ss.refresh_domain("flaky.com", fetch=_failer)
    assert out["refreshed"] == 0 and out["failed"] == 1
    rows = st.pending_retries()
    assert [r["keyword_id"] for r in rows] == [k["id"]]
    assert st.get_keyword(k["id"])["last_update_error"]["error"].startswith("Scraper failed")


def test_refresh_refuses_without_a_provider(serper_ready, monkeypatch):
    monkeypatch.setattr(settings, "serp_tracker_provider", "never")
    assert ss.refresh_domain("x.com") == {"error": "Scraper has not been set up yet"}


def test_the_hourly_retry_lane_recovers_the_queue(serper_ready):
    st.add_domain("retries.com")
    k = st.add_keyword("retry kw", "retries.com", device="mobile")
    ss.refresh_domain("retries.com", fetch=_failer)
    assert st.pending_retries()

    out = ss.retry_pending(fetch=lambda provider, parsed: '[{"title":"M","link":"https://www.retries.com/x","position":2}]')
    assert out["retried"] == 1 and out["recovered"] == 1 and out["remaining"] == 0
    row = st.get_keyword(k["id"])
    assert row["position"] == 1 and row["last_update_error"] is False  # slot 0 on page 1 is rank 1
    assert len(row["last_result"]) == 100
    # an empty queue stays cheap
    assert ss.retry_pending() == {"retried": 0}


def test_retry_keeps_failing_keywords_in_place(serper_ready):
    st.add_domain("dark.com")
    k = st.add_keyword("dark kw", "dark.com")
    ss.refresh_domain("dark.com", fetch=_failer)
    out = ss.retry_pending(fetch=_failer)
    assert out["retried"] == 1 and out["recovered"] == 0 and out["remaining"] == 1


# ------------------------------------------------------------------ insights


def test_insights_roll_buckets_movement_and_best(today_control):
    st.add_domain("stats.com")

    # a keyword improving into the top 3 across two recorded days
    rising = st.add_keyword("moving up", "stats.com")
    today_control["day"] = dt.date(2026, 10, 25)
    st.record_scrape_result(rising["id"], position=4)
    today_control["day"] = dt.date(2026, 10, 26)
    st.record_scrape_result(rising["id"], position=2)

    # a keyword declining past the top 50
    falling = st.add_keyword("far down", "stats.com")
    st.record_scrape_result(falling["id"], position=75)
    today_control["day"] = dt.date(2026, 10, 27)
    st.record_scrape_result(falling["id"], position=80)

    # and one lost entirely (never scraped: rank 0)
    st.add_keyword("lost one", "stats.com")

    summary = ss.insights("stats.com")
    assert summary["keywords"] == 3
    assert summary["distribution"] == {"top3": 1, "top10": 0, "top20": 0, "top50": 0, "beyond": 1, "lost": 1}
    assert summary["avg_position"] == round((2 + 80) / 2, 1)  # lost ranks are out of the average
    assert summary["best"] == 2
    assert summary["improved"] == 1 and summary["declined"] == 1  # 4 -> 2 up, 75 -> 80 down

    assert ss.insights("missing.com") == {
        "domain": "missing.com",
        "keywords": 0,
        "improved": 0,
        "declined": 0,
        "avg_position": None,
        "best": None,
        "distribution": {"top3": 0, "top10": 0, "top20": 0, "top50": 0, "beyond": 0, "lost": 0},
    }


# ---------------------------------------------------------------------- csv


def test_keywords_csv_lands_in_exports_with_the_reference_columns(serper_ready):
    st.add_domain("exporting.com")
    k = st.add_keyword("tagged kw", "exporting.com", tags="gear, shoes", city="Berlin", country="DE")
    st.record_scrape_result(k["id"], position=4, url="https://exporting.com/x")

    out = ss.keywords_csv("exporting.com")
    content = open(out["path"], encoding="utf-8").read()
    name = content.splitlines()[0]

    assert name == "ID,Keyword,Position,URL,Country,City,Device,Updated,Added,Tags"
    body = [r for r in content.splitlines()[1:] if r.strip()]
    assert len(body) == 1
    id_, kw, pos, url, country, city, device = body[0].split(",")[:7]
    assert int(id_) == k["id"]
    assert kw == "tagged kw"
    assert pos == "4"
    assert url == "https://exporting.com/x"
    assert country == "Germany" and city == "Berlin" and device == "desktop"
    # tags join raw (the reference's `tags.join(',')`) and csv-quotes the cell
    assert body[0].rstrip("\r").endswith('"gear,shoes"')

    # an unscraped keyword exports with dashes, never empty junk
    plain = st.add_keyword("fresh kw", "exporting.com")
    content2 = open(ss.keywords_csv("exporting.com")["path"], encoding="utf-8").read()
    fresh_row = [r for r in content2.splitlines() if r.startswith(f"{plain['id']},")]
    assert fresh_row and fresh_row[0].split(",")[2] == "-"
    assert "fresh kw" in fresh_row[0]
