from unittest.mock import patch

from seoforge import preferred


def test_embed_snippet_official_code():
    snip = preferred.embed_snippet("dark", "en", "standard")
    assert "google-add-preferred-source-btn" in snip["standard"]
    assert "data-theme=\"dark\"" in snip["standard"] and "data-lang=\"en\"" in snip["standard"]
    assert "news.google.com/swg/js/v1/publisher.js" in snip["standard"]
    assert "preferred-sources-control=\"manual\"" in snip["manual_custom_trigger"]
    assert "publisher.mjs" in snip["esm_module"] and "preferredSource.init" in snip["esm_module"]


def test_property_level_check():
    assert preferred._property_level("https://sub.example.com") is None
    assert preferred._property_level("https://example.com") is None
    warn = preferred._property_level("https://example.com/blog/some-uri")
    assert warn is not None and "path" in warn.lower()


def test_audit_no_network():
    fresh = {"sitemap_urls": 10, "updated_last_30_days": 4, "newest_lastmod": "2099-01-01",
             "fresh_enough": True, "advice": "weekly cadence"}
    with patch.object(preferred, "_freshness", lambda site: fresh), \
         patch.object(preferred.httpx, "get", side_effect=OSError), \
         patch.object(preferred.db, "save_report", return_value=777):
        rep = preferred.audit("https://example.com")
    assert rep["report_id"] == 777
    e = rep["eligible"]
    assert e["publishes_fresh_content"] is True
    assert e["property_level_ok"] is True
    assert e["button_on_site"] is False
    assert any("Search generative AI features" in c for c in rep["checklist"])
    assert rep["install"]["standard"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all preferred-sources tests passed")
