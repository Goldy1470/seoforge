from seoforge import onpage

Q = "best crm for d2c"


def test_rule1_title_length_and_phrase():
    long_t = "Best crm for d2c - the ultimate complete guide to using a CRM for D2C Brands 2026"
    warns = [x for x in onpage.rule_title(long_t, Q) if x["level"] == "warn"]
    assert warns and any("chars" in x["detail"] for x in warns)
    short = onpage.rule_title("Highprime", "")
    assert short[0]["level"] == "warn"
    good = onpage.rule_title("Best CRM Software For D2c Brands 2026 | Highprime", Q)
    assert good[0]["level"] == "ok"


def test_rule2_meta_beats():
    echo = onpage.rule_meta("best crm for d2c", "Best CRM For D2c")
    assert echo[0]["level"] == "warn"
    seller = onpage.rule_meta(
        "Best CRM for D2C - pricing tables for 3 plans, free demo in 48h, trusted by 40+ brands.",
        "Best CRM For D2c | Highprime",
    )
    assert seller[0]["level"] == "ok"
    missing = onpage.rule_meta("", "x")
    assert "no meta description" in missing[0]["detail"]


def test_rule3_headings_question_form():
    warns = onpage.rule_headings(["Features", "Pricing", "How it works?"])
    assert any("not in the customer's question form" in (x["detail"] or "") for x in warns)
    none = onpage.rule_headings([])
    assert "no h2/h3" in none[0]["detail"]
    legacy = onpage.rule_headings(None)
    assert legacy[0]["level"] == "skip"
    good = onpage.rule_headings(["What is a CRM for D2C?", "How long does implementation take?"])
    assert good[0]["level"] == "ok"


def test_rule4_first_sentence_answers():
    ok = onpage.rule_first("Best CRM for D2C brands works by syncing Shopify orders into pipelines in a day. Details follow.", "", Q)
    assert ok[0]["level"] == "ok"
    buried = onpage.rule_first("Founded in 2019 we love coffee. Our story began in a garage long ago and we grew fast.", "", Q)
    assert buried[0]["level"] == "warn"
    assert onpage.rule_first(None)[0]["level"] == "skip"


def test_rule5_images_alt():
    assert onpage.rule_images(0)[0]["level"] == "ok"
    assert onpage.rule_images(4)[0]["level"] == "warn"


def test_rule6_anchors_match_titles():
    gen = onpage._anchor_findings("click here", "Free Guides for D2C Brands")
    assert "generic anchor" in gen[0]["detail"]
    bad = onpage._anchor_findings("tax tips", "Free Guides for D2C Brands")
    assert any("destination title" in x["detail"] for x in bad)
    ok = onpage._anchor_findings("Free Guides for D2C Brands", "Free Guides for D2C Brands")
    assert ok[0]["level"] == "ok"


def test_audit_runs_on_crawl_db():
    rep = onpage.audit()
    assert rep["pages_audited"] >= 0 and set(rep["warnings_by_rule"]) == {
        "1-title", "2-meta", "3-headings", "4-first-sentence", "5-images", "6-internal-links"
    }
    single = onpage.audit("example")
    assert all("example" in f["url"] for f in single["findings"])


def test_fix_pack_six_rules():
    pack = onpage.fix_pack(Q)
    assert len(pack["blocks"]) == 6
    title = pack["blocks"][0]["deliver"]
    assert len(title) <= 60 and "d2c" in title.lower()
    meta = pack["blocks"][1]["deliver"]
    assert len(meta) <= 155
    levels = pack["self_check"]["1-title"]
    assert "warn" not in levels
    headings = pack["blocks"][2]["deliver"]
    assert headings and all(len(h) > 0 for h in headings)


def test_opportunities_feed_queue():
    opps = onpage.opportunities()
    assert all(o["type"] == "fix_onpage" and ("rule" in o["evidence"]) for o in opps)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all on-page tests passed")
