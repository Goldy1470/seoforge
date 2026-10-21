from __future__ import annotations

import asyncio

from . import backlinks, crawler, db, facets, gsc, internal_links, merchant, paa
from .config import settings


def seo_audit(site: str | None = None, with_gsc: bool = True) -> dict:
    crawl = asyncio.run(crawler.crawl_site(site or settings.site_url))
    crawl_summary = crawl["summary"]
    facets_r = _safe(lambda: facets.analyze(crawl["job_id"]))
    links_r = _safe(lambda: internal_links.build_report())
    geo_r = None
    try:
        from . import geo

        geo_r = geo.run_sync(site)
    except Exception as exc:
        geo_r = {"error": str(exc)}
    gsc_r = None
    if with_gsc:
        gsc_r = _safe(lambda: {"striking_distance": gsc.striking_distance()[:15], "cannibalization": gsc.cannibalization()[:10]})
    report = {
        "site": site or settings.site_url,
        "crawl": crawl_summary,
        "faceted_navigation": facets_r,
        "internal_links": {k: v for k, v in (links_r or {}).items() if k != "link_suggestions"},
        "ai_readiness": {k: v for k, v in (geo_r or {}).items() if k != "checks"},
        "gsc": gsc_r,
    }
    rid = db.save_report("seo_audit", report)
    report["report_id"] = rid
    return report


def _safe(fn):
    try:
        return fn()
    except SystemExit as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def content_plan(seeds: list[str], max_depth: int = 2) -> dict:
    tree = _run_paa_tree(seeds, max_depth)
    plan = paa.generate_build_list(limit=100)
    csv_path = paa.export_paa_csv()
    return {"paa_tree": tree, "plan": plan, "csv": csv_path}


def _run_paa_tree(seeds, max_depth):
    try:
        return paa.build_paa_tree(seeds, max_depth=max_depth)
    except SystemExit as e:
        return {"error": str(e)}


def link_building_plan() -> dict:
    return {
        "tracker": backlinks.bucket_by_category(),
        "next_high_dr_todo": backlinks.list_directories(min_dr=70)[:15],
        "digital_pr": backlinks.digital_pr_plan(),
        "manager": "use backlinks.update_status as each goes draft/submitted/live",
    }


def merchant_setup(products_csv: str | None = None) -> dict:
    if products_csv:
        n = merchant.load_products_csv(products_csv)
    else:
        n = len(merchant.products_from_db())
    feed = merchant.build_feed()
    return {"products_loaded": n, "feed": feed}


def full_pipeline(seeds: list[str] | None = None) -> dict:
    audit = seo_audit(with_gsc=bool(settings.gsc_property))
    content = content_plan(seeds or _default_seeds()) if seeds or settings.dataforseo_login else {"skipped": "no dataforseo"}
    link_plan = link_building_plan()
    return {"audit": audit, "content_plan": content, "link_plan": link_plan}


def _default_seeds() -> list[str]:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT title FROM pages WHERE status=200 ORDER BY depth, url LIMIT 8"
        ).fetchall()
    return [r["title"] or settings.brand_name for r in rows] or [settings.brand_name]
