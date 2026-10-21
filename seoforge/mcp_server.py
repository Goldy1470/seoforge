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
def openseo_capabilities() -> dict:
    """How to pair with OpenSEO (YC, openseo.so): MCP to attach + REST passthrough."""
    from . import keywords

    return asyncio.run(keywords.openseo_passthrough("ping"))


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
