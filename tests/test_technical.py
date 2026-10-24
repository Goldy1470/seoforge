from unittest.mock import patch

from seoforge import crawler, technical


def test_bfs_three_click_rule():
    adj = {
        "home": ["/a", "/b"],
        "/a": ["/a1"],
        "/a1": ["/deep"],
        "/b": ["/b1"],
    }
    dist = technical._bfs(adj, "home", 3)
    assert dist["/a"] == 1 and dist["/a1"] == 2 and dist["/deep"] == 3 and dist["/b1"] == 2
    two = technical._bfs(adj, "home", 2)
    assert "/deep" not in two


def test_parse_page_speed_and_mobile_capture():
    html = """<html><head><title>T</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="a.css"><link rel="stylesheet" href="b.css">
    <script src="s1.js"></script><script src="s2.js" async></script>
    <script id="__NEXT_DATA__" type="application/json">{}</script>
    </head><body><h1>H</h1><p>{}</p></body></html>"""
    rec = crawler._parse_page("https://x.com/", html, "https://x.com/")
    assert rec["viewport"] == 1
    assert rec["js_flag"] == "next"
    assert rec["render_blockers"] == 3


def test_parse_page_spa_shell_detection():
    html = """<html><head><title>app</title>
    <meta name="viewport" content="width=device-width">
    <script src="bundle.js" defer></script></head>
    <body><div id="app"></div></body></html>"""
    rec = crawler._parse_page("https://x.com/app", html, "https://x.com/")
    assert rec["js_flag"] == "spa" and rec["render_blockers"] == 0 and rec["viewport"] == 1
    clean = crawler._parse_page(
        "https://x.com/docs", "<html><body><p>" + "word " * 200 + "</p></body></html>", "https://x.com/"
    )
    assert clean["js_flag"] == "" and clean["viewport"] == 0


def test_crawlability_and_legacy_skip():
    rep = technical.crawlability()
    assert "pre_v07_rows" in rep and "rule" in rep
    assert rep["pages_checked"] >= 0


def test_fast_and_mobile_rules_run_on_db():
    s = technical.speed_rules()
    assert "heavy_pages" in s and "rule" in s
    m = technical.mobile_rules()
    assert "missing_viewport" in m and "pre_v07_rows" in m


def test_structure_on_crawl_db():
    rep = technical.structure()
    assert rep["homepage"].endswith("/")
    assert set(rep) >= {"beyond_limit", "reachable", "important_checked", "depth_map", "rule"}


def test_sitemap_policy_policy_lists():
    canned = {
        "problems": [
            {"severity": "high", "url": "https://site.com/x", "issue": "in sitemap but HTTP 404"},
            {"severity": "low", "url": "https://site.com/y", "issue": "in sitemap but thin"},
        ],
        "not_in_sitemap_preview": ["https://site.com/z"],
        "summary": {"high": 1, "low": 1},
        "robots_txt": True,
        "sitemap_urls": 3,
    }
    with patch("seoforge.indexing.crosscheck", return_value=canned):
        pol = technical.sitemap_policy()
    assert {p["url"] for p in pol["remove_from_sitemap"]} == {"https://site.com/x"}
    assert "google.com/ping" in pol["submit"]["google_ping"]
    assert "Bing" in pol["submit"]["bing"]


def test_index_status_graceful_without_gsc():
    with patch("seoforge.gsc.inspect_url", side_effect=SystemExit("no gsc auth")):
        r = technical.index_status(["https://example.com"])
    assert "skipped" in r and "gsc-auth" in r["note"]


def test_opportunities_types_and_legacy_skip():
    opps = technical.opportunities()
    allowed = {"fix_mobile", "fix_crawlability", "fix_speed"}
    assert all(o["type"] in allowed for o in opps)
    assert all(o["evidence"].get("rule") in ("2", "4", "5") for o in opps)


def test_report_saves_six_rules():
    with patch("seoforge.indexing.crosscheck", return_value={"problems": [], "summary": {}, "not_in_sitemap_preview": [], "robots_txt": True, "sitemap_urls": 0}):
        with patch.object(technical, "index_status", return_value={"skipped": "no gsc"}):
            rep = technical.report()
    assert rep["report_id"] >= 1
    for k in ("rule_1_structure", "rule_2_crawlability", "rule_3_indexing", "rule_4_speed", "rule_5_mobile", "rule_6_sitemap"):
        assert k in rep


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all technical tests passed")
