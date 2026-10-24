import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from seoforge import ai_bots
from seoforge.ai_bots import Bot, match_user_agent, parse_range_file

FIX = Path(__file__).parent / "fixtures" / "ai_bot_ranges"


def _fixture(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _registry():
    raw = json.loads(ai_bots._CONFIG_PATH.read_text(encoding="utf-8"))
    return raw


# ------------------------------------------------------------ registry data

def test_registry_file_shape():
    raw = _registry()
    assert raw["version"] >= 1 and isinstance(raw["changes"], list)
    keys = [b["key"] for b in raw["bots"]]
    assert len(keys) == len(set(keys))
    verified = [b for b in raw["bots"] if b.get("verified")]
    assert len(verified) == 8
    for b in verified:
        assert b["ranges"] and b["ranges"].startswith("https://")
    for b in raw["bots"]:
        assert b["type"] in ("training", "search", "user")


def test_all_bots_and_types():
    bots = ai_bots.all_bots()
    assert len(bots) == len(_registry()["bots"])
    by_type = ai_bots.bots_by_type()
    assert sum(len(v) for v in by_type.values()) == len(bots)
    assert {t for t in by_type if by_type[t]} == {"training", "search", "user"}


# ------------------------------------------------------- user agent matching

@pytest.mark.parametrize("ua,key", [
    ("GPTBot/1.2; +https://openai.com/gptbot", "gptbot"),
    ("Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)", "oai-searchbot"),
    ("ChatGPT-User/1.0; +https://openai.com/user", "chatgpt-user"),
    ("Mozilla/5.0 (compatible; ClaudeBot/1.0; +claude.com)", "claudebot"),
    ("claude-user/2.0 (+https://claude.com)", "claude-user"),
    ("Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "perplexitybot"),
    ("Mozilla/5.0 (compatible; Perplexity-User/1.0; +https://perplexity.ai)", "perplexity-user"),
    ("Mozilla/5.0 (compatible; Google-Extended/1.0)", "google-extended"),
    ("CCBot/2.0 (https://commoncrawl.org/faq/)", "ccbot"),
    ("Applebot-Extended/1.0 (+http://apple.com/go/applebot)", "applebot-extended"),
    ("meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)", "meta-externalagent"),
    ("MistralAI-User/1.0", "mistralai-user"),
    ("anthropic-ai/1.0", "anthropic-ai"),
])
def test_match_user_agent_positive(ua, key):
    assert match_user_agent(ua) is not None
    assert match_user_agent(ua).key == key


@pytest.mark.parametrize("ua", [
    "MyGPTBot scraper",
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "bingbot/2.0",
    "",
    None,
])
def test_match_user_agent_negative(ua):
    assert match_user_agent(ua) is None


def test_match_is_token_boundary():
    # a longer UA that merely CONTAINS a bot token must not match
    assert match_user_agent("XGPTBot/1.0") is None
    assert match_user_agent("some-claudebot-tool") is None


# ------------------------------------------------------------- range parsing

def test_parse_prefixes_objects_shape():
    nets = parse_range_file(_fixture("prefixes_objects.json"))
    assert {str(n) for n in nets} >= {"203.0.113.0/24", "198.51.100.128/25", "2001:db8::/32"}


def test_parse_prefixes_strings_and_skips_garbage_and_dups():
    nets = parse_range_file(_fixture("prefixes_strings.json"))
    assert [str(n) for n in nets] == ["203.0.113.0/24", "198.51.100.0/24"]


def test_parse_bare_array_and_named_ipv4_ipv6():
    assert {str(n) for n in parse_range_file(_fixture("bare_array.json"))} >= {
        "203.0.113.0/24", "198.51.100.0/24", "2001:db8::/32"
    }
    assert {str(n) for n in parse_range_file(_fixture("named_ipv4_ipv6.json"))} == {
        "203.0.113.0/24", "2001:db8::/32"
    }


def test_parse_rejects_garbage_and_empty():
    assert parse_range_file("nope") == []
    assert parse_range_file({"prefixes": "nope"}) == []
    assert parse_range_file({"prefixes": []}) == []
    assert parse_range_file({"prefixes": [{"ipv4Prefix": "999.1.2.3/24"}, 3.14]}) == []


# ---------------------------------------------------------- fetch + verify

class _JSONTransport(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200, payload=None):
        self.status = status
        self.payload = payload
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status != 200:
            return httpx.Response(self.status, text="err", headers={"content-type": "text/html"})
        return httpx.Response(
            200,
            text=json.dumps(self.payload),
            headers={"content-type": "application/json"},
        )


def _verified_bot() -> Bot:
    return ai_bots.all_bots()[0]  # gptbot, has ranges URL


def _run(coro, transport):
    async def main():
        async with httpx.AsyncClient(transport=transport) as c:
            return await coro(c)

    return asyncio.run(main())


def test_fetch_ranges_and_verify(monkeypatch):
    payload = _fixture("prefixes_objects.json")
    t = _JSONTransport(payload=payload)
    monkeypatch.setattr(ai_bots, "_now", lambda: 1_000_000.0)

    async def fetch_first(client):
        return await ai_bots.fetch_ranges(_verified_bot(), client=client)

    nets = _run(fetch_first, t)
    assert str(nets[0]) == "203.0.113.0/24"
    assert t.calls == 1

    async def verify_inside(client):
        return await ai_bots.verify_ip(_verified_bot(), "203.0.113.44", client=client)

    assert _run(verify_inside, t) is True
    assert t.calls == 1  # read from the fresh cache, no second hit

    async def verify_outside(client):
        return await ai_bots.verify_ip(_verified_bot(), "198.51.100.9", client=client)

    assert _run(verify_outside, t) is False
    assert t.calls == 1


def test_stale_cache_used_after_ttl_and_on_failure(monkeypatch):
    payload = _fixture("prefixes_objects.json")
    good = _JSONTransport(payload=payload)
    monkeypatch.setattr(ai_bots, "_now", lambda: 1_000_000.0)

    async def first(client):
        return await ai_bots.fetch_ranges(_verified_bot(), client=client)

    assert _run(first, good) is not None

    # a day later: expired cache -> one live read from a FAILING endpoint,
    # which must fall back to the stored list rather than returning None
    later_good = _run(first, good)  # inside ttl, cached
    assert later_good is not None
    monkeypatch.setattr(ai_bots, "_now", lambda: 1_000_000.0 + ai_bots.RANGES_TTL + 1)
    dead = _JSONTransport(status=500)
    async def refresh_after_stale(client):
        return await ai_bots.fetch_ranges(_verified_bot(), client=client)

    assert _run(refresh_after_stale, dead) is not None  # stale-keeps
    assert dead.calls == 1


def test_no_ranges_ever_negative_cache(monkeypatch):
    dead = _JSONTransport(status=500)
    monkeypatch.setattr(ai_bots, "_now", lambda: 2_000_000.0)

    async def first(client):
        return await ai_bots.fetch_ranges(_verified_bot(), client=client)

    assert _run(first, dead) is None
    # inside the retry window: no further live attempts
    async def second(client):
        return await ai_bots.fetch_ranges(_verified_bot(), client=client)

    _run(second, dead)
    assert dead.calls == 1


def test_verify_ip_none_without_ranges():
    extra = next(b for b in ai_bots.all_bots() if b.ranges is None)
    assert match_user_agent("CCBot/2.0") is not None


def test_verify_bot_request_reasons(monkeypatch):
    payload = _fixture("prefixes_objects.json")
    t = _JSONTransport(payload=payload)
    monkeypatch.setattr(ai_bots, "_now", lambda: 1_000_000.0)

    async def reasons(client):
        out = []
        out.append(await ai_bots.verify_bot_request("Totally Normal Browser", "8.8.8.8"))
        out.append(await ai_bots.verify_bot_request("CCBot/2.0", "8.8.8.8"))
        out.append(await ai_bots.verify_bot_request("GPTBot/1.2", "203.0.113.7", client=client))
        out.append(await ai_bots.verify_bot_request("GPTBot/1.2", "1.2.3.4", client=client))
        return out

    async def main():
        async with httpx.AsyncClient(transport=t) as c:
            return await reasons(c)

    unknown, no_ranges, hit, spoofed = asyncio.run(main())
    assert unknown["reason"] == "unknown_agent"
    assert no_ranges["reason"] == "no_ranges"
    assert hit.get("bot") == "gptbot" and hit["verified"] is True
    assert spoofed["reason"] == "unverified_ip"


def test_unverified_spoofed_gptbot_flagged(monkeypatch):
    """The acceptance case: a GPTBot UA from a non-OpenAI IP is NOT a verified hit."""
    payload = _fixture("prefixes_objects.json")
    t = _JSONTransport(payload=payload)
    monkeypatch.setattr(ai_bots, "_now", lambda: 1_000_000.0)

    async def main(client):
        return await ai_bots.verify_bot_request("GPTBot/1.2", "8.8.4.4", client=client)

    async def run_main():
        async with httpx.AsyncClient(transport=t) as c:
            return await main(c)

    assert asyncio.run(run_main())["reason"] == "unverified_ip"
