from __future__ import annotations

import asyncio
import random
import re
import string
from urllib.parse import urljoin

import httpx

from . import db
from . import safe_fetch
from .config import settings

INDEXNOW = "https://api.indexnow.org/indexnow"


async def _sitemap_urls(client: httpx.AsyncClient | None, origin: str, depth: int = 0) -> list[str]:
    out: list[str] = []
    try:
        r = await safe_fetch.fetch(
            urljoin(origin, "/sitemap.xml"), accept=None, client=client, truncate=True, timeout=30
        )
    except safe_fetch.FetchError:
        return out
    if r.status != 200:
        return out
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
    if "<sitemapindex" in r.text[:200].lower() and depth < 2:
        for sm in locs[:50]:
            out += await _sitemap_urls(client, sm, depth + 1)
    else:
        out += locs
    return out


async def _run_crosscheck(site: str) -> dict:
    async def _robots_ok() -> bool:
        try:
            r = await safe_fetch.fetch(urljoin(site, "/robots.txt"), accept=None, truncate=True, timeout=30)
            return r.status == 200
        except safe_fetch.FetchError:
            return False

    robots_ok = await _robots_ok()
    sitemap_urls = await _sitemap_urls(None, site)
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT url, status, meta_robots, canonical, word_count FROM pages WHERE status IS NOT NULL"
        ).fetchall()
    crawled = {r["url"].rstrip("/"): dict(r) for r in rows}
    sitemap_norm = {u.rstrip("/"): u for u in sitemap_urls}
    problems: list[dict] = []

    for url in sitemap_norm:
        row = crawled.get(url)
        if row is None:
            continue
        if row["status"] != 200:
            problems.append({"severity": "high", "url": url, "issue": f"in sitemap but HTTP {row['status']}"})
        elif row["meta_robots"] and "noindex" in row["meta_robots"].lower():
            problems.append({"severity": "high", "url": url, "issue": "in sitemap but meta noindex"})
        can = (row["canonical"] or "").rstrip("/")
        if row["canonical"] and can != url:
            problems.append({"severity": "medium", "url": url, "issue": f"in sitemap but canonical -> {can}"})
        if (row["word_count"] or 0) < 100:
            problems.append({"severity": "low", "url": url, "issue": f"in sitemap but thin ({row['word_count']} words)"})

    crawled_html = {u for u, r_ in crawled.items() if r_["status"] == 200 and (r_["word_count"] or 0) or 0 > 0}
    not_in_sitemap = sorted(crawled_html - set(sitemap_norm))[:100]

    result = {
        "site": site,
        "sitemap_urls": len(sitemap_urls),
        "crawled_html": len(crawled_html),
        "summary": {sev: sum(1 for p in problems if p["severity"] == sev) for sev in ("high", "medium", "low")},
        "problems": sorted(problems, key=lambda p: {"high": 0, "medium": 1, "low": 2}[p["severity"]])[:200],
        "not_in_sitemap_preview": not_in_sitemap[:25],
        "robots_txt": robots_ok,
    }
    db.save_report("sitemap_crosscheck", result)
    return result


def crosscheck(site: str | None = None) -> dict:
    site = (site or settings.site_url).rstrip("/")
    return asyncio.run(_run_crosscheck(site))


def make_indexnow_key(site: str | None = None) -> dict:
    """Creates the key + the one-time file you upload at the site root."""
    site = (site or settings.site_url).rstrip("/")
    key = "".join(random.choices(string.ascii_lowercase + string.digits, k=32))
    host = site.split("//")[-1]
    return {"host": host, "key": key, "upload_to": f"https://{host}/{key}.txt", "content": key}


def indexnow_submit(urls: list[str], key: str | None = None) -> dict:
    if not key:
        kg = make_indexnow_key()
        return {
            "error": "no key provided - one-time setup",
            **kg,
            "next": f"1) upload a file at {kg['upload_to']} containing: {kg['key']} -> "
            f"2) rerun indexnow_submit(urls, key='{kg['key']}')",
        }
    host = urls[0].split("//")[-1].split("/")[0]
    payload = {
        "host": host,
        "keyLocation": f"https://{host}/{key}.txt",
        "key": key,
        "urlList": urls[:10000],
    }
    with httpx.Client(timeout=60) as c:
        r = c.post(
            INDEXNOW,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    codes = {200: "ok", 202: "accepted", 400: "bad payload", 403: "key file missing/wrong", 422: "url not on host"}
    return {"submitted": len(urls[:10000]), "http": r.status_code, "note": codes.get(r.status_code, "see indexnow docs")}


def ping_sitemap() -> dict:
    sm = f"{settings.site_url.rstrip('/')}/sitemap.xml"
    try:
        r = httpx.get("https://www.google.com/ping", params={"sitemap": sm}, timeout=30)
        code = r.status_code
    except Exception as exc:
        code = f"error: {exc}"[:120]
    return {"sitemap": sm, "google_ping": code}


BRAVE_SUBMIT_URL = "https://search.brave.com/submit-url"

# The three operator-documented facts plus the honest reporting boundary.
# Brave documents that its crawler advertises no user agent of its own, so
# no Bravebot entry can live in the bot registry: a fabricated token would
# make every bot-report attribution a fiction.
BRAVE_GUIDANCE = [
    "Brave's crawler publishes no user-agent token of its own; it crawls where Googlebot can - a page Google cannot crawl is not Brave-crawled either. Fix Google crawlability first, never fabricate a Bravebot rule.",
    "robots.txt gates crawling, not indexing: robots.txt is not used to keep a page OUT of Brave's index - the robots noindex directive is the delisting control, re-applied when Brave re-fetches the page (this form does exactly that).",
    "No Bravebot token will ever appear in the bot report: with no published user agent or IP ranges, an ingest hit cannot honestly be attributed to Brave.",
]


def brave_submit(urls: list[str] | None = None) -> dict:
    """Submit URLs to Brave Search for (re-)indexing through the one lane
    Brave documents: the submit-url form, filled by the browser agent when
    it is installed and answered as manual steps when it is not."""
    from . import browser_agent

    targets: list[str] = [str(u).strip() for u in (urls or []) if str(u).strip()]
    if not targets:
        targets = [str(settings.site_url).strip()]

    results = []
    for url in targets:
        if not re.match(r"^https?://[^\s]+$", url):
            results.append({"url": url, "status": "invalid", "error": "not an http(s) URL"})
        elif not browser_agent.available():
            results.append(
                {
                    "url": url,
                    "status": "manual",
                    "steps": f"open {BRAVE_SUBMIT_URL}, paste this URL, submit once",
                }
            )
        else:
            try:
                output = browser_agent.brave_submit_url(url)
                results.append({"url": url, "status": "submitted", "output": str(output)[:300]})
            except Exception as exc:
                results.append({"url": url, "status": "failed", "error": str(exc)[:200]})

    return {
        "platform": "brave",
        "submit_form": BRAVE_SUBMIT_URL,
        "urls": targets,
        "results": results,
        "guidance": list(BRAVE_GUIDANCE),
    }
