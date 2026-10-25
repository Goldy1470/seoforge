"""Did the work help? (outcome capture for the work queue; derivation in
THIRD_PARTY_NOTICES.md).

An opportunity closes when someone marks the work done. Whether the work
moved anything is a separate question, and until now nothing asked it:
closed items waited forever with no verdict and no way to see one.

The answer comes from measuring the opportunity's own success metrics
twice — over a window ending before it was raised, and a window after it
closed — and comparing. Both reads go through the same data the other
reports read (GSC windows, rank snapshots, the AI-bot daily counters), so
a verdict here and the dashboard that summarizes the same days cannot
disagree.

What it deliberately does not do:

- claim causality. The metrics moved; whether this work moved them is
  not knowable from a before/after pair — the verdict is "measured after
  this closed", never "because of it";
- infer an outcome from completion. Closing six items is evidence of
  effort, not of effect;
- measure what it cannot read. A metric with no data in either window is
  reported as unmeasured rather than as zero, because zero is a number
  someone will believe.

Single-tenant deviations from the reference (SQLite instead of one
Postgres RPC per brand): the measurement windows and the noise floor are
module constants, the opportunity is the action, and the measurable
metrics are the site's own time series — GSC clicks/impressions/position
for its pages, the site's rank-snapshot position for its keywords, and
the AI-bot daily read counters for its pages.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import sqlite3
import sys

from . import bot_ingest, db
from .config import settings

# ------------------------------------------------- windows + noise floor

#: Length of each measurement window. Equal on both sides so the
#: comparison is between like periods.
MEASUREMENT_WINDOW_DAYS = 7

#: How long after closing before an opportunity can be measured. The
#: "after" window runs for MEASUREMENT_WINDOW_DAYS from the day following
#: the close, and it has to be *over* before it means anything: a window
#: still in progress reads low simply because its later days have not
#: happened, and reporting that as a decline would turn every recent
#: piece of work into a failure. The sweep filters on this cheaply;
#: validate checks the window itself, which is the condition that matters.
VALIDATION_WAIT_DAYS = MEASUREMENT_WINDOW_DAYS + 1

#: How much a metric must move to count as movement, as a fraction of its
#: before value. Below this, a difference is indistinguishable from the
#: week-to-week noise every one of these metrics carries.
MEANINGFUL_CHANGE_RATIO = 0.05

#: A position must move at least this far to count as a change:
#: rankings carry fractions, and day-to-day wobble is far smaller than
#: the count metrics' noise.
POSITION_CHANGE = 0.5

#: The verdict an opportunity carries from closure until measurement.
PENDING = "pending_measurement"

#: Outcome vocabulary (what History shows; movement, not causality).
OUTCOMES = ("improved", "declined", "no_meaningful_change", "not_measurable")

#: Count metrics read higher-is-better and derive with the noise-floor
#: ratio. A drop is a decline.
COUNT_KPIS = {
    "gsc_clicks": "clicks",
    "gsc_impressions": "impressions",
    "bot_reads": "reads",
}

#: Position metrics read lower-is-better; they are compared with the
#: position rule (the 0.5 floor) instead of the count ratio. Their
#: verdicts are recorded per metric and folded together for the one
#: outcome column only where the count metrics stand silent.
POSITION_KPIS = {
    "gsc_position": "position",
    "rank_position": "position",
}

_EPS = sys.float_info.epsilon


def utc_day(date: dt.date) -> str:
    """A whole-day bound as `yyyy-mm-dd` (UTC; the measurement grain)."""
    return date.isoformat()


def today() -> dt.date:
    """The measurement clock: today (UTC)."""
    return dt.datetime.now(dt.timezone.utc).date()


def window_ending_before(
    end_exclusive: dt.date, days: int = MEASUREMENT_WINDOW_DAYS
) -> tuple[str, str]:
    """Inclusive whole-day window of `days` ending the day before
    `end_exclusive`."""
    to = end_exclusive - dt.timedelta(days=1)
    return (utc_day(to - dt.timedelta(days=days - 1)), utc_day(to))


def window_starting_after(
    start_exclusive: dt.date, days: int = MEASUREMENT_WINDOW_DAYS
) -> tuple[str, str]:
    """Inclusive whole-day window of `days` starting the day after
    `start_exclusive`."""
    from_ = start_exclusive + dt.timedelta(days=1)
    return (utc_day(from_), utc_day(from_ + dt.timedelta(days=days - 1)))


def _day(text) -> dt.date | None:
    """The date part of a stored ISO stamp (None: no stamp, or not one)."""
    if not text:
        return None
    try:
        return dt.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


# -------------------------------------------------------------- verdicts

def derive_outcome(metrics: list[dict]) -> str:
    """The verdict, from the count metrics' measured pairs alone (the
    reference rule).

    A metric counts as moved only past MEANINGFUL_CHANGE_RATIO — these
    numbers drift week to week on their own, and calling a 1% wobble an
    improvement would make every outcome meaningless. Mixed movement reads
    as improved: something the work aimed at did move, and reporting that
    as "no change" would hide it. Unmeasured metrics are left out of the
    derivation, never read as zero.
    """
    measured = [m for m in metrics if m.get("before") is not None and m.get("after") is not None]
    if not measured:
        return "not_measurable"

    improved = 0
    declined = 0
    for m in measured:
        before = float(m["before"])
        after = float(m["after"])
        floor = abs(before) * MEANINGFUL_CHANGE_RATIO
        # A metric that was zero before has no ratio to scale by; any real
        # arrival counts as movement.
        threshold = _EPS if before == 0 else floor
        if after - before > threshold:
            improved += 1
        elif before - after > threshold:
            declined += 1

    if improved > 0:
        return "improved"
    if declined > 0:
        return "declined"
    return "no_meaningful_change"


def position_verdict(before, after, change: float = POSITION_CHANGE) -> str:
    """One position pair's verdict (the reference's lower-is-better
    position rule, as the rank panels already assume):

    - not_enough_data  no position on either side
    - improved         named at least `change` earlier (position dropped)
    - declined         named at least `change` later
    - unchanged        anything smaller
    """
    if before is None or after is None:
        return "not_enough_data"
    try:
        before = float(before)
        after = float(after)
    except (TypeError, ValueError):
        return "not_enough_data"
    if math.isnan(before) or math.isnan(after) or math.isinf(before) or math.isinf(after):
        return "not_enough_data"
    if before - after >= change:
        return "improved"
    if after - before >= change:
        return "declined"
    return "unchanged"


def outcome_from_verdicts(verdicts: list[str]) -> str | None:
    """The one verdict when only position metrics measured: their
    verdicts folded into the outcome vocabulary. None when there is no
    position metric at all (the count derivation decides alone)."""
    if not verdicts:
        return None
    if "improved" in verdicts:
        return "improved"
    if "declined" in verdicts:
        return "declined"
    if all(v == "not_enough_data" for v in verdicts):
        return "not_measurable"
    return "no_meaningful_change"


# ------------------------------------------------------- the target view

def _as_page_url(raw: str) -> str | None:
    """A target text as a page URL, when it names one: absolute URLs pass
    through, a stored path joins the site, everything else names no page."""
    text = str(raw or "").strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return text
    if text.startswith("/"):
        base = (settings.site_url or "").strip().rstrip("/")
        return f"{base}{text}" if base else None
    return None


def target_parts(kind: str, target: str, evidence: dict | None = None) -> dict:
    """What one opportunity can be measured on: its page (as a GSC page
    value and a bot-counter page key) and its query/keyword.

    Targets are free text from the queue's builders — a crawl URL, a GSC
    page string, a built-page path, `'<page> [<query>]'`, the bare query a
    cannibalization split names, or text with no measurable object (a
    directory's name, a hygiene issue). Leaving a part out is honest: a
    target that names no page and no query measures nothing.
    """
    evidence = evidence or {}
    text = str(target or "").strip()
    query = evidence.get("query") or evidence.get("keyword") or None
    page = None

    # `promote_to_top5` writes the page and its query as `'<page> [<q>]'`.
    bracket = re.fullmatch(r"(.+?)\s*\[(.+)\]", text)
    if bracket:
        page = _as_page_url(bracket.group(1))
        query = query or bracket.group(2).strip() or None
    else:
        page = _as_page_url(text)
        if page is None and not text.startswith("/") and kind == "repair_cannibalization":
            # The cannibalization rule writes the bare query as its target
            # when no single page carries the split — a query-only target.
            query = query or text or None

    return {
        "page": page,
        "page_key": bot_ingest.normalize_page_path(page).lower() if page else None,
        "query": query,
        "keyword": query,
    }


def site_domain() -> str | None:
    """The site's own domain in the shape the rank snapshots store: the
    registrable domain, lowercased (the crawler's offline PLS rules)."""
    site = (settings.site_url or "").strip()
    if not site:
        return None
    from .bot_ingest import _norm_host
    from .crawler import _registrable  # the PLS/registrable rules live there

    host = _norm_host(site)
    if not host:
        return None
    return _registrable(f"https://{host}") or host


# ------------------------------------------------- the measurable reads

def gsc_window_rows(dims: list[str], window: tuple[str, str]) -> list:
    """GSC rows for one explicit whole-day window — the same endpoint the
    dashboard's other GSC reads use, so the two cannot disagree for the
    same days. Empty when the property is not connected, or the window has
    no rows yet: the caller reports that as unmeasured, not zero."""
    from . import gsc

    try:
        return gsc.query(dims, extra={"startDate": window[0], "endDate": window[1]})
    except SystemExit:  # not connected (no property/token): unmeasured, cleanly
        return []
    except Exception:  # a dead request: unmeasured, too
        return []


def _norm_page_key(urlish) -> str:
    """A GSC page value or a target as the bucket it belongs to: without
    its scheme, `www.`, query, fragment or trailing slash, lowercased."""
    return bot_ingest.normalize_page_path(str(urlish or "")).lower()


def _row_page(row) -> str:
    keys = row.get("keys") if isinstance(row, dict) else None
    return str(keys[1]) if keys and len(keys) > 1 else ""


def _row_query(row) -> str:
    keys = row.get("keys") if isinstance(row, dict) else None
    return str(keys[0]) if keys else ""


def _gsc_matches(rows: list, page: str | None, query: str | None) -> list:
    """The window's rows belonging to this target: the page's bucket (and
    that query's rows for it, when one is named); a query-only target
    matches every page the query lands on."""
    if page:
        key = _norm_page_key(page)
        out = []
        for row in rows:
            row_page = _row_page(row)
            if not row_page or _norm_page_key(row_page) != key:
                continue
            if query and _row_query(row).lower() != query.lower():
                continue
            out.append(row)
        return out
    if query:
        return [r for r in rows if _row_query(r).lower() == query.lower()]
    return []


def gsc_metrics(rows: list, page: str | None, query: str | None = None) -> dict:
    """The target's GSC metrics from one window's pre-read rows: clicks
    and impressions summed over the matching rows, position as their
    mean. A metric the window cannot answer reads None (unmeasured) —
    never zero, because zero is a number someone will believe."""
    if not page and not query:
        return {"gsc_clicks": None, "gsc_impressions": None, "gsc_position": None}
    matched = _gsc_matches(rows, page, query)
    if not matched:
        return {"gsc_clicks": None, "gsc_impressions": None, "gsc_position": None}
    clicks = sum(float(r.get("clicks") or 0) for r in matched)
    impressions = sum(float(r.get("impressions") or 0) for r in matched)
    positions = [float(r.get("position")) for r in matched if r.get("position") is not None]
    return {
        "gsc_clicks": round(clicks, 1),
        "gsc_impressions": round(impressions, 1),
        "gsc_position": round(sum(positions) / len(positions), 1) if positions else None,
    }


def rank_position(
    conn: sqlite3.Connection, keyword: str | None, window: tuple[str, str]
) -> float | None:
    """The site's own best (lowest) position for a keyword over one
    window, from the rank snapshots the SERP runs already leave behind.
    None when no run covered the keyword there — unmeasured, not some
    other site's position."""
    if not keyword:
        return None
    domain = site_domain()
    if domain:
        row = conn.execute(
            "SELECT MIN(position) p FROM rank_snapshots"
            " WHERE keyword = ? COLLATE NOCASE"
            " AND (LOWER(domain) = ? OR LOWER(domain) LIKE '%' || ?)"
            " AND SUBSTR(searched_at, 1, 10) BETWEEN ? AND ?"
            " AND position IS NOT NULL",
            (keyword, domain.lower(), domain.lower(), window[0], window[1]),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT MIN(position) p FROM rank_snapshots"
            " WHERE keyword = ? COLLATE NOCASE"
            " AND SUBSTR(searched_at, 1, 10) BETWEEN ? AND ?"
            " AND position IS NOT NULL",
            (keyword, window[0], window[1]),
        ).fetchone()
    return float(row["p"]) if row and row["p"] is not None else None


def bot_reads(
    conn: sqlite3.Connection, page_key: str | None, window: tuple[str, str]
) -> int | None:
    """How often the tracked AI bots read the page over one window, from
    the daily counters the ingest keeps (past the event cap they still
    count). None without a page to key on; a page no bot read yet is that
    window's real zero."""
    if not page_key:
        return None
    row = conn.execute(
        "SELECT SUM(hits) h FROM bot_daily WHERE page = ? AND day BETWEEN ? AND ?",
        (page_key, window[0], window[1]),
    ).fetchone()
    return int(row["h"]) if row and row["h"] is not None else 0


def measure(
    conn: sqlite3.Connection,
    kind: str,
    target: str,
    evidence: dict | None,
    window: tuple[str, str],
    *,
    gsc_rows: list | None = None,
) -> list[dict]:
    """The success metrics over one window, in metric-table shape: a value
    per metric the window can answer, None (unmeasured) for the rest.
    `gsc_rows` lets a caller share one window read across measurements
    (tests hand a fake's rows here instead of the network)."""
    if gsc_rows is None:
        gsc_rows = gsc_window_rows(["query", "page"], window)
    parts = target_parts(kind, target, evidence)
    g = gsc_metrics(gsc_rows, parts["page"], parts["query"])
    return [
        {"metric": "gsc_clicks", "unit": "clicks", "value": g["gsc_clicks"]},
        {"metric": "gsc_impressions", "unit": "impressions", "value": g["gsc_impressions"]},
        {"metric": "gsc_position", "unit": "position", "value": g["gsc_position"]},
        {
            "metric": "rank_position",
            "unit": "position",
            "value": rank_position(conn, parts["keyword"], window),
        },
        {
            "metric": "bot_reads",
            "unit": "reads",
            "value": bot_reads(conn, parts["page_key"], window),
        },
    ]


# ------------------------------------------------------------- validate

def record_event(
    conn: sqlite3.Connection, opportunity_id: int, event: str, data: dict, now=None
) -> None:
    """The trail: one row, so a measurement sits in History alongside the
    work that preceded it."""
    conn.execute(
        "INSERT INTO opportunity_events(opportunity_id, event, data, at) VALUES (?,?,?,?)",
        (opportunity_id, event, json.dumps(data or {}, default=str), now or db.now()),
    )


def validate_opportunity(opportunity_id: int, *, now: dt.date | None = None) -> str | None:
    """Measure one closed opportunity and record the result.

    Returns the outcome written, or None when the opportunity is not
    ready — it was never closed, or its "after" window has not finished
    yet. A verdict is recorded once; the sweep does not re-measure an
    answered pair.
    """
    with db._conn() as conn:
        row = conn.execute(
            "SELECT id, type, target, evidence, status, created_at, completed_at, outcome"
            " FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if row is None:
            return None
        if row["outcome"] not in (None, PENDING):
            # Answered stays answered: the verdict was recorded once, and a
            # re-ask reports it without writing again.
            return row["outcome"]
        try:
            evidence = json.loads(row["evidence"] or "{}") or {}
        except ValueError:
            evidence = {}
        return _validate_row(conn, row, evidence, now)


def _validate_row(conn: sqlite3.Connection, row, evidence: dict, now) -> str | None:
    """The measurement itself, on the caller's transaction."""
    closed_at = _day(row["completed_at"])
    if closed_at is None:
        if row["status"] == "done":
            # Closed before completion stamps existed: without the close
            # date the windows cannot be built — not measurable, said
            # once. (Still-open rows just wait.)
            return _write(conn, row["id"], None, "not_measurable", now)
        return None

    raised = _day(row["created_at"])
    after = window_starting_after(closed_at)
    day = now or today()
    if raised is not None:
        before = window_ending_before(raised)
    else:
        # No raise date: the before window cannot be built — a gap in
        # whatever raised it, said plainly rather than invented.
        return _write(conn, row["id"], None, "not_measurable", now)
    # The window has to be complete. Measuring one whose last days have
    # not happened yet reads low for a reason that has nothing to do with
    # the work, and would file most recent work as a decline.
    if after[1] >= utc_day(day):
        return None

    table: dict[str, dict] = {}
    for window in (before, after):
        side = "before" if window is before else "after"
        for v in measure(conn, row["type"], row["target"], evidence, window):
            slot = table.setdefault(
                v["metric"], {"metric": v["metric"], "unit": v["unit"], "before": None, "after": None}
            )
            slot[side] = v["value"]

    # A target with nothing measured on either side: no data at all, or a
    # target that names no measureable object — the queue's own report
    # says not measurable without inventing a value.
    if all(v["before"] is None and v["after"] is None for v in table.values()):
        return _write(conn, row["id"], None, "not_measurable", now)

    positions = {
        v["metric"]: position_verdict(v["before"], v["after"])
        for v in table.values()
        if v["metric"] in POSITION_KPIS
    }
    counts = [v for v in table.values() if v["metric"] in COUNT_KPIS]
    count_outcome = derive_outcome(counts)
    folded = outcome_from_verdicts(list(positions.values()))

    # The one outcome column: the count derivation leads; where it stands
    # silent (nothing countable measured) the position verdicts speak, and
    # where it says "no change" but a position did move, the movement is
    # not hidden (mixed movement reads as improved, same as the count
    # rule inside derive_outcome). A declined count stands even then: a
    # measured drop is a verdict, and one moving metric does not erase it.
    if count_outcome == "not_measurable" and folded is not None:
        outcome = folded
    elif count_outcome == "no_meaningful_change" and folded == "improved":
        outcome = "improved"
    else:
        outcome = count_outcome

    validation = {
        "measuredAt": utc_day(day),
        "beforeWindow": {"from": before[0], "to": before[1]},
        "afterWindow": {"from": after[0], "to": after[1]},
        "metrics": [table[k] for k in sorted(table)],
        "positionVerdicts": positions,
    }
    return _write(conn, row["id"], validation, outcome, now)


def _write(conn: sqlite3.Connection, opportunity_id: int, validation: dict, outcome: str, now) -> str:
    conn.execute(
        "UPDATE opportunities SET outcome=?, validation=?, validated_at=? WHERE id=?",
        (
            outcome,
            json.dumps(validation, default=str) if validation else None,
            db.now(),
            opportunity_id,
        ),
    )
    record_event(
        conn,
        opportunity_id,
        "outcome_measured",
        {"outcome": outcome, "measuredAt": (validation or {}).get("measuredAt")},
    )
    return outcome


def list_outcomes(limit: int = 25) -> list[dict]:
    """The measured verdicts, newest first (History's result column)."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id, type, target, status, outcome, validation, created_at,"
            " completed_at, validated_at FROM opportunities"
            " WHERE outcome IS NOT NULL AND outcome != ?"
            " ORDER BY validated_at DESC LIMIT ?",
            (PENDING, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["validation"] = json.loads(d["validation"] or "{}") or None
        except (ValueError, TypeError):
            d["validation"] = None
        out.append(d)
    return out


def sweep(now: dt.date | None = None) -> dict:
    """Measure every closed opportunity whose settling period has passed.

    Best-effort per opportunity — one broken window read must not stop
    the rest from being measured.
    """
    day = now or today()
    cutoff = utc_day(day - dt.timedelta(days=VALIDATION_WAIT_DAYS))
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id FROM opportunities"
            " WHERE status = 'done' AND completed_at IS NOT NULL"
            " AND SUBSTR(completed_at, 1, 10) <= ?"
            " AND (outcome IS NULL OR outcome = ?)"
            " ORDER BY completed_at LIMIT 500",
            (cutoff, PENDING),
        ).fetchall()

    measured = 0
    failed = 0
    for r in rows:
        try:
            if validate_opportunity(r["id"], now=day) is not None:
                measured += 1
        except Exception:
            failed += 1
    return {"measured": measured, "failed": failed}
