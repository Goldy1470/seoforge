from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import db
from .config import settings

_PARAM_KINDS = {
    "id": "tracking",
    "user": "tracking",
    "mail": "tracking",
    "order": "tracking",
    "session": "tracking",
    "utm": "tracking",
    "gclid": "tracking",
    "fbclid": "tracking",
    "sort": "facet",
    "price": "facet",
    "max": "facet",
    "min": "facet",
    "color": "facet",
    "size": "facet",
    "brand": "facet",
    "material": "facet",
    "rating": "facet",
    "filter": "facet",
    "page": "pagination",
    "p": "pagination",
}


def analyze(job_id: str | None = None) -> dict:
    with db._conn() as conn:
        latest = job_id or _latest(conn)
        rows = conn.execute(
            "SELECT url, status, canonical, meta_robots, word_count, title FROM pages WHERE job_id=?",
            (latest,),
        ).fetchall()
    if not rows:
        raise SystemExit("Run a crawl first: python -m seoforge.cli crawl")

    per_param = defaultdict(list)
    for r in rows:
        params = parse_qs(urlsplit(r["url"]).query)
        for k in params:
            per_param[k].append(dict(r))

    facets = []
    for k, pages in sorted(per_param.items(), key=lambda kv: -len(kv[1])):
        base = urlsplit(pages[0]["url"]).netloc + urlsplit(pages[0]["url"]).path
        status = Counter(f"HTTP {p['status']}" for p in pages)
        self_canonical = sum(1 for p in pages if p["canonical"] and p["canonical"] == p["url"])
        rec = _PARAM_KINDS.get(k, "facet")
        content = avg_word_count(pages)
        if rec == "tracking":
            policy = "ignore — do not index; keep out of canonical"
        elif rec == "facet" and (self_canonical == len(pages) and content >= 250 and len(pages) >= 5):
            policy = "index,follow — unique facet pages with own canonical (crawlable for discovery)"
        elif rec == "facet":
            policy = "noindex,follow — duplicate/thin facets; canonical to base category"
        else:
            policy = "noindex,follow — pagination pages; canonical to itself if paginated by URL"
        facets.append(
            {
                "param": k,
                "kind": rec if rec in ("tracking", "facet", "pagination") else "facet",
                "pages_crawled": len(pages),
                "avg_words": content,
                "self_canonical_pct": round(100 * self_canonical / len(pages)),
                "http": dict(status),
                "policy": policy,
                "robots_rule": _robots_rule(k, policy),
            }
        )
    return {
        "job_id": latest,
        "urls_crawled": len(rows),
        "params_found": sorted(per_param),
        "facets": facets,
        "robots_snippet": "\n".join(f["robots_rule"] for f in facets if f["robots_rule"]),
        "canonical_map": _canonical_map(facets),
    }


def avg_word_count(pages: list[dict]) -> int:
    vals = [p["word_count"] or 0 for p in pages]
    return round(sum(vals) / len(vals)) if vals else 0


def _robots_rule(param: str, policy: str) -> str | None:
    if policy.startswith("ignore"):
        return f"Disallow: /*?*{param}="
    if policy.startswith("noindex,follow"):
        return None
    return None


def _canonical_map(facets: list[dict]) -> list[dict]:
    return [
        {"facet": f["param"], "canonical_target": "base category page"}
        for f in facets
        if f["policy"].startswith("noindex,follow")
    ]


def _latest(conn) -> str | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE kind='crawl' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def nginx_rules() -> str:
    """Optional server rules: strip tracking params from canonicals via 301."""
    return """# SEOForge - normalize tracking params (301 to cleaned URL)
server {
  if ($args ~* (utm_source=|gclid=|fbclid=|session=|mail=)) {
    return 301 $scheme://$host$request_uri?${arg_clean};
  }
}
# For Nuxt/Next/Shopify prefer middleware instead. Faceted pages should emit
# <link rel=canonical> + meta robots noindex,follow based on the analyze() report."""


def export_csv(report: dict) -> str:
    path = Path(settings.exports_dir) / "faceted_nav_report.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["param", "kind", "pages_crawled", "avg_words", "self_canonical_pct", "policy", "http_codes"])
        for fac in report["facets"]:
            w.writerow(
                [
                    fac["param"],
                    fac["kind"],
                    fac["pages_crawled"],
                    fac["avg_words"],
                    fac["self_canonical_pct"],
                    fac["policy"],
                    ", ".join(f"{k}:{v}" for k, v in fac["http"].items()),
                ]
            )
    return str(path)
