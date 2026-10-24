"""AI crawler registry: which user agents are AI bots, what they do, and
whether a hit actually comes from the operator's published IP ranges.

Registry lives in `data/ai_bots.json` (versioned file; see the module doc in
THIRD_PARTY_NOTICES.md for the derivation). Semantics ported from the
Ansvisor approach, re-implemented for Python:

* A token matches a user agent only on token boundaries — "GPTBot/1.2" and
  "(compatible; ClaudeBot/1.0; …)" match, "MyGPTBot" does not. User agents
  are easy to fake; the published IP ranges are not.
* Operator range files are fetched through the SSRF-safe fetcher, parsed
  tolerantly (operators speak different shapes) and cached in SQLite
  (`ai_bot_ranges`) for 24 h. A failed refresh keeps any previously read
  list; with none ever read, verification is unavailable (None) and the
  next attempt is held off briefly so a batch of events doesn't queue up
  on a dead endpoint.
"""
from __future__ import annotations

import ipaddress
import json
import re
import time
from dataclasses import asdict, dataclass

import httpx

from . import db, safe_fetch
from .config import ROOT

_CONFIG_PATH = ROOT / "data" / "ai_bots.json"

RANGES_TTL = 24 * 60 * 60
RANGES_RETRY = 5 * 60

_NETWORK_KEYS = ("ipv4Prefix", "ipv6Prefix", "prefix", "cidr", "ip", "range")


@dataclass(frozen=True)
class Bot:
    key: str
    name: str
    operator: str
    type: str  # training | search | user
    userAgent: str
    ranges: str | None
    verified: bool


def _load_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


_CONFIG = _load_config()
_CONFIG_VERSION = _CONFIG.get("version", 1)
_BOTS = [
    Bot(
        key=b["key"],
        name=b["name"],
        operator=b["operator"],
        type=b["type"],
        userAgent=b["userAgent"],
        ranges=b.get("ranges"),
        verified=bool(b.get("verified", False)),
    )
    for b in _CONFIG["bots"]
]


def all_bots() -> list[Bot]:
    """Every registry bot, registry order (which is also match order)."""
    return list(_BOTS)


def bots_by_type() -> dict:
    out: dict = {t: [] for t in ("training", "search", "user")}
    for bot in _BOTS:
        out.setdefault(bot.type, []).append(bot)
    return out


def bot_view(bot: Bot) -> dict:
    """JSON-ready bot record (ranges empty string rather than null)."""
    d = asdict(bot)
    d["ranges"] = bot.ranges or ""
    return d


def registry_summary() -> dict:
    """Registry overview for CLI / MCP output (bounded, no ranges fetched)."""
    bots = all_bots()
    return {
        "version": _CONFIG_VERSION,
        "count": len(bots),
        "verified_with_ranges": sum(1 for b in bots if b.verified),
        "by_type": {t: len(v) for t, v in bots_by_type().items()},
        "bots": [bot_view(b) for b in bots],
    }


_MATCHERS = [
    (bot, re.compile(rf"(^|[^a-z0-9-]){re.escape(bot.userAgent)}([^a-z0-9-]|$)", re.I))
    for bot in _BOTS
]


def match_user_agent(user_agent: str | None) -> Bot | None:
    """The bot a user agent names, or None. Token-boundary matching only."""
    if not isinstance(user_agent, str) or not user_agent:
        return None
    for bot, matcher in _MATCHERS:
        if matcher.search(user_agent):
            return bot
    return None


# ------------------------------------------------------------------ parsers


def _network(value: str) -> ipaddress._BaseNetwork | None:
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except (ValueError, AttributeError):
        return None


def parse_range_file(payload) -> list:
    """Tolerant parser for the operators' published range JSON shapes.

    Handles (their current + likely variants, never trusting one format):
    {"prefixes": [{"ipv4Prefix": "...", "ipv6Prefix": "..."}]} — the CURRENT
    shape of openai.com, claude.com and perplexity.com files; {"prefixes":
    ["a.b.c.d/e", ...]}; a bare JSON array of CIDR strings or objects;
    {"ranges"|"ips"|"addresses": [...]}; {"ipv4": [...], "ipv6": [...]};
    objects keyed {"prefix"|"cidr"|"ip"|"range": "..."}. Malformed entries
    are skipped, not fatal.
    """
    candidates: list = []
    if isinstance(payload, dict):
        prefixes = payload.get("prefixes")
        if isinstance(prefixes, list):
            candidates += prefixes
        for key in ("ranges", "ips", "addresses", "ipv4", "ipv6"):
            if isinstance(payload.get(key), list):
                candidates += payload[key]
    elif isinstance(payload, list):
        candidates = list(payload)
    out: list = []
    seen: set = set()
    for item in candidates:
        raw = item
        if isinstance(item, dict):
            for k in _NETWORK_KEYS:
                if raw := item.get(k):
                    break
        if not isinstance(raw, str):
            continue
        net = _network(raw)
        if net is None:
            continue
        if str(net) not in seen:
            seen.add(str(net))
            out.append(net)
    return out


# ------------------------------------------------------------ range fetching


def _now() -> float:
    return time.time()


def _cached(url: str):
    with db._conn() as conn:
        row = conn.execute(
            "SELECT prefixes_json, fetched_at FROM ai_bot_ranges WHERE url=?", (url,)
        ).fetchone()
    if row is None:
        return None, None, None
    raw = row["prefixes_json"]
    parsed = None
    if raw:
        try:
            parsed = [ipaddress.ip_network(n) for n in json.loads(raw)] or None
        except ValueError:
            parsed = None
    return raw, row["fetched_at"], parsed


def _store(url: str, prefixes_json: str, fetched_at: float) -> None:
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO ai_bot_ranges(url, prefixes_json, fetched_at) VALUES (?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET prefixes_json=excluded.prefixes_json, "
            "fetched_at=excluded.fetched_at",
            (url, prefixes_json, fetched_at),
        )


async def fetch_ranges(bot: Bot, *, client: httpx.AsyncClient | None = None) -> list | None:
    """The bot's published ranges as ip_network objects.

    Reads the 24 h cache first; otherwise fetches through safe_fetch and
    stores. A failed read keeps a previously stored list (possibly stale);
    with nothing stored it negative-caches for RANGES_RETRY so bursts don't
    hammer a dead endpoint. None = ranges unavailable right now.
    """
    if not bot.ranges:
        return None
    now = _now()
    raw, fetched_at, cached = _cached(bot.ranges)
    if fetched_at is not None:
        if not raw:  # negative cache: no refresh attempt while the retry window runs
            if now - fetched_at < RANGES_RETRY:
                return None
        elif cached and now - fetched_at < RANGES_TTL:
            return cached
    try:
        result = await safe_fetch.fetch(bot.ranges, accept=None, client=client, timeout=10)
        networks = parse_range_file(json.loads(result.text))
        if not networks:
            raise ValueError("no prefixes in range file")
        serial = json.dumps(sorted(str(n) for n in networks))
        _store(bot.ranges, serial, now)
        return networks
    except Exception:
        if cached:
            return cached  # stale-but-usable beats nothing
        # negative cache: suppress refresh attempts for RANGES_RETRY
        _store(bot.ranges, "", now)
        return None


async def verify_ip(bot: Bot, ip: str, **kwargs) -> bool | None:
    """True/False against the operator's published ranges.

    None = the bot publishes no ranges, or they are unavailable right now.
    """
    if not isinstance(ip, str) or not ip.strip():
        return None
    ranges = await fetch_ranges(bot, **kwargs)
    if not ranges:
        return None
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    for net in ranges:
        if addr.version == net.version and addr in net:
            return True
    return False


async def verify_bot_request(user_agent: str | None, ip: str | None, **kwargs) -> dict:
    """Whether a hit is a verified AI bot, or why it isn't.

    Reasons mirror the ingest design: unknown_agent (UA not on the list),
    no_ranges (published ranges unavailable), unverified_ip (UA matched but
    the IP is outside the operator's ranges — spoofed or stale ranges).
    """
    bot = match_user_agent(user_agent)
    if bot is None:
        return {"reason": "unknown_agent"}
    verified = await verify_ip(bot, ip or "", **kwargs)
    if verified is None:
        return {"reason": "no_ranges", "bot": bot.key, "bot_type": bot.type}
    if verified is False:
        return {
            "reason": "unverified_ip",
            "bot": bot.key,
            "bot_type": bot.type,
            "note": "user agent matched but the IP is outside the operator's published ranges",
        }
    return {
        "bot": bot.key,
        "name": bot.name,
        "operator": bot.operator,
        "bot_type": bot.type,
        "verified": True,
    }
