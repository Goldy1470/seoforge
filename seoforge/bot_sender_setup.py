"""What a person copies into Cloudflare or Vercel to send AI-bot requests
(options B and C), built when the connection is made so the secret is in it
once (derivation in THIRD_PARTY_NOTICES.md):

* a Cloudflare Logpush job on the http_requests dataset, filtered at the
  edge to user agents on the bot list (only matching lines are sent),
  posting to the ingest endpoint with the secret as a header;
* a Vercel drain (logs, JSON) to the ingest endpoint with the secret as a
  custom header. Vercel sends every request log; the server filters them,
  under the raw-record cap.

The Worker (option A) is bot-worker-script.py.
"""
from __future__ import annotations

import json
from urllib.parse import quote

from . import ai_bots

#: The http_requests fields bot-log-formats.py reads.
LOGPUSH_FIELDS = [
    "ClientRequestUserAgent",
    "ClientIP",
    "ClientRequestHost",
    "ClientRequestURI",
    "EdgeResponseStatus",
    "EdgeStartTimestamp",
    "ClientCountry",
]

#: Files stay within what the ingest endpoint reads (bot-log-formats.py).
LOGPUSH_MAX_UPLOAD_BYTES = 5_000_000
LOGPUSH_MAX_UPLOAD_RECORDS = 10_000

VERCEL_DRAIN_SOURCES = ["static", "lambda", "edge", "external", "redirect"]


def logpush_job(endpoint: str, secret: str) -> dict:
    """A Logpush job's settings for the Cloudflare API or dashboard."""
    filter_json = json.dumps(
        {
            "where": {
                "or": [
                    {
                        "key": "ClientRequestUserAgent",
                        "operator": "contains",
                        "value": bot.userAgent,
                    }
                    for bot in ai_bots.all_bots()
                ]
            }
        }
    )
    return {
        "dataset": "http_requests",
        "destination_conf": (
            f"{endpoint}?header_Authorization={quote('Bearer ' + secret, safe='')}"
        ),
        "output_options": {"field_names": LOGPUSH_FIELDS, "timestamp_format": "rfc3339"},
        "filter": filter_json,
        "max_upload_bytes": LOGPUSH_MAX_UPLOAD_BYTES,
        "max_upload_records": LOGPUSH_MAX_UPLOAD_RECORDS,
    }


def vercel_drain(endpoint: str, secret: str) -> dict:
    """A Vercel drain's settings (Team Settings › Drains › Add Drain › Logs)."""
    return {
        "endpoint": endpoint,
        "format": "json",
        "headers": [f"Authorization: Bearer {secret}"],
        "sources": VERCEL_DRAIN_SOURCES,
    }
