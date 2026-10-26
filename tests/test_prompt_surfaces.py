"""The prompt-positions surfaces: the five CLI commands and the four MCP
tools round-trip against the same kernel the Positions panel reads.
Answers are pasted in over a pinned clock and a pinned brand - nothing
here fetches anything live.
"""
import datetime as dt
import json
import sys

import pytest

from seoforge import db, prompts

TODAY = dt.date(2026, 10, 25)


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(prompts, "_today", lambda: TODAY)
    monkeypatch.setattr(
        prompts,
        "brand",
        lambda: {"name": "SeoForge", "domains": ["seoforge.example"]},
    )


def stamp(days_ago: int) -> str:
    return f"{(TODAY - dt.timedelta(days=days_ago)).isoformat()}T08:00:00+00:00"


# ------------------------------------------------------------------- CLI

def run_cli(*args):
    from seoforge import cli

    old_argv = sys.argv
    sys.argv = ["seoforge", *args]
    try:
        cli.main()
    finally:
        sys.argv = old_argv


def test_cli_prompt_round_trip(capsys):
    run_cli("prompts-add", "best seo automation tool", "--category", "rank")
    created = json.loads(capsys.readouterr().out)
    assert created["id"] > 0

    # idempotent: the same text never creates a second row, and the first
    # add's category stands (a re-add carries nothing in)
    run_cli("prompts-add", "best seo automation tool")
    same = json.loads(capsys.readouterr().out)
    assert same == created

    run_cli("prompts-list")
    listed = json.loads(capsys.readouterr().out)
    assert [(p["id"], p["text"], p["category"]) for p in listed] == [
        (created["id"], "best seo automation tool", "rank")
    ]

    run_cli(
        "answer-record",
        str(created["id"]),
        "chatgpt-web",
        "SeoForge is the answer.",
        "--citations",
        "https://one.example",
        "--at",
        stamp(1),
    )
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["prompt_id"] == created["id"]
    assert recorded["id"] > 0 and recorded["at"] == stamp(1)
    assert recorded["source"] == "manual" and recorded["sentiment"] == "neutral"
    assert recorded["mention_count"] == 1  # "SeoForge", said once
    assert recorded["citation_count"] == 0  # one.example is not ours

    # a bad platform is answered plainly, never raised through the CLI
    run_cli("answer-record", str(created["id"]), "not-a-platform", "whatever")
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "invalid" and "unknown platform" in error["message"]


def test_cli_prompt_states_and_sweep(capsys):
    pid = prompts.create_prompt("states prompt")

    run_cli("prompt-states", str(pid))
    states = json.loads(capsys.readouterr().out)
    assert [s["platform"] for s in states] == list(prompts.PLATFORMS)
    # every platform answers even before anything was pasted
    assert all(s["state"] == "no_runs" and s["runs"] == 0 for s in states)

    prompts.record_answer(
        pid,
        "grok-web",
        "SeoForge again.",
        citations=[{"url": "https://seoforge.example"}],
        at=stamp(1),
    )
    run_cli("prompt-states", str(pid))
    byp = {s["platform"]: s for s in json.loads(capsys.readouterr().out)}
    assert byp["grok-web"]["runs"] == 1 and byp["grok-web"]["state"] != "no_runs"
    assert all(s["state"] == "no_runs" for p, s in byp.items() if p != "grok-web")

    # with no id: every tracked prompt answers with its full platform row
    run_cli("prompt-states")
    everything = json.loads(capsys.readouterr().out)
    assert [d["id"] for d in everything] == [pid]
    assert everything[0]["text"] == "states prompt"
    assert len(everything[0]["platforms"]) == len(prompts.PLATFORMS)

    # no improve_ai_visibility work exists yet: the sweep invents nothing
    run_cli("prompt-sweep")
    assert json.loads(capsys.readouterr().out) == {
        "measured": 0,
        "finals": 0,
        "skipped": 0,
        "failed": 0,
    }


# -------------------------------------------------------------- MCP tools

def _tool(mod, name):
    obj = getattr(mod, name)
    return obj.fn if hasattr(obj, "fn") else obj


def test_mcp_prompt_tools_round_trip():
    from seoforge import mcp_server

    first = _tool(mcp_server, "prompt_create")("another buying question")
    assert first["id"] > 0
    assert _tool(mcp_server, "prompt_create")("another buying question") == first

    answer = _tool(mcp_server, "prompt_record_answer")(
        first["id"],
        "perplexity-web",
        "SeoForge does this - read on.",
        citations=["https://seoforge.example"],  # bare string joins as a url
        at=stamp(2),
    )
    assert answer["prompt_id"] == first["id"] and answer["source"] == "manual"
    assert answer["citation_count"] == 1  # seoforge.example is ours

    # the invalid paths answer instead of raising
    bad_platform = _tool(mcp_server, "prompt_record_answer")(first["id"], "nope", "x")
    assert bad_platform["error"] == "invalid" and "unknown platform" in bad_platform["message"]
    bad_prompt = _tool(mcp_server, "prompt_record_answer")(999001, "perplexity-web", "x")
    assert bad_prompt["error"] == "invalid" and "unknown prompt" in bad_prompt["message"]
    empty = _tool(mcp_server, "prompt_create")("   ")
    assert empty == {"error": "invalid", "message": "prompt text is required"}

    states = _tool(mcp_server, "prompt_states")(first["id"])
    assert [s["platform"] for s in states] == list(prompts.PLATFORMS)
    assert {s["platform"] for s in states if s["runs"] == 1} == {"perplexity-web"}


def test_mcp_prompt_gap_raises_only_the_weak_states():
    from seoforge import mcp_server

    healthy = _tool(mcp_server, "prompt_create")("gap test healthy prompt")
    _tool(mcp_server, "prompt_record_answer")(
        healthy["id"],
        "chatgpt-web",
        "SeoForge does this.",
        citations=["https://seoforge.example"],
        at=stamp(1),
    )

    weak = _tool(mcp_server, "prompt_create")("gap test weak prompt")
    _tool(mcp_server, "prompt_record_answer")(
        weak["id"], "chatgpt-web", "nothing here", at=stamp(1)
    )

    items = _tool(mcp_server, "prompt_gap")()
    targets = {it["target"] for it in items}
    assert "gap test weak prompt" in targets
    assert "gap test healthy prompt" not in targets
    item = next(it for it in items if it["target"] == "gap test weak prompt")
    assert item["type"] == "improve_ai_visibility" and item["effort"] == 2
    assert item["evidence"]["platforms"]["chatgpt-web"]["state"] == "not_mentioned"

    # an untracked surface: no prompts, no items
    with db._conn() as conn:
        conn.execute("DELETE FROM prompt_results")
        conn.execute("DELETE FROM prompts")
    assert _tool(mcp_server, "prompt_gap")() == []
