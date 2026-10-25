"""The bot-ingest surfaces: the public dashboard endpoint senders post to
(401 before the body is read, the connection's kind reading the body, 413 on
an oversized file), connection management, the CLI commands and the MCP
tools. The default verifier is replaced everywhere by the offline double.
"""
import gzip
import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seoforge import bot_ingest, dashboard

AT = "2026-10-19T09:58:00Z"


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


def _logpush_line(**over):
    line = {
        "ClientRequestUserAgent": "Mozilla/5.0; compatible; GPTBot/1.2",
        "ClientIP": "20.171.206.9",
        "ClientRequestHost": "acme.example",
        "ClientRequestURI": "/logpush",
        "EdgeResponseStatus": 200,
        "EdgeStartTimestamp": "2026-10-19T10:00:00Z",
        "ClientCountry": "US",
    }
    line.update(over)
    return json.dumps(line)


@pytest.fixture()
def client():
    return TestClient(dashboard.app)


@pytest.fixture()
def offline_verifier(monkeypatch):
    from seoforge import bot_ingest as bi

    def fake_default():  # the default verifier's shape: a factory returning verify
        async def verify(request):
            return verifier(request)

        return verify

    monkeypatch.setattr(bi, "_default_verifier", fake_default)
    return verifier


def gpt_body():
    return {"requests": [gpt(), gpt(userAgent="ClaudeBot/1.0", url="https://acme.example/gifts")]}


# --------------------------------------------------- the public endpoint


def test_ingest_401s_before_reading_a_body(client):
    # no connection: an unauthenticated caller must not make the server read 20 MB
    response = client.post("/bots/ingest", content=b'{"requests": []}')
    assert response.status_code == 401
    assert response.json() == {"error": "unknown_connection"}


def test_ingest_reads_a_worker_batch_by_the_secret(client, offline_verifier):
    connection = bot_ingest.create_connection("cloudflare_worker", ["acme.example"])
    response = client.post(
        "/bots/ingest",
        headers={"Authorization": f"Bearer {connection['secret']}"},
        json=gpt_body(),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["received"] == 2 and data["verified"] == 2 and data["stored"] == 2
    assert data["records"] == 2
    with dashboard.db._conn() as conn:
        assert conn.execute("SELECT count(*) c FROM bot_events").fetchone()["c"] == 2


def test_ingest_claims_a_logpush_file_and_answers_duplicates(client, offline_verifier):
    connection = bot_ingest.create_connection("cloudflare_logpush", ["acme.example"])
    body = gzip.compress(f"{_logpush_line()}\n{_logpush_line()}".encode())
    assert body[0] == 0x1F and body[1] == 0x8B  # the magic bytes decide, not the header
    first = client.post(
        "/bots/ingest",
        headers={"Authorization": f"Bearer {connection['secret']}"},
        content=body,
    )
    assert first.json() == {
        "received": 2,
        "verified": 2,
        "stored": 2,
        "skipped": {
            "invalid": 0,
            "other_host": 0,
            "unknown_agent": 0,
            "unverified_ip": 0,
            "no_ranges": 0,
        },
        "records": 2,
        "raw": 2,
        "crossed80": False,
    }
    # a resend (the sender timed out waiting) is answered without re-reading
    again = client.post(
        "/bots/ingest",
        headers={"Authorization": f"Bearer {connection['secret']}"},
        content=body,
    )
    assert again.json() == {"received": 0, "verified": 0, "stored": 0, "duplicate": True}


def test_ingest_answers_413_on_an_oversized_file(client):
    connection = bot_ingest.create_connection("cloudflare_logpush")
    # a small gzip that opens past the 16 MB read limit
    bomb = gzip.compress(b"\x20" * (16 * 1024 * 1024 + 1))
    response = client.post(
        "/bots/ingest",
        headers={"Authorization": f"Bearer {connection['secret']}"},
        content=bomb,
    )
    assert response.status_code == 413
    assert response.json()["error"] == "too_large"


def test_ingest_answers_400_on_an_unreadable_file(client):
    connection = bot_ingest.create_connection("cloudflare_logpush")
    # the gzip magic bytes, but not a gzip stream
    broken = b"\x1f\x8b" + b"garbage" * 100
    response = client.post(
        "/bots/ingest",
        headers={"Authorization": f"Bearer {connection['secret']}"},
        content=broken,
    )
    assert response.status_code == 400
    assert response.json() == {"error": "unreadable_body"}


# ------------------------------------------------ connections (dashboard)


def test_connection_crud_needs_more_than_any_secret(client, monkeypatch):
    # without the public endpoint, a connection can't be made
    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "")
    assert client.post("/bots/connections", json={"kind": "cloudflare_worker"}).status_code == 503

    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "https://api.example.com")
    created = client.post(
        "/bots/connections", json={"kind": "cloudflare_worker", "domains": ["acme.example"]}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["secret"].startswith("sfb_")
    assert body["endpoint"] == "https://api.example.com/bots/ingest"
    assert "SEOFORGE_BOT_SECRET" in body["worker_script"]
    assert body["logpush_job"] is None and body["vercel_drain"] is None
    assert bot_ingest.hash_secret(body["secret"]) != body["secret"]  # not echoed back plainly

    listing = client.get("/bots/connections").json()
    assert listing["connections"][0]["kind"] == "cloudflare_worker"
    assert listing["usage"]["event_cap"] == -1 and listing["usage"]["raw_cap"] == -1

    conn_id = body["connection"]["id"]
    assert client.delete(f"/bots/connections/{conn_id}").status_code == 204
    assert client.delete(f"/bots/connections/{conn_id}").status_code == 404


def test_logpush_connection_setup_carries_the_secret_bearer(client, monkeypatch):
    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "https://api.example.com")
    job = client.post("/bots/connections", json={"kind": "cloudflare_logpush"}).json()["logpush_job"]
    assert job["destination_conf"].startswith(
        "https://api.example.com/bots/ingest?header_Authorization=Bearer%20sfb_"
    )
    drain = client.post("/bots/connections", json={"kind": "vercel_drain"}).json()["vercel_drain"]
    bearer = [x for x in drain["headers"] if x.startswith("Authorization: Bearer ")]
    assert bearer and len(bearer[0]) > len("Authorization: Bearer sfb_")


def test_create_connection_rejects_a_bad_kind(client, monkeypatch):
    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "https://api.example.com")
    assert client.post("/bots/connections", json={"kind": "cloudflare_flume"}).status_code == 400


def test_connections_locked_when_report_off(client, monkeypatch):
    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "https://api.example.com")
    monkeypatch.setenv("SEOFORGE_BOT_EVENT_CAP", "0")
    response = client.post("/bots/connections", json={"kind": "cloudflare_worker"})
    assert response.status_code == 403
    assert response.json()["error"] == "locked"


def test_ingest_locked_connection_reads_nothing(client, monkeypatch, offline_verifier):
    connection = bot_ingest.create_connection("cloudflare_worker", ["acme.example"])
    monkeypatch.setenv("SEOFORGE_BOT_EVENT_CAP", "0")
    response = client.post(
        "/bots/ingest",
        headers={"Authorization": f"Bearer {connection['secret']}"},
        json=gpt_body(),
    )
    data = response.json()
    assert data["locked"] is True and data["stored"] == 0


# ------------------------------------------------------------------- CLI


def run_cli(*args):
    from seoforge import cli

    old_argv = sys.argv
    sys.argv = ["seoforge", *args]
    try:
        cli.main()
    finally:
        sys.argv = old_argv


def _gzip_file(parts) -> str:
    path = Path(tempfile.mkstemp(suffix=".ndjson.gz")[1])
    path.write_bytes(gzip.compress(("\n".join(parts)).encode()))
    return str(path)


def test_cli_round_trip(capsys, monkeypatch, offline_verifier):
    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "https://api.example.com")

    run_cli("bot-connections")
    assert json.loads(capsys.readouterr().out)["usage"]["stored"] == 0

    run_cli("bot-connection-add", "cloudflare_logpush", "--domain", "acme.example")
    created = json.loads(capsys.readouterr().out)
    secret = created["secret"]
    assert created["logpush_job"]["dataset"] == "http_requests"
    assert secret.startswith("sfb_")
    assert created["connection"]["domains"] == ["acme.example"]

    # a Logpush file read straight off disk (in any timestamp format)
    path = _gzip_file([_logpush_line(), _logpush_line(EdgeStartTimestamp=1792400000)])
    run_cli("bot-ingest-file", path, "--secret", secret)
    result = json.loads(capsys.readouterr().out)
    assert result["stored"] == 2 and result["records"] == 2
    assert result["skipped"] == {
        "invalid": 0,
        "other_host": 0,
        "unknown_agent": 0,
        "unverified_ip": 0,
        "no_ranges": 0,
    }

    # the same file resent through the CLI is the duplicate answer
    run_cli("bot-ingest-file", path, "--secret", secret)
    assert json.loads(capsys.readouterr().out)["duplicate"] is True

    run_cli("bot-report", "--days", "30")
    report = json.loads(capsys.readouterr().out)
    assert report["types"][0]["hits"] == 2
    assert report["alert"] == "waiting"  # the first day of data is younger than 30 days

    run_cli("bot-prune")
    assert json.loads(capsys.readouterr().out) == {"events": 0, "daily": 0, "files": 0}

    run_cli("bot-connection-revoke", str(created["connection"]["id"]))
    assert json.loads(capsys.readouterr().out) == {"revoked": True}


def test_cli_reports_an_unknown_secret(capsys, offline_verifier):
    path = Path(tempfile.mkstemp(suffix=".ndjson")[1])
    path.write_text(_logpush_line())
    with pytest.raises(SystemExit):
        run_cli("bot-ingest-file", str(path), "--secret", "sfb_nothing")
    assert json.loads(capsys.readouterr().out) == {"error": "unknown_connection"}


# --------------------------------------------------------------- MCP tools


def _tool(mod, name):
    obj = getattr(mod, name)
    return obj.fn if hasattr(obj, "fn") else obj


def test_mcp_tools_round_trip(monkeypatch, offline_verifier):
    from seoforge import mcp_server

    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "https://api.example.com")
    create = _tool(mcp_server, "bot_connection_create")

    created = create("cloudflare_logpush", ["acme.example"])
    assert created["logpush_job"]["destination_conf"].startswith(
        "https://api.example.com/bots/ingest?header_Authorization=Bearer%20sfb_"
    )

    listing = _tool(mcp_server, "bot_connections_list")()
    assert listing["connections"][0]["kind"] == "cloudflare_logpush"

    stored = _tool(mcp_server, "bot_ingest_requests")(
        created["connection"]["id"], [gpt(), gpt(ip="1.1.1.1")]
    )
    assert stored["verified"] == 1 and stored["skipped"]["unverified_ip"] == 1

    report = _tool(mcp_server, "bot_report")()
    assert any(t["bot_type"] == "training" and t["hits"] == 1 for t in report["types"])
    assert _tool(mcp_server, "bot_prune")() == {"events": 0, "daily": 0, "files": 0}

    revoked = _tool(mcp_server, "bot_connection_revoke")(created["connection"]["id"])
    assert revoked == {"revoked": True}
    assert _tool(mcp_server, "bot_ingest_requests")(created["connection"]["id"], [gpt()]) == {
        "error": "unknown_connection",
        "message": f"no live connection {created['connection']['id']}",
    }


def test_mcp_locked_and_not_set_up_paths(monkeypatch):
    from seoforge import mcp_server

    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "")  # not set up
    assert _tool(mcp_server, "bot_connection_create")("cloudflare_worker")["error"] == "not_set_up"

    monkeypatch.setenv("SEOFORGE_PUBLIC_SERVER_URL", "https://api.example.com")
    monkeypatch.setenv("SEOFORGE_BOT_EVENT_CAP", "0")
    assert _tool(mcp_server, "bot_connection_create")("cloudflare_worker")["error"] == "locked"


def test_public_ingest_endpoint_rules():
    f = bot_ingest.public_ingest_endpoint
    assert f({"SEOFORGE_PUBLIC_SERVER_URL": "https://api.example.com/"}) == (
        "https://api.example.com/bots/ingest"
    )
    assert f({"SEOFORGE_PUBLIC_SERVER_URL": "https://api.example.com/v1"}) == (
        "https://api.example.com/bots/ingest"  # the path is dropped: /bots/ingest is fixed
    )
    assert f({"SEOFORGE_PUBLIC_SERVER_URL": "not a url"}) is None
    assert f({"SEOFORGE_PUBLIC_SERVER_URL": "http://api.example.com"}) is None
    assert (
        f({"SEOFORGE_PUBLIC_SERVER_URL": "http://127.0.0.1:8082", "SEOFORGE_ENV": "dev"})
        == "http://127.0.0.1:8082/bots/ingest"
    )
    assert (
        f({"SEOFORGE_PUBLIC_SERVER_URL": "http://localhost:80", "SEOFORGE_ENV": "production"})
        is None
    )
    assert f({}) is None
