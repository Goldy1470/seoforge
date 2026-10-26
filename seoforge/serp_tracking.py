"""Position tracking: the domains and keywords followed daily, the
scrape results recorded into one honest row per keyword, and the
failed-scrape retry queue.

The data model is the reference's tracked-keyword design: a domain owns
keywords; each keyword row carries its device/country/city dimensions,
its current position, the full daily history and last scraped result as
JSON, and an error record for the last failed attempt. A successful
attempt stamps `last_updated`; a failed one keeps the old stamp and
records what went wrong instead. Failed keywords land in the retry
queue and stay there until a scrape succeeds.

The retry queue lives in the database (the reference keeps it in a
`failed_queue.json` file) — same queue, one less mutable file beside a
tenant's engine store.
"""
from __future__ import annotations

import datetime as _dt
import json
import re

from . import db

DEVICES = ("desktop", "mobile")
SCRAPE_STRATEGIES = ("", "basic", "custom", "smart")

_RETRY_COLUMNS = "keyword_id, error, queued_at"


def now() -> str:
    return db.now()


def _today() -> _dt.date:
    """The calendar day that owns today's history entry."""
    return _dt.datetime.now(_dt.timezone.utc).date()


def history_key(day: _dt.date | None = None) -> str:
    """One calendar-day key, month and day unpadded (`2026-10-2` not
    `2026-10-02`): the key every caller reads history by."""
    d = day or _today()
    return f"{d.year}-{d.month}-{d.day}"


def slugify(domain: str) -> str:
    """The domain's URL slug: lowercased, scheme/www stripped, the rest
    flattened (`Example.com/blog` -> `example-com-blog`)."""
    s = str(domain or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = re.sub(r"[/\\]+", "-", s)
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
    return s or "domain"


# ----------------------------------------------------------------- domains


def add_domain(
    domain: str,
    *,
    notification: bool = True,
    notification_interval: str = "daily",
    notification_emails: str = "",
    scrape_strategy: str = "",
    scrape_pagination_limit: int = 0,
    scrape_smart_full_fallback: bool = False,
    subdomain_matching: str = "",
) -> dict:
    """One tracked domain; adding twice answers the existing row. The
    name is normalized the way the reference reads domains: lowercase,
    scheme, `www.` and any path dropped — a domain, not a URL."""
    domain = str(domain or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].strip("/\\")
    if domain.startswith("www."):
        domain = domain[4:].strip("/\\")
    if not domain:
        raise ValueError("a domain is required")
    if notification_interval not in ("never", "daily", "other_day", "hourly", "weekly", "monthly"):
        raise ValueError(f"unknown notification_interval {notification_interval!r}")
    if scrape_strategy not in SCRAPE_STRATEGIES:
        raise ValueError(f"unknown scrape_strategy {scrape_strategy!r}: expect one of {SCRAPE_STRATEGIES}")
    existing = get_domain(domain)
    if existing is not None:
        return {**existing, "created": False}
    stamp = now()
    slug = slugify(domain)
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO serp_domains(domain, slug, keyword_count, last_updated, added, tags,"
            " notification, notification_interval, notification_emails,"
            " scrape_strategy, scrape_pagination_limit, scrape_smart_full_fallback, subdomain_matching)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                domain,
                slug,
                0,
                stamp,
                stamp,
                "[]",
                1 if notification else 0,
                notification_interval,
                str(notification_emails),
                scrape_strategy,
                int(scrape_pagination_limit),
                1 if scrape_smart_full_fallback else 0,
                str(subdomain_matching),
            ),
        )
    return {**get_domain(domain), "created": True}


def get_domain(domain_or_slug: str) -> dict | None:
    key = str(domain_or_slug or "").strip().lower()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM serp_domains WHERE domain = ? OR slug = ?",
            (key, slugify(key)),
        ).fetchone()
    return _parse_domain(row) if row else None


def list_domains() -> list[dict]:
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM serp_domains ORDER BY added, id").fetchall()
    return [_parse_domain(r) for r in rows]


def set_domain_scrape(domain_or_slug: str, **fields) -> dict | None:
    """Domain-level strategy overrides: scrape_strategy ('', basic, custom,
    smart), scrape_pagination_limit, scrape_smart_full_fallback and
    subdomain_matching (the comma-separated subdomains, '*' = all)."""
    row = get_domain(domain_or_slug)
    if row is None:
        return None
    allowed = {
        "scrape_strategy",
        "scrape_pagination_limit",
        "scrape_smart_full_fallback",
        "subdomain_matching",
    }
    if "scrape_strategy" in fields and fields["scrape_strategy"] not in SCRAPE_STRATEGIES:
        raise ValueError(
            f"unknown scrape_strategy {fields['scrape_strategy']!r}: expect one of {SCRAPE_STRATEGIES}"
        )
    sets, params = [], []
    for name, value in fields.items():
        if name not in allowed:
            raise ValueError(f"unknown field {name!r}: expect one of {sorted(allowed)}")
        sets.append(f"{name} = ?")
        params.append(int(value) if name == "scrape_pagination_limit" else value)
    if not sets:
        return row
    with db._conn() as conn:
        conn.execute(f"UPDATE serp_domains SET {', '.join(sets)} WHERE id = ?", (*params, row["id"]))
    return get_domain(row["domain"])


def delete_domain(domain_or_slug: str) -> dict | None:
    """Remove one domain and every keyword tracked under it."""
    row = get_domain(domain_or_slug)
    if row is None:
        return None
    with db._conn() as conn:
        conn.execute(
            "DELETE FROM serp_retry_queue WHERE keyword_id IN (SELECT id FROM serp_keywords WHERE domain = ?)",
            (row["domain"],),
        )
        removed = conn.execute(
            "DELETE FROM serp_keywords WHERE domain = ?", (row["domain"],)
        ).rowcount
        conn.execute("DELETE FROM serp_domains WHERE id = ?", (row["id"],))
    return {"domain": row["domain"], "keywordsRemoved": removed}


def _parse_domain(row) -> dict:
    return {
        **dict(row),
        "tags": json.loads(row["tags"] or "[]"),
        "notification": bool(row["notification"]),
        "scrape_smart_full_fallback": bool(row["scrape_smart_full_fallback"]),
    }


# ---------------------------------------------------------------- keywords


def add_keyword(
    keyword: str,
    domain: str,
    *,
    device: str = "desktop",
    country: str = "US",
    city: str = "",
    volume: int = 0,
    sticky: bool = False,
    tags: str | list = "",
) -> dict:
    """Track one keyword for a domain (the domain row is created when it
    does not exist yet). Duplicates stay allowed on purpose: the same
    words under desktop and mobile are two rows here, and the reference
    permits exact duplicates too."""
    keyword = str(keyword or "").strip()
    domain = str(domain or "").strip().lower()
    if not keyword:
        raise ValueError("a keyword is required")
    if not domain:
        raise ValueError("a domain is required")
    if device not in DEVICES:
        raise ValueError(f"unknown device {device!r}: expect one of {DEVICES}")
    country = str(country or "US").strip().upper() or "US"
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError(f"country must be a 2-letter code, got {country!r}")
    _domain_row = add_domain(domain)
    tag_list = _tag_list(tags)
    stamp = now()
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO serp_keywords(keyword, device, country, city, domain, position, url,"
            " volume, history, last_result, tags, sticky, updating, last_updated, added, last_update_error)"
            " VALUES (?,?,?,?,?, 0, '', ?, '{}', '[]', ?, ?, 0, ?, ?, 'false')",
            (
                keyword,
                device,
                country,
                str(city or "").strip(),
                domain,
                int(volume or 0),
                json.dumps(tag_list),
                1 if sticky else 0,
                stamp,
                stamp,
            ),
        )
        new_id = cur.lastrowid
        conn.execute(
            "UPDATE serp_domains SET keyword_count ="
            " (SELECT COUNT(*) FROM serp_keywords WHERE domain = ?) WHERE domain = ?",
            (domain, domain),
        )
    return get_keyword(new_id)


def _tag_list(tags: str | list) -> list:
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return [t.strip() for t in str(tags or "").split(",") if t.strip()]


def add_keywords(items: list[dict]) -> list[dict]:
    """Bulk track; every item needs keyword + domain, the rest defaults."""
    return [add_keyword(**it) for it in items]


def get_keyword(keyword_id: int) -> dict | None:
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM serp_keywords WHERE id = ?", (int(keyword_id),)).fetchone()
    return _parse_keyword(row) if row else None


def list_keywords(
    domain: str | None = None,
    *,
    country: str | None = None,
    device: str | None = None,
    keyword: str | None = None,
) -> list[dict]:
    """Tracked keywords, oldest first, with native JSON fields decoded."""
    sql = "SELECT * FROM serp_keywords"
    clauses, params = [], []
    if domain:
        clauses.append("domain = ?")
        params.append(str(domain).strip().lower())
    if country:
        clauses.append("country = ?")
        params.append(str(country).upper())
    if device:
        clauses.append("device = ?")
        params.append(str(device))
    if keyword:
        clauses.append("keyword LIKE ?")
        params.append(f"%{keyword}%")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY added, id"
    with db._conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_parse_keyword(r) for r in rows]


def delete_keywords(ids: str | list) -> int:
    """Remove tracked keywords by id (single or comma separated) and
    dequeue any pending retries for them."""
    if isinstance(ids, str):
        id_list = [int(tok) for tok in str(ids).split(",") if tok.strip().isdigit()]
    else:
        id_list = [int(i) for i in ids]
    if not id_list:
        return 0
    with db._conn() as conn:
        marks = ",".join("?" * len(id_list))
        domains = [
            r[0]
            for r in conn.execute(f"SELECT DISTINCT domain FROM serp_keywords WHERE id IN ({marks})", id_list)
        ]
        removed = conn.execute(f"DELETE FROM serp_keywords WHERE id IN ({marks})", id_list).rowcount
        conn.execute(f"DELETE FROM serp_retry_queue WHERE keyword_id IN ({marks})", id_list)
        for d in domains:
            conn.execute(
                "UPDATE serp_domains SET keyword_count ="
                " (SELECT COUNT(*) FROM serp_keywords WHERE domain = ?) WHERE domain = ?",
                (d, d),
            )
    return removed


def set_keyword_flags(keyword_id: int, *, sticky: bool | None = None, tags: str | list | None = None) -> dict | None:
    """The tracked keyword's operator flags: pin it (sticky) or re-tag it."""
    row = get_keyword(keyword_id)
    if row is None:
        return None
    sets, params = [], []
    if sticky is not None:
        sets.append("sticky = ?")
        params.append(1 if sticky else 0)
    if tags is not None:
        sets.append("tags = ?")
        params.append(json.dumps(_tag_list(tags)))
    if sets:
        with db._conn() as conn:
            conn.execute(f"UPDATE serp_keywords SET {', '.join(sets)} WHERE id = ?", (*params, int(keyword_id)))
    return get_keyword(keyword_id)


def set_keyword_updating(keyword_id: int, updating: bool) -> None:
    with db._conn() as conn:
        conn.execute(
            "UPDATE serp_keywords SET updating = ? WHERE id = ?",
            (1 if updating else 0, int(keyword_id)),
        )


def _parse_keyword(row) -> dict:
    out = dict(row)
    out["sticky"] = bool(out["sticky"])
    out["updating"] = bool(out["updating"])
    out["history"] = json.loads(out["history"] or "{}")
    out["tags"] = json.loads(out["tags"] or "[]")
    out["last_result"] = json.loads(out["last_result"] or "[]")
    raw_error = out["last_update_error"]
    try:
        out["last_update_error"] = json.loads(raw_error) if raw_error and raw_error != "false" else False
    except (TypeError, ValueError):
        out["last_update_error"] = False
    return out


# ------------------------------------------------------------------ record


def record_scrape_result(
    keyword_id: int,
    *,
    position: int,
    url: str = "",
    last_result: list | None = None,
    error: str | None = None,
    scraper: str = "",
    retry: bool = False,
) -> dict | None:
    """One scrape attempt, mirrored from the reference's
    updateKeywordPosition: today's history entry carries the new position
    on success (or the keyword's previous position when the scrape
    failed), `last_updated` is stamped only on success, the failed
    attempt is recorded verbatim (date, error, scraper) and cleared on
    success, `updating` ends either way, and the retry queue follows the
    error flag when retries are switched on."""
    row = get_keyword(keyword_id)
    if row is None:
        return None
    day_key = history_key()
    history = dict(row["history"])
    # a failed attempt never moves the rank: the keyword's previous
    # position is carried through, exactly as the reference does
    stored_position = int(position) if error is None else int(row["position"])
    history[day_key] = stored_position
    error_json = "false"
    if error is not None:
        error_json = json.dumps({"date": now(), "error": str(error), "scraper": scraper})
    with db._conn() as conn:
        if error is None:
            conn.execute(
                "UPDATE serp_keywords SET position = ?, url = ?, last_result = ?, history = ?,"
                " last_updated = ?, last_update_error = 'false', updating = 0 WHERE id = ?",
                (
                    stored_position,
                    str(url or ""),
                    json.dumps(last_result if last_result is not None else []),
                    json.dumps(history),
                    now(),
                    int(keyword_id),
                ),
            )
        else:
            # failed attempt: the previous stamp stays, the error is recorded
            conn.execute(
                "UPDATE serp_keywords SET position = ?, url = ?, last_result = ?, history = ?,"
                " last_update_error = ?, updating = 0 WHERE id = ?",
                (
                    stored_position,
                    str(url or ""),
                    json.dumps(last_result if last_result is not None else []),
                    json.dumps(history),
                    error_json,
                    int(keyword_id),
                ),
            )
    if retry and error is not None:
        queue_retry(keyword_id, error)
    if error is None:
        dequeue_retry(keyword_id)
    return get_keyword(keyword_id)


# -------------------------------------------------------------- retry queue


def queue_retry(keyword_id: int, error: str = "") -> bool:
    """Queue one failed keyword for the hourly retry lane (idempotent)."""
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO serp_retry_queue(keyword_id, error, queued_at) VALUES (?,?,?)",
            (int(keyword_id), str(error)[:200], now()),
        )
    return cur.rowcount > 0


def dequeue_retry(keyword_id: int) -> bool:
    with db._conn() as conn:
        cur = conn.execute("DELETE FROM serp_retry_queue WHERE keyword_id = ?", (int(keyword_id),))
    return cur.rowcount > 0


def pending_retries(limit: int = 100) -> list[dict]:
    """The queue, oldest first, flattened with the keyword row."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT q.keyword_id, q.error, q.queued_at,"
            " k.keyword, k.domain, k.device, k.country, k.position"
            " FROM serp_retry_queue q JOIN serp_keywords k ON k.id = q.keyword_id"
            " ORDER BY q.queued_at, q.keyword_id LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ derived


def _day_sort_key(key: str) -> tuple:
    y, m, d = (int(p) for p in str(key).split("-")[:3])
    return (y, m, d)


def last_week_history(history: dict, days: int = 7) -> dict:
    """The latest N calendar-day entries, sliced in calendar order."""
    ordered = sorted(history, key=_day_sort_key)
    return {key: history[key] for key in ordered[-days:]}


def best_position(history: dict) -> int | None:
    """Best (lowest positive) position ever seen; 0s are rank-lost noise."""
    positives = [v for v in history.values() if isinstance(v, int) and v > 0]
    return min(positives) if positives else None


def position_change(history: dict, position: int) -> int:
    """Movement against the previous calendar day, exactly as the digest
    computes it: previous - current; from 0 anything looks like a gain;
    dropping out of the results reads as the previous minus 100. The
    digest's calling convention: `history` already holds today's entry
    (the record landed before the digest ran), so the previous rank is
    the second-newest day in the dict."""
    entries = sorted(history.items(), key=lambda kv: _day_sort_key(kv[0]))
    if len(entries) < 2:
        return 0
    previous = entries[-2][1]
    if position == 0 and previous > 0:
        return previous - 100
    return previous - position
