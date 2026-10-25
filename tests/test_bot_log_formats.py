"""What each AI-bot sender posts read into one request shape; what a person
copies into Cloudflare or Vercel; and the Worker's source. Mirrors the
reference fixtures (Logpush ns/s/RFC-3339 stamps, the test upload, Vercel
build lines skipped, line/size limits, the secret only in a Worker Secret).
"""
import datetime as dt
import gzip
import json

import pytest

from seoforge import ai_bots, bot_log_formats, bot_sender_setup, bot_worker_script
from seoforge.bot_log_formats import (
    MAX_INFLATED_BYTES,
    MAX_LOG_LINES,
    LogTooLargeError,
    read_logpush_body,
    read_sender_body,
    read_vercel_body,
    read_worker_body,
)

# ------------------------------------------------------------ log formats


def logpush_line(**over):
    line = {
        "ClientRequestUserAgent": "Mozilla/5.0; compatible; GPTBot/1.2",
        "ClientIP": "20.171.206.9",
        "ClientRequestHost": "acme.example",
        "ClientRequestURI": "/menu?x=1",
        "EdgeResponseStatus": 200,
        "EdgeStartTimestamp": 1792400000000000000,
        "ClientCountry": "us",
    }
    line.update(over)
    return json.dumps(line)


def test_read_logpush_fields_in_any_timestamp_format():
    text = "\n".join(
        [
            logpush_line(),
            logpush_line(EdgeStartTimestamp=1792400000),  # unix seconds
            logpush_line(EdgeStartTimestamp="2026-10-19T09:00:00Z"),  # RFC 3339
            "{not json",
            "",
        ]
    )
    parsed = read_logpush_body(text)
    assert parsed["records"] == 3
    assert parsed["requests"][0] == {
        "userAgent": "Mozilla/5.0; compatible; GPTBot/1.2",
        "ip": "20.171.206.9",
        "url": "https://acme.example/menu?x=1",
        "status": 200,
        "at": "2026-10-19T08:53:20.000Z",
        "country": "us",
    }
    assert parsed["requests"][1]["at"] == "2026-10-19T08:53:20.000Z"
    assert parsed["requests"][2]["at"] == "2026-10-19T09:00:00.000Z"


def test_logpush_gzipped_test_upload_and_magic_bytes():
    parsed = read_sender_body(
        "cloudflare_logpush", gzip.compress(b'{"content":"tests"}')
    )
    assert parsed == {"records": 1, "requests": []}
    opened = read_sender_body(
        "cloudflare_logpush",
        gzip.compress(f"{logpush_line()}\n{logpush_line()}".encode()),
    )
    assert len(opened["requests"]) == 2


def vercel_entry(**over):
    entry = {
        "id": "1",
        "source": "static",
        "host": "acme.vercel.app",
        "timestamp": 1792400000000,
        "proxy": {
            "timestamp": 1792400000500,
            "method": "GET",
            "host": "acme.example",
            "path": "/gifts?ref=a",
            "userAgent": ["Mozilla/5.0 (compatible; ClaudeBot/1.0)"],
            "statusCode": 200,
            "clientIp": "160.79.104.10",
            "region": "fra1",
        },
    }
    entry.update(over)
    return entry


def test_read_vercel_array_or_ndjson_skipping_the_rest():
    build = {"id": "2", "source": "build", "message": "Build completed"}
    want = {
        "records": 2,
        "requests": [
            {
                "userAgent": "Mozilla/5.0 (compatible; ClaudeBot/1.0)",
                "ip": "160.79.104.10",
                "url": "https://acme.example/gifts?ref=a",
                "status": 200,
                "at": "2026-10-19T08:53:20.500Z",
                "country": None,
            }
        ],
    }
    from_array = read_vercel_body(json.dumps([vercel_entry(), build]))
    assert from_array == want
    from_lines = read_vercel_body(
        f"{json.dumps(vercel_entry())}\n{json.dumps(build)}"
    )
    assert from_lines == want


def test_worker_body_and_unknown_kinds():
    assert read_worker_body('{"requests":[{"ip":"1.1.1.1"}]}') == {
        "records": 1,
        "requests": [{"ip": "1.1.1.1"}],
    }
    assert read_worker_body("{bad") == {"records": 0, "requests": []}
    assert read_sender_body("ftp", b"x") == {"records": 0, "requests": []}
    assert read_sender_body("vercel_drain", b"") == {"records": 0, "requests": []}
    assert read_worker_body("") == {"records": 0, "requests": []}


def test_limits_not_broken_by_size_or_lines():
    lines = "\n".join(["{}"] * (MAX_LOG_LINES + 1))
    with pytest.raises(LogTooLargeError):
        read_logpush_body(lines)
    # a small gzip that opens past the limit
    bomb = gzip.compress(b"\x20" * (MAX_INFLATED_BYTES + 1))
    with pytest.raises(LogTooLargeError):
        read_sender_body("cloudflare_logpush", bomb)


def test_file_at_the_line_limit_is_read():
    lines = "\n".join([logpush_line()] * MAX_LOG_LINES)
    parsed = read_logpush_body(lines)
    assert parsed["records"] == MAX_LOG_LINES
    assert len(parsed["requests"]) == MAX_LOG_LINES


def test_a_line_with_an_unreadable_time_is_dropped():
    parsed = read_logpush_body(
        "\n".join([logpush_line(), logpush_line(EdgeStartTimestamp=1e30)])
    )
    assert parsed["records"] == 2
    assert len(parsed["requests"]) == 1
    # a Vercel entry whose timestamp is garbage reads as no request
    vercel = read_vercel_body(
        json.dumps(
            [
                {
                    "proxy": {
                        "clientIp": "1.2.3.4",
                        "userAgent": ["GPTBot"],
                        "path": "/",
                        "timestamp": "never",
                    }
                }
            ]
        )
    )
    assert vercel == {"records": 1, "requests": []}


def test_missing_hosts_and_paths_still_count_as_records():
    # a proxy log with no host and no path is still a record; ingest drops it
    vercel = read_vercel_body(json.dumps([{"proxy": {"timestamp": 1792400000500}}]))
    assert vercel == {
        "records": 1,
        "requests": [
            {
                "userAgent": None,
                "ip": None,
                "url": None,
                "status": None,
                "at": "2026-10-19T08:53:20.500Z",
                "country": None,
            }
        ],
    }


# ----------------------------------------------------------- sender setup


def test_logpush_job_filters_to_the_bot_list_and_sends_the_secret_as_a_header():
    endpoint = "https://api.example/bots/ingest"
    job = bot_sender_setup.logpush_job(endpoint, "sfb_s3cret")
    assert job["dataset"] == "http_requests"
    assert job["destination_conf"] == (
        "https://api.example/bots/ingest?header_Authorization=Bearer%20sfb_s3cret"
    )
    filter_json = json.loads(job["filter"])
    assert [c["value"] for c in filter_json["where"]["or"]] == [
        b.userAgent for b in ai_bots.all_bots()
    ]
    assert all(
        c["key"] == "ClientRequestUserAgent" and c["operator"] == "contains"
        for c in filter_json["where"]["or"]
    )
    # files stay within what the endpoint reads (bot-log-formats.py)
    assert job["max_upload_records"] == 10_000
    assert job["max_upload_bytes"] == 5_000_000
    assert job["output_options"]["field_names"] == bot_sender_setup.LOGPUSH_FIELDS
    assert job["output_options"]["timestamp_format"] == "rfc3339"


def test_logpush_fields_are_the_fields_the_server_reads():
    fields = bot_sender_setup.LOGPUSH_FIELDS
    line = {f: (200 if f == "EdgeResponseStatus" else "x") for f in fields}
    line["EdgeStartTimestamp"] = "2026-10-19T09:00:00Z"
    line["ClientIP"] = "1.2.3.4"
    line["ClientRequestHost"] = "acme.example"
    line["ClientRequestURI"] = "/p"
    request = read_logpush_body(json.dumps(line))["requests"][0]
    assert request["ip"] == "1.2.3.4"
    assert request["status"] == 200
    assert request["at"] == "2026-10-19T09:00:00.000Z"
    assert request["url"] == "https://acme.example/p"


def test_vercel_drain_sends_json_logs_with_the_secret_as_a_custom_header():
    assert bot_sender_setup.vercel_drain("https://api.example/bots/ingest", "sfb_s3cret") == {
        "endpoint": "https://api.example/bots/ingest",
        "format": "json",
        "headers": ["Authorization: Bearer sfb_s3cret"],
        "sources": ["static", "lambda", "edge", "external", "redirect"],
    }


# ----------------------------------------------------------- worker script


def test_worker_script_names_every_bot_on_the_list():
    script = bot_worker_script.build_worker_script("https://api.example/bots/ingest")
    for bot in ai_bots.all_bots():
        assert json.dumps(bot.userAgent.lower()) in script
    assert json.dumps("https://api.example/bots/ingest") in script  # the endpoint


def test_worker_secret_sits_in_a_worker_secret_never_in_the_code():
    script = bot_worker_script.build_worker_script("https://api.example/bots/ingest")
    assert "sfb_" not in script
    assert f"env.{bot_worker_script.WORKER_SECRET_NAME}" in script
    assert bot_worker_script.WORKER_SECRET_NAME == "SEOFORGE_BOT_SECRET"


def test_worker_batches_sends_after_the_response_and_passes_through():
    script = bot_worker_script.build_worker_script("https://api.example/bots/ingest")
    assert f"BATCH = {bot_worker_script.WORKER_BATCH}" in script
    assert "setTimeout(r, 10000)" in script  # also sends on a 10 s debounce
    assert "ctx.passThroughOnException()" in script
    assert "const response = await fetch(request);" in script  # through, first
    # the page is sent without its query string, which can carry personal data
    assert "return u.origin + u.pathname;" in script
    # a batch flush is a POST with the secret as Bearer and requests wrapped
    assert "JSON.stringify({ requests })" in script
    assert "Authorization: 'Bearer ' + secret" in script


def test_worker_registry_and_edge_check_use_the_same_list():
    script = bot_worker_script.build_worker_script("https://api.example/bots/ingest")
    tokens = [b.userAgent.lower() for b in ai_bots.all_bots()]
    start = script.index("const BOTS = ") + len("const BOTS = ")
    end = script.index("];", start) + 1
    embedded = json.loads(script[start:end])
    assert embedded == tokens
