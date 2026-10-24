import json
import re
from pathlib import Path

import pytest

from seoforge import ai_sources
from seoforge.ai_sources import (
    ai_source_pattern,
    classify,
    classify_ai_source,
    classify_ai_surface,
    landing_medium,
)

CONFIG = json.loads((Path("data") / "ai_sources.json").read_text(encoding="utf-8"))
SOURCES = ai_sources.all_sources()


def _source(key: str):
    return ai_sources.get_source(key)


# ------------------------------------------------------------ registry data

def test_registry_file_shape():
    assert CONFIG["version"] == 3
    keys = [s["key"] for s in CONFIG["sources"]]
    assert len(keys) == len(set(keys)) == 13
    assert keys == [
        "chatgpt.com", "perplexity.ai", "claude.ai", "gemini.google.com",
        "copilot.microsoft.com", "grok.com", "deepseek.com", "meta.ai",
        "you.com", "phind.com", "poe.com", "labs.google", "bing.com",
    ]
    bing = _source("bing.com")
    assert bing.referrerPath == "/ck/a"
    assert bing.utm is False and bing.analytics is False
    chatgpt = _source("chatgpt.com")
    assert ("shopping", "feed") == (
        next(s for s in chatgpt.surfaces)["key"], next(s for s in chatgpt.surfaces)["medium"]
    )


def test_every_source_matches_its_hosts_and_subdomains():
    for source in SOURCES:
        for host in source.hosts:
            # the raw referrer must carry a referrerPath-gated source's path
            stub = f"https://{host}/" + (source.referrerPath or "page")
            via_referrer = classify_ai_source(host, origin="referrer", referrer=stub)
            assert via_referrer == source.key, host
            via_subdomain = classify_ai_source(f"m.{host}", origin="referrer", referrer=stub)
            assert via_subdomain == source.key, f"m.{host}"
            if source.analytics:
                assert classify_ai_source(host, origin="analytics") == source.key
            else:
                assert classify_ai_source(host, origin="analytics") is None


def test_every_source_with_alias_matches_it_exactly_in_utm():
    for source in SOURCES:
        if not source.aliases:
            continue
        assert classify_ai_source(source.aliases[0], origin="utm") == source.key
        # aliases are exact: a decorated value is not the assistant
        assert classify_ai_source(f"not-{source.aliases[0]}", origin="utm") is None


# ------------------------------------------------------- matching mechanics

def test_substring_names_never_book_ai_traffic():
    # the real-world lesson: a *.workers.dev proxy with "claude" in its name
    assert classify("https://claude-proxy.workers.dev/page") is None
    assert classify_ai_source("greatchatgpt.com") is None
    assert classify_ai_source("notyou.com") is None
    # subdomains of listed hosts DO match (documented shapes)
    assert classify("https://search.chatgpt.com/") is not None
    assert classify_ai_source("m.perplexity.ai") == "perplexity.ai"


def test_normalize_strips_www_and_case():
    assert classify_ai_source("  WWW.Perplexity.AI  ") == "perplexity.ai"
    assert classify_ai_source(None) is None
    assert classify_ai_source(123) is None
    assert classify_ai_source("") is None


def test_unknown_origin_raises_and_empty_values_match_nothing():
    with pytest.raises(ValueError):
        classify_ai_source("chatgpt", origin="ga4")
    with pytest.raises(ValueError):
        ai_sources.ai_source_pattern(origin="nope")


# ---------------------------------------------------- bing's gated referrer

def test_bing_counts_only_through_its_redirect_path():
    assert classify_ai_source("bing.com", referrer="https://www.bing.com/ck/a?tok=x") == "bing.com"
    assert classify_ai_source("bing.com", referrer="https://www.bing.com/search?q=ai") is None
    assert classify_ai_source("bing.com", origin="utm", referrer="") is None


# -------------------------------------------------------- classify composite

def test_classify_referrer_first_then_utm_fallback():
    hit = classify("https://chatgpt.com/")
    assert hit.source == "chatgpt.com" and hit.origin == "referrer" and hit.surface is None

    fallback = classify("https://unrelated.example.com/", utm_source="perplexity")
    assert fallback.source == "perplexity.ai" and fallback.origin == "utm" and fallback.surface is None

    # a listed referrer wins over a different utm_source
    assert classify("https://claude.ai/", utm_source="openai").source == "claude.ai"
    assert classify(None, None) is None
    assert classify("", "") is None


def test_shopping_surface_from_medium_argument_or_landing_url():
    arg = classify("https://chatgpt.com/", utm_medium="feed")
    assert arg.source == "chatgpt.com" and arg.surface == "shopping"

    via_landing = classify(None, utm_source="openai", landing_url="https://shop.example/p?UTM_Medium=feed&id=1")
    assert via_landing.source == "chatgpt.com" and via_landing.surface == "shopping"

    not_shopping = classify("https://chatgpt.com/", utm_medium="rss-feed")
    assert not_shopping.surface is None

    upper = classify("https://chatgpt.com/", utm_medium="FEED")
    assert upper.surface == "shopping"  # exact medium, any case


def test_surface_is_source_scoped():
    assert classify_ai_surface("chatgpt.com", "feed") == "shopping"
    assert classify_ai_surface("perplexity.ai", "feed") is None
    assert classify_ai_surface("chatgpt.com", "") is None
    assert classify_ai_surface("nope.example", "feed") is None
    assert classify_ai_surface("chatgpt.com", None) is None


def test_landing_medium_first_param_any_case():
    assert landing_medium("https://x.com/p?a=1&utm_medium=feed&b=2") == "feed"
    assert landing_medium("https://x.com/p?UTM_MEDIUM=feed&x=1") == "feed"
    assert landing_medium("https://x.com/p?utm_medium=one&utm_medium=two") == "one"
    assert landing_medium("https://x.com/p?utm_source=chatgpt") == ""
    assert landing_medium("not a url") == ""
    assert landing_medium(None) == ""


# ------------------------------------------------------------- GA4 pattern

def test_ai_source_pattern_matches_hosts_subdomains_and_aliases():
    pattern = ai_source_pattern()  # analytics origin: bing excluded
    for good in (
        "perplexity.ai", "m.perplexity.ai", "chatgpt.com", "search.chatgpt.com",
        "gemini.google.com", "copilot.com", "grok.com",
        "chatgpt", "openai", "claude", "bard", "gemini", "copilot",
        "www.chatgpt",
    ):
        assert re.fullmatch(pattern, good), good
    for bad in (
        "bing.com", "greatchatgpt", "notyou.com", "chatgpt.com.evil.io",
        "claude-proxy.workers.dev", "claude-something", "",
    ):
        assert re.fullmatch(pattern, bad) is None, bad


def test_pattern_excludes_opted_out_by_origin():
    # every listed host at the referrer origin is in, bing included
    assert re.fullmatch(ai_source_pattern(origin="referrer"), "bing.com")
    assert "bing" not in ai_source_pattern(origin="analytics")


# ------------------------------------------------------------ summary view

def test_sources_summary_bounded():
    summary = ai_sources.sources_summary()
    assert summary["version"] == 3 and summary["count"] == 13
    assert summary["sources"][0]["key"] == "chatgpt.com"
    assert summary["sources"][-1]["referrer_path"] == "/ck/a"
    view = ai_sources.source_view(_source("claude.ai"))
    assert view["aliases"] == ["claude"] and view["surfaces"] == []
