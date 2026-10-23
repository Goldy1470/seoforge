from unittest.mock import patch

from seoforge import research

NO_CREDS = SystemExit("Set DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD")


def test_provider_one_tool_rule():
    p = research.provider()
    assert "difficulty scores differ between tools" in p["why"]
    assert "DataForSEO" in p["tool"]
    assert research.seed_ideas(["crm"])["provider"]["tool"] == p["tool"]


def test_seed_ideas_customer_language():
    s = research.seed_ideas(["amazon marketing agency"])
    assert "best amazon marketing agency" in s["seeds"]
    assert "amazon marketing agency for d2c brands" in s["seeds"]
    assert len(s["seeds"]) == len(set(s["seeds"]))


def test_intent_classifier_buckets():
    assert research._classify_text("How to track amazon sales - Blog Post") == "teach"
    assert research._classify_text("Best CRM Software: Top 5 Compared") == "compare"
    assert research._classify_text("CRM Pricing & Buy Now") == "sell"


def test_intent_checker_deterministic_offline():
    with patch.object(research.keywords, "serp", side_effect=NO_CREDS):
        r = research.check_intent("best crm for d2c")
    assert r["dominant"] == "compare"
    assert "cred" in r["mode"] or "google" in r["mode"]


def test_judge_verdicts():
    assert research.judge(50)["verdict"] == "skip"
    assert research.judge(1200, 0.3)["verdict"] == "page"
    assert research.judge(1200, 0.9)["verdict"] == "needs-edge"
    assert research.judge(300, "LOW")["verdict"] == "page"
    assert research.judge(None)["verdict"] == "unknown"


def test_group_keywords_one_page_rule():
    g = research.group_keywords(
        ["best crm for d2c", "crm for d2c", "crm pricing", "is crm worth it", "crm alternatives"]
    )
    members_all = [m for x in g["groups"] for m in x["members"]]
    assert len(members_all) == 5 and len(g["groups"]) >= 2
    assert any(x["primary"] == "crm for d2c" and x["slug"] == "crm-for-d2c" for x in g["groups"])
    assert "never compete with yourself" in g["rule"]
    assert len({x["slug"] for x in g["groups"]}) == len(g["groups"])


def test_keyword_worth_graceful_without_creds():
    with patch.object(research.keywords, "keyword_suggestions", side_effect=NO_CREDS):
        w = research.worth_a_page(["crm for d2c", "email marketing pricing"])
    assert "skipped" in w and all(r["verdict"] == "unknown" for r in w["results"])


def test_workflow_saves_report():
    with patch.object(research.keywords, "serp", side_effect=NO_CREDS):
        with patch.object(research.keywords, "keyword_suggestions", side_effect=NO_CREDS):
            rep = research.workflow(["growth guide"], deep_on=1)
    assert rep["report_id"] >= 1
    first = rep["rule_4_intent"]["checked"][0]
    assert {"keyword", "dominant"} <= set(first)
    assert rep["rule_6_groups"]["keyword_count"] > 0
    assert rep["rule_3_one_tool"]["tool"] == research.provider()["tool"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all research-playbook tests passed")
