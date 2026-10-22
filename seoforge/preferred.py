from __future__ import annotations

import asyncio
import datetime as dt
import re
import urllib.parse
from urllib.parse import urljoin, urlsplit

import httpx

from . import db, indexing
from .config import settings

DOCS = "https://developers.google.com/search/docs/appearance/preferred-sources"
PUBLISHER_JS = "https://news.google.com/swg/js/v1/publisher.js"
PUBLISHER_MJS = "https://news.google.com/swg/js/v1/publisher.mjs"

ROI = "users click a Preferred Source ~2x more; badge shows in Top Stories + AI Overviews + AI Mode (600k+ sources picked)"


def embed_snippet(theme: str = "light", lang: str | None = None, mode: str = "standard") -> dict:
    """Copy-paste set. theme: light|dark. lang: override (defaults to page language)."""
    lang = lang or settings.default_language
    standard = (
        '<script async src="%s"></script>\n'
        '<div google-add-preferred-source-btn data-theme="%s" data-lang="%s"></div>' % (PUBLISHER_JS, theme, lang)
    )
    manual_js = (
        '<script async preferred-sources-control="manual" src="%s"></script>\n'
        "const PREFERRED_SOURCE_CB = globalThis.PREFERRED_SOURCE || [];\n"
        "// e.g. render when a custom button is clicked:\n"
        "// PREFERRED_SOURCE_CB.push({btn: myButtonElement, options: {theme: '%s', lang: '%s'}});\n"
        % (PUBLISHER_JS, theme, lang)
    )
    esm = (
        'import { preferredSource } from "%s";\n'
        "preferredSource.init({ theme: '%s', lang: '%s' });" % (PUBLISHER_MJS, theme, lang)
    )
    return {
        "domain_required": settings.site_url.split("//")[-1],
        "standard": standard,
        "manual_custom_trigger": manual_js,
        "esm_module": esm,
        "notes": [
            "button renders a Google-styled localized control; clicking adds the site to the reader's Google account preferred sources and returns them to the page",
            "add only on pages where readers commit (article footers, home) - the add flow requires a signed-in Google account on the reader's side",
        ],
    }


def _freshness(site: str) -> dict:
    """Preferred-sources eligibility leans on publishing fresh/regular content."""
    lastmods = []
    try:
        async def pull():
            async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=30, follow_redirects=True) as c:
                return await indexing._sitemap_urls(c, site)

        urls = asyncio.run(pull())
        async def mods():
            async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=30, follow_redirects=True) as c:
                r = await c.get(urljoin(site, "/sitemap.xml"))
                return r.text if r.status_code == 200 else ""
        xml = asyncio.run(mods())
        for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
            loc = re.search(r"<loc>\s*([^<\s]+)", block)
            lastmod = re.search(r"<lastmod>\s*([^<\s]+)", block)
            if loc and lastmod:
                lastmods.append((loc.group(1), lastmod.group(1)[:10]))
        with db._conn() as conn:
            newest_pages = [r[0] for r in conn.execute(
                "SELECT harvested_at FROM pages ORDER BY harvested_at DESC LIMIT 100").fetchall()]
    except Exception:
        newest_pages = []
        lastmods = []
    now = dt.date.today()
    fresh = [u for u, m in lastmods if m and (now - dt.date.fromisoformat(m)) <= dt.timedelta(days=30)]
    newest = max((m for _, m in lastmods), default=None)
    return {
        "sitemap_urls": len(lastmods),
        "updated_last_30_days": len(set(fresh)),
        "newest_lastmod": newest,
        "fresh_enough": bool(fresh) and bool(newest and (now - dt.date.fromisoformat(newest)) <= dt.timedelta(days=45)),
        "advice": "source preferences only surface sources that are updated regularly - keep a weekly publishing cadence (guides/blog/question pages qualify)",
    }


def _property_level(site: str) -> str | None:
    """Only domain-level and subdomain-level properties are eligible."""
    s = urlsplit(site)
    if s.path and s.path.strip("/") not in ("", "index.html"):
        return f"current site URL includes a path ({s.path}) - use a domain or subdomain root property"
    return None


def audit(site: str | None = None) -> dict:
    site = (site or settings.site_url).rstrip("/")
    warnings: list[str] = []
    prop_issue = _property_level(site)
    if prop_issue:
        warnings.append(prop_issue)
    fresh = _freshness(site)
    html = None
    try:
        r = httpx.get(site, headers={"User-Agent": "Mozilla/5.0"}, timeout=25, follow_redirects=True)
        html = r.text if r.status_code == 200 else None
    except Exception:
        pass
    button_present = bool(html) and (
        "google-add-preferred-source-btn" in html or "swg/js/v1/publisher" in html
    )
    snippet = embed_snippet()
    report = {
        "site": site.split("//")[-1],
        "docs": DOCS,
        "roi": ROI,
        "eligible": {
            "property_level_ok": prop_issue is None,
            "publishes_fresh_content": fresh["fresh_enough"],
            "freshness": fresh,
            "sgea_search_console": "action: check your site is included in 'Search generative AI features' in Search Console (Settings); only then does it show in the source-preferences tool",
            "button_on_site": button_present,
            "verify_tool": f"search '{site.split('//')[-1]}' in google.com > Profile > Search personalization > Source preferences",
        },
        "install": snippet,
        "checklist": [
            *(["register the site as a domain-level property in Search Console"] if prop_issue else []),
            "confirm inclusion in Search generative AI features (Search Console)",
            *(["embed the button - snippet below (footer of high-intent pages)"] if not button_present else ["replace placeholder with the official google-add-preferred-source-btn div"]),
            "keep weekly fresh content so the source stays available in the tool",
            "tell your audience: 'add us as a preferred source in Google' (email, WhatsApp, footer)",
        ],
        "warnings": warnings,
    }
    report["report_id"] = db.save_report("preferred_sources", report)
    return report
