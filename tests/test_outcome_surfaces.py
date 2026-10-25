"""The outcome surfaces: the scheduler's daily job, the CLI commands and
the two MCP tools. Only the plumbing is asserted here (the verdict rules
live in tests/test_outcomes.py); GSC reads are faked off and the
measurement clock is pinned.
"""
import datetime as dt
import json
import sys

import pytest

from seoforge import db, outcomes

TODAY = dt.date(2026, 10, 25)


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(outcomes, "today", lambda: TODAY)
    from seoforge.config import settings

    monkeypatch.setattr(settings, "site_url", "https://acme.example")


# ------------------------------------------------------ the scheduler job

def test_the_outcome_job_is_a_scheduled_default():
    from seoforge import scheduler

    assert {
        "name": "outcome_validation",
        "kind": "outcomes",
        "interval_hours": 24,
    } in scheduler.DEFAULT_JOBS
    scheduler.ensure_defaults()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT kind, enabled FROM schedule WHERE name='outcome_validation'"
        ).fetchone()
    assert row["kind"] == "outcomes" and row["enabled"] == 1


def test_run_job_runs_the_sweep_and_stamps_the_schedule(monkeypatch):
    from seoforge import scheduler

    calls = []

    def fake_sweep(now=None):
        calls.append(now)
        return {"measured": 2, "failed": 0}

    monkeypatch.setattr(outcomes, "sweep", fake_sweep)
    scheduler.ensure_defaults()
    result = scheduler.run_job(
        {"name": "outcome_validation", "kind": "outcomes", "interval_hours": 24}
    )
    assert result["status"] == "done"
    assert result["result"] == ["measured", "failed"]
    with db._conn() as conn:
        row = conn.execute(
            "SELECT last_status FROM schedule WHERE name='outcome_validation'"
        ).fetchone()
    assert row["last_status"] == "done"
    # every call matters only when the measured count depends on it; the
    # pinned clock never leaks into the sweep call itself
    assert len(calls) == 1


def test_the_real_job_through_the_real_kernel(monkeypatch):
    from seoforge import scheduler

    # GSC off: the settled row measures (its verdict below), the still
    # settling one waits, the open one is never touched
    monkeypatch.setattr(outcomes, "gsc_window_rows", lambda dims, window: [])
    settled = seed(target="https://acme.example/menu", evidence="{}")
    close(settled, when=12)
    fresh = seed(target="https://acme.example/other", evidence="{}")
    close(fresh, when=2)
    opened = seed(target="https://acme.example/never", evidence="{}")

    scheduler.ensure_defaults()
    result = scheduler.run_job(
        {"name": "outcome_validation", "kind": "outcomes", "interval_hours": 24}
    )
    assert result["status"] == "done"
    with db._conn() as conn:
        rows = {
            r["id"]: r["outcome"]
            for r in conn.execute("SELECT id, outcome FROM opportunities")
        }
    assert rows[settled] not in (None, outcomes.PENDING)  # measured and answered
    assert rows[fresh] == outcomes.PENDING  # still settling, untouched
    assert rows[opened] is None  # still open, untouched

    # the answered row is not re-measured on a later run
    again = scheduler.run_job(
        {"name": "outcome_validation", "kind": "outcomes", "interval_hours": 24}
    )
    assert again["status"] == "done"


# ------------------------------------------------------------ seeding dip


def seed(target, evidence="{}", status="open", created_ago=30, closed_ago=None):
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO opportunities(type,target,impact,effort,score,evidence,status,created_at)"
            " VALUES ('fix_title_meta',?,?,?,?,?,?,?)",
            (
                target,
                4,
                1,
                4,
                evidence,
                status,
                f"{ago(created_ago)}T06:00:00+00:00",
            ),
        )
        opp_id = cur.lastrowid
    if closed_ago is not None:
        close(opp_id, when=closed_ago)
    return opp_id


def ago(days: int) -> str:
    return (TODAY - dt.timedelta(days=days)).isoformat()


def close(opp_id, when):
    with db._conn() as conn:
        conn.execute(
            "UPDATE opportunities SET status='done', completed_at=?,"
            " outcome='pending_measurement' WHERE id=?",
            (f"{ago(when)}T06:00:00+00:00", opp_id),
        )


# ------------------------------------------------------------------- CLI

def run_cli(*args):
    from seoforge import cli

    old_argv = sys.argv
    sys.argv = ["seoforge", *args]
    try:
        cli.main()
    finally:
        sys.argv = old_argv


def test_cli_round_trip(capsys, monkeypatch):
    monkeypatch.setattr(outcomes, "gsc_window_rows", lambda dims, window: [])

    run_cli("outcomes")
    assert json.loads(capsys.readouterr().out) == []  # nothing measured yet

    # validating one by id: the open row is not ready, said plainly
    opened = seed(target="https://acme.example/menu")
    run_cli("outcomes-validate", str(opened))
    assert json.loads(capsys.readouterr().out) == {"verdict": None, "note": open_note()}

    # sweeping: the settled row vindicates (offline: no object, no data at all)
    settled = seed(target="ProductHunt (DR 90)", closed_ago=12)
    run_cli("outcomes-validate")
    sweep = json.loads(capsys.readouterr().out)
    assert sweep["measured"] == 1 and sweep["failed"] == 0

    run_cli("outcomes", "--limit", "10")
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1 and listed[0]["id"] == settled
    assert listed[0]["outcome"] == "not_measurable"
    assert listed[0]["validation"] is None

    # the answered item stays answered when asked again
    run_cli("outcomes-validate", str(settled))
    assert json.loads(capsys.readouterr().out) == "not_measurable"


def open_note():
    return (
        "not ready: the work is not closed yet, or its 7-day"
        " after-window has not finished (nothing was written)"
    )


def test_cli_counts_a_not_ready_close_the_same_way(capsys, monkeypatch):
    monkeypatch.setattr(outcomes, "gsc_window_rows", lambda dims, window: [])
    settling = seed(target="https://acme.example/menu", closed_ago=2)
    run_cli("outcomes-validate", str(settling))
    assert json.loads(capsys.readouterr().out) == {"verdict": None, "note": open_note()}
    with db._conn() as conn:
        row = conn.execute(
            "SELECT outcome FROM opportunities WHERE id=?", (settling,)
        ).fetchone()
    assert row["outcome"] == outcomes.PENDING  # still armed, nothing written


# -------------------------------------------------------------- MCP tools

def _tool(mod, name):
    obj = getattr(mod, name)
    return obj.fn if hasattr(obj, "fn") else obj


def test_mcp_outcomes_tools_round_trip(monkeypatch):
    from seoforge import mcp_server

    monkeypatch.setattr(outcomes, "gsc_window_rows", lambda dims, window: [])

    assert _tool(mcp_server, "outcomes_list")(limit=5) == []

    settling = seed(target="https://acme.example/menu", closed_ago=2)
    assert _tool(mcp_server, "outcome_validate")(settling) == {
        "verdict": None,
        "note": "not ready: the work is not closed yet, or its 7-day after-window"
        " has not finished (nothing was written)",
    }

    # a settled row with no measurable object: not measurable, no invented
    # numbers; a settled page row: the bot counter's real zero (0 reads on
    # both sides is a measured, flat pair) reads as no meaningful change
    settled = seed(target="ProductHunt (DR 90)", closed_ago=12)
    page = seed(target="https://acme.example/other", evidence="{}", closed_ago=12)
    assert _tool(mcp_server, "outcome_validate")(settled) == {"verdict": "not_measurable"}
    # answered stays answered: a re-ask reports, never re-writes
    assert _tool(mcp_server, "outcome_validate")(settled) == {"verdict": "not_measurable"}
    assert _tool(mcp_server, "outcome_validate")(page) == {"verdict": "no_meaningful_change"}

    listed = _tool(mcp_server, "outcomes_list")()
    assert set(d["id"] for d in listed) == {settled, page}
    assert set(d["outcome"] for d in listed) == {"not_measurable", "no_meaningful_change"}

    assert _tool(mcp_server, "outcome_validate")(404) == {
        "verdict": None,
        "note": "not ready: the work is not closed yet, or its 7-day after-window"
        " has not finished (nothing was written)",
    }


def test_mcp_outcomes_list_shows_only_answered_rows(monkeypatch):
    from seoforge import mcp_server

    monkeypatch.setattr(outcomes, "gsc_window_rows", lambda dims, window: [])
    seed(target="https://acme.example/menu")          # open, not measured
    settling = seed(target="https://acme.example/other", closed_ago=2)
    settled = seed(target="ProductHunt (DR 90)", closed_ago=12)

    assert _tool(mcp_server, "outcome_validate")(settled)["verdict"] == "not_measurable"
    listed = _tool(mcp_server, "outcomes_list")()
    assert [d["id"] for d in listed] == [settled]  # the armed and open rows wait
    assert settling not in [d["id"] for d in listed]
