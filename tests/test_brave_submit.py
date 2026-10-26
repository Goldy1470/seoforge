"""The Brave submit lane, offline: the no-browser handoff, the agent-filled
path faked at the seam, the failure and invalid paths, and the CLI/MCP
round trips. The three operator-documented facts the lane must carry are
asserted, and nothing here opens a page.
"""
import json
import sys

import pytest

from seoforge import indexing


@pytest.fixture(autouse=True)
def origin(monkeypatch):
    from seoforge.config import settings

    monkeypatch.setattr(settings, "site_url", "https://acme.example")


def test_without_a_browser_every_answer_is_the_manual_steps(monkeypatch):
    from seoforge import browser_agent

    monkeypatch.setattr(browser_agent, "available", lambda: False)
    out = indexing.brave_submit(["https://one.example", "not a url"])

    assert out["platform"] == "brave"
    assert out["submit_form"] == "https://search.brave.com/submit-url"
    assert out["urls"] == ["https://one.example", "not a url"]
    by_url = {r["url"]: r for r in out["results"]}
    manual = by_url["https://one.example"]
    assert manual["status"] == "manual"
    assert "search.brave.com/submit-url" in manual["steps"]
    assert by_url["not a url"] == {
        "url": "not a url",
        "status": "invalid",
        "error": "not an http(s) URL",
    }

    # the lane carries the operator-documented facts, never folklore
    assert len(out["guidance"]) == 3
    assert any("noindex" in g for g in out["guidance"])
    assert any("no user-agent token" in g for g in out["guidance"])
    assert any("bot report" in g for g in out["guidance"])


def test_default_target_is_the_site_origin_and_the_agent_fills_it(monkeypatch):
    from seoforge import browser_agent

    monkeypatch.setattr(browser_agent, "available", lambda: True)

    def fake_fill(url: str) -> str:
        return f"submitted: {url}"

    monkeypatch.setattr(browser_agent, "brave_submit_url", fake_fill)
    out = indexing.brave_submit()
    assert out["urls"] == ["https://acme.example"]
    assert out["results"] == [
        {
            "url": "https://acme.example",
            "status": "submitted",
            "output": "submitted: https://acme.example",
        }
    ]


def test_the_agent_failing_leaves_an_honest_failed_status(monkeypatch):
    from seoforge import browser_agent

    monkeypatch.setattr(browser_agent, "available", lambda: True)

    def boom(url: str) -> str:
        raise RuntimeError("proof-of-work captcha refused")

    monkeypatch.setattr(browser_agent, "brave_submit_url", boom)
    out = indexing.brave_submit(["https://two.example"])
    assert out["results"] == [
        {
            "url": "https://two.example",
            "status": "failed",
            "error": "proof-of-work captcha refused",
        }
    ]


def run_cli(*args):
    from seoforge import cli

    old_argv = sys.argv
    sys.argv = ["seoforge", *args]
    try:
        cli.main()
    finally:
        sys.argv = old_argv


def test_cli_and_mcp_round_trip(monkeypatch, capsys):
    from seoforge import browser_agent, mcp_server

    monkeypatch.setattr(browser_agent, "available", lambda: False)

    run_cli("brave-submit", "https://three.example")
    out = json.loads(capsys.readouterr().out)
    assert out["urls"] == ["https://three.example"]
    assert out["results"][0]["status"] == "manual"

    tool = getattr(mcp_server, "brave_submit")
    tool = tool.fn if hasattr(tool, "fn") else tool
    assert tool(["https://four.example"])["results"][0]["status"] == "manual"
    # no URLs at all: the configured origin is the target
    assert tool()["urls"] == ["https://acme.example"]
