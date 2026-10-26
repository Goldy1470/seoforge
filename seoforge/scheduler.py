from __future__ import annotations

import asyncio
import json
import sys
import time

from . import crawler, db, indexing, integrations, notify, outcomes, paa, prompts
from .config import settings

DEFAULT_JOBS = [
    {"name": "nightly_delta", "kind": "delta", "interval_hours": 24},
    {"name": "opps_refresh", "kind": "opps", "interval_hours": 12},
    {"name": "weekly_sitemap_check", "kind": "sitemap", "interval_hours": 24 * 7},
    {"name": "serp_watch", "kind": "watch", "interval_hours": 24},
    {"name": "outcome_validation", "kind": "outcomes", "interval_hours": 24},
    {"name": "prompt_positions", "kind": "prompts", "interval_hours": 24},
    # the position tracker's three lanes (the reference cron's schedule)
    {"name": "serp_positions_daily", "kind": "serp_refresh", "interval_hours": 24},
    {"name": "serp_retries_hourly", "kind": "serp_retries", "interval_hours": 1},
    {"name": "serp_digest_daily", "kind": "serp_digest", "interval_hours": 24},
]


def ensure_defaults() -> None:
    with db._conn() as conn:
        for job in DEFAULT_JOBS:
            conn.execute(
                """INSERT INTO schedule(name,kind,interval_hours,enabled,params)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(name) DO NOTHING""",
                (job["name"], job["kind"], job["interval_hours"], 1, "{}"),
            )


def add_job(name: str, kind: str, interval_hours: float, params: dict | None = None) -> str:
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO schedule(name,kind,interval_hours,enabled,params)
               VALUES (?,?,?,1,?)
               ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,
                 interval_hours=excluded.interval_hours, params=excluded.params, enabled=1""",
            (name, kind, interval_hours, json.dumps(params or {})),
        )
    return f"{name} every {interval_hours}h"


def list_schedule() -> list[dict]:
    with db._conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM schedule ORDER BY interval_hours").fetchall()]


def due() -> list[dict]:
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for j in list_schedule():
        if not j["enabled"]:
            continue
        last = j["last_run"]
        if not last:
            out.append(j)
            continue
        try:
            age = now - dt.datetime.fromisoformat(last)
        except ValueError:
            out.append(j)
            continue
        if age >= dt.timedelta(hours=j["interval_hours"]):
            out.append(j)
    return out


def run_job(job: dict) -> dict:
    kind = job["kind"]
    stamp = db.now()
    try:
        if kind == "delta":
            result = nightly_delta()
        elif kind == "opps":
            result = integrations.recompute()
        elif kind == "crawl":
            result = asyncio.run(crawler.crawl_site(settings.site_url))
        elif kind == "sitemap":
            result = indexing.crosscheck()
        elif kind == "watch":
            result = serp_watch()
        elif kind == "paa":
            result = asyncio.run(paa.build_paa_tree([settings.brand_name], max_depth=2))
        elif kind == "outcomes":
            result = outcomes.sweep()
        elif kind == "prompts":
            result = prompts.outcome_sweep()
        elif kind == "serp_refresh":
            from . import serp_scraper

            result = serp_scraper.refresh_domain(None)
        elif kind == "serp_retries":
            from . import serp_scraper

            result = serp_scraper.retry_pending()
        elif kind == "serp_digest":
            from . import serp_digest

            result = serp_digest.digest_all()
        else:
            result = {"error": f"unknown job kind {kind}"}
        status = "done"
    except SystemExit as exc:
        status, result = "skipped", {"error": str(exc)}
    except Exception as exc:
        status, result = "failed", {"error": f"{type(exc).__name__}: {exc}"[:300]}
    with db._conn() as conn:
        conn.execute("UPDATE schedule SET last_run=?, last_status=? WHERE name=?", (stamp, status, job["name"]))
    keys = list(result)[:6] if isinstance(result, dict) else result
    return {"name": job["name"], "status": status, "result": keys if kind != "delta" else result.get("delta_count", result)}


def tick(only_due: bool = True) -> list[dict]:
    ensure_defaults()
    jobs = due() if only_due else list_schedule()
    if not jobs:
        return [{"name": "(none due)", "status": "idle"}]
    return [run_job(j) for j in jobs]


def daemon(poll_seconds: int = 300) -> None:
    print(f"schedule loop started ({settings.site_url}); ctrl+c to stop", flush=True)
    while True:
        try:
            for r in tick(only_due=True):
                print(f"[{time.strftime('%H:%M:%S')}] {r['name']}: {r['status']}", flush=True)
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as exc:
            print(f"scheduler error: {exc}", flush=True)
        time.sleep(poll_seconds)


def gsc_window(dims: list[str], start: str, end: str) -> list[dict]:
    """Arbitrary date-window Search Console query."""
    from urllib.parse import urljoin

    import httpx as _hx

    from . import gsc

    body = {
        "startDate": start,
        "endDate": end,
        "dimensions": dims,
        "rowLimit": 25000,
        "dataState": "all",
    }
    r = _hx.post(
        f"{gsc.API}/webmasters/v3/sites/{gsc._quote(settings.gsc_property)}/searchAnalytics/query",
        headers={"Authorization": f"Bearer {gsc._access_token()}"},
        json=body,
        timeout=90,
    )
    r.raise_for_status()
    return r.json().get("rows", [])


def _gsc_delta(recent_days: int = 2, baseline_days: int = 7) -> list[dict]:
    """Compare the last complete 2-day window against the 7 days before it."""
    import datetime as dt

    today = dt.date.today()
    recent_start = (today - dt.timedelta(days=recent_days + 2)).isoformat()
    recent_end = (today - dt.timedelta(days=3)).isoformat()
    baseline_end = (today - dt.timedelta(days=3 + recent_days)).isoformat()
    baseline_start = (today - dt.timedelta(days=3 + recent_days + baseline_days)).isoformat()
    rec = {(r["keys"][0], r["keys"][1]): r for r in gsc_window(["query", "page"], recent_start, recent_end)}
    base = {(r["keys"][0], r["keys"][1]): r for r in gsc_window(["query", "page"], baseline_start, baseline_end)}
    movers = []
    for key, r in rec.items():
        b = base.get(key)
        if not b:
            continue
        click_delta = round((r["clicks"] - b["clicks"]) / max(b["clicks"], 1) * 100) if b["clicks"] else 0
        pos_delta = round(b["position"] - r["position"], 1)
        if abs(click_delta) >= 40 and max(r["impressions"], b["impressions"]) >= 50:
            movers.append(
                {
                    "query": key[0],
                    "page": key[1],
                    "clicks_now": r["clicks"],
                    "clicks_baseline": b["clicks"],
                    "click_delta_pct": click_delta,
                    "position_delta": pos_delta,
                    "signal": "gain" if (click_delta > 0 or pos_delta > 0) else "loss",
                }
            )
    return sorted(movers, key=lambda m: -abs(m["click_delta_pct"]))


def notify_lines_from_delta(rows: list[dict]) -> list[str]:
    lines = []
    for m in rows[:8]:
        sign = "+" if m["click_delta_pct"] > 0 else ""
        lines.append(
            f'- {m["query"]}: {sign}{m["click_delta_pct"]}% clicks (pos {m["position_delta"]:+}) '
            f'-> {m["page"]}'
        )
    return lines


def nightly_delta() -> dict:
    """GSC last-2-day vs prior-7-day movers + refreshed opportunity queue -> digest."""
    movers = _gsc_delta()
    opps = integrations.recompute(persist=True)
    lines = [f"{settings.site_url} | movers: {len(movers)}"]
    lines += notify_lines_from_delta(movers)
    lines.append("")
    for o in (opps.get("top") or [])[:5]:
        lines.append(f'- {o["type"]} {o["target"]} (impact {o["impact"]}/effort {o["effort"]})')
    notify.send("SEOForge daily digest", lines)
    return {
        "movers": movers[:20],
        "mover_count": len(movers),
        "opportunities_count": opps.get("count"),
        "quick_wins": opps.get("quick_wins"),
    }


def serp_watch() -> dict:
    """Snapshot every watchlist keyword (you vs your named competitor)."""
    from . import keywords

    with db._conn() as conn:
        kws = [dict(r) for r in conn.execute("SELECT * FROM watchlist").fetchall()]
    if not kws:
        return {"error": "watchlist empty - add with watch_add"}
    mine = settings.site_url.split("//")[-1].removeprefix("www.")
    out = []
    for k in kws:
        try:
            s = asyncio.run(keywords.serp(k["keyword"]))
            own = next((o for o in s["organic"] if mine in (o.get("domain") or "")), None)
            comp = next(
                (o for o in s["organic"] if k.get("competitor") and k["competitor"].split("//")[-1] in (o.get("domain") or "")),
                None,
            ) if k.get("competitor") else None
            out.append(
                {
                    "keyword": k["keyword"],
                    "you": own["rank"] if own else ">20",
                    "competitor": comp["rank"] if comp else (">20" if comp is not None else None),
                    "notes_added": mine,
                }
            )
        except SystemExit as exc:
            out.append({"keyword": k["keyword"], "error": str(exc)})
        except Exception as exc:
            out.append({"keyword": k["keyword"], "error": str(exc)[:200]})
    ranked = [o for o in out if isinstance(o.get("you"), int)]
    db.save_report("serp_watch", {"results": out})
    return {"checked": len(kws), "you_ranked_for": len(ranked), "results": out}


def watch_add(keyword: str, competitor: str = "", note: str = "") -> str:
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO watchlist(keyword,competitor,added_at,note) VALUES (?,?,?,?)
               ON CONFLICT(keyword) DO UPDATE SET competitor=excluded.competitor, note=excluded.note""",
            (keyword, competitor or None, db.now(), note),
        )
    return f"watching {keyword}"


def watch_list() -> list[dict]:
    with db._conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM watchlist").fetchall()]
