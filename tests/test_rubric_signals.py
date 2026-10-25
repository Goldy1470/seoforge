"""The 34 deterministic rubric signals + the audit context builder.

Threshold buckets mirror the reference implementations; the AI-bot-access
cases are adapted to SEOForge's 17-bot registry. Everything runs offline:
contexts are built from fixture HTML, and the context-builder test pins a
fake resolver + MockTransport so no request ever leaves the machine.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from bs4 import BeautifulSoup

from seoforge import rubric, rubric_signals, safe_fetch
from seoforge.rubric_signals import AuditContext

PUBLIC = "93.184.216.34"  # resolving target for MockTransport tests
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()


# ------------------------------------------------------------------- ctx


def _ctx(
    html="",
    *,
    url: str = "https://example.com/page",
    protocol: str | None = None,
    robots_txt: str | None = "",
    llms_txt: str | None = "",
    now: float = NOW,
    text: str | None = None,
    html_bytes: int | None = None,
):
    """An AuditContext built from fixture HTML the same way the real builder does."""
    soup = BeautifulSoup(html, "lxml")
    loose = BeautifulSoup(html, "lxml")
    for tag in loose(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    body = loose.body or loose
    extracted = re.sub(r"\s+", " ", body.get_text(" ")).strip()
    if text is not None:
        extracted = text
    return AuditContext(
        url=url,
        origin=f"{urlsplit(url).scheme}://{urlsplit(url).netloc}",
        protocol=protocol if protocol is not None else urlsplit(url).scheme,
        status_code=200,
        html=html,
        html_bytes=html_bytes if html_bytes is not None else len(html.encode("utf-8")),
        soup=soup,
        text=extracted,
        word_count=len(extracted.split()) if extracted else 0,
        robots_txt=robots_txt,
        llms_txt=llms_txt,
        query=None,
        now=now,
    )


def _product_ld(**fields) -> str:
    payload = {"@context": "https://schema.org", "@type": "Product", **fields}
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


# ------------------------------------------------------- context builder


def _sig(key):
    return next(s for s in rubric_signals.signal_registry if s.key == key)


def test_normalize_url_adds_scheme_and_rejects_rubbish():
    assert rubric_signals.normalize_url("example.com/page") == "https://example.com/page"
    assert rubric_signals.normalize_url(" http://example.io ") == "http://example.io"
    # The reference's URL parser reads a stray "ftp:" as a host name too.
    assert rubric_signals.normalize_url("ftp://example.io") == "https://ftp://example.io"
    with pytest.raises(rubric_signals.AuditPageError) as err:
        rubric_signals.normalize_url("  ")
    assert err.value.status == 400
    # A URL parser would also choke: whitespace in the host, or no host at all.
    for bad in ("not a url at all!!", "https://"):
        with pytest.raises(rubric_signals.AuditPageError) as err:
            rubric_signals.normalize_url(bad)
        assert err.value.status == 400


def test_build_audit_context_fetches_page_robots_and_llms(monkeypatch):
    async def fake_resolve(host, resolver=None):  # matches safe_fetch._resolve's signature
        return [PUBLIC]

    monkeypatch.setattr(safe_fetch, "_resolve", fake_resolve)
    page_html = (
        "<html><head><style>.x{color:red}</style>"
        '<script type="application/ld+json">{"junk":true}</script></head>'
        "<body><h1>Pricing</h1><p>Plans for every team.</p></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"})
        if path.endswith("/llms.txt"):
            return httpx.Response(404, text="nope", headers={"content-type": "text/plain"})
        return httpx.Response(200, text=page_html, headers={"content-type": "text/html; charset=utf-8"})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await rubric_signals.build_audit_context(
                "https://example.com/pricing", client=client, now=NOW
            )

    ctx = asyncio.run(main())
    assert ctx.url == "https://example.com/pricing"
    assert ctx.origin == "https://example.com"
    assert ctx.protocol == "https"
    assert ctx.status_code == 200
    assert ctx.html_bytes == len(page_html.encode("utf-8"))
    assert ctx.robots_txt == "User-agent: *\nAllow: /"
    assert ctx.llms_txt is None  # 404 -> not fetched
    assert "Pricing" in ctx.text and ctx.word_count >= 5
    # script/style noise stays out of the readable text
    assert "junk" not in ctx.text and "color:red" not in ctx.text


def test_build_audit_context_raises_on_http_error(monkeypatch):
    async def fake_resolve(host, resolver=None):
        return [PUBLIC]

    monkeypatch.setattr(safe_fetch, "_resolve", fake_resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom", headers={"content-type": "text/html"})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await rubric_signals.build_audit_context("https://example.com/", client=client)

    with pytest.raises(rubric_signals.AuditPageError) as err:
        asyncio.run(main())
    assert err.value.status == 500


# --------------------------------------------------------- structure (13)


def test_structural_depth_passes_on_nested_clean_heading_tree():
    html = "<h1>A</h1><h2>B</h2><h3>C</h3>"
    v = _sig("structural-depth").evaluate(_ctx(html))
    assert v["status"] == "pass" and v["score"] == 1
    assert v["evidence"] == {"depth": 3, "levelSkips": 0, "levels": [1, 2, 3]}


def test_structural_depth_warns_or_fails_on_flat_or_broken_headings():
    for html, status, score in [
        ("<h1>A</h1>", "fail", 0.2),
        ("<h1>A</h1><h3>B</h3>", "warn", 0.6),  # level skip, depth 2
    ]:
        v = _sig("structural-depth").evaluate(_ctx(html))
        assert v["status"] == status and v["score"] == score


def test_internal_linking_buckets():
    five = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(5))
    assert _sig("internal-linking").evaluate(_ctx(five))["status"] == "pass"
    assert _sig("internal-linking").evaluate(_ctx('<a href="/one">o</a>'))["status"] == "warn"
    v = _sig("internal-linking").evaluate(_ctx("<p>none</p>"))
    assert v["status"] == "fail" and v["score"] == 0


def test_page_weight_buckets():
    kinds = [
        (200 * 1024, "pass", 1),
        (600 * 1024, "warn", 0.5),
        (1100 * 1024, "fail", 0),
    ]
    for size, status, score in kinds:
        ctx = _ctx("<p>hi</p>", html_bytes=size)
        v = _sig("page-weight").evaluate(ctx)
        assert v["status"] == status and v["score"] == score


def test_json_ld_presence_and_validity_buckets():
    good = _product_ld(name="X", brand="B", description="ok")
    broken = '<script type="application/ld+json">{not json'
    html = f"<head>{good}{broken}</head>"
    assert _sig("json-ld-presence").evaluate(_ctx(html))["status"] == "pass"
    v = _sig("json-ld-validity").evaluate(_ctx(html))
    assert v["status"] == "warn" and v["evidence"]["parsed"] == 1 and v["evidence"]["blocks"] == 2
    v = _sig("json-ld-validity").evaluate(_ctx("<head></head>"))
    assert v == {"status": "fail", "score": 0, "evidence": {"reason": "no JSON-LD blocks"}}


def test_json_ld_relevance_prefers_high_value_types():
    v = _sig("json-ld-relevance").evaluate(_ctx(f"<head>{_product_ld(name='X')}</head>"))
    assert v == {"status": "pass", "score": 1, "evidence": {"types": ["product"]}}
    v = _sig("json-ld-relevance").evaluate(_ctx('<script type="application/ld+json">{"@type":"WebPage"}</script>'))
    assert v["status"] == "warn" and v["score"] == 0.5
    assert _sig("json-ld-relevance").evaluate(_ctx("<head></head>"))["status"] == "fail"


def test_faq_schema_needs_two_questions():
    two = json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"name": f"q{i}"} for i in range(2)],
    })
    assert _sig("faq-schema").evaluate(_ctx(f'<script type="application/ld+json">{two}</script>'))["status"] == "pass"
    one = json.dumps({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"name": "q0"}]})
    assert _sig("faq-schema").evaluate(_ctx(f'<script type="application/ld+json">{one}</script>'))["status"] == "fail"


def test_h1_quality_buckets():
    assert _sig("h1-quality").evaluate(_ctx("<h1>Between twenty and seventy characters</h1>"))["status"] == "pass"
    v = _sig("h1-quality").evaluate(_ctx("<h1>First heading of the article here</h1><h1>Second heading too</h1>"))
    assert v["status"] == "warn" and v["evidence"]["count"] == 2
    assert _sig("h1-quality").evaluate(_ctx("<p>none</p>"))["status"] == "fail"


def test_h2_coverage_buckets():
    assert _sig("h2-coverage").evaluate(_ctx("<h2>a</h2><h2>b</h2><h2>c</h2>"))["status"] == "pass"
    assert _sig("h2-coverage").evaluate(_ctx("<h2>a</h2>"))["status"] == "warn"
    assert _sig("h2-coverage").evaluate(_ctx("<p>x</p>"))["status"] == "fail"


def test_lists_buckets():
    assert _sig("lists").evaluate(_ctx("<ul><li>1</li><li>2</li><li>3</li></ul>"))["status"] == "pass"
    v = _sig("lists").evaluate(_ctx("<ul><li>1</li><li>2</li></ul>"))
    assert v["status"] == "warn" and v["score"] == 0.5
    assert _sig("lists").evaluate(_ctx("<p>x</p>"))["status"] == "fail"


def test_tables_buckets():
    assert _sig("tables").evaluate(_ctx("<table><thead><tr><th>t</th></tr></thead><tbody><tr><td>d</td></tr></tbody></table>"))["status"] == "pass"
    assert _sig("tables").evaluate(_ctx("<table><tr><td>d</td></tr></table>"))["status"] == "warn"
    assert _sig("tables").evaluate(_ctx("<p>x</p>"))["status"] == "fail"


def test_alt_text_buckets():
    assert _sig("alt-text").evaluate(_ctx("<p>x</p>")) == {"status": "na", "score": None, "evidence": {"images": 0}}
    v = _sig("alt-text").evaluate(_ctx('<img alt="a"><img src="b.png">'))
    assert v["status"] == "warn" and v["score"] == 0.5
    v = _sig("alt-text").evaluate(_ctx('<img alt="a"><img alt="b">'))
    assert v["status"] == "pass"
    v = _sig("alt-text").evaluate(_ctx('<img src="a"><img src="b">'))
    assert v["status"] == "fail"


def test_open_graph_buckets():
    four = "".join(f'<meta property="og:{k}" content="v">'
                   for k in ("title", "description", "image", "type"))
    assert _sig("open-graph").evaluate(_ctx(four)) == {
        "status": "pass", "score": 1,
        "evidence": {"present": ["og:title", "og:description", "og:image", "og:type"], "missing": []},
    }
    v = _sig("open-graph").evaluate(_ctx('<meta property="og:title" content="v">'))
    assert v["status"] == "warn" and v["score"] == 0.25
    assert _sig("open-graph").evaluate(_ctx("<p>x</p>"))["status"] == "fail"


# -------------------------------------------------------- authority (7)


def test_outbound_authority_links_buckets():
    two = '<a href="https://en.wikipedia.org/wiki/SEO">w</a><a href="https://www.nature.com">n</a>'
    one = '<a href="https://www.nytimes.com">nyt</a>'
    assert _sig("outbound-authority-links").evaluate(_ctx(two))["status"] == "pass"
    v = _sig("outbound-authority-links").evaluate(_ctx(one))
    assert v["status"] == "warn" and v["evidence"]["count"] == 1
    v = _sig("outbound-authority-links").evaluate(_ctx('<a href="/local">l</a>'))
    assert v["status"] == "fail"


def test_citation_density_buckets():
    two = '<a href="https://a.io">1</a><a href="https://b.io">2</a><p>ten words here now</p>'
    assert _sig("citation-density").evaluate(_ctx(two))["status"] == "pass"
    sparse = "word " * 520 + '<a href="https://a.io">1</a>'  # 1 per ~521 words
    v = _sig("citation-density").evaluate(_ctx(sparse))
    assert v["status"] == "warn"
    v = _sig("citation-density").evaluate(_ctx("<p>no links</p>"))
    assert v["status"] == "fail"


def test_statistic_density_buckets():
    rich = "word " * 10 + "40% $2M 15 people"  # 3 numeric claims in 13 tokens
    assert _sig("statistic-density").evaluate(_ctx(rich))["status"] == "pass"
    thin = "word " * 1329 + "40% and 15"  # 2 in 1332 tokens -> ~1.5/1000
    assert _sig("statistic-density").evaluate(_ctx(thin))["status"] == "warn"
    assert _sig("statistic-density").evaluate(_ctx("no numbers here at all"))["status"] == "fail"


def test_quotations():
    assert _sig("quotations").evaluate(_ctx("<blockquote>real quote</blockquote>"))["status"] == "pass"
    v = _sig("quotations").evaluate(_ctx("<p>x</p>"))
    assert v == {"status": "fail", "score": 0, "evidence": {"quotations": 0}}


def test_author_byline_sources():
    byline = json.dumps({"@context": "https://schema.org", "@type": "Article", "author": {"name": "Jane Doe"}})
    v = _sig("author-byline").evaluate(_ctx(f'<script type="application/ld+json">{byline}</script>'))
    assert v["status"] == "pass" and v["evidence"]["schemaAuthor"] is True
    v = _sig("author-byline").evaluate(_ctx('<meta name="author" content="Jane Doe">'))
    assert v["status"] == "pass" and v["evidence"]["metaAuthor"] is True
    v = _sig("author-byline").evaluate(_ctx('<div class="author-byline">Jane Doe</div>'))
    assert v["status"] == "pass" and v["evidence"]["relAuthor"] is True
    v = _sig("author-byline").evaluate(_ctx("<p>Nothing that names a writer.</p>"))
    assert v["status"] == "fail"


def test_date_markup_and_freshness_buckets():
    fresh = _ctx('<meta property="article:published_time" content="2025-09-01T08:00:00Z">')
    assert _sig("date-markup").evaluate(fresh)["status"] == "pass"
    assert _sig("freshness").evaluate(fresh)["status"] == "pass"  # 272 days old

    stale = _ctx('<time datetime="2024-09-15">Sep 2024</time>')
    assert _sig("freshness").evaluate(stale)["status"] == "warn"
    ancient = _ctx('<time datetime="2023-01-01">Jan 2023</time>')
    assert _sig("freshness").evaluate(ancient)["status"] == "fail"
    assert _sig("freshness").evaluate(_ctx("<p>x</p>"))["status"] == "fail"
    assert _sig("date-markup").evaluate(_ctx("<p>x</p>"))["status"] == "fail"


# --------------------------------------------------------- content (4)


def test_readability_buckets():
    easy = "the cat sat on the mat and the dog ran in the sun " * 4 + "the big red hen"  # 56 one-syllable words, 1 sentence
    assert _sig("readability").evaluate(_ctx(text=easy))["status"] == "pass"
    mid = "the cat sat on the mat and the dog ran in the sun " * 6 + "the big red hen sat"  # 83 words
    assert _sig("readability").evaluate(_ctx(text=mid))["status"] == "warn"
    v = _sig("readability").evaluate(_ctx("<p>too short</p>"))
    assert v["status"] == "na" and v["score"] is None


def test_length_buckets():
    assert _sig("length").evaluate(_ctx(text="word " * 900))["status"] == "pass"
    assert _sig("length").evaluate(_ctx(text="word " * 500))["status"] == "warn"
    v = _sig("length").evaluate(_ctx(text="too few words here"))
    assert v["status"] == "fail" and v["score"] == 0


def test_youtube_embed():
    assert _sig("youtube-embed").evaluate(
        _ctx('<iframe src="https://www.youtube.com/embed/x"></iframe>')
    )["status"] == "pass"
    v = _sig("youtube-embed").evaluate(_ctx('<iframe src="https://vimeo.com/1"></iframe>'))
    assert v == {"status": "fail", "score": 0, "evidence": {"youtubeEmbed": False}}


def test_product_description_opening_passes_a_good_product_page():
    html = (
        _product_ld(
            name="Acme Ridge Men’s Trail Running Shoe",
            brand={"@type": "Brand", "name": "Acme"},
            description="Men’s trail running shoe, 268 g, 6 mm drop, with a grippy outsole for wet rock and mud.",
        )
    )
    v = _sig("product-description-opening").evaluate(_ctx(f"<head>{html}</head>"))
    assert v["status"] == "pass" and v["score"] == 1
    assert "268 g" in v["evidence"]["attributes"] and "6 mm" in v["evidence"]["attributes"]
    assert "Product type named there: yes" in v["evidence"]["reason"]


def test_product_description_opening_fails_on_brand_story_opening():
    description = (
        "Since 1987, our family has crafted every pair by hand in a small workshop at the foot of the Alps. "
        "We believe the mountains belong to everyone, and every step you take carries a little of our story. "
        "This men’s trail running shoe weighs 268 g and has a 6 mm drop."
    )
    html = _product_ld(name="Acme Men’s Trail Running Shoe", brand="Acme", description=description)
    v = _sig("product-description-opening").evaluate(_ctx(f"<head>{html}</head>"))
    assert v["status"] == "fail" and v["score"] == 0
    assert v["evidence"]["cut"] < description.index("trail running shoe")
    assert "Product type named there: no" in v["evidence"]["reason"]
    assert "Key attributes there: none" in v["evidence"]["reason"]


def test_product_description_opening_warns_with_one_attribute():
    html = _product_ld(
        name="Summit Daypack Backpack",
        brand="Summit",
        description="A lightweight backpack for every adventure, made by people who care.",
    )
    v = _sig("product-description-opening").evaluate(_ctx(f"<head>{html}</head>"))
    assert v["status"] == "warn" and v["score"] == 0.5


def test_product_description_opening_reads_microdata_description():
    html = (
        _product_ld(name="Northwind Crew Neck T-Shirt", brand="Northwind")
        + '<div itemprop="description"><p>Slim fit organic cotton crew neck t-shirt.</p></div>'
    )
    v = _sig("product-description-opening").evaluate(_ctx(f"<body>{html}</body>"))
    assert v["status"] == "pass"


def test_product_description_opening_na_rules():
    assert _sig("product-description-opening").evaluate(_ctx("<title>Blog</title>"))["status"] == "na"
    html = _product_ld(name="Acme Trail Shoe", brand="Acme")  # no description anywhere
    assert _sig("product-description-opening").evaluate(_ctx(f"<head>{html}</head>"))["status"] == "na"


# ------------------------------------------------------------- trust (7)


def test_https_and_mixed_content():
    assert _sig("https").evaluate(_ctx("<p>x</p>")) == {
        "status": "pass", "score": 1, "evidence": {"https": True, "mixedContentRefs": 0},
    }
    v = _sig("https").evaluate(_ctx('<script src="http://example.com/x.js"></script>'))
    assert v["status"] == "warn" and v["evidence"]["mixedContentRefs"] == 1
    v = _sig("https").evaluate(_ctx("<p>x</p>", url="http://example.com/page"))
    assert v == {"status": "fail", "score": 0, "evidence": {"https": False, "mixedContentRefs": 0}}


def test_ai_bot_access_names_blocked_bots_and_fails():
    robots = (
        "User-agent: GPTBot\nDisallow: /\n\n"
        "User-agent: ClaudeBot\nAllow: /\n\n"
        "User-agent: PerplexityBot\nDisallow: /"
    )
    v = _sig("ai-bot-access").evaluate(_ctx("", robots_txt=robots))
    assert v == {
        "status": "fail", "score": 0,
        "evidence": {"aiBotsMentioned": ["gptbot", "claudebot", "perplexitybot"],
                     "blocked": True, "blockedBots": ["gptbot", "perplexitybot"]},
    }


def test_ai_bot_access_group_scoping():
    # A later group's rule is not read as an allowed bot's rule.
    robots = "User-agent: ClaudeBot\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /"
    assert _sig("ai-bot-access").evaluate(_ctx("", robots_txt=robots))["evidence"]["blockedBots"] == ["gptbot"]
    # One group may name several agents; it blocks each tracked bot among them.
    robots = "User-agent: GPTBot\nUser-agent: ClaudeBot\nDisallow: / # all"
    assert _sig("ai-bot-access").evaluate(_ctx("", robots_txt=robots))["evidence"]["blockedBots"] == [
        "gptbot", "claudebot"
    ]


def test_ai_bot_access_allow_wins_and_reads_versions():
    # The group naming the bot allows the site, while a later "*" group blocks all.
    robots = "User-agent: ClaudeBot\nAllow: /\n\nUser-agent: *\nDisallow: /"
    v = _sig("ai-bot-access").evaluate(_ctx("", robots_txt=robots))
    assert v["status"] == "pass" and v["evidence"]["blocked"] is False
    # A version after the name still names the bot.
    v = _sig("ai-bot-access").evaluate(_ctx("", robots_txt="User-agent: GPTBot/1.1\nDisallow: /"))
    assert v["status"] == "fail" and v["evidence"]["blockedBots"] == ["gptbot"]
    # A bot's groups count together, and allowing the whole site wins a tie.
    v = _sig("ai-bot-access").evaluate(
        _ctx("", robots_txt="User-agent: GPTBot\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /")
    )
    assert v["status"] == "pass"


def test_ai_bot_access_without_robots_or_mentions():
    v = _sig("ai-bot-access").evaluate(_ctx("", robots_txt=None))
    assert v == {"status": "warn", "score": 0.5, "evidence": {"robotsTxt": False}}
    v = _sig("ai-bot-access").evaluate(_ctx("", robots_txt="User-agent: *\nDisallow: /tmp"))
    assert v["status"] == "warn" and v["evidence"]["aiBotsMentioned"] == []


def test_llms_txt_canonical_viewport_buckets():
    assert _sig("llms-txt-presence").evaluate(_ctx("", llms_txt="# llms.txt"))["status"] == "pass"
    assert _sig("llms-txt-presence").evaluate(_ctx(""))["status"] == "fail"

    assert _sig("canonical").evaluate(_ctx('<link rel="canonical" href="https://example.com/x">'))["status"] == "pass"
    assert _sig("canonical").evaluate(_ctx(""))["status"] == "fail"

    assert _sig("viewport").evaluate(_ctx('<meta name="viewport" content="width=device-width">'))["status"] == "pass"
    assert _sig("viewport").evaluate(_ctx(""))["status"] == "fail"


def test_twitter_card_and_meta_description_buckets():
    card = '<meta name="twitter:card" content="summary"><meta name="twitter:image" content="x.png">'
    assert _sig("twitter-card").evaluate(_ctx(card))["status"] == "pass"
    v = _sig("twitter-card").evaluate(_ctx('<meta name="twitter:card" content="summary">'))
    assert v["status"] == "warn" and v["evidence"]["hasImage"] is False
    assert _sig("twitter-card").evaluate(_ctx(""))["status"] == "fail"

    good = "An honest guide to AI visibility for growing teams, covering audits, fixes and monitoring in depth."  # 105 chars
    assert _sig("meta-description").evaluate(_ctx(f'<meta name="description" content="{good}">'))["status"] == "pass"
    assert _sig("meta-description").evaluate(_ctx('<meta name="description" content="too short">'))["status"] == "warn"
    assert _sig("meta-description").evaluate(_ctx(""))["status"] == "fail"


# ------------------------------------------------------------ e-e-a-t (3)


def test_first_person_buckets():
    text = "We tested 12 tools over six months. We found gaps in every audit. In our testing the winner was clear."
    assert _sig("first-person").evaluate(_ctx(text=text))["status"] == "pass"
    v = _sig("first-person").evaluate(_ctx(text="We tested 12 tools and the result was close."))
    assert v["status"] == "warn" and v["score"] == 0.5
    v = _sig("first-person").evaluate(_ctx(text="A neutral page with no first person evidence at all here."))
    assert v["status"] == "fail"


def test_privacy_terms_buckets():
    links = '<a href="/privacy">Privacy Policy</a> <a href="/terms">Terms of Service</a>'
    assert _sig("privacy-terms").evaluate(_ctx(links))["status"] == "pass"
    v = _sig("privacy-terms").evaluate(_ctx('<a href="/privacy-policy">privacy</a>'))
    assert v["status"] == "warn" and v["evidence"]["terms"] is False
    assert _sig("privacy-terms").evaluate(_ctx('<a href="/docs">docs</a>'))["status"] == "fail"


def test_contact_info_buckets():
    assert _sig("contact-info").evaluate(_ctx('<a href="mailto:hi@example.com">mail</a>'))["status"] == "pass"
    assert _sig("contact-info").evaluate(_ctx(text="write to hi@example.net any time"))["status"] == "pass"
    v = _sig("contact-info").evaluate(_ctx('<a href="/about">about</a>'))
    assert v == {"status": "fail", "score": 0,
                 "evidence": {"mailto": False, "tel": False, "contactLink": False, "emailInText": False}}


# --------------------------------------------------------- registry + engine


def test_registry_covers_rubric_minus_deferred():
    keys = [s.key for s in rubric_signals.signal_registry]
    assert len(keys) == 34 and len(set(keys)) == 34
    assert len(rubric_signals.DEFERRED_SIGNAL_KEYS) == 14
    assert set(rubric.SIGNALS_BY_KEY) == set(keys) | set(rubric_signals.DEFERRED_SIGNAL_KEYS)
    sizes = [rubric_signals.STRUCTURE_SIGNALS, rubric_signals.AUTHORITY_SIGNALS,
             rubric_signals.CONTENT_SIGNALS, rubric_signals.TRUST_SIGNALS,
             rubric_signals.EEAT_SIGNALS]
    assert [len(s) for s in sizes] == [13, 7, 4, 7, 3]


def test_signal_keys_align_with_rubric_descriptors():
    for sig in rubric_signals.signal_registry:
        descriptor = rubric.SIGNALS_BY_KEY[sig.key]
        assert descriptor["category"] and descriptor["label"], sig.key


def test_run_signals_records_na_when_the_context_is_broken():
    ctx = SimpleNamespace()  # nothing useful on it: every evaluator raises
    rows = rubric.run_signals(ctx)
    assert len(rows) == 34
    assert all(r["status"] == "na" and r["score"] is None for r in rows)
    assert all("error" in r["evidence"] for r in rows)
    scored = rubric.score_audit(rows)
    assert scored["total_score"] is None


def test_run_signals_over_a_rich_page_keeps_rows_in_shape():
    html = (
        "<html><head>"
        '<meta name="description" content="An honest guide to AI visibility for growing teams, covering audits.">'
        '<meta name="viewport" content="width=device-width">'
        '<link rel="canonical" href="https://example.com/page">'
        + _product_ld(name="Acme Ridge Shoe", brand="Acme", description="Trail running shoe, 292 g, waterproof.")
        + "</head><body><h1>A Ridge Shoe For Trail Days</h1><p>word word word word word word word word.</p>"
        '<a href="/one">1</a><a href="/two">2</a><a href="/three">3</a><a href="/four">4</a><a href="/five">5</a>'
        '<a href="https://en.wikipedia.org/wiki/SEO">w</a>'
        "</body></html>"
    )
    ctx = _ctx(html)
    rows = rubric.run_signals(ctx)
    assert [r["key"] for r in rows] == [s.key for s in rubric_signals.signal_registry]
    for row in rows:
        assert row["status"] in {"pass", "warn", "fail", "na"}
        if row["status"] != "na":
            assert isinstance(row["score"], (int, float)) and 0 <= row["score"] <= 1


# ------------------------------------------------------------- audit_url


RICH_HTML = (
    "<html><head>"
    '<meta name="description" content="An honest guide to AI visibility for growing teams, covering audits">'
    '<meta name="viewport" content="width=device-width">'
    '<meta property="article:published_time" content="2025-09-01T08:00:00Z">'
    '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article",'
    '"author":{"name":"Jane Doe"},"headline":"A Complete Guide"}</script>'
    '<script type="application/ld+json">{"@context":"https://schema.org","@type":"FAQPage",'
    '"mainEntity":[{"name":"q0"},{"name":"q1"}]}</script>'
    "</head><body>"
    "<h1>A Complete Guide To AI Visibility For Teams</h1><h2>One</h2><h2>Two</h2><h2>Three</h2>"
    "<ul><li>a</li><li>b</li><li>c</li></ul>"
    '<a href="/one">1</a><a href="/two">2</a><a href="/three">3</a><a href="/four">4</a><a href="/five">5</a>'
    '<a href="https://en.wikipedia.org/wiki/SEO">w</a><a href="https://www.nature.com">n</a>'
    "<blockquote>We tested 12 tools and found gaps in every audit.</blockquote>"
    "</body></html>"
)


def test_audit_url_runs_scores_and_persists(monkeypatch):
    ctx = _ctx(RICH_HTML, robots_txt="User-agent: *\nAllow: /", llms_txt="# llms.txt\n\n## Guides\n- /guide")

    async def fake_build(raw_url, *, query=None, client=None, now=None):
        assert raw_url == "example.com/guide" or raw_url == "https://example.com/guide"
        assert query == "how do I get cited"
        return ctx

    monkeypatch.setattr(rubric_signals, "build_audit_context", fake_build)

    report = rubric_signals.audit_url("example.com/guide", query="how do I get cited")
    assert report["url"] == "https://example.com/page"
    assert len(report["signals"]) == 34
    assert len(report["deferred"]) == 14
    statuses = {s["status"] for s in report["signals"]}
    assert statuses <= {"pass", "warn", "fail", "na"}
    # categories all evaluated on this fixture page
    cats = report["score"]["category_scores"]
    assert all(c["evaluated"] > 0 for c in cats.values())
    assert 0 <= report["score"]["total_score"] <= 1
    labels = [s["label"] for s in report["signals"]]
    assert all(labels)  # every signal row carries its descriptor label
    assert {s["category"] for s in report["signals"]} == {"structure", "authority", "content", "trust", "eeat"}
    # the report is persisted for history / the dashboard
    from seoforge import db

    with db._conn() as conn:
        stored_row = conn.execute(
            "SELECT payload FROM reports WHERE kind='rubric_audit' AND id=?",
            (report["report_id"],),
        ).fetchone()
    stored = json.loads(stored_row["payload"])
    assert stored["url"] == "https://example.com/page"
    assert abs(stored["score"]["total_score"] - report["score"]["total_score"]) < 1e-9


def test_audit_url_surfaces_audit_page_errors(monkeypatch):
    async def fake_build(raw_url, **kwargs):
        raise rubric_signals.AuditPageError(403, "Blocked by the site")

    monkeypatch.setattr(rubric_signals, "build_audit_context", fake_build)
    with pytest.raises(rubric_signals.AuditPageError) as err:
        rubric_signals.audit_url("https://blocked.example/")
    assert err.value.status == 403
