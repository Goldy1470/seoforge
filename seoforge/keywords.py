from __future__ import annotations

import base64
from urllib.parse import quote

import httpx

from .config import settings

DFS = "https://api.dataforseo.com/v3"


def _dfs_client() -> httpx.AsyncClient:
    if not (settings.dataforseo_login and settings.dataforseo_password):
        raise SystemExit("Set DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD (sign up at dataforseo.com)")
    auth = base64.b64encode(
        f"{settings.dataforseo_login}:{settings.dataforseo_password}".encode()
    ).decode()
    return httpx.AsyncClient(
        base_url=DFS, headers={"Authorization": f"Basic {auth}"}, timeout=120
    )


async def keyword_suggestions(keyword: str, location: int | None = None) -> list[dict]:
    async with _dfs_client() as c:
        r = await c.post(
            "/dataforseo_labs/google/keyword_ideas/live",
            json=[
                {
                    "search_seed_keyword": keyword,
                    "search_seed_keyword_type": "seed",
                    "location_code": location or 2840,
                    "language_code": "en",
                    "include_serp_info": True,
                }
            ],
        )
        r.raise_for_status()
        data = r.json()
    tasks = data.get("tasks") or []
    items = (tasks[0].get("result", [{}]) or [{}])[0].get("items", []) if tasks else []
    out = []
    for it in items:
        kw = it.get("keyword", "")
        info = it.get("keyword_info") or {}
        out.append(
            {
                "keyword": kw,
                "volume": info.get("search_volume"),
                "competition": info.get("competition"),
                "cpc": info.get("cpc"),
                "difficulty": it.get("serp_info", {}).get("ranked_organic_metrics", {}).get("etv")
                or it.get("competitive_rate"),
            }
        )
    return out


async def serp(keyword: str, location: int | None = None, **opts) -> dict:
    async with _dfs_client() as c:
        r = await c.post(
            "/serp/google/organic/live/advanced",
            json=[
                {
                    "keyword": keyword,
                    "location_code": location or 2840,
                    "language_code": "en",
                    **opts,
                }
            ],
        )
        r.raise_for_status()
        data = r.json()
    tasks = data.get("tasks") or []
    result = ((tasks[0].get("result") or [{}])[0] if tasks else {}).get("items") or []
    organic = []
    paa = []
    for item in result:
        t = item.get("type")
        if t == "organic" and item.get("rank_group", 99) <= 20:
            organic.append(
                {
                    "rank": item.get("rank_group"),
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "domain": item.get("domain"),
                }
            )
        elif t == "people_also_ask":
            for it in item.get("items", []) or item.get("people_also_ask", []):
                q = it.get("expanded_element", {}).get("expanded_element")
                paa.append(q)
    _snapshot(keyword, organic)
    return {"organic": organic, "people_also_ask": paa}


def _snapshot(keyword: str, organic: list[dict]) -> None:
    import asyncio as _a

    from . import db

    with db._conn() as conn:
        conn.executemany(
            "INSERT INTO rank_snapshots(keyword,domain,position,url,searched_at) VALUES (?,?,?,?,?)",
            [
                (
                    keyword,
                    o.get("domain"),
                    o.get("rank"),
                    o.get("url"),
                    db.now(),
                )
                for o in organic
            ],
        )


def rank_history(keyword: str | None = None, domain: str | None = None, limit: int = 500) -> list[dict]:
    from . import db

    from .config import settings as _s

    with db._conn() as conn:
        if keyword and domain:
            rows = conn.execute(
                "SELECT * FROM rank_snapshots WHERE keyword=? AND domain=? ORDER BY searched_at DESC LIMIT ?",
                (keyword, domain.removeprefix("www."), limit),
            ).fetchall()
        elif keyword:
            rows = conn.execute(
                "SELECT * FROM rank_snapshots WHERE keyword=? ORDER BY searched_at DESC LIMIT ?",
                (keyword, limit),
            ).fetchall()
        elif domain:
            rows = conn.execute(
                "SELECT * FROM rank_snapshots WHERE domain=? ORDER BY searched_at DESC LIMIT ?",
                (domain.removeprefix("www."), limit),
            ).fetchall()
        else:
            mine = _s.site_url.split("/")[2].removeprefix("www.")
            rows = conn.execute(
                "SELECT * FROM rank_snapshots WHERE domain=? ORDER BY searched_at DESC LIMIT ?",
                (mine, limit),
            ).fetchall()
    return [dict(r) for r in rows]


async def backlinks_for(domain: str, limit: int = 100) -> list[dict]:
    async with _dfs_client() as c:
        r = await c.post(
            "/backlinks/backlinks/live",
            json=[{"target": domain, "limit": limit, "exclude_internal_backlinks": True}],
        )
        r.raise_for_status()
        data = r.json()
    tasks = data.get("tasks") or []
    items = ((tasks[0].get("result") or [{}]) if tasks else [{}])[0].get("items", [])
    return [
        {
            "from_domain": it.get("domain_from"),
            "from_url": it.get("url_from"),
            "to_url": it.get("url_to"),
            "anchor": (it.get("anchor") or "")[:120],
            "do_follow": it.get("dofollow"),
            "dr": it.get("domain_info", {}).get("rank"),
            "rank": it.get("rank"),
        }
        for it in items
    ]


async def domain_metrics(domain: str) -> dict:
    async with _dfs_client() as c:
        r = await c.post(
            "/backlinks/summary/live",
            json=[{"target": domain, "include_subdomains": True}],
        )
        raise_for(r)
        data = r.json()
    result = (((data.get("tasks") or [{}])[0]).get("result") or [{}])[0]
    pages_info = result.get("page_summary", {})
    bl_info = result.get("backlinks", {})
    return {
        "domain_rank": bl_info.get("rank"),
        "backlinks": bl_info.get("total"),
        "referring_domains": bl_info.get("referring_links_and_domains")
        or bl_info.get("referring_domains"),
        "dofollow_domains": bl_info.get("referring_links_and_domains_dofollow"),
        "keywords": result.get("organic_keywords"),
    }


def raise_for(resp: httpx.Response):
    resp.raise_for_status()


async def openseo_passthrough(endpoint: str = "status", params: dict | None = None) -> dict:
    """Live OpenSEO integration status (hosted MCP / API key / self-host on Cloudflare or Docker)."""
    from . import openseo

    return openseo.status()


def competitor_gap(keyword: str, competitor_domain: str) -> dict:
    import asyncio

    async def run():
        s = await serp(keyword)
        kw = await keyword_suggestions(keyword)
        return {"serp": s, "keyword_ideas": kw[:25], "competitor_domain": competitor_domain}

    return asyncio.run(run())
