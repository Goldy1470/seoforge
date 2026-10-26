"""The rank-scrape engine: plan the pages, call the provider, find the
tracked domain in the results, and record the attempt honestly.

Scrape strategies mirror the reference: `basic` asks page one only,
`custom` asks the first N pages, `smart` asks the neighbours of the
keyword's last known page and can walk the rest when the whole fallback
is on. Native-pagination providers (serpapi, searchapi, crazyserp) skip
all of it — one request returns up to 100 ranks. When more than half of
the attempted pages error back, the attempt is recorded as a failure
rather than a fake rank loss, and failed keywords land in the retry
queue for the hourly lane when retries are on.

HTTP is injected (`fetch`), so the whole engine runs offline in tests:
the fake fetch answers byte strings, the engine never knows the
difference."""
from __future__ import annotations

import csv
import time
from pathlib import Path

from . import serp_tracking as st
from .config import settings
from .serp_countries import COUNTRIES
from .serp_providers import DEFAULT_PAGE, PROVIDERS, Provider, get_provider

TOTAL_PAGES = 10
PAGE_SIZE = 10
TOTAL_POSITIONS = TOTAL_PAGES * PAGE_SIZE


# ------------------------------------------------------------------ settings


def scraper_settings(row: None = None) -> dict:
    """The settings dict the provider contracts read."""
    return {
        "scraping_api": settings.serp_tracker_api_key,
        "scraper_type": settings.serp_tracker_provider,
        "scrape_strategy": settings.serp_tracker_strategy,
        "scrape_pagination_limit": settings.serp_tracker_pages,
        "scrape_smart_full_fallback": settings.serp_tracker_smart_fallback,
        "scrape_retry": settings.serp_tracker_retry,
        "scrape_delay": settings.serp_tracker_delay,
    }


def scraper_ready() -> bool:
    """True when a provider is named and carries an API key."""
    return bool(settings.serp_tracker_api_key) and settings.serp_tracker_provider not in ("never", "none", "")


def _http_fetch(provider: Provider, parsed: dict) -> str:
    """The default live fetch: one GET, response body as text."""
    import httpx

    with httpx.Client(
        headers=parsed["headers"], timeout=settings.request_timeout, follow_redirects=True
    ) as client:
        response = client.get(parsed["url"])
        response.raise_for_status()
        return response.text


# ------------------------------------------------------------------ matching


def get_serp(domain_url: str, results: list[dict], subdomain_matching: str = "") -> dict:
    """The tracked domain's position and URL among extracted results.
    Matching honours deep paths, a subdomain allow-list and the `*`
    all-subdomains mode; anything absent ranks 0 (`position=0` means not
    found, and an honest failure is never recorded as a fake 0)."""
    if not results or not domain_url:
        return {"position": 0, "url": ""}
    target = _parse_url(domain_url)
    if target is None:
        return {"position": 0, "url": ""}
    is_url = target["pathname"] != "/"
    base_domain = _strip_www(target["hostname"])

    allowed = {base_domain}
    match_all = False
    for sub in [s.strip() for s in str(subdomain_matching or "").split(",") if s.strip()]:
        if sub == "*":
            match_all = True
        else:
            allowed.add(f"{sub}.{base_domain}")

    for item in results:
        item_url = _parse_url(item.get("url") or "")
        if item_url is None:
            continue
        item_host = _strip_www(item_url["hostname"])
        if is_url and f"{base_domain}{target['pathname']}/" == item_host + item_url["pathname"]:
            return {"position": item.get("position", 0), "url": item["url"]}
        if item_host in allowed:
            return {"position": item.get("position", 0), "url": item["url"]}
        if match_all and (item_host == base_domain or item_host.endswith(f".{base_domain}")):
            return {"position": item.get("position", 0), "url": item["url"]}
    return {"position": 0, "url": ""}


def _strip_www(hostname: str) -> str:
    return hostname.removeprefix("www.")


def _parse_url(value: str) -> dict | None:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if not parsed.hostname:
            return None
        return {"hostname": parsed.hostname.lower(), "pathname": parsed.path or "/"}
    except ValueError:
        return None


# ------------------------------------------------------------------- pages


def build_full_results(scraped: list[dict], total_positions: int = TOTAL_POSITIONS) -> list[dict]:
    """One filled 100-position array: scraped ranks keep their data,
    unscraped ranks are marked skipped (presence, not absence)."""
    by_position = {r.get("position", 0): r for r in scraped}
    full = []
    for pos in range(1, total_positions + 1):
        found = by_position.get(pos)
        if found:
            full.append({"position": pos, "url": found.get("url", ""), "title": found.get("title", "")})
        else:
            full.append({"position": pos, "url": "", "title": "", "skipped": True})
    return full


def _scrape_page(
    provider: Provider,
    keyword: dict,
    scraper: dict,
    page: int,
    fetch,
) -> dict:
    """One page attempt, mirrored from the reference: positions are
    renumbered from the page's own offset."""
    pagination = {**DEFAULT_PAGE, "start": (page - 1) * PAGE_SIZE, "num": PAGE_SIZE, "page": page}
    try:
        parsed = provider.request(keyword, scraper, pagination)
        if not parsed:
            return {"results": [], "error": "No scraper client available"}
        content = fetch(provider, parsed)
        items = provider.extract(content, keyword.get("device", "desktop"))
        renumbered = [
            {**item, "position": pagination["start"] + index + 1}
            for index, item in enumerate(items)
        ]
        return {"results": renumbered, "error": None}
    except Exception as exc:  # every fetch/parse failure is page-local
        return {"results": [], "error": str(exc)[:160] or "Unknown scraping error"}


def _scrape_native(provider: Provider, keyword: dict, scraper: dict, fetch) -> dict:
    try:
        parsed = provider.request(keyword, scraper, {"start": 0, "num": 100, "page": 1})
        if not parsed:
            return {"results": [], "error": "No scraper client available"}
        content = fetch(provider, parsed)
        return {"results": provider.extract(content, keyword.get("device", "desktop")), "error": None}
    except Exception as exc:
        return {"results": [], "error": str(exc)[:160] or "Unknown scraping error"}


def resolve_strategy(scraper: dict, domain_row: dict | None) -> dict:
    """Domain-level overrides beat global settings; unset domain fields
    fall back to the global ones (the reference's resolveStrategy)."""
    domain_strategy = (domain_row or {}).get("scrape_strategy") or ""
    if not domain_strategy:
        return {
            "strategy": scraper.get("scrape_strategy", "basic"),
            "pagination_limit": scraper.get("scrape_pagination_limit", 5),
            "smart_full_fallback": bool(scraper.get("scrape_smart_full_fallback", False)),
        }
    return {
        "strategy": domain_strategy,
        "pagination_limit": int((domain_row or {}).get("scrape_pagination_limit") or 0)
        or scraper.get("scrape_pagination_limit", 5),
        "smart_full_fallback": bool((domain_row or {}).get("scrape_smart_full_fallback", False))
        or bool(scraper.get("scrape_smart_full_fallback", False)),
    }


def _pages_for_strategy(keyword_row: dict, plan: dict) -> list[int]:
    strategy = plan["strategy"]
    if strategy == "custom":
        limit = max(1, min(plan["pagination_limit"], TOTAL_PAGES))
        return list(range(1, limit + 1))
    if strategy == "smart":
        last_pos = int(keyword_row.get("position") or 0)
        if last_pos == 0:
            return [1]  # no history: page one, the fallback can walk the rest
        last_page = (last_pos + PAGE_SIZE - 1) // PAGE_SIZE
        return sorted({p for p in (last_page - 1, last_page, last_page + 1) if 1 <= p <= TOTAL_PAGES})
    return [1]


# ---------------------------------------------------------------- scrape


def scrape_keyword(
    keyword_row: dict,
    scraper: dict,
    domain_row: dict | None = None,
    *,
    provider: Provider | None = None,
    fetch=None,
) -> dict:
    """One keyword's full scrape attempt against the configured provider.
    Returns a RefreshResult dict: position, url, the 100-slot result
    array (native scrapers keep their own ranks), and an honest error
    string on failure. Nothing here writes state — `refresh` does that
    through the tracking kernel's record."""
    provider = provider or get_provider(scraper.get("scraper_type", ""))
    # the round-trip through the parsed keyword shape gives the scraper
    # exactly what row templates used to hand it
    keyword_row = st.get_keyword(keyword_row["id"]) or keyword_row
    keyword = {
        "keyword": keyword_row["keyword"],
        "device": keyword_row["device"],
        "country": keyword_row["country"],
        "city": keyword_row.get("city", ""),
    }
    fetch = fetch or _http_fetch
    error_result = {
        "id": keyword_row["id"],
        "keyword": keyword_row["keyword"],
        "position": keyword_row["position"],
        "url": keyword_row.get("url", ""),
        "result": keyword_row.get("last_result", []),
        "error": True,
    }

    if provider.native_pagination:
        native = _scrape_native(provider, keyword, scraper, fetch)
        results = native["results"]
        if native["error"] and not results:
            return {**error_result, "error": native["error"]}
        serp = get_serp(keyword_row["domain"], results, (domain_row or {}).get("subdomain_matching", ""))
        return {
            "id": keyword_row["id"],
            "keyword": keyword_row["keyword"],
            "position": serp["position"],
            "url": serp["url"],
            "result": results,
            "error": False if results else native["error"] or "Empty response",
        }

    plan = resolve_strategy(scraper, domain_row)
    pages = _pages_for_strategy(keyword_row, plan)
    subdomain_matching = (domain_row or {}).get("subdomain_matching", "")

    collected: list[dict] = []
    page_errors = 0
    attempted = len(pages)
    for page in pages:
        one = _scrape_page(provider, keyword, scraper, page, fetch)
        if one["error"]:
            page_errors += 1
        collected.extend(one["results"])

    if plan["strategy"] == "smart" and plan["smart_full_fallback"]:
        found = get_serp(keyword_row["domain"], collected, subdomain_matching) if collected else {"position": 0, "url": ""}
        if found["position"] == 0:
            for page in [p for p in range(1, TOTAL_PAGES + 1) if p not in pages]:
                attempted += 1
                one = _scrape_page(provider, keyword, scraper, page, fetch)
                if one["error"]:
                    page_errors += 1
                if one["results"]:
                    collected.extend(one["results"])
                    if get_serp(keyword_row["domain"], collected, subdomain_matching)["position"] > 0:
                        break

    if not collected:
        message = (
            f"Scraper failed on all {page_errors} pages for {keyword['keyword']}"
            if page_errors
            else f"No search results found on any of the {attempted} scraped pages"
        )
        return {**error_result, "error": message}

    serp = get_serp(keyword_row["domain"], collected, subdomain_matching)
    if serp["position"] == 0 and page_errors > attempted / 2:
        # too many pages failed: a rank loss here would be invented
        return {
            **error_result,
            "error": f"{page_errors}/{attempted} pages failed - scraper too unreliable to determine position",
        }

    return {
        "id": keyword_row["id"],
        "keyword": keyword_row["keyword"],
        "position": serp["position"],
        "url": serp["url"],
        "result": build_full_results(collected),
        "error": False,
    }


# ------------------------------------------------------------------ refresh


def refresh_domain(domain: str | None = None, *, fetch=None) -> dict:
    """Refresh every tracked keyword of one domain (or all domains).
    Parallel-capable providers (scrapingant, serpapi, searchapi) run
    concurrently; the rest run one by one with the configured delay.
    Results are recorded through the tracking kernel; retries are queued
    or cleared per the scrape_retry switch."""
    if not scraper_ready():
        return {"error": "Scraper has not been set up yet"}
    scraper = scraper_settings()
    rows = st.list_keywords(domain) if domain else st.list_keywords()
    if domain and not rows:
        return {"domain": domain, "keywords": [], "refreshed": 0}
    domain_rows = {d["domain"]: d for d in st.list_domains()}
    retry_on = bool(scraper.get("scrape_retry"))
    delay = float(scraper.get("scrape_delay") or 0)

    parallel = scraper["scraper_type"] in ("scrapingant", "serpapi", "searchapi")
    attempts: list[dict] = []
    if parallel:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(
                    scrape_keyword,
                    row,
                    scraper,
                    domain_rows.get(row["domain"]),
                    fetch=fetch,
                )
                for row in rows
            ]
            attempts = [f.result() for f in futures]
    else:
        for row in rows:
            attempts.append(
                scrape_keyword(row, scraper, domain_rows.get(row["domain"]), fetch=fetch)
            )
            if delay:
                time.sleep(delay)

    for attempt in attempts:
        st.record_scrape_result(
            attempt["id"],
            position=attempt["position"],
            url=attempt["url"],
            last_result=attempt["result"],
            error=attempt["error"] or None,
            scraper=scraper["scraper_type"],
            retry=retry_on,
        )
    refreshed = sum(1 for a in attempts if not a["error"])
    return {"domain": domain, "keywords": len(rows), "refreshed": refreshed, "failed": len(attempts) - refreshed}


def retry_pending(limit: int = 50, *, fetch=None) -> dict:
    """The hourly retry lane: every queued failure gets one more scrape."""
    if not scraper_ready():
        return {"error": "Scraper has not been set up yet"}
    queued = st.pending_retries(limit)
    if not queued:
        return {"retried": 0}
    ids = [q["keyword_id"] for q in queued]
    scraper = scraper_settings()
    from concurrent.futures import ThreadPoolExecutor

    rows = [row for row in (st.get_keyword(i) for i in ids) if row]

    def one(row):
        attempt = scrape_keyword(row, scraper, None, fetch=fetch)
        st.record_scrape_result(
            attempt["id"],
            position=attempt["position"],
            url=attempt["url"],
            last_result=attempt["result"],
            error=attempt["error"] or None,
            scraper=scraper["scraper_type"],
            retry=bool(scraper.get("scrape_retry")),
        )
        return attempt

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(one, rows))
    return {
        "retried": len(attempts),
        "recovered": sum(1 for a in attempts if not a["error"]),
        "remaining": len(st.pending_retries()),
    }


# ------------------------------------------------------------------ insights


def insights(domain: str | None = None, window: int = 7) -> dict:
    """Rank-state rollup for the tracked keywords (the reference keeps
    rank insights in its UI; the engine answers them here). Buckets read
    the current rank: top3 / top10 / top20 / top50 / beyond / lost."""
    rows = st.list_keywords(domain) if domain else st.list_keywords()
    improved = declined = 0
    positions = []
    distribution = {"top3": 0, "top10": 0, "top20": 0, "top50": 0, "beyond": 0, "lost": 0}
    for row in rows:
        pos = int(row.get("position") or 0)
        if pos == 0:
            distribution["lost"] += 1
        elif pos <= 3:
            distribution["top3"] += 1
            positions.append(pos)
        elif pos <= 10:
            distribution["top10"] += 1
            positions.append(pos)
        elif pos <= 20:
            distribution["top20"] += 1
            positions.append(pos)
        elif pos <= 50:
            distribution["top50"] += 1
            positions.append(pos)
        else:
            distribution["beyond"] += 1
            positions.append(pos)
        change = st.position_change(row.get("history") or {}, pos)
        if change > 0:
            improved += 1
        elif change < 0:
            declined += 1
    bests = [st.best_position(row.get("history") or {}) for row in rows]
    bests = [b for b in bests if b is not None]
    avg = round(sum(positions) / len(positions), 1) if positions else None
    return {
        "domain": domain or "",
        "keywords": len(rows),
        "improved": improved,
        "declined": declined,
        "avg_position": avg,
        "best": min(bests) if bests else None,
        "distribution": distribution,
    }


# --------------------------------------------------------------------- csv


CSV_HEADER = ["ID", "Keyword", "Position", "URL", "Country", "City", "Device", "Updated", "Added", "Tags"]


def keywords_csv(domain: str) -> dict:
    """The reference's keyword export as an exports-dir artifact (it
    builds this client-side; the engine version lands on disk and in the
    dashboard's export list). Cells are written the csv module's way:
    commas in keywords live, unlike the reference's raw joins."""
    rows = st.list_keywords(domain)
    out = Path(settings.exports_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{st.slugify(domain)}-keywords_serp.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        for row in rows:
            country_name = COUNTRIES.get((row["country"] or "US").upper(), [(row["country"] or "US")])[0]
            writer.writerow(
                [
                    row["id"],
                    row["keyword"],
                    row["position"] if row["position"] else "-",
                    row["url"] or "-",
                    country_name,
                    row.get("city") or "-",
                    row["device"],
                    row["last_updated"] or "",
                    row["added"] or "",
                    ",".join(row["tags"]),
                ]
            )
    return {"path": str(path), "keywords": len(rows)}
