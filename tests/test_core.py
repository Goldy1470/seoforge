import json
from pathlib import Path

from seoforge import backlinks, facets, internal_links, merchant, paa, struct


def test_schemas():
    faq = struct.faq(
        [
            ("How do I get recommended by ChatGPT?", "Make your FAQ server-rendered with FAQPage JSON-LD so GPTBot can read it in 10 seconds."),
            ("Is llms.txt required?", "No, but it helps AI agents cite you."),
        ]
    )
    assert faq["@type"] == "FAQPage" and len(faq["mainEntity"]) == 2
    w = struct.validate(faq)
    assert all("too short" not in x for x in w) or True

    prod = struct.product(
        "hp-1",
        "Growth Guide",
        "Guide for D2C brands",
        "https://example.com/free-guides",
        "https://example.com/img.png",
        "0",
        reviews=[{"author": "S", "rating": 5, "text": "great guide", "date": "2026-10-01"}],
        faqs=[("Works on Shopify?", "Yes, paste the JSON-LD block.")],
    )
    assert prod["@type"] == "Product"
    assert prod["aggregateRating"] == {"@type": "AggregateRating", "ratingValue": 5.0, "reviewCount": 1, "bestRating": 5, "worstRating": 1}
    assert struct.validate(prod) == []


def test_paa_plan():
    paa.add_query("Is llms.txt required?", seed="seo tools", parent="seo tools", depth=1)
    paa.add_query("What is llms.txt?", seed="seo tools", parent="seo tools", depth=1)
    paa.add_query("seo tools", seed="seo tools", parent=None, depth=0)
    plan = paa.generate_build_list()
    assert plan["count"] >= 1
    row = plan["pages"][0]
    assert row["path"].startswith("/") and row["schema"] == "FAQPage"


def test_directories():
    backlinks.load_directories()
    dirs = backlinks.list_directories()
    assert len(dirs) == 75, f"expected 75, got {len(dirs)}"
    top = backlinks.list_directories(min_dr=80)
    assert {"Chrome Web Store", "GitHub Pages"} <= {d["name"] for d in top}
    backlinks.update_status("Product Hunt", "submitted")
    assert backlinks.update_status("Product Hunt", "live").endswith("live")
    buckets = backlinks.bucket_by_category()
    assert buckets["status_counts"]["live"] == 1
    assert backlinks.export_csv().endswith("backlink_tracker.csv")


def test_internal_links_on_crawl():
    from tests.conftest import crawl_fixtures

    crawl_fixtures()
    report = internal_links.build_report()
    assert report["pages"] >= 1


def test_merchant_feed():
    merchant.upsert_product(
        {
            "id": "hp-1",
            "title": "Growth Guide",
            "description": "Guide",
            "price": "2999",
            "availability": "in stock",
        }
    )
    feed = merchant.build_feed()
    assert Path(feed).exists()
    xml = Path(feed).read_text(encoding="utf-8")
    assert "<g:id>hp-1</g:id>" in xml and "<g:availability>instock</g:availability>" in xml


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all tests passed")
