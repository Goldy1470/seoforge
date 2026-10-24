"""AI-traffic classifier: which AI assistant sent a visit, and through which
surface.

The registry lives in `data/ai_sources.json` (versioned file; derivation in
THIRD_PARTY_NOTICES.md). Semantics ported from the reference approach in
Python:

* A value matches a source when it is one of its exact aliases, or one of its
  hosts or any subdomain of a host. Substring matching is deliberately
  absent: a random host with "claude" in its name once showed up in a real
  property and must not be booked as AI traffic.
* Origins: a referrer is the primary signal. A utm_source tag counts only for
  sources that haven't opted out (utm: false — Bing's links say nothing about
  Copilot without the /ck/a redirect path, which only a referrer carries).
  The GA4 (analytics) origin follows the same rule with its own flag and is
  used by the Analytics side, not by the on-site classifier.
* Surfaces: a medium matched exactly, in any case, picks the surface a visit
  came through ("feed" = ChatGPT Shopping; "rss-feed" is not).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from .config import ROOT

_CONFIG_PATH = ROOT / "data" / "ai_sources.json"

ORIGINS = ("referrer", "utm", "analytics")


@dataclass(frozen=True)
class Source:
    key: str
    hosts: tuple[str, ...]
    aliases: tuple[str, ...]
    surfaces: tuple[dict, ...]  # {"key": surface key, "medium": exact medium}
    referrerPath: str | None
    utm: bool
    analytics: bool


def _load() -> tuple[int, list[Source]]:
    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    sources = [
        Source(
            key=s["key"],
            hosts=tuple(s.get("hosts", [])),
            aliases=tuple(s.get("aliases", [])),
            surfaces=tuple(s.get("surfaces", [])),
            referrerPath=s.get("referrerPath"),
            utm=bool(s.get("utm", True)),
            analytics=bool(s.get("analytics", True)),
        )
        for s in raw["sources"]
    ]
    return raw.get("version", 1), sources


_VERSION, _SOURCES = _load()


def all_sources() -> list[Source]:
    return list(_SOURCES)


def get_source(key: str) -> Source | None:
    for source in _SOURCES:
        if source.key == key:
            return source
    return None


def normalize_source(raw) -> str:
    """Lowercased, trimmed, without one leading "www." — or ''."""
    if not isinstance(raw, str):
        return ""
    value = raw.strip().lower()
    if value.startswith("www."):
        value = value[4:]
    return value


def _counts(source: Source, origin: str) -> bool:
    if origin == "utm":
        return source.utm
    if origin == "analytics":
        return source.analytics
    return True


def _match(value: str, source: Source) -> bool:
    if value in source.aliases:
        return True
    return any(value == host or value.endswith("." + host) for host in source.hosts)


def classify_ai_source(raw, *, origin: str = "referrer", referrer: str = "") -> str | None:
    """The source key behind one value at one origin, or None.

    Unrecognised is a normal outcome: most traffic isn't from an assistant.
    For the referrer origin, pass the raw referrer too: a source with a
    referrerPath (Bing /ck/a) only counts when the referrer carries it.
    """
    if origin not in ORIGINS:
        raise ValueError(f"unknown AI source origin: {origin}")
    value = normalize_source(raw)
    if not value:
        return None
    for source in _SOURCES:
        if not _counts(source, origin):
            continue
        if origin == "referrer" and source.referrerPath:
            if source.referrerPath not in (referrer or ""):
                continue
        if _match(value, source):
            return source.key
    return None


@dataclass(frozen=True)
class Classification:
    source: str          # source key, e.g. "chatgpt.com"
    surface: str | None  # e.g. "shopping"; None for the source's ordinary links
    origin: str          # "referrer" or "utm"


def classify(
    referrer: str | None = None,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    landing_url: str | None = None,
) -> Classification | None:
    """Which AI assistant a visit came from, and through which surface.

    Order mirrors the tracking snippet: the referrer first (its host, with
    the referrerPath gate for sources like Bing's /ck/a redirect); the
    utm_source tag second. The surface comes from the medium — the
    utm_medium argument when given, else the landing URL's utm_medium.
    """
    if referrer:
        try:
            host = urlsplit(referrer).hostname or ""
        except ValueError:
            host = ""
        key = classify_ai_source(host, origin="referrer", referrer=referrer)
        if key:
            return Classification(key, _surface_for(key, utm_medium, landing_url), "referrer")
    if utm_source:
        key = classify_ai_source(utm_source, origin="utm")
        if key:
            return Classification(key, _surface_for(key, utm_medium, landing_url), "utm")
    return None


def _surface_for(source_key: str, utm_medium, landing_url) -> str | None:
    medium = utm_medium if isinstance(utm_medium, str) and utm_medium else landing_medium(landing_url)
    return classify_ai_surface(source_key, medium)


def landing_medium(url) -> str:
    """A landing URL's utm_medium: the first such parameter, its name in any
    case. Empty without one, or for a value that isn't a URL."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        query = urlsplit(url).query
    except ValueError:
        return ""
    for name, value in parse_qsl(query, keep_blank_values=True):
        if name.lower() == "utm_medium":
            return value
    return ""


def classify_ai_surface(source_key: str, medium) -> str | None:
    """The surface of `source_key` a visit came through, from its medium
    (a landing URL's utm_medium or an Analytics session medium), or None
    for the source's ordinary links. Exact match, in any case: "feed" is
    ChatGPT Shopping, "rss-feed" isn't."""
    value = medium.lower() if isinstance(medium, str) else ""
    if not value:
        return None
    source = get_source(source_key)
    if source is None:
        return None
    for surface in source.surfaces:
        if surface.get("medium", "").lower() == value:
            return surface.get("key")
    return None


def ai_source_pattern(*, origin: str = "analytics") -> str:
    """The same match as one regular expression, RE2 syntax (for asking GA4
    for these sessions): a listed host or any subdomain of one, or an alias
    exactly. Sources opted out of the origin are excluded."""
    if origin not in ORIGINS:
        raise ValueError(f"unknown AI source origin: {origin}")
    hosts: list[str] = []
    aliases: list[str] = []
    for source in _SOURCES:
        if not _counts(source, origin):
            continue
        hosts.extend(source.hosts)
        aliases.extend(source.aliases)
    host_part = "|".join(re.escape(host) for host in hosts)
    alias_part = "|".join(re.escape(alias) for alias in aliases)
    return rf"^((.+\.)?({host_part})|(www\.)?({alias_part}))$"


def source_view(source: Source) -> dict:
    return {
        "key": source.key,
        "hosts": list(source.hosts),
        "aliases": list(source.aliases),
        "surfaces": [dict(s) for s in source.surfaces],
        "referrer_path": source.referrerPath or "",
        "utm": source.utm,
        "analytics": source.analytics,
    }


def sources_summary() -> dict:
    """Registry overview for CLI / MCP output (bounded list)."""
    return {
        "version": _VERSION,
        "count": len(_SOURCES),
        "sources": [source_view(s) for s in _SOURCES],
    }
