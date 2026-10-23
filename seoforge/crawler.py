from __future__ import annotations

import asyncio
import datetime as _dt
import json
import re
import urllib.robotparser as robotparser
from collections import Counter, deque
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from . import db
from .config import settings

_R_STATUS_PASS = 200


def _norm(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path or "/", "", ""))


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _registrable(url: str) -> str:
    host = _host(url)
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


async def _load_sitemaps(client: httpx.AsyncClient, origin: str) -> list[str]:
    urls: list[str] = []

    async def parse(sm):
        try:
            r = await client.get(sm)
        except Exception:
            return
        if r.status_code != 200:
            return
        if "<urlset" in r.text[:200]:
            for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", r.text):
                urls.append(_norm(m.group(1)))
        elif "<sitemapindex" in r.text[:200]:
            for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", r.text):
                locs.append(_norm(m.group(1)))

    locs: list[str] = []
    robots = await _fetch_robots(client, origin)
    sitemap_paths = re.findall(r"sitemap:\s*(\S+)", robots)
    if not sitemap_paths:
        sitemap_paths = [urljoin(origin, "/sitemap.xml")]
    for sm in sitemap_paths[:20]:
        if sm.endswith(".xml"):
            await parse(sm)
            for nested in locs[:15]:
                if nested not in sitemap_paths:
                    sitemap_paths.append(nested)
    return list(dict.fromkeys(urls))


async def _fetch_robots(client: httpx.AsyncClient, origin: str) -> str:
    try:
        r = await client.get(urljoin(origin, "/robots.txt"))
        return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


def _parse_page(url: str, text: str, base: str) -> dict:
    soup = BeautifulSoup(text, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    robots_tag = soup.find("meta", attrs={"name": "robots"})
    canonical = soup.find("link", rel="canonical")
    h1 = " | ".join(h.get_text(" ", strip=True) for h in soup.find_all("h1")[:2])
    headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h2", "h3"])][:10]
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    first_text = text[:400]
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a.get("href", "")).split("#")[0]
        if href.startswith(("http://", "https://")):
            links.append(
                {
                    "href": _norm(href),
                    "anchor": a.get_text(" ", strip=True)[:120],
                    "rel": " ".join(a.get("rel", [])) or "",
                }
            )
    imgs = soup.find_all("img")
    missing_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())
    jsonld_types = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "{}")
            items = data if isinstance(data, list) else [data]
            jsonld_types += [
                d.get("@type", "?") for d in items if isinstance(d, dict)
            ]
        except Exception:
            jsonld_types.append("INVALID")
    return {
        "title": title,
        "meta_desc": (meta.get("content") or "").strip() if meta else "",
        "meta_robots": robots_tag.get("content", "") if robots_tag else "",
        "canonical": (
            urljoin(base, canonical.get("href", "")).split("#")[0] if canonical else ""
        ),
        "h1": h1,
        "headings": headings,
        "first_text": first_text,
        "word_count": len(text.split()),
        "links": links,
        "images_missing_alt": missing_alt,
        "jsonld": jsonld_types,
    }


async def _fetch(client: httpx.AsyncClient, url: str, rp: robotparser.RobotFileParser):
    if rp and not rp.can_fetch(settings.user_agent, url):
        return None, 0, 0
    t0 = asyncio.get_event_loop().time()
    resp = None
    size = 0
    try:
        resp = await client.get(url, follow_redirects=True)
        size = len(resp.content)
    except Exception:
        pass
    ms = int((asyncio.get_event_loop().time() - t0) * 1000)
    return resp, ms, size


async def crawl_site(start_url: str, job_id: str | None = None) -> dict:
    start = _norm(start_url or settings.site_url)
    origin = urljoin(start, "/")
    reg_domain = _registrable(start)
    job_id = job_id or db.start_job("crawl", {"start": start})

    sem = asyncio.Semaphore(settings.max_concurrency)
    rp = robotparser.RobotFileParser()
    rp.set_url(urljoin(origin, "/robots.txt"))
    try:
        rp.read()
    except Exception:
        rp = None

    async with httpx.AsyncClient(
        timeout=settings.request_timeout,
        headers={"User-Agent": settings.user_agent},
        verify=True,
        http2=True,
    ) as client:
        sitemap_urls = await _load_sitemaps(client, origin)
        queue = deque([(start, 0)])
        seen = {u for u in sitemap_urls[: settings.max_pages] if _registrable(u) == reg_domain}
        queue.extendleft((u, 0) for u in reversed(sitemap_urls[: settings.max_pages]))
        pages: dict[str, dict] = {}
        edges: list[tuple[str, str, str]] = []
        status_counter: Counter = Counter()

        async def worker():
            while queue and len(pages) < settings.max_pages:
                url, depth = queue.popleft()
                if url in pages or depth > settings.max_depth:
                    continue
                async with sem:
                    resp, ms, size = await _fetch(client, url, rp)
                if resp is None:
                    pages[url] = {"status": -1, "depth": depth}
                    status_counter["robot-blocked"] += 1
                    continue
                status_counter[resp.status_code] += 1
                rec = {
                    "status": resp.status_code,
                    "depth": depth,
                    "response_ms": ms,
                    "size_bytes": size,
                    "final_url": _norm(str(resp.url)),
                }
                if (
                    resp.status_code == _R_STATUS_PASS
                    and "text/html" in resp.headers.get("content-type", "")
                ):
                    rec.update(_parse_page(url, resp.text, str(resp.url)))
                    rec["links_out"] = len(rec.get("links", []))
                    for l in rec.get("links", []):
                        edges.append((url, l["href"], l["anchor"]))
                        tgt = l["href"]
                        if (
                            _registrable(tgt) == reg_domain
                            and tgt not in pages
                            and (tgt, depth + 1) not in queue
                            and len(pages) + len(queue) < settings.max_pages
                        ):
                            queue.append((tgt, depth + 1))
                        if tgt not in seen:
                            seen.add(tgt)
                pages[url] = rec

        await asyncio.gather(*[worker() for _ in range(settings.max_concurrency)])

    summary = _summarize(pages, edges)
    _persist(job_id, pages, edges, summary)
    db.finish_job(job_id, summary)
    return {"job_id": job_id, "pages": len(pages), "summary": summary}


def _summarize(pages: dict, edges) -> dict:
    n = max(len(pages), 1)
    html_pages = {u: r for u, r in pages.items() if r.get("status") == 200 and r.get("title") is not None}
    titles = Counter(r["title"].lower() for r in html_pages.values() if r.get("title"))
    descs = Counter(r["meta_desc"] for r in html_pages.values() if r.get("meta_desc"))
    m = {
        "total_urls": len(pages),
        "status": dict(Counter(str(r["status"]) for r in pages.values())),
        "missing_title": sum(1 for r in html_pages.values() if not r.get("title")),
        "missing_meta_desc": sum(1 for r in html_pages.values() if not r.get("meta_desc")),
        "long_title": sum(1 for r in html_pages.values() if len(r.get("title", "")) > 60),
        "long_meta_desc": sum(1 for r in html_pages.values() if len(r.get("meta_desc", "")) > 155),
        "duplicate_titles": {t: c for t, c in titles.most_common(20) if c > 1},
        "duplicate_meta_desc": {d: c for d, c in descs.most_common(20) if c > 1},
        "missing_h1": sum(1 for r in html_pages.values() if not r.get("h1")),
        "noindex": [u for u, r in pages.items() if "noindex" in (r.get("meta_robots") or "")],
        "noncanonical": [
            u for u, r in html_pages.items() if r.get("canonical") and r["canonical"] != u
        ],
        "thin_content": [u for u, r in html_pages.items() if (r.get("word_count") or 0) < 250],
        "images_missing_alt_total": sum((r.get("images_missing_alt") or 0) for r in html_pages.values()),
        "avg_response_ms": round(sum((r.get("response_ms") or 0) for r in pages.values()) / n),
        "jsonld_pages": sum(1 for r in html_pages.values() if r.get("jsonld")),
        "h1_mismatch": sum(
            1 for r in html_pages.values() if r.get("h1") and r.get("title")
            and _norm_text(r["h1"])[:90] != _norm_text(r["title"])[:90]
        ),
        "anchors": len(edges),
    }
    return m


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


SQL_UPSERT_PAGE = """INSERT INTO pages(job_id,url,status,depth,title,meta_desc,h1,canonical,
   meta_robots,word_count,links_out,response_ms,size_bytes,jsonld,
   images_missing_alt,headings,first_text,harvested_at)
   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
   ON CONFLICT(url) DO UPDATE SET
     job_id=excluded.job_id, status=excluded.status, depth=excluded.depth,
     title=excluded.title, meta_desc=excluded.meta_desc, h1=excluded.h1,
     canonical=excluded.canonical, meta_robots=excluded.meta_robots,
     word_count=excluded.word_count, links_out=excluded.links_out,
     response_ms=excluded.response_ms, size_bytes=excluded.size_bytes,
     jsonld=excluded.jsonld, images_missing_alt=excluded.images_missing_alt,
     headings=excluded.headings, first_text=excluded.first_text,
     harvested_at=excluded.harvested_at"""


def _persist(job_id: str, pages: dict, edges, summary: dict) -> None:
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    with db._conn() as conn:
        for url, r in pages.items():
            conn.execute(
                SQL_UPSERT_PAGE,
                (
                    job_id,
                    url,
                    r.get("status"),
                    r.get("depth"),
                    r.get("title"),
                    r.get("meta_desc"),
                    r.get("h1"),
                    r.get("canonical"),
                    r.get("meta_robots"),
                    r.get("word_count"),
                    r.get("links_out"),
                    r.get("response_ms"),
                    r.get("size_bytes"),
                    json.dumps(r.get("jsonld")),
                    r.get("images_missing_alt"),
                    json.dumps(r.get("headings")),
                    r.get("first_text"),
                    stamp,
                ),
            )
        conn.executemany(
            "INSERT INTO links(job_id,source,target,anchor,rel) VALUES (?,?,?,?,?)",
            [(job_id, s, t, a, "") for s, t, a in edges],
        )
