from __future__ import annotations

import asyncio
import json
import sys

from fastmcp import FastMCP

from .config import settings

mcp = FastMCP(
    "SEOForge",
    instructions=(
        "End-to-end SEO SaaS: technical crawl, Google Search Console, PAA question-pages, "
        "backlinks (73 directories + digital PR), internal linking, faceted navigation, "
        "review/product/FAQ schema, Merchant Center feed, AI-visibility (GEO), browser agent."
    ),
)


@mcp.tool()
def crawl_site(url: str = "") -> dict:
    """Crawl a site like Screaming Frog: titles, metas, canonicals, status, links, JSON-LD, speed."""
    from . import crawler

    return asyncio.run(crawler.crawl_site(url or settings.site_url))


@mcp.tool()
def seo_audit(url: str = "", with_gsc: bool = True) -> dict:
    """Full technical onboarding audit: crawl + facets + internal links + AI readiness + GSC."""
    from . import orchestrator

    return orchestrator.seo_audit(url or None, with_gsc)


@mcp.tool()
def gsc_auth() -> str:
    """One-time Google Search Console OAuth login (opens browser)."""
    from . import gsc

    return gsc.run_oauth_flow()


@mcp.tool()
def gsc_striking_distance(min_pos: int = 5, max_pos: int = 20, min_impressions: int = 50) -> list:
    """Queries ranking 5-20 with impressions - the shortlist for PAA/content work."""
    from . import gsc

    return gsc.striking_distance(min_pos, max_pos, min_impressions)


@mcp.tool()
def gsc_cannibalization() -> list:
    """Multiple URLs ranking for one query."""
    from . import gsc

    return gsc.cannibalization()


@mcp.tool()
def gsc_low_ctr(min_impressions: int = 500, max_ctr: float = 0.02) -> list:
    """High impression + low CTR = fix title/meta description."""
    from . import gsc

    return gsc.low_ctr(min_impressions, max_ctr)


@mcp.tool()
def gsc_inspect(url: str) -> dict:
    """Index status for a URL (urlInspection API)."""
    from . import gsc

    return gsc.inspect_url(url)


@mcp.tool()
def gsc_pages(days: int = 90) -> list:
    """Per-page GSC table: clicks, impressions, CTR, position."""
    from . import gsc

    return gsc.pages(days)


@mcp.tool()
def competitor_gap(keyword: str, competitor_domain: str = "") -> dict:
    """SERP + keyword ideas for a keyword, framed against one competitor domain."""
    from . import keywords

    return keywords.competitor_gap(keyword, competitor_domain)


@mcp.tool()
def rank_history(keyword: str = "", domain: str = "") -> list:
    """Historical rank snapshots (google_serp saves a snapshot on every run)."""
    from . import keywords

    return keywords.rank_history(keyword or None, domain or None)


@mcp.tool()
def core_web_vitals(url: str = "", desktop: bool = False) -> dict:
    """PageSpeed Insights mobile (+desktop) run: PSI score, LCP/TBT/CLS, CrUX field data, fix hints."""
    from . import perf

    return perf.cwv(url or settings.site_url, desktop)


@mcp.tool()
def indexing_push(urls: list[str], indexnow_key: str = "") -> dict:
    """Submit new/updated URLs to search engines: IndexNow (+ Bing/Yandex/Seznam) with sitemap ping."""
    from . import indexing

    out = {"google_sitemap_ping": indexing.ping_sitemap()}
    if urls:
        out["indexnow"] = indexing.indexnow_submit(urls, indexnow_key or None)
    return out


@mcp.tool()
def keyword_ideas(seed: str) -> list:
    """Keyword ideas + volume from DataForSEO."""
    from . import keywords

    return asyncio.run(keywords.keyword_suggestions(seed))


@mcp.tool()
def google_serp(keyword: str) -> dict:
    """Google organic SERP + People Also Ask for a keyword."""
    from . import keywords

    return asyncio.run(keywords.serp(keyword))


@mcp.tool()
def backlink_gap(domain: str = "") -> dict:
    """Your live backlinks + domain metrics (DataForSEO)."""
    from . import backlinks

    return backlinks.gap(domain or None)


@mcp.tool()
def openseo_status() -> dict:
    """Live OpenSEO integration status: hosted MCP / API key / self-host mode + costs."""
    from . import openseo

    return openseo.status()


@mcp.tool()
def openseo_setup(include_skills: bool = True) -> dict:
    """Paste-ready commands to attach OpenSEO MCP+skills to Codex (or Cursor/Claude variants)."""
    from . import openseo

    return openseo.setup_codex(include_skills)


@mcp.tool()
def openseo_ping() -> dict:
    """Live MCP handshake against OpenSEO (uses no SEO credits)."""
    from . import openseo

    return asyncio.run(openseo.ping())


@mcp.tool()
def openseo_skills() -> list:
    """The 12 official OpenSEO skills and what each feeds back into seoforge."""
    from . import openseo

    return openseo.skills_map()


@mcp.tool()
def openseo_selfhost() -> dict:
    """Self-host OpenSEO for $0: Cloudflare Workers path (the documented one; not Vercel) or Docker."""
    from . import openseo

    return openseo.selfhost_guide()


@mcp.tool()
def aeo_funnel(category_id: str = "") -> dict:
    """GEO/AEO funnel per category: be found -> answered -> cited -> recommended -> chosen. Scores + fixes."""
    from . import aeo

    return aeo.funnel_audit(category_id or None)


@mcp.tool()
def aeo_matrix() -> list:
    """Funnel scores for every category in data/aeo_categories.json."""
    from . import aeo

    return aeo.funnel_matrix()


@mcp.tool()
def aeo_llms_full() -> str:
    """Generate llms-full.txt (index + corpus) from the crawl DB - llms.txt alone is outdated."""
    from . import aeo

    return aeo.llms_full_txt_from_crawl()


@mcp.tool()
def value_vs_semrush(openseo_hosted: bool = False, markdown: bool = False) -> dict | str:
    """Semrush $139.95/mo vs the SEOForge stack: module coverage matrix + cost model."""
    from . import value

    report = value.value_report(openseo_hosted)
    return value.render_markdown(report) if markdown else report


@mcp.tool()
def paa_tree(seeds: list[str], max_depth: int = 2) -> dict:
    """Expand People Also Ask boxes into a question tree (how/what/best/vs)."""
    from . import paa

    return asyncio.run(paa.build_paa_tree(seeds, max_depth))


@mcp.tool()
def question_page_plan(min_questions: int = 2) -> dict:
    """Turn the PAA tree into question-pages to build (path, title, meta, schema, interlinks)."""
    from . import paa

    plan = paa.generate_build_list(limit=500)
    pages = [p for p in plan["pages"] if p["questions"] >= min_questions]
    return {"count": len(pages), "pages": pages}


@mcp.tool()
def paa_export() -> str:
    """Export the PAA build-list CSV."""
    from . import paa

    return paa.export_paa_csv()


@mcp.tool()
def page_scaffolds(min_questions: int = 3, limit: int = 15) -> dict:
    """Write one markdown brief + FAQPage JSON-LD template per question page (Codex fills copy)."""
    from . import paa

    return paa.page_scaffold(min_questions, limit)


@mcp.tool()
def sitemap_crosscheck(site: str = "") -> dict:
    """Sitemap vs crawl DB: 404/noindex/thin/canonical drift, pages missing from sitemap."""
    from . import indexing

    return indexing.crosscheck(site or None)


@mcp.tool()
def ai_citation_check(questions: list[str]) -> dict:
    """Ask a frontier LLM buying questions; count how often it cites your brand/domain (GEO)."""
    from . import geo

    return geo.ai_citation_check(questions)


@mcp.tool()
def internal_links_report() -> dict:
    """Orphans, dead links, PageRank top pages, TF-IDF link suggestions."""
    from . import internal_links

    return internal_links.build_report()


@mcp.tool()
def faceted_navigation_report() -> dict:
    """Classify crawl params: index / noindex-follow / ignore + robots.txt snippet."""
    from . import facets

    return facets.analyze()


@mcp.tool()
def backlink_directories(status: str = "", min_dr: int = 0) -> list:
    """The 73 startup backlink places with DR + submission status."""
    from . import backlinks

    return backlinks.list_directories(status or None, min_dr)


@mcp.tool()
def backlink_update(name: str, status: str, notes: str = "") -> str:
    """Update a directory row: todo|drafting|submitted|live|rejected|skipped."""
    from . import backlinks

    return backlinks.update_status(name, status, notes or None)


@mcp.tool()
def digital_pr_plan() -> list:
    """Digital PR tactics: unlinked mentions, journalists, data assets, podcast circuit."""
    from . import backlinks

    return backlinks.digital_pr_plan()


@mcp.tool()
def merchant_center_feed(csv_path: str = "") -> str:
    """Build/refresh the Google Merchant Center XML feed (import CSV first if given)."""
    from . import merchant

    if csv_path:
        merchant.load_products_csv(csv_path)
    return merchant.build_feed()


@mcp.tool()
def add_product(product_json: str) -> str:
    """Add one product for merchant feed + product schema. e.g. {"id","title","price","link",...}."""
    from . import merchant

    return merchant.upsert_product_json(product_json)


@mcp.tool()
def faq_schema(qa_json: str) -> str:
    """FAQPage JSON-LD from [{"question","answer"}]. Use for PAA pages + product FAQs."""
    from . import struct

    return struct.render(struct.faq(json.loads(qa_json)))


@mcp.tool()
def product_schema(product_json: str) -> str:
    """Product JSON-LD with offers, AggregateRating, Reviews. Add "faqs" to embed FAQ block."""
    from . import struct

    d = json.loads(product_json)
    return struct.render(
        struct.product(
            d["id"], d["title"], d["description"], d["link"], d["image"],
            d["price"], d.get("currency", "INR"), d.get("availability", "InStock"),
            d.get("brand"), d.get("gtin"), d.get("reviews"), d.get("faqs"),
        )
    )


def _extract(d: dict, keys: list, defaults: list):
    return [d.get(k, deft) for k, deft in zip(keys, defaults)]


@mcp.tool()
def validate_schema(schema_json: str) -> list:
    """Check required fields for Google rich results."""
    from . import struct

    return struct.validate(json.loads(schema_json))


@mcp.tool()
def ai_readiness(site: str = "") -> dict:
    """GEO test: llms.txt, AI robots (GPTBot...), sitemap, FAQ 'ten-second' test, score."""
    from . import geo

    return asyncio.run(geo.check_ai_readiness(site or None))


@mcp.tool()
def llms_txt() -> str:
    """Generate an llms.txt from your crawl DB."""
    from . import geo

    return geo.llms_txt_from_crawl()


@mcp.tool()
def opportunity_queue(status: str = "open", kind: str = "", limit: int = 25) -> list:
    """THE work queue: GSC + PAA + crawl + CWV + backlinks + rank data merged and scored (impact/effort)."""
    from . import integrations

    return integrations.queue(status, kind, limit)


@mcp.tool()
def opportunities_refresh() -> dict:
    """Re-blend all sources into the opportunity queue (calls gsc cache; refreshes if stale)."""
    from . import integrations

    return integrations.recompute()


@mcp.tool()
def opportunity_done(type_target: str) -> str:
    """Mark an opportunity done; format '<type>|<target>'."""
    from . import integrations

    return integrations.mark_done(type_target)


@mcp.tool()
def gsc_delta() -> dict:
    """Nightly-style delta now: last 2 complete days GSC vs prior 7-day baseline + refreshed queue."""
    from . import scheduler

    return scheduler.nightly_delta()


@mcp.tool()
def watch_add(keyword: str, competitor: str = "") -> str:
    """Track a keyword in the rank-watch list (serp_watch job snapshots it daily)."""
    from . import scheduler

    return scheduler.watch_add(keyword, competitor)


@mcp.tool()
def watch_list() -> list:
    """Current keyword watchlist."""
    from . import scheduler

    return scheduler.watch_list()


@mcp.tool()
def watch_run() -> dict:
    """Rank-snapshot all watchlist keywords now."""
    from . import scheduler

    return scheduler.serp_watch()


@mcp.tool()
def scheduler_status() -> list:
    """Scheduled jobs, intervals, last run/status."""
    from . import scheduler

    return scheduler.list_schedule()


@mcp.tool()
def scheduler_run(only_due: bool = True) -> list:
    """Run due scheduled jobs now (nightly_delta, opps_refresh, sitemap check, serp watch)."""
    from . import scheduler

    return scheduler.tick(only_due)


@mcp.tool()
def browser_task(task: str) -> str:
    """Run any browser automation with the browser-use agent (logins, submissions, scraping)."""
    from . import browser_agent

    return browser_agent.run(task)


@mcp.tool()
def submit_to_directory(name: str, product_info: str = "{}") -> str:
    """Use the browser agent to submit your product to one of the 73 directories."""
    from . import browser_agent

    info = json.dumps({**json.loads(product_info), "brand": settings.brand_name})
    return asyncio.run(browser_agent.submit_directory(name, json.loads(info)))


@mcp.tool()
def verify_listing(url: str) -> dict:
    """Verify a submitted listing/mention is live."""
    from . import browser_agent

    return asyncio.run(browser_agent.verify_listing(url, settings.brand_name))


def main():
    args = [a for a in sys.argv[1:] if a]
    if args and args[0] == "http":
        mcp.run(transport="http", host="127.0.0.1", port=8811)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
