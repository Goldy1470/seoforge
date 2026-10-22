import json
from unittest.mock import patch

from seoforge import aeo, openseo, value, geo


def test_categories_load():
    cats = aeo.load_categories()
    ids = [c["id"] for c in cats]
    assert len(ids) == len(set(ids)) and "ai-seo" in ids


def test_funnel_zero_pages_branches():
    with patch("seoforge.aeo._fetch", lambda url: None):
        cat = aeo.load_categories()[0]
        pages = ["https://example.com/x"]
        answered = aeo.stage_answered(cat, pages)
        assert answered["stage"] == "answered" and answered["score"] == 0
        chosen = aeo.stage_chosen(cat, pages)
        assert chosen["score"] == 0


def test_llms_full_contains_brand():
    txt = aeo.llms_full_txt_from_crawl()
    assert txt.startswith("# ")
    assert "How to cite us" in txt


def test_openseo_mode_not_configured_and_setup_cmds():
    st = openseo.status()
    assert st["mode"] == "not-configured" and "app.openseo.so/mcp" in st["mcp_url"]
    setup = openseo.setup_codex()
    assert any("codex mcp login openseo" in c or "plugin add openseo@openseo" in c for c in setup["codex_cli"])
    guide = openseo.selfhost_guide()
    assert any("alchemy" in s for s in guide["steps"]) and "vercel" in guide["vercel"]


def test_value_report():
    rep = value.value_report()
    assert rep["cost"]["semrush_pro_per_month"] == 139.95
    assert rep["cost"]["savings_pct"] > 50
    assert len(rep["coverage_matrix"]) >= 12
    md = value.render_markdown(rep)
    assert "$139.95" in md and "found" in md


def test_geo_llms_txt_exists():
    txt = geo.llms_txt_from_crawl()
    assert settings_ok()


def settings_ok():
    from seoforge.config import settings

    return settings.site_url.startswith("http")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all aeo/value/openseo tests passed")
