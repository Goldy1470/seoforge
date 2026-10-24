from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from . import backlinks, crawler, db, facets, gsc, internal_links, keywords, merchant, paa
from .config import settings


def _out(o, full=False):
    if isinstance(o, dict) and not full:
        o = {k: v for k, v in o.items()}
    print(json.dumps(o, indent=2, default=str, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser("seoforge", description="End-to-end SEO SaaS toolkit + MCP server")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("crawl")
    sp.add_argument("url", nargs="?", default=settings.site_url)

    sub.add_parser("audit")
    sp = sub.add_parser("gsc-auth")
    sp = sub.add_parser("gsc-opportunities")
    sp.add_argument("kind", nargs="?", default="striking", choices=["striking", "cannibal", "lowctr", "pages"])

    sp = sub.add_parser("keyword-ideas")
    sp.add_argument("seed")
    sp = sub.add_parser("serp")
    sp.add_argument("keyword")
    sp = sub.add_parser("competitor-gap")
    sp.add_argument("keyword")
    sp.add_argument("competitor_domain", nargs="?")
    sp = sub.add_parser("rank-history")
    sp.add_argument("--keyword", default="")
    sp.add_argument("--domain", default="")
    sp = sub.add_parser("cwv")
    sp.add_argument("url", nargs="?")
    sp.add_argument("--desktop", action="store_true")
    sp = sub.add_parser("sitemap-check")
    sp.add_argument("url", nargs="?")
    sp = sub.add_parser("indexnow")
    sp.add_argument("urls", nargs="*")
    sp.add_argument("--key", default="")
    sp = sub.add_parser("ai-cite")
    sp.add_argument("questions", nargs="+")
    sp = sub.add_parser("scaffold")
    sp.add_argument("--min-q", type=int, default=3)
    sp.add_argument("--limit", type=int, default=15)

    sub.add_parser("backlink-gap")
    sub.add_parser("openseo")

    sp = sub.add_parser("paa")
    sp.add_argument("seeds", nargs="+")
    sp.add_argument("--depth", type=int, default=2)
    sp = sub.add_parser("build-list")
    sp.add_argument("--min-q", type=int, default=1)

    sub.add_parser("links")
    sub.add_parser("facets")
    sp = sub.add_parser("dirs")
    sp.add_argument("--status", default="")
    sp.add_argument("--min-dr", type=int, default=0)
    sp = sub.add_parser("dirs-update")
    sp.add_argument("name")
    sp.add_argument("status")
    sub.add_parser("pr")

    sp = sub.add_parser("add-product")
    sp.add_argument("json")
    sp = sub.add_parser("import-products")
    sp.add_argument("csv")
    sub.add_parser("feed")

    sp = sub.add_parser("schema")
    sp.add_argument("kind", choices=["faq", "product", "org", "review"])
    sp.add_argument("json", nargs="?")

    sub.add_parser("geo")
    sub.add_parser("llms")
    sp = sub.add_parser("screenshot")
    sp.add_argument("url")
    sp = sub.add_parser("openseo-setup")
    sp.add_argument("--no-skills", action="store_true")
    sub.add_parser("openseo-status")
    sub.add_parser("openseo-ping")
    sub.add_parser("openseo-selfhost")
    sp = sub.add_parser("aeo")
    sp.add_argument("category", nargs="?")
    sub.add_parser("aeo-matrix")
    sub.add_parser("llms-full")
    sp = sub.add_parser("value")
    sp.add_argument("--openseo-hosted", action="store_true")
    sp.add_argument("--markdown", action="store_true")
    sp = sub.add_parser("preferred")
    sp.add_argument("url", nargs="?")
    sp = sub.add_parser("preferred-btn")
    sp.add_argument("--theme", default="light", choices=["light", "dark"])
    sp.add_argument("--lang", default="")
    sp.add_argument("--mode", default="standard", choices=["standard", "manual", "esm"])
    sp = sub.add_parser("research")
    sp.add_argument("--products", nargs="*")
    sp = sub.add_parser("problems")
    sp.add_argument("--products", nargs="*")
    sp = sub.add_parser("seeds")
    sp.add_argument("--products", nargs="*")
    sp = sub.add_parser("intent")
    sp.add_argument("keyword")
    sp = sub.add_parser("worth")
    sp.add_argument("keywords", nargs="+")
    sp = sub.add_parser("groups")
    sp.add_argument("keywords", nargs="*")
    sp = sub.add_parser("onpage")
    sp.add_argument("url", nargs="?")
    sp = sub.add_parser("onpage-pack")
    sp.add_argument("query")
    sp.add_argument("--usp", default="")
    sp = sub.add_parser("structure")
    sp.add_argument("--max-clicks", type=int, default=3)
    sub.add_parser("crawlability")
    sub.add_parser("speed")
    sub.add_parser("mobile")
    sp = sub.add_parser("index-status")
    sp.add_argument("--limit", type=int, default=25)
    sub.add_parser("sitemap-policy")
    sub.add_parser("technical")
    sp = sub.add_parser("watch-add")
    sp.add_argument("keyword")
    sp.add_argument("--competitor", default="")
    sub.add_parser("watch-list")
    sub.add_parser("watch-run")
    sub.add_parser("delta")
    sp = sub.add_parser("opps")
    sp.add_argument("--kind", default="")
    sp.add_argument("--limit", type=int, default=25)
    sub.add_parser("opps-refresh")
    sp = sub.add_parser("schedule")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--daemon", nargs="?", const=300, type=int)
    sub.add_parser("schedule-status")
    sub.add_parser("serve")
    sub.add_parser("mcp")
    args = ap.parse_args()

    cmd = args.cmd
    if cmd == "crawl":
        _out(asyncio.run(crawler.crawl_site(args.url)))
    elif cmd == "audit":
        from . import orchestrator

        _out(orchestrator.seo_audit(), full=True)
    elif cmd == "gsc-auth":
        _out(gsc.run_oauth_flow())
    elif cmd == "gsc-opportunities":
        if args.kind == "striking":
            _out(gsc.striking_distance())
        elif args.kind == "cannibal":
            _out(gsc.cannibalization())
        elif args.kind == "pages":
            _out(gsc.pages())
        else:
            _out(gsc.low_ctr())
    elif cmd == "keyword-ideas":
        _out(asyncio.run(keywords.keyword_suggestions(args.seed)))
    elif cmd == "serp":
        _out(asyncio.run(keywords.serp(args.keyword)))
    elif cmd == "competitor-gap":
        from . import orchestrator
        _out(keywords.competitor_gap(args.keyword, args.competitor_domain or ""))
    elif cmd == "rank-history":
        _out(keywords.rank_history(args.keyword or None, args.domain or None))
    elif cmd == "cwv":
        from . import perf
        _out(perf.cwv(args.url or settings.site_url, args.desktop))
    elif cmd == "sitemap-check":
        from . import indexing
        _out(indexing.crosscheck(args.url or None), full=True)
    elif cmd == "indexnow":
        from . import indexing
        _out(indexing.indexnow_submit(args.urls, args.key or None))
    elif cmd == "ai-cite":
        from . import geo
        _out(geo.ai_citation_check(args.questions))
    elif cmd == "scaffold":
        _out(paa.page_scaffold(args.min_q, args.limit))
    elif cmd == "backlink-gap":
        _out(backlinks.gap())
    elif cmd == "openseo":
        _out(asyncio.run(keywords.openseo_passthrough("ping")))
    elif cmd == "paa":
        _out(asyncio.run(paa.build_paa_tree(args.seeds, args.depth)))
    elif cmd == "build-list":
        plan = paa.generate_build_list(limit=300)
        pages = [p for p in plan["pages"] if p["questions"] >= args.min_q]
        _out({"pages": pages, "csv": paa.export_paa_csv()})
    elif cmd == "links":
        _out(internal_links.build_report(), full=True)
    elif cmd == "facets":
        _out(facets.analyze(), full=True)
    elif cmd == "dirs":
        _out(backlinks.list_directories(args.status or None, args.min_dr))
    elif cmd == "dirs-update":
        _out(backlinks.update_status(args.name, args.status))
    elif cmd == "pr":
        _out(backlinks.digital_pr_plan())
    elif cmd == "add-product":
        _out(merchant.upsert_product_json(args.json))
    elif cmd == "import-products":
        _out({"imported": merchant.load_products_csv(args.csv)})
    elif cmd == "feed":
        _out({"feed": merchant.build_feed()})
    elif cmd == "schema":
        from . import struct

        if args.kind == "faq":
            qa = json.loads(args.json or '[{"question":"Q?","answer":"A"}]')
            _out(json.loads(struct.render(struct.faq(qa))))
        elif args.kind == "product":
            d = json.loads(args.json)
            _out(json.loads(_render_product(d)))
        elif args.kind == "org":
            _out(struct.render(struct.organization()))
        elif args.kind == "review":
            d = json.loads(args.json)
            _out(json.loads(_render_product(d)))
    elif cmd == "watch-add":
        from . import scheduler
        _out(scheduler.watch_add(args.keyword, args.competitor))
    elif cmd == "watch-list":
        from . import scheduler
        _out(scheduler.watch_list())
    elif cmd == "watch-run":
        from . import scheduler
        _out(scheduler.serp_watch())
    elif cmd == "delta":
        from . import scheduler
        _out(scheduler.nightly_delta(), full=True)
    elif cmd == "opps":
        from . import integrations
        _out(integrations.queue("open", args.kind, args.limit))
    elif cmd == "opps-refresh":
        from . import integrations
        _out(integrations.recompute(), full=True)
    elif cmd == "schedule":
        from . import scheduler
        _out(scheduler.tick(not args.all))
        if args.daemon:
            scheduler.daemon(args.daemon)
    elif cmd == "schedule-status":
        from . import scheduler
        _out(scheduler.list_schedule())
    elif cmd == "openseo-status":
        from . import openseo
        _out(openseo.status())
    elif cmd == "openseo-setup":
        from . import openseo
        _out(openseo.setup_codex(not args.no_skills))
    elif cmd == "openseo-ping":
        from . import openseo
        _out(asyncio.run(openseo.ping()))
    elif cmd == "openseo-selfhost":
        from . import openseo
        _out(openseo.selfhost_guide())
    elif cmd == "aeo":
        from . import aeo
        _out(aeo.funnel_audit(args.category or None), full=True)
    elif cmd == "aeo-matrix":
        from . import aeo
        _out(aeo.funnel_matrix())
    elif cmd == "llms-full":
        from . import aeo
        print(aeo.llms_full_txt_from_crawl())
    elif cmd == "value":
        from . import value
        if args.markdown:
            print(value.render_markdown(value.value_report(args.openseo_hosted)))
        else:
            _out(value.value_report(args.openseo_hosted), full=True)
    elif cmd == "preferred":
        from . import preferred

        _out(preferred.audit(args.url or None), full=True)
    elif cmd == "preferred-btn":
        from . import preferred

        _out(preferred.embed_snippet(args.theme, args.lang or None, args.mode), full=True)
    elif cmd == "research":
        from . import research

        _out(research.workflow(args.products), full=True)
    elif cmd == "problems":
        from . import research

        _out(research.audience_problems(args.products), full=True)
    elif cmd == "seeds":
        from . import research

        _out(research.seed_ideas(args.products), full=True)
    elif cmd == "intent":
        from . import research

        _out(research.check_intent(args.keyword), full=True)
    elif cmd == "worth":
        from . import research

        _out(research.worth_a_page(args.keywords), full=True)
    elif cmd == "groups":
        from . import research

        _out(research.group_keywords(args.keywords or None), full=True)
    elif cmd == "onpage":
        from . import onpage

        _out(onpage.audit(args.url or None), full=True)
    elif cmd == "onpage-pack":
        from . import onpage

        _out(onpage.fix_pack(args.query, args.usp), full=True)
    elif cmd == "structure":
        from . import technical

        _out(technical.structure(args.max_clicks), full=True)
    elif cmd == "crawlability":
        from . import technical

        _out(technical.crawlability(), full=True)
    elif cmd == "speed":
        from . import technical

        _out(technical.speed_rules(), full=True)
    elif cmd == "mobile":
        from . import technical

        _out(technical.mobile_rules(), full=True)
    elif cmd == "index-status":
        from . import technical

        _out(technical.index_status(None, args.limit), full=True)
    elif cmd == "sitemap-policy":
        from . import technical

        _out(technical.sitemap_policy(), full=True)
    elif cmd == "technical":
        from . import technical

        _out(technical.report(), full=True)
    elif cmd == "geo":
        from . import geo

        _out(geo.run_sync(), full=True)
    elif cmd == "llms":
        from . import geo

        print(geo.llms_txt_from_crawl())
    elif cmd == "screenshot":
        from . import browser_agent

        _out(browser_agent.screenshot(args.url))
    elif cmd == "serve":
        from . import dashboard

        dashboard.serve()
    elif cmd == "mcp":
        from . import mcp_server

        mcp_server.main()


def _render_product(d: dict) -> str:
    from . import struct

    return struct.render(
        struct.product(
            d["id"], d["title"], d["description"], d["link"], d["image"],
            d["price"], d.get("currency", "INR"), d.get("availability", "InStock"),
            d.get("brand"), d.get("gtin"), d.get("reviews"), d.get("faqs"),
        )
    )


if __name__ == "__main__":
    main()
