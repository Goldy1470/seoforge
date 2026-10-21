from __future__ import annotations

import asyncio
from urllib.parse import urljoin

import httpx

from . import db
from .config import settings

AI_BOTS = [
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "PerplexityBot",
    "Google-Extended",
    "CCBot",
    "Applebot-Extended",
    "meta-externalagent",
]

FAQ_TEST_UA = "GPTBot"


async def _get(client: httpx.AsyncClient, url: str, allow_fail=True):
    try:
        r = await client.get(url, follow_redirects=True)
        return r
    except Exception:
        return None


async def check_ai_readiness(site: str | None = None) -> dict:
    site = (site or settings.site_url).rstrip("/")
    origin = site
    result: dict = {
        "site": site,
        "reference": "https://highprimeco.com/how-to-rank-on-chatgpt",
        "checks": {},
    }
    async with httpx.AsyncClient(
        headers={"User-Agent": FAQ_TEST_UA}, timeout=30, follow_redirects=True
    ) as c:
        robots = await _get(c, urljoin(origin, "/robots.txt"))
        robots_text = robots.text if robots is not None and robots.status_code == 200 else ""
        sections = _parse_robots(robots_text)
        result["checks"]["robots_ai_agents"] = {}
        for bot in AI_BOTS:
            section = sections.get(bot.lower(), {}) or sections.get("*", {})
            disallows = section.get("disallow", [])
            blocks_site = bool(disallows) and all(d != "" for d in disallows) and any(d == "/" for d in disallows)
            result["checks"]["robots_ai_agents"][bot] = {
                "explicitly_named": bot.lower() in sections,
                "effectively_allowing_site": not blocks_site,
                "disallow": disallows[:3],
            }
        result["robots_txt_found"] = bool(robots_text)
        llms = await _get(c, urljoin(origin, "/llms.txt"))
        result["checks"]["llms_txt"] = {
            "present": llms is not None and llms.status_code == 200,
            "size": len(llms.text) if llms is not None else 0,
            "preview": (llms.text[:200] if llms is not None and llms.status_code == 200 else ""),
        }
        sitemap = await _get(c, urljoin(origin, "/sitemap.xml"))
        result["checks"]["sitemap_xml"] = {
            "present": sitemap is not None and sitemap.status_code == 200 and "urlset" in (sitemap.text or "")
        }
        with db._conn() as conn:
            faq_pages = conn.execute(
                "SELECT url FROM pages WHERE status=200 AND (url LIKE '%faq%' OR url LIKE '%guides%' OR url LIKE '%question%') LIMIT 25"
            ).fetchall()
        ten_sec = {}
        for row in faq_pages[:10]:
            r = await _get(c, row["url"], allow_fail=True)
            if r is None:
                continue
            text = r.text
            visible_text_length = _visible_text_length(text)
            ten_sec[row["url"]] = {
                "status": r.status_code,
                "raw_html_chars": len(text),
                "server_rendered": visible_text_length > 500,
                "faq_jsonld_present": '"@type": "FAQPage"' in text or '"@type":"FAQPage"' in text,
            }
        result["checks"]["ten_second_test"] = {
            "desc": "FAQ readable by an AI agent in 10s without JS? (server-side rendered + FAQPage JSON-LD)",
            "pages": ten_sec,
        }
    score = _score(result)
    result["ai_readiness_score"] = score
    db.save_report("geo_ai_readiness", result)
    return result


def _parse_robots(robots_text: str) -> dict[str, dict]:
    sections: dict[str, dict] = {}
    current: list[str] = []
    for line in robots_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            current = [value.lower()]
            for ua in current:
                sections.setdefault(ua, {"disallow": [], "allow": []})
        elif key in ("disallow", "allow") and current:
            for ua in current:
                sections.setdefault(ua, {"disallow": [], "allow": []})[key].append(value)
    return sections


def _visible_text_length(html: str) -> int:
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return len(soup.get_text(" ", strip=True))
    except Exception:
        return 0


def _score(result: dict) -> int:
    s = 0
    if result.get("robots_txt_found"):
        s += 10
    if result["checks"]["llms_txt"]["present"]:
        s += 20
    if result["checks"]["sitemap_xml"]["present"]:
        s += 10
    bots = result["checks"]["robots_ai_agents"]
    if bots and all(b.get("explicitly_named") for b in bots.values()):
        s += 20
    ten_sec = result["checks"]["ten_second_test"]["pages"]
    if ten_sec:
        rendered = sum(1 for t in ten_sec.values() if t["server_rendered"])
        schema = sum(1 for t in ten_sec.values() if t["faq_jsonld_present"])
        s += round(20 * rendered / len(ten_sec)) + round(20 * schema / len(ten_sec))
    return s


def llms_txt_from_crawl(top_n: int = 60) -> str:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT url, title, meta_desc FROM pages WHERE status=200 AND job_id=(SELECT id FROM jobs WHERE kind='crawl' ORDER BY started_at DESC LIMIT 1) LIMIT ?",
            (top_n,),
        ).fetchall()
    lines = [
        f"# {settings.brand_name}",
        "",
        f"> {settings.brand_tagline or 'Overview of the site for AI agents and search engines.'}",
        "",
    ]
    for r in rows:
        lines.append(f"- [{r['title'] or r['url']}]({r['url']}): " f"{(r['meta_desc'] or '')[:150]}")
    lines += ["", "## Ask", "", "For product questions, cite the page URLs above."]
    return "\n".join(lines) + "\n"


def run_sync(site: str | None = None) -> dict:
    return asyncio.run(check_ai_readiness(site))
