"""AI-bot request ingest, arrived at a site's own edge (worker / Logpush /
drain) and POSTed here by its bot report (AT-010-style pipeline, derivation in
THIRD_PARTY_NOTICES.md).

One connection per way the site sends events, each with its own secret: the
sender presents it, only its SHA-256 is stored, and the person sees it once
when the connection is made.

A batch is verified request by request — a page on this site's domains, user
agent on the SEOForge bot registry, IP inside the operator's published ranges
(`ai_bots.py`) — then written in one step that holds the month's event cap:
rows while there is room, bot x page x day counters always (hits past the
cap fold onto the bot's '(other)' row of the day, so the cap is a hard
ceiling on stored rows but keeps totals honest). A log sender's file is
claimed by the SHA-256 of its body, counted against the raw cap and written
in that same transaction; a resent file answers as a duplicate without being
re-read. No IP is kept — the pages are what the report reads, keyed by
`normalize_page_path`. Crossing 80% of the event cap raises the internal
alert once a month.

Single-tenant deviations from the reference (SQLite instead of one Postgres
RPC per brand): caps come from settings (`SEOFORGE_BOT_EVENT_CAP`,
`SEOFORGE_BOT_RAW_CAP`; -1 = unlimited, 0 = the report is off), the usage
rows are keyed by month alone, and there are no database roles.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
from urllib.parse import unquote, urlsplit

from . import ai_bots, db

# ------------------------------------------------------------- constants

CONNECTION_KINDS = ("cloudflare_worker", "cloudflare_logpush", "vercel_drain")

SECRET_PREFIX = "sfb_"

#: The most requests one ingested batch may carry.
MAX_BATCH = 500

#: Requests older than this are dropped: a sender catching up after a week
#: isn't the site's reading now.
MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

#: Verified rows are kept 90 days; the daily counters 400.
EVENT_RETENTION_DAYS = 90
DAILY_RETENTION_DAYS = 400
#: File hashes kept for spotting a sender's retry.
FILE_HASH_RETENTION_DAYS = 7

#: Rows the prune deletes per table per transaction.
PRUNE_BATCH = 5000
#: Transactions per daily run at most; the rest waits a day.
MAX_PRUNE_CALLS = 200

_SKIP_REASONS = ("invalid", "other_host", "unknown_agent", "unverified_ip", "no_ranges")


def _cap_env(name: str) -> int:
    """A numeric process-environment cap: -1 unlimited, 0 off."""
    raw = os.getenv(name, "-1").strip()
    try:
        return int(raw)
    except ValueError:
        return -1


# ---------------------------------------------------- page normalization


def normalize_page_path(value) -> str:
    """A URL or path reduced to the page it names: host, query, fragment and
    trailing slash removed, percent escapes decoded. Root and empty read as
    '/' — a landing page of '' is a page too. Case is preserved (callers
    lower() it to key pages and keep this value to display)."""
    text = str(value or "")
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*", "", text)
    text = text.split("?", 1)[0]
    text = text.split("#", 1)[0]
    try:  # decode where possible: a raw '%' mid-path must not crash the ingest
        text = unquote(text)
    except Exception:  # pragma: no cover — unquote never raises on str
        pass
    trimmed = text.rstrip("/")
    return trimmed or "/"


# ---------------------------------------------------------- hosts (AT-010)


def _norm_host(url) -> str | None:
    """Lowercased hostname with a leading www stripped; the port is ignored."""
    try:
        host = urlsplit(str(url)).hostname
    except ValueError:
        return None
    if not host:
        return None
    return re.sub(r"^www\.", "", host.lower())


def norm_stored_domain(domain) -> str | None:
    """Normalized host of a stored domain; domains are free text."""
    if not domain:
        return None
    value = str(domain).strip()
    if not value:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = f"https://{value}"
    return _norm_host(value)


def on_brand_domain(url, domains) -> bool:
    """True when `url`'s host is one of the domains or a subdomain of one."""
    host = _norm_host(url)
    if not host:
        return False
    stored = [norm_stored_domain(d) for d in (domains or [])]
    return any(d and (host == d or host.endswith(f".{d}")) for d in stored)


def on_brand_site(url, domains) -> bool:
    """Whether a request's page is this site: host on the connection's domains
    or a subdomain. A Logpush zone with several hostnames, or a drain set for
    a whole team, also sends other sites' requests, which would fill the
    report. A bare path (a worker on the site's own route may send one)
    passes, and so does every page when no domain is stored."""
    if not domains or not re.match(r"^https?://", str(url or ""), re.I):
        return True
    return on_brand_domain(url, domains)


# ------------------------------------------------------- connections


def new_connection_secret() -> str:
    """A new connection secret: 32 random bytes, shown once."""
    return f"{SECRET_PREFIX}{secrets.token_urlsafe(32)}"


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_connection(kind: str, domains: list[str] | None = None, *, now: str | None = None) -> dict:
    """A connection: its id, the secret shown once, and what the sender copies.

    The secret is returned in plaintext once; only its SHA-256 is stored.
    `domains` are normalized free text hosts (used to drop other sites'
    requests, which a Logpush zone or a team-wide drain also sends).
    """
    if kind not in CONNECTION_KINDS:
        raise ValueError(f"kind must be one of {'|'.join(CONNECTION_KINDS)}")
    secret = new_connection_secret()
    stamp = now or db.now()
    cleaned = [d for d in (norm_stored_domain(x) for x in (domains or [])) if d]
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO bot_connections(kind, secret_hash, domains, created_at)"
            " VALUES (?,?,?,?)",
            (kind, hash_secret(secret), json.dumps(cleaned), stamp),
        )
        return {
            "id": cur.lastrowid,
            "kind": kind,
            "domains": cleaned,
            "created_at": stamp,
            "secret": secret,
        }


def connection_for_secret(secret) -> dict | None:
    """The live connection a secret belongs to; None otherwise (unknown,
    revoked, or not one this server could have issued)."""
    if (
        not isinstance(secret, str)
        or not secret.startswith(SECRET_PREFIX)
        or len(secret) > 200
    ):
        return None
    with db._conn() as conn:
        row = conn.execute(
            "SELECT id, kind, domains, revoked_at FROM bot_connections"
            " WHERE secret_hash = ?",
            (hash_secret(secret),),
        ).fetchone()
    if row is None or row["revoked_at"]:
        return None
    try:
        domains = json.loads(row["domains"])
    except (ValueError, TypeError):
        domains = []
    return {"id": row["id"], "kind": row["kind"], "domains": domains or []}


def revoke_connection(connection_id: int) -> bool:
    """Revoke one (deletes nothing: events stay readable); False when it was
    already revoked or never was."""
    with db._conn() as conn:
        cur = conn.execute(
            "UPDATE bot_connections SET revoked_at = ?"
            " WHERE id = ? AND revoked_at IS NULL",
            (db.now(), connection_id),
        )
        return cur.rowcount > 0


def list_connections(conn: sqlite3.Connection) -> list[dict]:
    """All connections, in id order, with this month's use read separately by
    the caller."""
    rows = conn.execute(
        "SELECT id, kind, domains, created_at, last_event_at, revoked_at"
        " FROM bot_connections ORDER BY id"
    ).fetchall()
    out = []
    for row in rows:
        try:
            domains = json.loads(row["domains"])
        except (ValueError, TypeError):
            domains = []
        out.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "domains": domains,
                "created_at": row["created_at"],
                "last_event_at": row["last_event_at"],
                "revoked": bool(row["revoked_at"]),
            }
        )
    return out


def connection_by_id(conn: sqlite3.Connection, connection_id: int) -> dict | None:
    """The live connection an id names (the tools' way to pick one without
    showing its secret); None when it is revoked or never was."""
    row = conn.execute(
        "SELECT id, kind, domains FROM bot_connections"
        " WHERE id = ? AND revoked_at IS NULL",
        (connection_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        domains = json.loads(row["domains"])
    except (ValueError, TypeError):
        domains = []
    return {"id": row["id"], "kind": row["kind"], "domains": domains or []}


def current_caps() -> dict:
    """This server's caps as set right now (the connection list reads them)."""
    return {
        "event_cap": _cap_env("SEOFORGE_BOT_EVENT_CAP"),
        "raw_cap": _cap_env("SEOFORGE_BOT_RAW_CAP"),
    }


def public_ingest_endpoint(env: dict | None = None) -> str | None:
    """Where a connection sends its events: the server's public address
    (`SEOFORGE_PUBLIC_SERVER_URL`), never the Host a request came in on. The
    address is written into the site's Worker, Logpush job or drain, where it
    is hard to change later. https only, but for localhost outside
    production. None when it isn't set or can't be used."""
    env = os.environ if env is None else env
    try:
        url = urlsplit(env.get("SEOFORGE_PUBLIC_SERVER_URL") or "")
    except ValueError:
        return None
    hostname = url.hostname
    if not hostname or not url.scheme:
        return None
    local = hostname in ("localhost", "127.0.0.1", "::1")
    allowed = url.scheme == "https" or (
        url.scheme == "http" and local and env.get("SEOFORGE_ENV") != "production"
    )
    if not allowed:
        return None
    return f"{url.scheme}://{url.netloc}/bots/ingest"


# ------------------------------------------------------------ ingest


def _read_request(raw, now: float) -> dict | None:
    """One request as a sender reports it: user agent, IP, URL, status, time,
    country. Facts that can't be read drop the request, not the batch."""
    if not isinstance(raw, dict):
        return None
    user_agent = raw.get("userAgent", raw.get("ua"))
    url = raw.get("url", raw.get("path"))
    ip = raw.get("ip")
    if not isinstance(user_agent, str) or not isinstance(ip, str) or not isinstance(url, str):
        return None
    stamp = _parse_time(raw.get("at", raw.get("ts")), now)
    if stamp is None:
        return None
    if stamp > now + 60 * 60 * 1000 or stamp < now - MAX_AGE_MS:
        return None
    try:
        status_value = int(raw.get("status"))
    except (TypeError, ValueError):
        status_value = None
    if status_value is not None and not 100 <= status_value < 600:
        status_value = None
    country = raw.get("country")
    if not isinstance(country, str) or len(country) < 2:
        country = None
    return {
        "userAgent": user_agent[:512],
        "ip": ip,
        "url": url[:2048],
        "status": status_value,
        "country": country[:2].upper() if country else None,
        "at": dt.datetime.fromtimestamp(stamp / 1000, tz=dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _parse_time(value, now: float) -> float | None:
    """A reported time as epoch ms: ISO/RFC-3339 string, or the numeric
    timestamp senders use — a Logpush EdgeStartTimestamp (ns, unix s or unix
    ms, however the job was set) or epoch ms — told apart by size. A missing
    `at` is now (a Worker reports its wall clock). None when it isn't one."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return now
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if not n or n < 0:  # nonsense; a far-off time is dropped by the age window
            return None
        # ns, ms or s since the epoch, told apart by magnitude.
        return n / 1e6 if n > 1e15 else (n if n > 1e12 else n * 1000)
    text = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return _parse_time(float(text), now)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp() * 1000


async def verify_requests(list_of_raw, domains, *, verifier, now: float) -> tuple[list[dict], dict]:
    """The bots among a sender's requests, as events to write, and why the
    rest were skipped (verified before the ranges are read: another site's
    request isn't verified at all). `verifier` may be sync (offline fakes) or
    return a coroutine (the real `ai_bots.verify_bot_request`)."""
    skipped = {reason: 0 for reason in _SKIP_REASONS}
    events: list[dict] = []
    for raw in list_of_raw:
        request = _read_request(raw, now)
        if request is None:
            skipped["invalid"] += 1
            continue
        if not on_brand_site(request["url"], domains):
            skipped["other_host"] += 1
            continue
        verdict = verifier(request)
        if asyncio.iscoroutine(verdict):
            verdict = await verdict
        bot = verdict.get("bot") if isinstance(verdict, dict) else None
        # ai_bots.verify_bot_request puts the bot in its failed verdicts, too
        # ({"reason": "unverified_ip", "bot": ...}), so only a verdict marked
        # verified — or one that returns the bot record itself — counts.
        ok = (
            isinstance(bot, dict) and bool(bot.get("key") or bot.get("bot"))
        ) or (
            isinstance(bot, str) and bool(verdict.get("verified"))
        )
        if not ok:
            reason = verdict.get("reason") if isinstance(verdict, dict) else None
            skipped[reason if reason in skipped else "unknown_agent"] += 1
            continue
        bot = verdict["bot"]
        if isinstance(bot, dict):  # verifier returned the bot record
            key = str(bot.get("key") or bot.get("bot") or "")
            bot_type = bot.get("type") or bot.get("bot_type") or verdict.get("bot_type")
        else:  # verifier returned the bot key string (the registry's shape)
            key = str(bot)
            bot_type = verdict.get("bot_type")
        events.append(
            {
                "bot": key,
                "bot_type": bot_type,
                "page": request["url"],
                "status": request["status"],
                "country": request["country"],
                "at": request["at"],
            }
        )
    return events, skipped


def _apply_verified(
    conn: sqlite3.Connection,
    connection_id: int,
    events: list[dict],
    cap: int,
    month: str,
    received_at: str,
) -> dict:
    """The reference's ``ai_bot_ingest`` write, as SQL on the caller's open
    transaction (no BEGIN/COMMIT here — the log-file path must fold claim,
    raw count and this write into one transaction). Rows are stored while the
    month has room (cap < 0 = no cap), counters are always added (by page for
    events stored, on the bot's '(other)' row of the day for the rest), a
    tie crossing 80% of the cap alerts once, and the connection's
    last_event_at moves. `events` must already carry `page_key`.
    """
    conn.execute("INSERT OR IGNORE INTO bot_usage(month) VALUES (?)", (month,))
    row = conn.execute(
        "SELECT stored_events, alerted_at FROM bot_usage WHERE month = ?", (month,)
    ).fetchone()
    used = int(row["stored_events"])
    alerted = row["alerted_at"]
    n = len(events)
    room = n if cap < 0 else max(0, min(n, cap - used))

    for ev in events[:room]:
        conn.execute(
            "INSERT INTO bot_events(bot, bot_type, page, status, country,"
            " occurred_at, received_at) VALUES (?,?,?,?,?,?,?)",
            (
                ev["bot"],
                ev["bot_type"],
                (ev["page_key"] or "").lower(),
                ev["status"],
                ev["country"],
                ev["at"],
                received_at,
            ),
        )

    counters: dict[tuple[str, str, str], dict] = {}
    for i, ev in enumerate(events):
        day = str(ev["at"])[:10]
        page = ev["page_key"] if i < room else "(other)"
        key = (day, ev["bot"], page)
        bucket = counters.setdefault(
            key, {"bot_type": ev["bot_type"], "hits": 0, "last_seen": ev["at"]}
        )
        bucket["hits"] += 1
        if ev["at"] > bucket["last_seen"]:
            # one bot row per (day, bot, page): when the group mixes types the
            # earlier-reading type wins, as in the reference's counter
            if ev["bot_type"] == "search":
                bucket["bot_type"] = "search"
        bucket["last_seen"] = max(bucket["last_seen"], ev["at"])
    for (day, bot, page), bucket in counters.items():
        conn.execute(
            "INSERT INTO bot_daily(day, bot, bot_type, page, hits, last_seen)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(day, bot, page) DO UPDATE SET"
            " hits = hits + excluded.hits,"
            " last_seen = MAX(last_seen, excluded.last_seen),"
            " bot_type = MIN(bot_type, excluded.bot_type)",
            (day, bot, bucket["bot_type"], page, bucket["hits"], bucket["last_seen"]),
        )

    crossed80 = bool(cap > 0 and alerted is None and used + room >= 0.8 * cap)
    capped_now = cap >= 0 and room < n
    conn.execute(
        "UPDATE bot_usage SET stored_events = stored_events + ?,"
        " counted_events = counted_events + ?,"
        " capped_at = COALESCE(capped_at, CASE WHEN ? THEN ? END),"
        " alerted_at = CASE WHEN ? THEN ? ELSE alerted_at END"
        " WHERE month = ?",
        (room, n, capped_now, received_at, crossed80, received_at, month),
    )
    if n > 0:
        conn.execute(
            "UPDATE bot_connections SET last_event_at = ? WHERE id = ?",
            (received_at, connection_id),
        )
    return {"received": n, "stored": room, "used": used + room, "cap": cap, "crossed80": crossed80}


def _write_verified(
    conn: sqlite3.Connection, connection_id: int, events: list[dict], cap: int, month: str, received_at: str
) -> dict:
    """`_apply_verified` in its own transaction (the batch path opens none)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = _apply_verified(conn, connection_id, events, cap, month, received_at)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def _now_epoch_ms() -> float:
    return dt.datetime.now(dt.timezone.utc).timestamp() * 1000


def _default_verifier():
    """Verification through the registry and the operator's published IP
    ranges (`ai_bots.py`). Needs the ranges cache and, usually, network
    access — tests inject their own verifier instead."""

    async def verify(request: dict) -> dict:
        return await ai_bots.verify_bot_request(request["userAgent"], request["ip"])

    return verify


async def ingest_bot_requests(connection, requests, *, verifier=None) -> dict:
    """Verify and store one batch (at most MAX_BATCH requests) for a
    connection. Returns what was received, verified and stored, and why the
    rest wasn't counted."""
    conn = db.get_db()
    try:
        now_ms = _now_epoch_ms()
        verifier = verifier or _default_verifier()
        list_ = requests if isinstance(requests, list) else []
        list_ = list_[:MAX_BATCH]
        events, skipped = await verify_requests(
            list_, connection["domains"], verifier=verifier, now=now_ms
        )
        cap = _cap_env("SEOFORGE_BOT_EVENT_CAP")
        if events and cap == 0:
            # the report is off: store nothing, not even counters
            return {
                "received": len(list_),
                "verified": len(events),
                "stored": 0,
                "skipped": skipped,
                "locked": True,
            }
        stored = 0
        extra: dict = {}
        if events:
            month = dt.datetime.fromtimestamp(now_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m")
            received_at = db.now()
            for ev in events:
                ev["page_key"] = normalize_page_path(ev["page"])
            result = _write_verified(conn, connection["id"], events, cap, month, received_at)
            stored = result["stored"]
            extra = {k: result[k] for k in ("used", "cap", "crossed80")}
        return {"received": len(list_), "verified": len(events), "stored": stored, "skipped": skipped, **extra}
    finally:
        conn.close()


def log_file_hash(body: bytes | str) -> str:
    """A log file's key (Logpush job / drain): the SHA-256 of its body. A
    sender that times out waiting sends the same file again; the key keeps it
    from being read twice."""
    data = body if isinstance(body, bytes) else str(body or "").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def file_was_read(conn: sqlite3.Connection, connection_id: int, body_hash: str) -> bool:
    """Whether this connection's file was read already, so a resent one is
    answered before it is opened."""
    row = conn.execute(
        "SELECT 1 FROM bot_files WHERE connection_id = ? AND body_hash = ?",
        (connection_id, body_hash),
    ).fetchone()
    return row is not None


async def ingest_log_file(connection, body_hash, parsed, *, verifier=None) -> dict:
    """Read a log sender's file (Logpush / Vercel). The requests are verified
    first — a verification call may touch the network and must not hold the
    write lock — then the file is claimed by its hash, its records are
    counted against the month's raw cap and its verified events are written,
    all in one transaction: a failure leaves nothing behind, so the sender's
    retry reads as new. A resent file answers as a duplicate without being
    written; past the raw cap the records are only counted; a report that is
    off reads nothing (`locked`).

    `parsed` is `{records, requests}` from `bot_log_formats.read_sender_body`.
    """
    conn = db.get_db()
    try:
        now_ms = _now_epoch_ms()
        verifier = verifier or _default_verifier()
        raw_cap = _cap_env("SEOFORGE_BOT_RAW_CAP")
        cap = _cap_env("SEOFORGE_BOT_EVENT_CAP")
        records = int(parsed.get("records", len(parsed.get("requests") or [])))
        if raw_cap == 0 or cap == 0:
            return {"received": records, "verified": 0, "stored": 0, "locked": True}

        list_ = list(parsed.get("requests") or [])[:MAX_BATCH]
        events, skipped = await verify_requests(
            list_, connection["domains"], verifier=verifier, now=now_ms
        )
        for ev in events:
            ev["page_key"] = normalize_page_path(ev["page"])

        month = dt.datetime.fromtimestamp(now_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m")
        received_at = db.now()
        conn.execute("BEGIN IMMEDIATE")
        try:
            if file_was_read(conn, connection["id"], body_hash):
                conn.commit()
                return {"received": 0, "verified": 0, "stored": 0, "skipped": skipped, "duplicate": True}
            # claim + raw count + write, one transaction
            conn.execute(
                "INSERT OR IGNORE INTO bot_files(connection_id, body_hash, received_at)"
                " VALUES (?,?,?)",
                (connection["id"], body_hash, received_at),
            )
            conn.execute("INSERT OR IGNORE INTO bot_usage(month) VALUES (?)", (month,))
            row = conn.execute(
                "SELECT raw_records FROM bot_usage WHERE month = ?", (month,)
            ).fetchone()
            raw_now = int(row["raw_records"])
            allowed = raw_cap < 0 or raw_now < raw_cap
            used_raw = raw_now + max(records, 0)
            raw_capped_now = raw_cap >= 0 and used_raw >= raw_cap
            conn.execute(
                "UPDATE bot_usage SET raw_records = ?,"
                " raw_capped_at = COALESCE(raw_capped_at, CASE WHEN ? THEN ? END)"
                " WHERE month = ?",
                (used_raw, raw_capped_now, received_at, month),
            )
            if not allowed:
                # past the raw cap a file's records are only counted
                conn.commit()
                return {"received": records, "verified": len(events), "stored": 0, "skipped": skipped, "rawCapped": True}
            write = _apply_verified(conn, connection["id"], events, cap, month, received_at)
            conn.commit()
            return {
                "received": len(list_),
                "verified": len(events),
                "stored": write["stored"],
                "skipped": skipped,
                "raw": used_raw,
                "crossed80": write.get("crossed80", False),
            }
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


# -------------------------------------------------------------- prune


def prune_bot_events(now=None) -> dict:
    """The daily prune of rows past their retention — PRUNE_BATCH rows per
    table per transaction, until a pass deletes fewer than that from every
    table. At most MAX_PRUNE_CALLS passes; when the rest is still over the
    retention a day later it is read again then."""
    conn = db.get_db()
    try:
        now_ms = now if now is not None else _now_epoch_ms()

        def cutoff(days: int) -> str:
            t = dt.datetime.fromtimestamp(
                (now_ms - days * 24 * 60 * 60 * 1000) / 1000, tz=dt.timezone.utc
            )
            return t.isoformat()

        bounds = (
            cutoff(EVENT_RETENTION_DAYS),
            cutoff(DAILY_RETENTION_DAYS)[:10],
            cutoff(FILE_HASH_RETENTION_DAYS),
        )
        totals = {"events": 0, "daily": 0, "files": 0}
        for _ in range(MAX_PRUNE_CALLS):
            conn.execute("BEGIN IMMEDIATE")
            try:
                d1 = conn.execute(
                    "DELETE FROM bot_events WHERE id IN ("
                    " SELECT id FROM bot_events WHERE occurred_at < ? LIMIT ?)",
                    (bounds[0], PRUNE_BATCH),
                ).rowcount
                d2 = conn.execute(
                    "DELETE FROM bot_daily WHERE rowid IN ("
                    " SELECT rowid FROM bot_daily WHERE day < ? LIMIT ?)",
                    (bounds[1], PRUNE_BATCH),
                ).rowcount
                d3 = conn.execute(
                    "DELETE FROM bot_files WHERE rowid IN ("
                    " SELECT rowid FROM bot_files WHERE received_at < ? LIMIT ?)",
                    (bounds[2], PRUNE_BATCH),
                ).rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            counts = {"events": d1, "daily": d2, "files": d3}
            for k, v in counts.items():
                totals[k] += v
            if all(v < PRUNE_BATCH for v in counts.values()):
                return totals
        return totals
    finally:
        conn.close()


# ------------------------------------------------------------- report


def _cited_not_crawled(conn: sqlite3.Connection, since_day: str) -> tuple[list[dict], int]:
    """The reference's uncrawled alert, on the alert's 'on' semantics:
    pages an AI answer cited (prompt_results rows in the 30-day window,
    own-domain citations only) that no search-type bot read in the same
    30 days. Ranked by citations, capped at 10; the count reports all of
    them, not just the capped ten."""
    from . import prompts

    is_own = prompts.own_domain_matcher(prompts.brand()["domains"])
    counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT citations FROM prompt_results WHERE substr(at, 1, 10) >= ?", (since_day,)
    ).fetchall():
        try:
            cites = json.loads(row["citations"] or "[]")
        except (ValueError, TypeError):
            cites = []
        for cite in cites:
            url = cite.get("url") if isinstance(cite, dict) else None
            if not url or not is_own(url):
                continue
            key = normalize_page_path(url).lower()
            if not key or key == "(other)":
                continue
            counts[key] = counts.get(key, 0) + 1
    crawled = {
        r["page"]
        for r in conn.execute(
            "SELECT DISTINCT page FROM bot_daily WHERE bot_type = 'search' AND day >= ?",
            (since_day,),
        ).fetchall()
    }
    missing = [
        {"page": page, "citations": hits}
        for page, hits in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if page not in crawled
    ]
    return missing[:10], len(missing)


def bot_report(conn: sqlite3.Connection, since: str | None = None, *, limit: int = 20) -> dict:
    """The AI-bot report read from the bot x page x day counters, which hold
    every request even past the month's cap: per type (training crawlers,
    search crawlers, user fetches — hits, pages read, when last seen, bots
    seen), per bot, and the most read pages with each type's last read.

    The reference's `uncrawled` alert rides at the end: with the alert 'on',
    cited pages no search bot has read in the last 30 days rank by citations,
    capped at 10 with the full count beside them. The alert itself is
    'waiting' until the first day of data is 30 days old, 'stale' when no
    live connection has reported within 2 days or search reads fold onto
    '(other)' rows in the last 30 days (the cap was hit and the pages are no
    longer known), then 'on'; only then does anything uncrawled read."""
    today = dt.date.today()
    since_day = since or (today - dt.timedelta(days=89)).isoformat()
    g = conn.execute(
        "SELECT bot_type, bot, page, SUM(hits) AS hits, MAX(last_seen) AS last_seen"
        " FROM bot_daily WHERE day >= ? GROUP BY bot_type, bot, page",
        (since_day,),
    ).fetchall()

    by_type: dict[str, dict] = {}
    by_bot: dict[str, dict] = {}
    pages: dict[str, dict] = {}
    for row in g:
        bot_type, bot, page, hits, last_seen = (
            row["bot_type"], row["bot"], row["page"], int(row["hits"]), row["last_seen"],
        )
        t = by_type.setdefault(
            bot_type, {"bot_type": bot_type, "hits": 0, "pages": set(), "last_seen": "", "bots": set()}
        )
        t["hits"] += hits
        t["bots"].add(bot)
        t["last_seen"] = max(t["last_seen"], last_seen)
        if page != "(other)":
            t["pages"].add(page)
        b = by_bot.setdefault(
            bot, {"bot": bot, "bot_type": bot_type, "hits": 0, "pages": set(), "last_seen": ""}
        )
        b["hits"] += hits
        b["last_seen"] = max(b["last_seen"], last_seen)
        if page != "(other)":
            b["pages"].add(page)
        p = pages.setdefault(
            page, {"page": page, "hits": 0, "training_last": "", "search_last": "", "user_last": ""}
        )
        p["hits"] += hits
        if bot_type in ("training", "search", "user"):
            k = f"{bot_type}_last"
            p[k] = max(p[k], last_seen)

    first_day = conn.execute("SELECT MIN(day) AS d FROM bot_daily").fetchone()["d"]
    last_seen = conn.execute("SELECT MAX(last_seen) AS s FROM bot_daily").fetchone()["s"]

    waiting_until = (today - dt.timedelta(days=29)).isoformat()
    alert = "waiting"
    if first_day is None or first_day > waiting_until:
        alert = "waiting"
    else:
        recent_bound = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
        ).isoformat(timespec="seconds")
        recent = conn.execute(
            "SELECT 1 FROM bot_connections WHERE revoked_at IS NULL AND last_event_at >= ?",
            (recent_bound,),
        ).fetchone()
        folded = conn.execute(
            "SELECT 1 FROM bot_daily WHERE bot_type = 'search' AND page = '(other)'" " AND day >= ?",
            ((today - dt.timedelta(days=29)).isoformat(),),
        ).fetchone()
        alert = "stale" if (recent is None or folded is not None) else "on"

    types_out = [
        {
            "bot_type": val["bot_type"],
            "hits": val["hits"],
            "pages": len(val["pages"]),
            "last_seen": val["last_seen"],
            "bots": sorted(val["bots"]),
        }
        for val in sorted(by_type.values(), key=lambda v: v["bot_type"])
    ]
    bots_out = [
        {
            "bot": val["bot"],
            "bot_type": val["bot_type"],
            "hits": val["hits"],
            "pages": len(val["pages"]),
            "last_seen": val["last_seen"],
        }
        for val in sorted(by_bot.values(), key=lambda v: v["bot"])
    ]
    pages_out = sorted(pages.values(), key=lambda p: -p["hits"])[:limit]
    if alert == "on":
        uncrawled, uncrawled_count = _cited_not_crawled(
            conn, (today - dt.timedelta(days=29)).isoformat()
        )
    else:
        # waiting or stale: the 30-day view is incomplete by definition,
        # so nothing is claimed to be uncrawled yet
        uncrawled, uncrawled_count = None, None
    return {
        "first_day": first_day,
        "last_seen": last_seen,
        "since": since_day,
        "alert": alert,
        "types": types_out,
        "bots": bots_out,
        "pages": pages_out,
        "uncrawled": uncrawled,
        "uncrawled_count": uncrawled_count,
    }
