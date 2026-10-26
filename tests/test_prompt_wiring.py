"""Prompt-position wiring (task 6.2): the daily sweep that measures
`improve_ai_visibility` opportunities from recorded prompt answers — the
lazy immutable baseline, interim reads while work is open or tracking, the
day-30 final verdict folded once and trailed — plus the bot report's
uncrawled alert, the per-prompt improve queue, and the scheduler job.

Everything offline: answers are pasted-in rows with absolute stamps and
every clock is pinned.
"""
import datetime as dt
import json

import pytest

from seoforge import bot_ingest, db, integrations, outcomes, prompts, scheduler

TODAY = dt.date(2026, 10, 26)
NOW = dt.datetime.now(dt.timezone.utc)
SITE = "https://acme.shop"


@pytest.fixture(autouse=True)
def acme(monkeypatch):
    from seoforge.config import settings

    monkeypatch.setattr(settings, "site_url", SITE)
    monkeypatch.setattr(settings, "brand_name", "Acme")
    # the measurement clocks: the prompt sweep and the outcomes kernel both
    monkeypatch.setattr(prompts, "_today", lambda: TODAY)
    monkeypatch.setattr(outcomes, "today", lambda: TODAY)
    return settings


def stamp(ago: int) -> str:
    return (TODAY - dt.timedelta(days=ago)).isoformat() + "T09:00:00+00:00"


def rstamp(ago: int) -> str:
    """Stamps for the bot report, relative to the real today — the report's
    on/stale/waiting gates read the real clock, not the pinned one."""
    return (dt.date.today() - dt.timedelta(days=ago)).isoformat() + "T09:00:00+00:00"


def rday(ago: int) -> str:
    return (dt.date.today() - dt.timedelta(days=ago)).isoformat()


def answer(prompt_id, platform="chatgpt-web", ago=1, cited=True, mention=True):
    cites = [{"url": f"{SITE}/pricing"}] if cited else []
    text = "Acme is the pick." if mention else "Try other tools."
    return prompts.record_answer(prompt_id, platform, text, cites, at=stamp(ago))


def opp(target, *, status="open", created_ago=10, completed_ago=None, type_="improve_ai_visibility"):
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO opportunities(type, target, impact, effort, score, status, created_at, completed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                type_,
                target,
                8,
                2,
                4.0,
                status,
                stamp(created_ago),
                stamp(completed_ago) if completed_ago is not None else None,
            ),
        )
        return cur.lastrowid


def opp_row(opp_id):
    with db._conn() as conn:
        return conn.execute("SELECT * FROM opportunities WHERE id=?", (opp_id,)).fetchone()


def opp_events(opp_id):
    with db._conn() as conn:
        return conn.execute(
            "SELECT event, data FROM opportunity_events WHERE opportunity_id=? ORDER BY id",
            (opp_id,),
        ).fetchall()


def by_platform(snapshots):
    return {s["platform"]: s for s in snapshots}


# ------------------------------------------------------- kernel ownership


def test_the_outcomes_kernel_leaves_prompt_rows_to_the_prompt_sweep(monkeypatch):
    prompt_text = "Which payroll tool should a 3-person agency run?"
    prompts.create_prompt(prompt_text)
    improve_id = opp(prompt_text, status="open", created_ago=5)
    other_id = opp(
        "Rewrite thin titles",
        type_="promote_to_top5",
        status="done",
        created_ago=30,
        completed_ago=20,
    )
    monkeypatch.setattr(outcomes, "gsc_window_rows", lambda dims, window: [])

    # the outcomes kernel measures the other kind and never the prompt rows
    assert outcomes.sweep()["measured"] == 1
    assert opp_row(other_id)["outcome"] == "not_measurable"
    assert outcomes.validate_opportunity(improve_id) is None
    assert opp_row(improve_id)["outcome"] is None
    assert opp_events(improve_id) == []

    # the prompt sweep owns its own row's reading instead
    res = prompts.outcome_sweep()
    assert res["measured"] == 1 and res["finals"] == 0
    payload = json.loads(opp_row(improve_id)["validation"])
    assert payload["kind"] == "prompt_positions" and payload["final"] is False


# ------------------------------------------------------------ interim reads


def test_open_rows_get_one_interim_read_per_sweep_and_never_a_verdict():
    text = "Open work prompt?"
    prompt_id = prompts.create_prompt(text)
    opp_id = opp(text, status="open", created_ago=10)
    answer(prompt_id, ago=2)

    first = prompts.outcome_sweep()
    assert first == {"measured": 1, "finals": 0, "skipped": 0, "failed": 0}
    row = opp_row(opp_id)
    assert row["outcome"] is None
    payload = json.loads(row["validation"])
    assert payload["final"] is False
    assert by_platform(payload["current"])["chatgpt-web"]["runs"] == 1
    assert payload["measuredAt"] == TODAY.isoformat()

    # the daily re-read just refreshes, it never answers
    assert prompts.outcome_sweep()["measured"] == 1
    assert opp_row(opp_id)["outcome"] is None


def test_closed_rows_stay_interim_while_the_tracking_window_runs():
    prompt_id = prompts.create_prompt("Tracking prompt?")
    text = "Tracking prompt?"
    opp_id = opp(text, status="done", created_ago=40, completed_ago=10)
    answer(prompt_id, ago=43)  # inside the baseline window: created-7..created-1
    answer(prompt_id, ago=5)

    res = prompts.outcome_sweep()
    assert res["measured"] == 1 and res["finals"] == 0
    row = opp_row(opp_id)
    assert row["outcome"] is None
    assert json.loads(row["validation"])["final"] is False


def test_the_baseline_is_the_immutable_window_before_the_work_was_raised():
    prompt_id = prompts.create_prompt("Baseline window prompt?")
    text = "Baseline window prompt?"
    opp_id = opp(text, status="open", created_ago=10)
    # outside the raised-7..raised-1 window: must not reach the baseline read
    answer(prompt_id, ago=25)
    answer(prompt_id, ago=2)

    prompts.outcome_sweep()
    payload = json.loads(opp_row(opp_id)["validation"])
    baseline = by_platform(payload["baseline"])
    assert baseline["chatgpt-web"]["state"] == "no_runs" and baseline["chatgpt-web"]["runs"] == 0
    # the fresh answer lands in the current 7-day window; the old one belongs
    # to no window the sweep reads (and never to the baseline)
    assert by_platform(payload["current"])["chatgpt-web"]["runs"] == 1


# ------------------------------------------------------------ final verdicts


def final_fixture_prompt(text):
    """A prompt with two platforms improved/declined symmetrical: baseline
    rows land in created-7..created-1 (ago 47..53 for created_ago=50 rows),
    the final read is the window ending 30 days after close (ago 1)."""
    prompt_id = prompts.create_prompt(text)
    return prompt_id


def test_the_final_verdict_records_once_and_trails_the_event():
    text = "Which CRM should a small agency buy?"
    prompt_id = prompts.create_prompt(text)
    # baseline (created-7..created-1): 3 runs, 1 mention -> not_mentioned
    for platform in ("chatgpt-web", "perplexity-web"):
        answer(prompt_id, platform=platform, ago=50)
        answer(prompt_id, platform=platform, ago=50, mention=False)
        answer(prompt_id, platform=platform, ago=50, mention=False)
    # the final week (ends-6..ends): mentioned and cited early -> doing_well
    for platform in ("chatgpt-web", "perplexity-web"):
        answer(prompt_id, platform=platform, ago=3)
        answer(prompt_id, platform=platform, ago=2)
    opp_id = opp(text, status="done", created_ago=45, completed_ago=31)

    res = prompts.outcome_sweep()
    assert res["measured"] == 0 and res["finals"] == 1
    row = opp_row(opp_id)
    assert row["outcome"] == "improved"
    payload = json.loads(row["validation"])
    assert payload["final"] is True
    baseline = by_platform(payload["baseline"])
    assert baseline["chatgpt-web"]["state"] == "not_mentioned" and baseline["chatgpt-web"]["runs"] == 3
    summary = payload["summary"]
    assert summary["improved"] == 2 and summary["declined"] == 0

    # the trail carries the same word, once; a second sweep rewrites nothing
    got = opp_events(opp_id)
    assert [e["event"] for e in got] == ["position_outcome_recorded"]
    assert json.loads(got[0]["data"])["outcome"] == "improved"
    assert prompts.outcome_sweep() == {"measured": 0, "finals": 0, "skipped": 0, "failed": 0}
    assert row["outcome"] == "improved"


def test_positions_that_collapse_fold_to_declined():
    text = "Decline prompt?"
    prompt_id = prompts.create_prompt(text)
    for platform in ("chatgpt-web", "perplexity-web"):
        answer(prompt_id, platform=platform, ago=50)  # doing_well before
    for platform in ("chatgpt-web", "perplexity-web"):
        answer(prompt_id, platform=platform, ago=3)
        answer(prompt_id, platform=platform, ago=2, mention=False)
        answer(prompt_id, platform=platform, ago=1, mention=False)
    opp_id = opp(text, status="done", created_ago=45, completed_ago=31)

    res = prompts.outcome_sweep()
    assert res["finals"] == 1
    assert opp_row(opp_id)["outcome"] == "declined"


def test_a_prompt_with_no_recorded_answers_folds_to_not_measurable():
    text = "Ever empty prompt?"
    prompts.create_prompt(text)
    opp_id = opp(text, status="done", created_ago=45, completed_ago=31)

    res = prompts.outcome_sweep()
    assert res["finals"] == 1
    assert opp_row(opp_id)["outcome"] == "not_measurable"


def test_rows_that_name_no_tracked_prompt_are_skipped_not_invented():
    prompts.create_prompt("A genuinely tracked prompt")
    opp_id = opp("This prompt was never tracked", status="open", created_ago=3)

    res = prompts.outcome_sweep()
    assert res["skipped"] == 1 and res["measured"] == 0 and res["failed"] == 0
    assert opp_row(opp_id)["validation"] is None


# ---------------------------------------------------------------- scheduler


def test_the_scheduler_pair_runs_the_prompt_positions_job_daily():
    jobs = {j["name"]: j for j in scheduler.DEFAULT_JOBS}
    assert jobs["prompt_positions"] == {
        "name": "prompt_positions",
        "kind": "prompts",
        "interval_hours": 24,
    }


def test_run_job_routes_the_prompts_kind_to_the_sweep(monkeypatch):
    seen = {}

    def fake(*args, **kwargs):
        seen["calls"] = seen.get("calls", 0) + 1
        seen["signature"] = (args, kwargs)
        return {"measured": 3, "finals": 1, "skipped": 0, "failed": 0}

    monkeypatch.setattr(prompts, "outcome_sweep", fake)
    result = scheduler.run_job({"name": "prompt_positions", "kind": "prompts"})
    assert result["name"] == "prompt_positions" and result["status"] == "done"
    assert result["result"] == ["measured", "finals", "skipped", "failed"]
    # the scheduler calls the sweep bare: defaults (limit 1000, live clock) stay internal
    assert seen["calls"] == 1 and seen["signature"] == ((), {})


# ------------------------------------------------------------ uncrawled alert


def test_the_uncrawled_alert_counts_cited_pages_no_search_bot_read():
    prompt_id = prompts.create_prompt("Which payroll tool should a startup use?")
    prompts.record_answer(
        prompt_id, "chatgpt-web", "Acme is the pick.", [{"url": f"{SITE}/pricing"}],
        at=rstamp(3),
    )
    prompts.record_answer(
        prompt_id, "perplexity-web", "Use Acme.",
        [{"url": f"{SITE}/gifts"}, {"url": f"{SITE}/pricing"}],
        at=rstamp(2),
    )
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO bot_connections(kind, secret_hash, domains, created_at, last_event_at)"
            " VALUES ('cloudflare_worker','h1','acme.shop',?,?)",
            (rstamp(40), NOW.isoformat(timespec="seconds")),
        )
        conn.executemany(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
            [
                # a search read 45 days ago: outside the 30-day window, not coverage
                (rday(45), "OAI-SearchBot", "search", "/pricing", 1, rstamp(45)),
                # a fresh read of a page nobody cited
                (rday(1), "OAI-SearchBot", "search", "/menu", 1, rstamp(1)),
                # training reads never count as search coverage
                (rday(2), "GPTBot", "training", "/pricing", 3, rstamp(2)),
            ],
        )
        report = bot_ingest.bot_report(conn)

    assert report["alert"] == "on"
    assert report["uncrawled"] == [
        {"page": "/pricing", "citations": 2},
        {"page": "/gifts", "citations": 1},
    ]
    assert report["uncrawled_count"] == 2


def test_a_stale_alert_claims_nothing_uncrawled():
    prompts.create_prompt("Stale prompt?")
    prompts.record_answer(
        prompts.create_prompt("Stale prompt?"), "chatgpt-web",
        "Acme is the pick.", [{"url": f"{SITE}/pricing"}], at=rstamp(3),
    )
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO bot_connections(kind, secret_hash, domains, created_at, last_event_at)"
            " VALUES ('cloudflare_worker','h1','acme.shop',?,?)",
            (rstamp(40), rstamp(10)),  # silent for 10 days -> stale
        )
        conn.executemany(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
            [(rday(35), "GPTBot", "training", "/pricing", 1, rstamp(35))],
        )
        report = bot_ingest.bot_report(conn)

    assert report["alert"] == "stale"
    assert report["uncrawled"] is None
    assert report["uncrawled_count"] is None


def test_a_waiting_alert_claims_nothing_uncrawled():
    prompts.create_prompt("Waiting prompt?")
    prompts.record_answer(
        prompts.create_prompt("Waiting prompt?"), "chatgpt-web",
        "Acme is the pick.", [{"url": f"{SITE}/pricing"}], at=rstamp(1),
    )
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO bot_connections(kind, secret_hash, domains, created_at, last_event_at)"
            " VALUES ('cloudflare_worker','h1','acme.shop',?,?)",
            (rstamp(2), NOW.isoformat(timespec="seconds")),
        )
        conn.executemany(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen) VALUES (?,?,?,?,?,?)",
            [(rday(1), "GPTBot", "training", "/pricing", 1, rstamp(1))],
        )
        report = bot_ingest.bot_report(conn)

    assert report["alert"] == "waiting"
    assert report["uncrawled"] is None
    assert report["uncrawled_count"] is None


# ------------------------------------------------------------- the queue


def test_the_ai_visibility_gap_raises_one_prompt_shaped_item_per_weak_prompt():
    weak = prompts.create_prompt("Weak but present payroll prompt?")
    answer(weak, platform="chatgpt-web", ago=2, cited=False)  # mentioned, never cited
    ghost = prompts.create_prompt("Ghost payroll prompt?")
    answer(ghost, platform="google-aio", ago=3)               # 1 of 3 runs mentions
    answer(ghost, platform="google-aio", ago=2, mention=False)
    answer(ghost, platform="google-aio", ago=1, mention=False)

    items = integrations._ai_visibility_gap()
    by_target = {i["target"]: i for i in items}
    assert set(by_target) == {"Weak but present payroll prompt?", "Ghost payroll prompt?"}
    assert all(i["type"] == "improve_ai_visibility" and i["effort"] == 2 for i in items)
    # an unmentioned prompt outweighs a merely-uncited one
    assert by_target["Weak but present payroll prompt?"]["impact"] == 6
    assert by_target["Ghost payroll prompt?"]["impact"] == 8
    assert by_target["Ghost payroll prompt?"]["evidence"]["platforms"]["google-aio"]["state"] == "not_mentioned"


def test_the_gap_stays_quiet_without_weak_or_any_prompts():
    # nothing tracked at all
    assert integrations._ai_visibility_gap() == []

    # tracked and doing well everywhere it has runs
    good = prompts.create_prompt("Doing well payroll prompt?")
    answer(good, platform="chatgpt-web", ago=2)
    answer(good, platform="chatgpt-web", ago=1)
    answer(good, platform="perplexity-web", ago=1)
    assert integrations._ai_visibility_gap() == []
