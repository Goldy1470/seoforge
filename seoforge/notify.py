from __future__ import annotations

import json
import os
from collections import defaultdict
from urllib.parse import quote

import httpx

from . import db
from .config import settings


def send(title: str, lines: list[str]) -> str:
    """Slack-compatible webhook if set; otherwise persists the digest as a report."""
    body = f"*{title}*\n" + "\n".join(lines)
    url = os.getenv("SLACK_WEBHOOK_URL") or os.getenv("WEBHOOK_URL")
    if url:
        try:
            r = httpx.post(url, json={"text": body}, timeout=30)
            return f"webhook {r.status_code}"
        except Exception as exc:
            return f"webhook failed: {exc}"[:120]
    rid = db.save_report("digest", {"title": title, "lines": lines})
    return f"saved report #{rid} (set SLACK_WEBHOOK_URL to auto-send)"


def slack(bookmark: str = "") -> str:
    return bookmark or "digest channel = SLACK_WEBHOOK_URL env var"


def flush(report_id: int) -> dict:
    with db._conn() as conn:
        row = conn.execute("SELECT payload FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        return {}
    return json.loads(row["payload"])

def group_by_page(rows: list[dict]) -> dict:
    g = defaultdict(list)
    for r in rows:
        g[r.get("page", "")].append(r)
    return dict(g)
