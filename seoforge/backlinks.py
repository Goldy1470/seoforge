from __future__ import annotations

import csv
import json
from pathlib import Path

from . import db
from .config import settings

STATUSES = {"todo", "drafting", "submitted", "live", "rejected", "skipped"}


def load_directories() -> int:
    """Seed the directory tracker from data/directories_73.csv (made once)."""
    src = Path(settings.directories_csv)
    if not src.exists():
        return 0
    with db._conn() as conn, src.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            conn.execute(
                """INSERT INTO directories(name,dr,category,url,status,notes)
                   VALUES (?,?,?,?,COALESCE((
                       SELECT status FROM directories WHERE name=?),'todo'),?)
                   ON CONFLICT(name) DO UPDATE SET dr=excluded.dr,
                       category=excluded.category, url=COALESCE(NULLIF(directories.url,''),
                       excluded.url),
                       notes=CASE WHEN excluded.notes='' THEN directories.notes
                                  ELSE excluded.notes END""",
                (
                    row["name"].strip(),
                    int(row["dr"] or 0),
                    row["category"].strip(),
                    row["url"].strip(),
                    row["name"].strip(),
                    row.get("notes", "").strip(),
                ),
            )
    return 1


def list_directories(status: str | None = None, min_dr: int = 0) -> list[dict]:
    with db._conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM directories WHERE status=? AND dr>=? ORDER BY dr DESC",
                (status, min_dr),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM directories WHERE dr>=? ORDER BY dr DESC", (min_dr,)
            ).fetchall()
    return [dict(r) for r in rows]


def update_status(name: str, status: str, notes: str | None = None) -> str:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    with db._conn() as conn:
        if notes is not None:
            conn.execute(
                "UPDATE directories SET status=?, notes=?, submitted_at=CASE WHEN ? IN ('submitted','live') THEN COALESCE(submitted_at, datetime('now')) ELSE submitted_at END WHERE name=?",
                (status, notes, status, name),
            )
        else:
            conn.execute(
                "UPDATE directories SET status=? WHERE name=?",
                (status, name),
            )
    return f"{name} -> {status}"


def bucket_by_category() -> dict:
    from collections import Counter, defaultdict

    dirs = list_directories()
    by_cat = defaultdict(list)
    for d in dirs:
        by_cat[d["category"]].append(d["name"])
    stats = Counter(d["status"] for d in dirs)
    return {"total": len(dirs), "status_counts": dict(stats), "categories": dict(by_cat)}


def gap(target_domain: str | None = None) -> dict:
    """Backlink gap via DataForSEO run: which high-DR places already link to competitors."""
    target = (target_domain or settings.site_url).split("/")[2].removeprefix("www.")
    if not settings.dataforseo_login:
        return {"error": "Set DATAFORSEO_LOGIN/PASSWORD to run backlink pulls"}
    import asyncio

    from .keywords import backlinks_for, domain_metrics

    mine = asyncio.run(backlinks_for(target))
    live = [b["from_domain"] for b in mine if b.get("do_follow")]
    return {
        "target": target,
        "metrics": asyncio.run(domain_metrics(target)),
        "total_backlinks_found": len(mine),
        "do_follow_domains": len(set(live)),
    }


def digital_pr_plan(brand: str | None = None) -> list[dict]:
    brand = brand or settings.brand_name
    return [
        {
            "tactic": "Unlinked brand mentions",
            "how": f'Search: "{brand}" -site:{(settings.site_url.split("/")[2])} -linkedin.com -youtube.com',
            "tool": "browser_agent, find brand citations and email for a link",
        },
        {
            "tactic": "Journalist request platforms",
            "how": "Sign up to Qwoted, Featured, SourceBottle, ProfNet; answer daily with data-backed quotes",
            "asset": "1 spokesperson + 1 data point from your own usage",
        },
        {
            "tactic": "Linkable data asset",
            "how": "Publish an original dataset (ex: 'AI SEO adoption in D2C, n=...') and pitch to the 15 highest-DR outlets in the tracker",
            "metric": "DR>=80 first",
        },
        {
            "tactic": "Guide / tool linkable page",
            "how": "Build a free-guide page (people also ask build list) and email everyone ranking below you for the same PAA cluster",
        },
        {
            "tactic": "Podcast circuit",
            "how": "Pitch SaaStr / Mixergy / Bootstrappers / SaaS Club from the tracker; each gives a DR 59-78 backlink",
        },
    ]


def export_csv() -> str:
    path = Path(settings.exports_dir) / "backlink_tracker.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "dr", "category", "url", "status", "notes", "submitted_at"])
        for d in list_directories():
            w.writerow(
                [d["name"], d["dr"], d["category"], d["url"] or "", d["status"], d["notes"] or "", d["submitted_at"] or ""]
            )
    return str(path)
