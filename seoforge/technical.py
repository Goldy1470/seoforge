"""Technical SEO: the six rules, each as a runnable check.

1 Site structure  - every important page within three clicks of the homepage
2 Crawlability    - words live in raw HTML; AI crawlers (GPTBot, ClaudeBot) do not run JavaScript
3 Indexing        - batch over the GSC "pages Google skipped" problem via urlInspection
4 Page speed      - load the main content first: render-blocking head resources counted
5 Mobile friendly - viewport present (one-handed phone use start: no pinch-zoom)
6 XML sitemap     - list only what you want ranked, submitted to Google and Bing
"""
from __future__ import annotations

from collections import Counter, deque
from urllib.parse import urlsplit

from . import db
from .config import settings


def homepage() -> str:
    from .crawler import _norm

    return _norm(settings.site_url)


def _same_host(a: str, b: str) -> bool:
    try:
        return urlsplit(a).netloc.lower().removeprefix("www.") == urlsplit(b).netloc.lower().removeprefix("www.")
    except Exception:
        return False


def _bfs(adj: dict, origin: str, max_clicks: int) -> dict:
    dist = {origin: 0}
    q = deque([(origin, 0)])
    while q:
        u, d = q.popleft()
        if d >= max_clicks:
            continue
        for v in adj.get(u, []):
            if v not in dist:
                dist[v] = d + 1
                q.append((v, d + 1))
    return dist


def structure(max_clicks: int = 3) -> dict:
    """Rule 1: BFS click-distance from the homepage over the crawl's link graph."""
    origin = homepage()
    with db._conn() as conn:
        edges = conn.execute("SELECT source,target FROM links LIMIT 30000").fetchall()
        important = conn.execute(
            "SELECT page, SUM(impressions) imp FROM gsc_cache GROUP BY page HAVING SUM(impressions) >= 20 ORDER BY imp DESC LIMIT 60"
        ).fetchall()
        if not important:
            important = conn.execute(
                "SELECT url AS page, word_count AS imp FROM pages WHERE status=200 AND word_count>250 ORDER BY word_count DESC LIMIT 25"
            ).fetchall()
    adj: dict[str, list] = {}
    for e in edges:
        src, tgt = e["source"], e["target"]
        if not src or not tgt or not _same_host(src, tgt):
            continue
        adj.setdefault(src, []).append(tgt)
        if _same_home(src, origin):
            adj.setdefault(origin, []).append(tgt)
    dist = _bfs(adj, origin, max_clicks)
    rows = []
    for r in important:
        page = r["page"]
        clicks = dist.get(page)
        rows.append({"page": page, "clicks_from_home": clicks, "impressions": r["imp"]})
    return {
        "homepage": origin,
        "max_clicks": max_clicks,
        "rule": "every important page within three clicks of the homepage - if it is deeper, link it from a top hub",
        "important_checked": len(rows),
        "beyond_limit": [r for r in rows if r["clicks_from_home"] is None or r["clicks_from_home"] > max_clicks],
        "reachable": [r for r in rows if r["clicks_from_home"] is not None and r["clicks_from_home"] <= max_clicks],
        "depth_map": {u: d for u, d in sorted(dist.items(), key=lambda x: x[1])[:60]},
    }


def _same_home(src: str, origin: str) -> bool:
    try:
        return urlsplit(src).path in ("", "/") and _same_host(src, origin)
    except Exception:
        return False


def crawlability() -> dict:
    """Rule 2: JS-shell detection + raw-words capture per crawled page."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT url, word_count, first_text, js_flag, viewport FROM pages WHERE status=200 ORDER BY harvested_at DESC LIMIT 300"
        ).fetchall()
    legacy = sum(1 for r in rows if r["js_flag"] is None)
    shells = [r["url"] for r in rows if r["js_flag"] == "spa"]
    nextjs = [r["url"] for r in rows if r["js_flag"] == "next"]
    raw_words = [r["url"] for r in rows if (r["word_count"] or 0) >= 300 and (r["first_text"] or "")]
    return {
        "rule": "AI crawlers do not run JavaScript - your words must exist in the raw HTML (GPTBot, ClaudeBot, PerplexityBot)",
        "pages_checked": len(rows),
        "pre_v07_rows": legacy,
        "js_shells": shells,
        "nextjs_detected": nextjs,
        "raw_html_healthy": len(raw_words),
        "note": "run geo/aeo funnel 'found' for the per-agent robots.txt verdict; run llms-full for the citation pack",
    }


def index_status(urls=None, limit: int = 25) -> dict:
    """Rule 3: batch urlInspection - how many pages Google skipped and why."""
    from . import gsc

    if not urls:
        with db._conn() as conn:
            urls = [
                r["page"]
                for r in conn.execute(
                    "SELECT page, SUM(impressions) imp FROM gsc_cache GROUP BY page ORDER BY imp DESC LIMIT ?",
                    (limit,),
                )
            ]
            if not urls:
                urls = [r["url"] for r in conn.execute(
                    "SELECT url FROM pages WHERE status=200 AND title IS NOT NULL LIMIT ?", (limit,)
                )]
    rows, errors = [], []
    try:
        for u in urls:
            try:
                res = gsc.inspect_url(u)
                r = res.get("inspectionResult", {}).get("indexStatusResult", {})
                coverage = r.get("coverageState", "?")
                verdict = r.get("verdict", "?")
                rows.append(
                    {
                        "url": u,
                        "verdict": verdict,
                        "coverage": coverage,
                        "fetch": r.get("pageFetchState", ""),
                        "indexing_state": r.get("indexingState", ""),
                        "skipped": verdict != "PASS",
                        "sitemaps": res.get("inspectionResult", {}).get("sitemapResult", {}),
                    }
                )
            except SystemExit:
                raise
            except Exception as exc:
                errors.append({"url": u, "error": str(exc)[:120]})
    except SystemExit as exc:
        return {"skipped": str(exc), "note": "run gsc-auth once to use the GSC pages report"}
    counts = Counter(r["coverage"] for r in rows)
    return {
        "rule": "check the pages report in Search Console for pages Google skipped - then fix the reason row by row",
        "checked": len(rows),
        "indexed": sum(1 for r in rows if r["verdict"] == "PASS"),
        "skipped": sum(1 for r in rows if r["skipped"]),
        "reason_counts": dict(counts),
        "rows": rows[:40],
        "errors": errors,
    }


def speed_rules() -> dict:
    """Rule 4: main content first = low render-blocking head resources, fast TTFB, small HTML."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT url, render_blockers, response_ms, size_bytes, js_flag FROM pages WHERE status=200 ORDER BY harvested_at DESC LIMIT 300"
        ).fetchall()
    heavy, blockers_rows, legacy = [], [], 0
    for r in rows:
        blockers = r["render_blockers"]
        if blockers is None:
            legacy += 1
            continue
        ms = r["response_ms"] or 0
        size = r["size_bytes"] or 0
        if blockers > 6:
            heavy.append({"url": r["url"], "blockers": blockers, "ms": ms, "kb": round(size / 1024)})
    return {
        "rule": "load the main content first, then everything else - defer/async the head scripts, inline the critical CSS",
        "pages_checked": len(rows),
        "pre_v07_rows": legacy,
        "heavy_pages": heavy[:25],
        "note": "core_web_vitals MCP tool gives LCP/TBT/CLS + CrUX field data per URL",
    }


def mobile_rules() -> dict:
    """Rule 5: viewport sanity on every page (one-handed phone use starts with no pinch-zoom)."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT url, viewport FROM pages WHERE status=200 ORDER BY harvested_at DESC LIMIT 300"
        ).fetchall()
    legacy = sum(1 for r in rows if r["viewport"] is None)
    missing = [r["url"] for r in rows if r["viewport"] == 0]
    return {
        "rule": "every page easy to use on a phone one-handed - viewport meta is the floor, CWV mobile is the ceiling",
        "pages_checked": len(rows),
        "pre_v07_rows": legacy,
        "missing_viewport": missing[:40],
        "next": "run core_web_vitals (mobile) for LCP/INP/CLS field data",
    }


def sitemap_policy() -> dict:
    """Rule 6: only ranked pages in the sitemap + submission to Google and Bing."""
    from . import indexing

    cross = indexing.crosscheck()
    keep, remove = [], []
    problem_urls = {p["url"] for p in cross.get("problems", [])}
    with db._conn() as conn:
        sitemapish = [r["url"] for r in conn.execute(
            "SELECT DISTINCT url FROM pages WHERE status=200 AND title IS NOT NULL LIMIT 500"
        )]
    remove = [
        {"url": p["url"], "issue": p["issue"], "severity": p["severity"]}
        for p in cross.get("problems", [])
        if p["severity"] in ("high", "medium")
    ]
    split = urlsplit(settings.site_url)
    sm = f"{split.scheme}://{split.netloc}/sitemap.xml"
    return {
        "rule": "list only the pages you want ranked, and submit the sitemap to Google and Bing",
        "keep_in_sitemap": len(sitemapish) - len(problem_urls),
        "remove_from_sitemap": remove[:60],
        "missing_from_sitemap": cross.get("not_in_sitemap_preview", [])[:20],
        "summary": cross.get("summary", {}),
        "submit": {
            "google_ping": f"https://www.google.com/ping?sitemap={sm} (also Search Console -> Sitemaps; CLI indexing_push does it)",
            "bing": "IndexNow key + submit (CLI indexnow, Bing/Yandex/Seznam) and Bing Webmaster -> Sitemaps upload",
        },
        "facts": {"robots_txt": cross.get("robots_txt"), "sitemap_urls": cross.get("sitemap_urls")},
    }


def report() -> dict:
    """All six technical rules in one pass, saved as technical_audit."""
    try:
        r3 = index_status(limit=10)
    except Exception as exc:
        r3 = {"skipped": str(exc)[:120]}
    r6 = sitemap_policy()
    out = {
        "site": settings.site_url,
        "rule_1_structure": structure(),
        "rule_2_crawlability": crawlability(),
        "rule_3_indexing": r3,
        "rule_4_speed": speed_rules(),
        "rule_5_mobile": mobile_rules(),
        "rule_6_sitemap": r6,
    }
    rid = db.save_report("technical_audit", out)
    out["report_id"] = rid
    return out


def opportunities() -> list:
    """Technical-rule violations as work-queue items."""
    out = []
    try:
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT url, viewport, render_blockers, js_flag FROM pages WHERE status=200 LIMIT 400"
            ).fetchall()
        for r in rows:
            if r["viewport"] is None:
                continue
            if r["viewport"] == 0:
                out.append({
                    "type": "fix_mobile",
                    "target": f"viewport|{r['url']}",
                    "impact": 3,
                    "effort": 2,
                    "sources": ["crawler"],
                    "evidence": {"rule": "5", "issue": "no width=device-width viewport meta"},
                })
            if r["js_flag"] == "spa":
                out.append({
                    "type": "fix_crawlability",
                    "target": f"js_shell|{r['url']}",
                    "impact": 6,
                    "effort": 2,
                    "sources": ["crawler"],
                    "evidence": {"rule": "2", "issue": "JS app shell with almost no raw-HTML words - AI crawlers and Google skip it"},
                })
            elif r["js_flag"] == "next":
                out.append({
                    "type": "fix_crawlability",
                    "target": f"ssr_check|{r['url']}",
                    "impact": 4,
                    "effort": 1,
                    "sources": ["crawler"],
                    "evidence": {"rule": "2", "issue": "Next.js detected - confirm pages are server-rendered, not client-only"},
                })
            if (r["render_blockers"] or 0) > 6:
                out.append({
                    "type": "fix_speed",
                    "target": f"blockers|{r['url']}",
                    "impact": 2.5,
                    "effort": 2,
                    "sources": ["crawler"],
                    "evidence": {"rule": "4", "blockers": r["render_blockers"], "issue": "render-blocking head resources - main content loads late"},
                })
    except Exception:
        return out
    return out
