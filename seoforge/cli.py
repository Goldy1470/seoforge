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
    sp.add_argument("kind", nargs="?", default="striking", choices=["striking", "cannibal", "lowctr"])

    sp = sub.add_parser("keyword-ideas")
    sp.add_argument("seed")
    sp = sub.add_parser("serp")
    sp.add_argument("keyword")

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
        else:
            _out(gsc.low_ctr())
    elif cmd == "keyword-ideas":
        _out(asyncio.run(keywords.keyword_suggestions(args.seed)))
    elif cmd == "serp":
        _out(asyncio.run(keywords.serp(args.keyword)))
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
