"""The rank tracker's surfaces: every MCP tool round trip (offline,
injecting nothing), the scheduler's three tracker lanes, and the CLI
command wiring."""
import datetime as dt

from seoforge import db, scheduler
from seoforge.config import settings


def _tool(mod, name):
    obj = getattr(mod, name)
    return obj.fn if hasattr(obj, "fn") else obj


# ---------------------------------------------------------------- MCP tools


def test_mcp_serp_domain_tools_round_trip():
    from seoforge import mcp_server

    made = _tool(mcp_server, "serp_domain_add")(
        "acme.com", notification_emails="a@acme.com", scrape_strategy="basic"
    )
    assert made["created"] is True and made["scrape_strategy"] == "basic"
    again = _tool(mcp_server, "serp_domain_add")("acme.com")
    assert again["created"] is False and again["id"] == made["id"]

    bad = _tool(mcp_server, "serp_domain_add")("vocabulary.com", scrape_strategy="aggressive")
    assert bad["error"] == "invalid" and "scrape_strategy" in bad["message"]

    listed = _tool(mcp_server, "serp_domains_list")()
    assert [d["domain"] for d in listed] == ["acme.com"]


def test_mcp_serp_keyword_tools_round_trip():
    from seoforge import mcp_server

    k = _tool(mcp_server, "serp_keyword_add")("rank me", "keys.com", device="mobile", country="de")
    assert k["country"] == "DE" and k["device"] == "mobile"  # normalized as the kernel does

    bad = _tool(mcp_server, "serp_keyword_add")("rank me", "keys.com", device="tablet")
    assert bad["error"] == "invalid" and "device" in bad["message"]

    listed = _tool(mcp_server, "serp_keywords_list")()
    assert len(listed) == 1 and listed[0]["keyword"] == "rank me"
    filtered = _tool(mcp_server, "serp_keywords_list")(domain="keys.com", keyword="rank")
    assert filtered
    empty = _tool(mcp_server, "serp_keywords_list")(country="GB")
    assert empty == []


def test_mcp_serp_refresh_answers_honestly_without_a_setup_provider():
    from seoforge import mcp_server

    out = _tool(mcp_server, "serp_refresh")("missing.com")
    assert out == {"error": "Scraper has not been set up yet"}


def test_mcp_serp_retries_insights_and_csv():
    from seoforge import mcp_server, serp_tracking as st

    assert _tool(mcp_server, "serp_retries")() == []

    st.add_keyword("tier check", "stats.com")
    summary = _tool(mcp_server, "serp_insights")(domain="stats.com")
    assert summary["keywords"] == 1
    assert summary["distribution"]["lost"] == 1

    export = _tool(mcp_server, "serp_csv")("stats.com")
    assert export["keywords"] == 1
    assert export["path"].replace("\\", "/").endswith("stats-com-keywords_serp.csv")

    st.queue_retry(st.list_keywords("stats.com")[0]["id"], "boom")
    queued = _tool(mcp_server, "serp_retries")()
    assert queued[0]["error"] == "boom"


def test_mcp_serp_digest_answers_honestly_without_smtp():
    from seoforge import mcp_server

    out = _tool(mcp_server, "serp_digest")()
    assert out["sent"] == 0 and out["results"] == []


# ---------------------------------------------------------------- scheduler


def test_scheduler_registers_the_three_tracker_lanes():
    assert {"name": "serp_positions_daily", "kind": "serp_refresh", "interval_hours": 24} in scheduler.DEFAULT_JOBS
    assert {"name": "serp_retries_hourly", "kind": "serp_retries", "interval_hours": 1} in scheduler.DEFAULT_JOBS
    assert {"name": "serp_digest_daily", "kind": "serp_digest", "interval_hours": 24} in scheduler.DEFAULT_JOBS

    names = {j["name"] for j in scheduler.DEFAULT_JOBS}
    assert len(names) == len(scheduler.DEFAULT_JOBS)  # unique by name


def test_scheduler_lanes_dispatch_the_engine_calls():
    refresh = scheduler.run_job({"name": "serp_positions_daily", "kind": "serp_refresh"})
    assert refresh == {"name": "serp_positions_daily", "status": "done", "result": ["error"]}

    retries = scheduler.run_job({"name": "serp_retries_hourly", "kind": "serp_retries"})
    # the retry lane honours the setup gate exactly as the reference cron does
    assert retries["result"] == ["error"]

    digest = scheduler.run_job({"name": "serp_digest_daily", "kind": "serp_digest"})
    assert digest["result"] == ["sent", "results"]

    stale = scheduler.run_job({"name": "serp_reticks_hourly", "kind": "serp_rety"})
    assert stale["status"] == "done" and stale["result"] == ["error"]


def test_scheduler_lanes_run_a_real_refresh_through_the_kernel(monkeypatch):
    """Provider configured + history writes flow end to end via the job lane."""
    from seoforge import mcp_server, serp_tracking as st

    monkeypatch.setattr(settings, "serp_tracker_provider", "serper")
    monkeypatch.setattr(settings, "serp_tracker_api_key", "SK")

    st.add_domain("lanes.com", scrape_strategy="basic")
    k = st.add_keyword("lane kw", "lanes.com")

    body = '[{"title":"L","link":"https://www.lanes.com/a","position":4}]'
    # run_job imports lazily, so patch the fetch the branch will call
    import seoforge.serp_scraper as ss

    monkeypatch.setattr(ss, "_http_fetch", lambda provider, parsed: body)

    out = scheduler.run_job({"name": "serp_positions_daily", "kind": "serp_refresh"})
    assert out["status"] == "done" and out["result"] == ["domain", "keywords", "refreshed", "failed"]
    row = st.get_keyword(k["id"])
    assert row["position"] == 1  # slot 0 on page 1 renumbered

    # and the digest lane can read the same state through MCP
    summary = _tool(mcp_server, "serp_insights")(domain="lanes.com")
    assert summary["distribution"]["top3"] == 1
