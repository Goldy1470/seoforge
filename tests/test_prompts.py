"""The citation-tracking parser and kernel, mirrored case by case from the
reference's response-parser/prompt-improve fixture matrix (brand and
competitor names debranded: Acme, Globex, Initech, Umbrella, Quanta,
Nimbus — the answers here are synthetic too, never scraped).

Pure functions run against no database; the storage/state tests use the
suite's own isolated DB. Real answer text is only ever supplied by a
paste-in, so nothing here touches the network.
"""
import datetime as dt
import json

import pytest

from seoforge import db, prompts


BRAND = {"name": "Acme", "domains": ["acme.com", "acme-corp.com"]}


# ------------------------------------------------------------------ counts

def test_mention_counts_name_and_domains_case_insensitively():
    text = "Acme is great. You can visit acme.com or email contact@acme-corp.com. ACME is case-insensitive."
    # name matches twice standalone + once inside each domain (word
    # boundaries), domains once each: 4 + 2
    assert prompts.count_occurrences(prompts.matchable_text(text), "Acme") == 4
    assert prompts.parse_response(text, [], BRAND)["mention_count"] == 6


def test_markdown_links_and_bare_urls_are_stripped_before_counting():
    text = "Check [Acme](https://acme.com/about) or go to bare URL https://acme.com/home directly."
    result = prompts.parse_response(text, [], BRAND)
    assert result["mention_count"] == 1  # only the link's display text


def test_an_answer_the_brand_is_absent_from_counts_nothing():
    result = prompts.parse_response("Some other text about Globex and globex.com.", [], BRAND)
    assert result["mention_count"] == 0


# -------------------------------------------------------------Parse shapes

def test_parse_response_counts_mentions_in_markdown_link_discard_the_url():
    result = prompts.parse_response(
        "We recommend [Acme](https://acme.com). Acme rules!", [], BRAND
    )
    assert result["mention_count"] == 2


# --------------------------------------------------------- citation counts

def test_citation_count_is_hostname_based_case_insensitive_and_www_aware():
    cites = [
        {"url": "https://ACME.com/page1", "title": "Page 1"},
        {"url": "https://www.acme-corp.com/info", "title": "Page 2"},
    ]
    assert prompts.parse_response("Acme is featured in these links.", cites, BRAND)[
        "citation_count"
    ] == 2


def test_citation_count_covers_a_brand_subdomain():
    cites = [{"url": "https://docs.acme.com/setup", "title": "Docs"}]
    assert prompts.parse_response("Acme docs.", cites, BRAND)["citation_count"] == 1


def test_a_brand_domain_inside_another_sites_path_or_query_is_not_a_citation():
    cites = [
        {"url": "https://other.com/acme-corp.com/info", "title": "Path lookalike"},
        {"url": "https://other.com/?ref=acme.com", "title": "Query lookalike"},
    ]
    assert prompts.parse_response("Acme is referenced.", cites, BRAND)["citation_count"] == 0


def test_one_citation_is_counted_once_even_when_several_domains_match():
    cites = [{"url": "https://acme.com/acme-corp.com", "title": "Double match"}]
    assert prompts.parse_response("Acme link.", cites, BRAND)["citation_count"] == 1


# ------------------------------------------------------- visibility score

def test_positive_sentiment_and_mixed_citations_score_to_the_reference_value():
    text = "Acme Acme Acme Acme"  # 4 mentions -> 40
    cites = [{"url": "https://acme.com", "title": "Link 1"}, {"url": "https://other.com", "title": "Link 2"}]
    # 40 + 15 + round(1/2 × 15 = 7.5 up) + 15
    assert prompts.parse_response(text, cites, BRAND, "positive")["visibility_score"] == 78


def test_neutral_sentiment_adds_seven_only_when_mentioned():
    cites = [{"url": "https://acme.com", "title": "Link 1"}]
    assert prompts.parse_response("Acme Acme", cites, BRAND, "neutral")["visibility_score"] == 57


def test_negative_sentiment_adds_nothing():
    cites = [{"url": "https://acme.com", "title": "Link 1"}]
    assert prompts.parse_response("Acme Acme", cites, BRAND, "negative")["visibility_score"] == 50


def test_the_visibility_score_caps_at_100():
    cites = [
        {"url": "https://acme.com", "title": "Link 1"},
        {"url": "https://acme-corp.com", "title": "Link 2"},
    ]
    assert prompts.parse_response("Acme Acme Acme Acme Acme Acme", cites, BRAND, "positive")[
        "visibility_score"
    ] == 100


def test_empty_answers_and_unrelated_answers_score_zero():
    result = prompts.parse_response("", [], BRAND)
    assert result["mention_count"] == 0 and result["citation_count"] == 0
    assert result["visibility_score"] == 0 and result["competitor_mentions"] == []
    unrelated = prompts.parse_response(
        "Completely unrelated text about other things.",
        [{"url": "https://unrelated.com", "title": "Unrelated"}],
        BRAND,
    )
    assert unrelated["visibility_score"] == 0


# ------------------------------------------------------ competitor metrics

COMPETITORS = [
    {"id": "1", "name": "Globex", "domain": "globex.com"},
    {"id": "2", "name": "Initech", "domain": ""},  # missing domain
]


def test_competitor_metrics_match_the_reference_fixture():
    text = "Globex is alright, but Initech is better. Check globex.com or initech.org."
    cites = [
        {"url": "https://globex.com/about", "title": "Globex Inc"},
        {"url": "https://initech.org/info", "title": "Initech Inc"},
    ]
    result = prompts.parse_response(text, cites, BRAND, "neutral", COMPETITORS)
    by_id = {c["competitor_id"]: c for c in result["competitor_mentions"]}
    # Globex: name twice (word boundary inside globex.com) + domain once = 3;
    # citation by substring in the host -> 1; score 30 + 15 + 8 + 7
    assert by_id["1"]["mention_count"] == 3
    assert by_id["1"]["citation_count"] == 1
    assert by_id["1"]["visibility_score"] == 60
    # Initech: name twice (boundary inside initech.org), no domain, 0 citations;
    # score 20 + 0 + 0 + 7
    assert by_id["2"]["mention_count"] == 2
    assert by_id["2"]["citation_count"] == 0
    assert by_id["2"]["visibility_score"] == 27


# ------------------------------------------------------ mention positions

POSITION_COMPETITORS = [
    {"id": "c1", "name": "Globex", "domain": "globex.com"},
    {"id": "c2", "name": "Initech", "domain": "initech.com"},
    {"id": "c3", "name": "Umbrella", "domain": "umbrella.com"},
]


def test_the_brand_ranked_before_every_competitor_is_first():
    r = prompts.compute_mention_position("Acme leads the market, ahead of Globex and Initech.", BRAND, POSITION_COMPETITORS)
    assert r["mention_position"] == 1 and r["mentioned_entity_count"] == 3


def test_the_brand_rank_counted_by_who_is_named_before_it():
    r = prompts.compute_mention_position("Top picks: Globex, Initech, and also Acme as a budget option.", BRAND, POSITION_COMPETITORS)
    assert r["mention_position"] == 3 and r["mentioned_entity_count"] == 3


def test_an_unmentioned_brand_reads_null_not_zero():
    r = prompts.compute_mention_position("Globex and Umbrella dominate this space.", BRAND, POSITION_COMPETITORS)
    assert r["mention_position"] is None and r["mentioned_entity_count"] == 2


def test_a_domain_occurrence_counts_as_a_brand_mention_for_positioning():
    r = prompts.compute_mention_position("Globex is popular, but acme.com has the best docs.", BRAND, POSITION_COMPETITORS)
    assert r["mention_position"] == 2 and r["mentioned_entity_count"] == 2


def test_names_that_only_exist_inside_a_url_are_ignored():
    r = prompts.compute_mention_position(
        "Initech is the leader. See [comparison](https://acme.com/vs) for details.", BRAND, POSITION_COMPETITORS
    )
    assert r["mention_position"] is None and r["mentioned_entity_count"] == 1


def test_an_answer_that_names_nobody_counts_zero_entities():
    r = prompts.compute_mention_position("Nothing relevant here.", BRAND, POSITION_COMPETITORS)
    assert r["mention_position"] is None and r["mentioned_entity_count"] == 0


def test_each_entity_is_placed_at_its_earliest_occurrence():
    r = prompts.compute_mention_position("Globex... later Acme appears. Globex again at the end.", BRAND, POSITION_COMPETITORS)
    assert r["mention_position"] == 2 and r["mentioned_entity_count"] == 2


def test_each_mentioned_competitor_ranks_among_all_mentioned_entities():
    r = prompts.compute_mention_position(
        "Globex leads, then Acme, with Umbrella as a distant third.", BRAND, POSITION_COMPETITORS
    )
    assert r["mention_position"] == 2
    assert r["competitor_positions"]["c1"] == 1  # Globex
    assert r["competitor_positions"]["c3"] == 3  # Umbrella
    assert "c2" not in r["competitor_positions"]  # Initech absent


def test_competitors_rank_even_when_the_brand_is_not_mentioned():
    r = prompts.compute_mention_position("Initech and Globex dominate here.", BRAND, POSITION_COMPETITORS)
    assert r["mention_position"] is None
    assert r["competitor_positions"]["c2"] == 1  # Initech
    assert r["competitor_positions"]["c1"] == 2  # Globex


def test_parse_response_embeds_each_competitor_rank():
    competitors = [
        {"id": "c1", "name": "Globex", "domain": "globex.com"},
        {"id": "c2", "name": "Initech", "domain": "initech.com"},
    ]
    result = prompts.parse_response(
        "Globex is first here, Acme second, and nobody mentions the rest.", [], BRAND, "neutral", competitors
    )
    by_id = {c["competitor_id"]: c for c in result["competitor_mentions"]}
    assert by_id["c1"]["mention_position"] == 1
    assert by_id["c2"]["mention_position"] is None
    assert result["mention_position"] == 2


# ------------------------------------------------- non-ASCII names (#871)

def count_of(text, name):
    return prompts.count_occurrences(prompts.matchable_text(text), name)


def test_names_starting_or_ending_with_accented_letters_match():
    assert count_of("I like Émile Studio. Émile Studio is great.", "Émile Studio") == 2
    assert count_of("Ümit ve Eda, Ümit en çok bilinen.", "Ümit") == 2
    assert count_of("Çınar Bankası ve Çınar Bankası şubeleri", "Çınar Bankası") == 2


def test_names_starting_or_ending_with_punctuation_match():
    assert count_of("I like Acme!. Acme! is great.", "Acme!") == 2
    assert count_of("I like C++. C++ is great.", "C++") == 2
    assert count_of("Acme, Inc. and Acme, Inc. again", "Acme, Inc.") == 2


def test_a_name_inside_a_longer_word_is_still_ignored():
    assert count_of("Acmegroup and SuperAcme are different.", "Acme") == 0
    assert count_of("Ümitler and Tümit are different.", "Ümit") == 0
    assert count_of("Acmeé is not Acme.", "Acme") == 1  # the é keeps the boundary


def test_names_in_scripts_without_spaces_match_with_no_boundary():
    assert count_of("我推荐星云科技，星云科技的服务很好。", "星云科技") == 2
    assert count_of("하늘랩은 좋은 회사입니다. 하늘랩의 제품", "하늘랩") == 2


def test_plain_ascii_behaviour_is_unchanged():
    assert count_of("I like Acme. Acme is great.", "Acme") == 2
    assert count_of("Acme's pricing beats acme-lite.", "Acme") == 2


def test_a_non_ascii_name_positions_among_its_competitors():
    text = "Eda öne çıkıyor, ardından Ümit ve İpek geliyor."
    r = prompts.compute_mention_position(
        text, {"name": "Ümit", "domains": []},
        [{"id": "c1", "name": "Eda", "domain": None}, {"id": "c2", "name": "İpek", "domain": None}],
    )
    assert r["mention_position"] == 2
    assert r["mentioned_entity_count"] == 3
    assert r["competitor_positions"]["c2"] == 3


def test_non_ascii_competitor_names_parse_into_metrics():
    result = prompts.parse_response(
        "İpek ve Eda, İpek daha ucuz.", [], BRAND, "neutral",
        [{"id": "c1", "name": "İpek", "domain": None}],
    )
    assert result["competitor_mentions"][0]["mention_count"] == 2
    assert result["competitor_mentions"][0]["mention_position"] == 1


# ---------------------------------------------------------- entity markup

ENTITY_TEXT = "\n".join(
    [
        '1. <Entity category="software" value="Quanta" disambig="Nimbus AI search optimization platform"/> — tracks answers.',
        '2. <Entity category="software" value="Acme" /> — open source.',
        "3. Nimbus — agency platform.",
    ]
)
ENTITY_COMPETITORS = [
    {"id": "quanta", "name": "Quanta", "domain": None},
    {"id": "nimbus", "name": "Nimbus", "domain": None},
    {"id": "software", "name": "Software", "domain": None},
]


def test_entity_markup_counts_only_the_names_the_answer_reads():
    result = prompts.parse_response(ENTITY_TEXT, [], BRAND, "neutral", ENTITY_COMPETITORS)
    by_id = {c["competitor_id"]: c for c in result["competitor_mentions"]}
    assert result["mention_count"] == 1  # the tag's value, once
    assert by_id["quanta"]["mention_count"] == 1
    assert by_id["nimbus"]["mention_count"] == 1  # only the answer body, not a disambig
    assert by_id["software"]["mention_count"] == 0


def test_entity_markup_ranks_entities_by_where_the_answer_names_them():
    r = prompts.compute_mention_position(ENTITY_TEXT, BRAND, ENTITY_COMPETITORS)
    assert r["mention_position"] == 2  # Quanta first, Acme second, Nimbus third
    assert r["mentioned_entity_count"] == 3
    assert r["competitor_positions"]["quanta"] == 1
    assert r["competitor_positions"]["nimbus"] == 3
    assert "software" not in r["competitor_positions"]


def test_a_brand_named_only_inside_a_tag_is_still_a_mention():
    assert count_of('Try <Entity category="software" value="Acme"/>.', "Acme") == 1


def test_a_tag_without_a_value_stays_literal_and_escapes_unescape():
    assert prompts.strip_entity_markup('<Entity category="software"/>') == '<Entity category="software"/>'
    assert prompts.strip_entity_markup('<Entity value="AT&amp;T"/>.') == "AT&T."
    # an invalid escape stays literal, but the tag itself is still replaced
    assert prompts.strip_entity_markup('<Entity value="A&#0;B"/>') == "A&#0;B"
    assert prompts.strip_entity_markup('<Entity value="A&#xFF21;B"/>') == "AＡB"


# ------------------------------------------------------ hostname + ranks

def test_extract_hostname_normalizes_and_rejects_schemeless_relative_refs():
    assert prompts.extract_hostname("https://www.FOO.com/bar") == "foo.com"
    assert prompts.extract_hostname("  https://Acme.COM/x  ") == "acme.com"
    assert prompts.extract_hostname("acme.com/bar") == "acme.com"  # reference fallback
    assert prompts.extract_hostname("mailto:a@b") is None
    assert prompts.extract_hostname("") is None
    assert prompts.extract_hostname(None) is None


def test_brand_citation_rank_is_the_first_own_domain_among_the_sources():
    cites = [
        {"url": "https://other.com/a"},
        {"url": "https://docs.acme.com/b"},
        {"url": "https://acme-corp.com/c"},
        {"url": ""},  # counts nothing, ranks nothing
    ]
    r = prompts.brand_citation_rank(cites, BRAND["domains"])
    assert r == {"rank": 2, "total": 3}


def test_no_own_citation_or_no_domains_rank_nothing():
    assert prompts.brand_citation_rank([{"url": "https://other.com/a"}], BRAND["domains"]) is None
    assert prompts.brand_citation_rank([{"url": "https://acme.com/a"}], []) is None
    assert prompts.brand_citation_rank("not-a-list", BRAND["domains"]) is None


# ---------------------------------------------------- platform classification

def _row(mention=1, position=1, entities=2, citations=None):
    return {
        "mention_count": mention,
        "mention_position": position if mention else None,
        "mentioned_entity_count": entities if mention else None,
        "citations": list(citations or []),
    }


def _own_citations(n=1):
    return [{"url": "https://acme.example/p"} for _ in range(n)]


def test_no_runs_classifies_as_no_runs():
    state = prompts.classify_platform([], ["acme.example"])
    assert state["state"] == "no_runs" and state["runs"] == 0


def test_under_half_the_runs_mentioning_is_not_mentioned():
    rows = [_row(), _row(mention=0), _row(mention=0)]
    state = prompts.classify_platform(rows, ["acme.example"])
    assert state["state"] == "not_mentioned" and state["mention_position"] == 1.0
    # 1 of 2 runs is exactly the rate: mentioned, not missing
    edge = prompts.classify_platform([_row(), _row(mention=0)], ["acme.example"])
    assert edge["state"] != "not_mentioned"


def test_averaged_positions_read_late_so_one_lucky_first_is_still_late():
    rows = [_row(position=1), _row(position=4)]
    state = prompts.classify_platform(rows, [])
    assert state["state"] == "mentioned_late" and state["mention_position"] == 2.5


def test_named_first_but_not_cited_early_is_not_cited():
    early = [_row(citations=[{"url": "https://acme.example/a"}, {"url": "https://x.com/b"}, {"url": "https://y.com/c"}])]
    assert prompts.classify_platform(early, ["acme.example"])["state"] == "doing_well"
    # ranked first but cited nowhere: not_cited
    assert prompts.classify_platform([_row()], ["acme.example"])["state"] == "not_cited"
    # cited but too late in the source list: not_cited
    late = [_row(citations=[{"url": "https://x.com/a"}, {"url": "https://y.com/c"}, {"url": "https://acme.example/z"}])]
    assert prompts.classify_platform(late, ["acme.example"])["state"] == "not_cited"


def test_snapshot_keeps_the_panel_shape():
    state = prompts.classify_platform([_row(citations=_own_citations())], ["acme.example"])
    snap = prompts.platform_snapshot("chatgpt-web", state)
    assert snap["platform"] == "chatgpt-web" and snap["state"] == "doing_well"
    assert snap["runs"] == 1 and snap["mentioned_runs"] == 1 and snap["cited_runs"] == 1
    assert snap["citation_rank"] == {"rank": 1.0, "total": 1.0}


# --------------------------------------------------------------- verdicts

def _snap(platform, runs, state, position):
    return {
        "platform": platform,
        "state": state,
        "runs": runs,
        "mention_position": position,
        "citation_rank": None,
    }


def test_verdicts_follow_the_improve_kernel_rules():
    v = prompts.platform_verdict
    assert v(None, _snap("p", 2, "not_cited", 1)) == "not_enough_data"
    assert v(_snap("p", 0, "no_runs", None), _snap("p", 2, "doing_well", 1)) == "not_enough_data"
    # not mentioned -> mentioned
    assert v(_snap("p", 3, "not_mentioned", 1), _snap("p", 2, "doing_well", 1)) == "improved"
    # mentioned -> not mentioned
    assert v(_snap("p", 2, "doing_well", 1), _snap("p", 3, "not_mentioned", 1)) == "declined"
    # not mentioned on both sides
    assert v(_snap("p", 2, "not_mentioned", 1), _snap("p", 2, "not_mentioned", 1)) == "unchanged"
    # positions: 0.5 floor, lower is better
    assert v(_snap("p", 2, "not_cited", 2.0), _snap("p", 2, "not_cited", 1.5)) == "improved"
    assert v(_snap("p", 2, "not_cited", 1.5), _snap("p", 2, "not_cited", 2.0)) == "declined"
    assert v(_snap("p", 2, "not_cited", 2.0), _snap("p", 2, "not_cited", 1.4)) == "improved"  # 0.6
    assert v(_snap("p", 2, "not_cited", 1.4), _snap("p", 2, "not_cited", 2.0)) == "declined"
    assert v(_snap("p", 2, "not_cited", 2.0), _snap("p", 2, "not_cited", 2.0)) == "unchanged"
    assert v(_snap("p", 2, "not_cited", 2.0), _snap("p", 2, "not_cited", 1.7)) == "unchanged"  # 0.3
    # NULL positions say nothing
    assert v(_snap("p", 2, "not_cited", None), _snap("p", 2, "not_cited", 1.0)) == "unchanged"


def test_compare_outcome_reports_the_history_summary():
    baseline = [
        _snap("chatgpt-web", 4, "not_cited", 2.0),
        _snap("google-aio", 3, "not_mentioned", 1.0),
        _snap("copilot-web", 0, "no_runs", None),
    ]
    current = [
        _snap("chatgpt-web", 4, "doing_well", 1.5),
        _snap("google-aio", 3, "doing_well", 1.0),
        _snap("copilot-web", 2, "doing_well", 1.0),
    ]
    out = prompts.compare_outcome(baseline, current)
    assert [p["verdict"] for p in out["platforms"]] == ["improved", "improved", "not_enough_data"]
    s = out["summary"]
    assert s == {
        "total": 3,
        "measurable": 2,
        "improved": 2,
        "declined": 0,
        "avg_before": 2.0,  # chatgpt only: google-aio was not mentioned before
        "avg_after": 1.5,  # same reason: only chatgpt is ranked mentioned on both sides
    }


def test_the_outcome_window_is_seven_days_ending_at_the_day_asked():
    assert prompts.outcome_window("2026-10-25") == {"from": "2026-10-18", "to": "2026-10-25"}
    assert prompts.outcome_window(dt.date(2026, 10, 25)) == {"from": "2026-10-18", "to": "2026-10-25"}


# -------------------------------------------------------------- storage

@pytest.fixture(autouse=True)
def acme(monkeypatch):
    from seoforge.config import settings

    monkeypatch.setattr(settings, "site_url", "https://acme.example/shop")
    monkeypatch.setattr(settings, "brand_name", "Acme")
    return settings


def test_create_prompt_is_idempotent_by_text():
    first = prompts.create_prompt("Best CRM for a 3-person agency?", category="comparison")
    again = prompts.create_prompt("Best CRM for a 3-person agency?")
    assert first == again
    with pytest.raises(ValueError):
        prompts.create_prompt("   ")
    listing = prompts.list_prompts()
    assert len(listing) == 1 and listing[0]["category"] == "comparison"
    assert listing[0]["source"] == "manual" and listing[0]["is_active"] == 1


def test_record_answer_parses_and_stores_the_metrics_at_write_time():
    prompt_id = prompts.create_prompt("Which payroll tool for startups?")
    cites = [{"url": "https://acme.example/pricing", "title": "Pricing"}, {"url": "https://other.com/x"}]
    competitors = [{"id": "c1", "name": "Globex", "domain": "globex.example"}]
    stored = prompts.record_answer(
        prompt_id, "chatgpt-web", "Globex is fine, but Acme is simpler.", cites, "neutral", competitors,
        at="2026-10-24T09:00:00+00:00",
    )
    assert stored["mention_count"] == 1 and stored["citation_count"] == 1
    assert stored["mention_position"] == 2 and stored["mentioned_entity_count"] == 2
    assert stored["competitor_mentions"][0]["name"] == "Globex"
    assert stored["source"] == "manual"

    rows = prompts.get_prompt_results(prompt_id)
    assert len(rows) == 1 and rows[0]["platform"] == "chatgpt-web"
    assert rows[0]["citations"] == cites
    assert rows[0]["competitor_mentions"][0]["competitor_id"] == "c1"
    assert rows[0]["sentiment"] == "neutral"

    # append-only: a second paste-in never rewrites the first
    prompts.record_answer(prompt_id, "perplexity-web", "Acme.", at="2026-10-25T08:00:00+00:00")
    assert len(prompts.get_prompt_results(prompt_id)) == 2
    assert len(prompts.get_prompt_results(prompt_id, platform="chatgpt-web")) == 1


def test_record_answer_rejects_unknown_platforms_and_prompts():
    prompt_id = prompts.create_prompt("A prompt")
    with pytest.raises(ValueError):
        prompts.record_answer(prompt_id, "bing-search", "answer")
    with pytest.raises(ValueError):
        prompts.record_answer(404, "chatgpt-web", "answer")


def test_platform_states_read_every_asked_platform_from_the_stored_rows():
    prompt_id = prompts.create_prompt("What is the best payroll tool?")
    # chatgpt-web: both runs mention the brand in first place, cited early
    prompts.record_answer(prompt_id, "chatgpt-web", "Acme is the best pick.", [{"url": "https://acme.example/p"}], at="2026-10-24T09:00:00+00:00")
    prompts.record_answer(prompt_id, "chatgpt-web", "Try Acme.", [{"url": "https://acme.example/p"}], at="2026-10-25T09:00:00+00:00")
    # google-aio: 1 of 3 runs mentions -> not_mentioned despite rank 1
    prompts.record_answer(prompt_id, "google-aio", "Acme.", [], at="2026-10-25T09:00:00+00:00")
    prompts.record_answer(prompt_id, "google-aio", "Globex is fine.", [], at="2026-10-25T09:00:00+00:00")
    prompts.record_answer(prompt_id, "google-aio", "Initech too.", [], at="2026-10-25T09:00:00+00:00")

    states = {s["platform"]: s for s in prompts.platform_states(prompt_id, platforms=prompts.PLATFORMS)}
    assert states["chatgpt-web"]["state"] == "doing_well" and states["chatgpt-web"]["runs"] == 2
    assert states["google-aio"]["state"] == "not_mentioned" and states["google-aio"]["runs"] == 3
    assert states["perplexity-web"]["state"] == "no_runs" and states["perplexity-web"]["runs"] == 0

    # a window filter narrows rows the same way the sweep will read them
    windowed = {s["platform"]: s for s in prompts.platform_states(prompt_id, since="2026-10-25", until="2026-10-25")}
    assert windowed["chatgpt-web"]["runs"] == 1
    assert windowed["google-aio"]["mentioned_runs"] == 1


def test_seed_prompts_pulls_paa_questions_and_watchlist_exactly_once():
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO paa_questions(question, seed, created_at) VALUES (?,?,?)",
            ("How do I track AI citations to my site?", "ai traffic", "2026-10-20T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO watchlist(keyword, competitor, added_at) VALUES (?,?,?)",
            ("best payroll tool", "globex.example", "2026-10-21T00:00:00Z"),
        )
    seeded = prompts.seed_prompts()
    assert seeded == {"paa": 1, "watchlist": 1}
    texts = {p["text"]: p for p in prompts.list_prompts()}
    assert set(texts) == {"How do I track AI citations to my site?", "best payroll tool"}
    assert texts["best payroll tool"]["category"] == "watchlist"
    # idempotent
    assert prompts.seed_prompts() == {"paa": 0, "watchlist": 0}
    assert len(prompts.list_prompts()) == 2


def test_the_single_tenant_brand_comes_from_settings():
    b = prompts.brand()
    assert b["name"] == "Acme" and b["domains"] == ["acme.example"]
    # the citation matcher covers subdomains of the one registrable domain
    assert prompts.own_domain_matcher(b["domains"])("https://docs.acme.example/x")
    assert not prompts.own_domain_matcher(b["domains"])("https://other.com/?ref=acme.example")
