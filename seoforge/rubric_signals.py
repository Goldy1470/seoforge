"""The 34 deterministic rubric signals + the audit context they read.

Ported from the reference Site-Audit implementation (derivation in
THIRD_PARTY_NOTICES.md). Shape mirrors the reference evaluator contract,
re-implemented for BeautifulSoup/httpx:

    Signal(key).evaluate(ctx) -> {"status": "pass"|"warn"|"fail"|"na",
                                  "score": 0..1 or None,
                                  "evidence": {...small object...}}

The 13 LLM-judged signals and the Wikidata-backed brand-entity signal are
deliberately not implemented here (they need an LLM round-trip / external
lookup — DEFERRED_SIGNAL_KEYS). The scorer's partial-coverage rule keeps the
total honest until they land.

The context is one page fetch (through SEOForge's SSRF-safe fetcher — no JS
rendering, unlike the reference's scraper) + the public robots.txt / llms.txt,
parsed once. Evaluator helpers keep `ctx.soup` read-only: text extraction runs
on a separate parse so the shared document keeps its JSON-LD blocks.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from . import ai_bots, safe_fetch
from .product_description import OPENING_CHARS, OPENING_WORDS, check_description_opening


# --------------------------------------------------------------- context


@dataclass
class AuditContext:
    url: str
    origin: str
    protocol: str
    status_code: int
    html: str
    html_bytes: int
    soup: object
    text: str
    word_count: int
    robots_txt: str | None
    llms_txt: str | None
    query: str | None = None
    now: float = 0.0


class AuditPageError(Exception):
    """The page could not be fetched into an auditable state."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def normalize_url(raw) -> str:
    """Normalize user-supplied input into an absolute http(s) URL.

    Same semantics as the reference: a schemeless input gets https:// and
    anything a URL parser could hiccup on (no host, whitespace in the host,
    a non-http scheme the input templated) is a 400.
    """
    trimmed = str(raw or "").strip()
    if not trimmed:
        raise AuditPageError(400, "A URL is required")
    with_scheme = (
        trimmed if re.match(r"^https?://", trimmed, re.I) else f"https://{trimmed}"
    )
    parts = urlsplit(with_scheme)
    host = parts.hostname or ""
    if (
        parts.scheme.lower() not in ("http", "https")
        or not host
        or any(ch.isspace() for ch in host)
    ):
        raise AuditPageError(400, f"Invalid URL: {raw}")
    return with_scheme


async def _fetch_text(url: str, client=None) -> str | None:
    try:
        r = await safe_fetch.fetch(url, accept=None, client=client, timeout=10)
    except safe_fetch.FetchError:
        return None
    return r.text if 200 <= r.status < 400 else None


async def build_audit_context(
    raw_url, *, query: str | None = None, client=None, now: float | None = None
) -> AuditContext:
    url = normalize_url(raw_url)
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"

    try:
        page = await safe_fetch.fetch(url, accept=None, client=client, timeout=25)
    except safe_fetch.FetchError as err:
        # Refused/HTTP-error fetches read as an un-auditable page, not a crash.
        raise AuditPageError(err.status or 502, f"Failed to fetch page ({err})") from err
    html = page.text or ""
    if page.status >= 400 or not html.strip():
        raise AuditPageError(page.status or 502, f"Failed to fetch page (status {page.status})")

    soup = BeautifulSoup(html, "lxml")

    # Plain text on a throwaway parse so the shared soup keeps its JSON-LD.
    soup_text = BeautifulSoup(html, "lxml")
    for tag in soup_text(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    body = soup_text.body or soup_text
    text = re.sub(r"\s+", " ", body.get_text(" ")).strip()

    robots_txt, llms_txt = await _fetch_text(f"{origin}/robots.txt", client), await _fetch_text(
        f"{origin}/llms.txt", client
    )

    return AuditContext(
        url=url,
        origin=origin,
        protocol=parts.scheme,
        status_code=page.status,
        html=html,
        html_bytes=html_bytes(html),
        soup=soup,
        text=text,
        word_count=len(text.split()) if text else 0,
        robots_txt=robots_txt,
        llms_txt=llms_txt,
        query=query,
        now=now if now is not None else time.time(),
    )


def html_bytes(html: str) -> int:
    return len(html.encode("utf-8"))


# ---------------------------------------------------------------- helpers


def json_ld(soup) -> dict:
    """Parse every <script type="application/ld+json"> block; flatten @graph
    / arrays into one list of typed nodes. {nodes, total, valid}."""
    nodes: list[dict] = []
    total = 0
    valid = 0
    for script in soup.select('script[type="application/ld+json"]'):
        total += 1
        try:
            parsed = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        valid += 1
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                nodes.extend(n for n in graph if isinstance(n, dict))
            else:
                nodes.append(item)
    return {"nodes": nodes, "total": total, "valid": valid}


def types_of(node: dict) -> list[str]:
    """A node's @type as a lowercase string array."""
    t = (node or {}).get("@type")
    if not t:
        return []
    types = t if isinstance(t, list) else [t]
    return [x.lower() for x in types if isinstance(x, str)]


def classify_links(ctx) -> dict:
    """Split the page's anchors into internal vs external netlocations."""
    host = urlsplit(ctx.url).netloc
    internal: list[str] = []
    external: list[str] = []
    for a in ctx.soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if (
            not href
            or href.startswith("#")
            or href.startswith("mailto:")
            or href.startswith("tel:")
            or href.startswith("javascript:")
        ):
            continue
        try:
            u = urlsplit(urljoin(ctx.url, href))
        except ValueError:
            continue
        if u.scheme not in ("http", "https") or not u.netloc:
            continue
        if u.netloc == host:
            internal.append(u.netloc)
        else:
            external.append(u.netloc)
    return {"internal": internal, "external": external}


def meta_content(soup, key: str) -> str | None:
    """Lowercased? no — the content of the first <meta> by name or property."""
    el = soup.select_one(f'meta[name="{key}"], meta[property="{key}"]')
    if el is None:
        return None
    value = el.get("content")
    return value.strip() if isinstance(value, str) else None


# --- Readability (Flesch Reading Ease) ---


def _count_syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    stripped = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    stripped = re.sub(r"^y", "", stripped)
    groups = re.findall(r"[aeiouy]{1,2}", stripped)
    return len(groups) or 1


def flesch_reading_ease(text: str) -> float | None:
    """Flesch Reading Ease for an English-ish body of text; None when there
    isn't enough text to score."""
    words = [w for w in text.split() if w]
    if len(words) < 30:
        return None
    sentences = len(re.findall(r"[.!?]+(?:\s|$)", text)) or 1
    syllables = sum(_count_syllables(w) for w in words)
    return 206.835 - 1.015 * (len(words) / sentences) - 84.6 * (syllables / len(words))


def count_phrases(text: str, phrases: list[str]) -> int:
    """Count occurrences of a list of phrases (case-insensitive)."""
    lower = text.lower()
    count = 0
    for phrase in phrases:
        p = phrase.lower()
        if not p:
            continue
        start = 0
        while True:
            idx = lower.find(p, start)
            if idx == -1:
                break
            count += 1
            start = idx + len(p)
    return count


def robots_groups(robots_txt) -> list[dict]:
    """robots.txt parsed as its groups: the user agents a group names and
    whether its rules disallow or allow the whole site. Consecutive
    user-agent lines share a group; comments and blank lines are skipped. An
    agent is kept as the crawler matches it: its name in lower case, up to a
    version ("GPTBot/1.1")."""
    groups: list[dict] = []
    current: dict | None = None
    agent_line = False
    for raw_line in str(robots_txt or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if current is None or not agent_line:
                current = {"agents": [], "disallow_all": False, "allow_all": False}
                groups.append(current)
            agent = value.lower()
            m = re.match(r"^[a-z_-]+", agent)
            current["agents"].append(m.group(0) if m else agent)
            agent_line = True
        else:
            agent_line = False
            if current and value == "/":
                if field == "disallow":
                    current["disallow_all"] = True
                if field == "allow":
                    current["allow_all"] = True
    return groups


_AUTHORITY_PATTERNS = [
    re.compile(r"(^|\.)edu$", re.I),
    re.compile(r"(^|\.)gov$", re.I),
    re.compile(r"(^|\.)wikipedia\.org$", re.I),
    re.compile(r"(^|\.)who\.int$", re.I),
    re.compile(r"(^|\.)nih\.gov$", re.I),
    re.compile(r"ncbi\.nlm\.nih\.gov$", re.I),
    re.compile(r"(^|\.)nature\.com$", re.I),
    re.compile(r"sciencedirect\.com$", re.I),
    re.compile(r"(^|\.)doi\.org$", re.I),
    re.compile(r"arxiv\.org$", re.I),
    re.compile(r"(^|\.)nytimes\.com$", re.I),
    re.compile(r"(^|\.)washingtonpost\.com$", re.I),
    re.compile(r"(^|\.)bloomberg\.com$", re.I),
    re.compile(r"(^|\.)reuters\.com$", re.I),
    re.compile(r"(^|\.)ft\.com$", re.I),
    re.compile(r"(^|\.)bbc\.(com|co\.uk)$", re.I),
]


def _is_authority_host(host: str) -> bool:
    return any(pattern.search(host) for pattern in _AUTHORITY_PATTERNS)


def _collect_dates(ctx) -> list[float]:
    """Candidate dates (epoch seconds) from JSON-LD, <time datetime>, OG meta."""
    def push(value, out):
        if not isinstance(value, str) or not value.strip():
            return
        v = value.strip()
        try:
            out.append(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
            return
        except ValueError:
            pass
        try:
            out.append(parsedate_to_datetime(v).timestamp())
        except (TypeError, ValueError):
            return

    out: list[float] = []
    for node in json_ld(ctx.soup)["nodes"]:
        push(node.get("datePublished"), out)
        push(node.get("dateModified"), out)
    for el in ctx.soup.select("time[datetime]"):
        push(el.get("datetime"), out)
    push(meta_content(ctx.soup, "article:published_time"), out)
    push(meta_content(ctx.soup, "article:modified_time"), out)
    return out


def text_of(value) -> str:
    """A JSON-LD value as plain text: a string, or the name of a node."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        return value["name"]
    return ""


# ------------------------------------------------------------ signal shell


class Signal:
    """One evaluator: key + evaluate(ctx).__call__ set via decorator use."""

    __slots__ = ("key", "evaluate")

    def __init__(self, key: str):
        self.key = key

    def __call__(self, fn):
        self.evaluate = fn
        return self


def _round2(value: float) -> float:
    return round(value + 0.0, 2)


# ------------------------------------------------------------- structure (13)


@Signal("structural-depth")
def _structural_depth(ctx):
    levels = ctx.soup.select("h1, h2, h3, h4, h5, h6")
    present: set[int] = set()
    skips = 0
    prev = 0
    for el in levels:
        m = re.search(r"\d", el.name or "")
        lvl = int(m.group(0)) if m else 0
        if not lvl:
            continue
        present.add(lvl)
        if prev and lvl > prev + 1:
            skips += 1
        prev = lvl
    depth = len(present)
    has_nesting = {1, 2, 3}.issubset(present)

    status, score = "fail", 0.2
    if has_nesting and skips == 0:
        status, score = "pass", 1
    elif depth >= 2:
        status, score = "warn", 0.6
    return {
        "status": status,
        "score": score,
        "evidence": {"depth": depth, "levelSkips": skips, "levels": sorted(present)},
    }


@Signal("internal-linking")
def _internal_linking(ctx):
    count = len(classify_links(ctx)["internal"])
    status, score = "fail", 0
    if count >= 5:
        status, score = "pass", 1
    elif count >= 1:
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"internalLinks": count}}


@Signal("page-weight")
def _page_weight(ctx):
    kb = ctx.html_bytes // 1024
    status, score = "pass", 1
    if kb > 1024:
        status, score = "fail", 0
    elif kb > 500:
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"htmlKb": kb}}


@Signal("json-ld-presence")
def _json_ld_presence(ctx):
    total = json_ld(ctx.soup)["total"]
    return {
        "status": "pass" if total > 0 else "fail",
        "score": 1 if total > 0 else 0,
        "evidence": {"blockCount": total},
    }


@Signal("json-ld-validity")
def _json_ld_validity(ctx):
    data = json_ld(ctx.soup)
    nodes, total, valid = data["nodes"], data["total"], data["valid"]
    if total == 0:
        return {"status": "fail", "score": 0, "evidence": {"reason": "no JSON-LD blocks"}}
    well_formed = 0
    for node in nodes:
        context_str = json.dumps(node.get("@context") or "").lower()
        if "schema.org" in context_str and types_of(node):
            well_formed += 1
    status, score = "fail", 0
    if valid == total and well_formed > 0:
        status, score = "pass", 1
    elif valid > 0 and well_formed > 0:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {"blocks": total, "parsed": valid, "wellFormedNodes": well_formed},
    }


# schema.org types that signal a specific, AI-citable content intent.
_HIGH_VALUE_TYPES = {
    "article", "newsarticle", "blogposting", "product", "faqpage", "howto",
    "recipe", "review", "qapage", "techarticle", "medicalwebpage", "event",
    "softwareapplication",
}


@Signal("json-ld-relevance")
def _json_ld_relevance(ctx):
    nodes = json_ld(ctx.soup)["nodes"]
    all_types = [t for node in nodes for t in types_of(node)]
    if not all_types:
        return {"status": "fail", "score": 0, "evidence": {"types": []}}
    has_high_value = any(t in _HIGH_VALUE_TYPES for t in all_types)
    return {
        "status": "pass" if has_high_value else "warn",
        "score": 1 if has_high_value else 0.5,
        "evidence": {"types": list(dict.fromkeys(all_types))[:8]},
    }


@Signal("faq-schema")
def _faq_schema(ctx):
    nodes = json_ld(ctx.soup)["nodes"]
    faq = next((n for n in nodes if "faqpage" in types_of(n)), None)
    qa = len(faq.get("mainEntity")) if faq and isinstance(faq.get("mainEntity"), list) else 0
    ok = faq is not None and qa >= 2
    return {
        "status": "pass" if ok else "fail",
        "score": 1 if ok else 0,
        "evidence": {"faqPage": faq is not None, "questionCount": qa},
    }


@Signal("h1-quality")
def _h1_quality(ctx):
    h1s = ctx.soup.select("h1")
    count = len(h1s)
    text = re.sub(r"\s+", " ", h1s[0].get_text()).strip() if h1s else ""
    length = len(text)
    exactly_one = count == 1
    ideal_length = 20 <= length <= 70
    status, score = "fail", 0
    if exactly_one and ideal_length:
        status, score = "pass", 1
    elif count >= 1 and 10 <= length <= 90:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {"count": count, "length": length, "text": text[:120]},
    }


@Signal("h2-coverage")
def _h2_coverage(ctx):
    count = len(ctx.soup.select("h2"))
    status, score = "fail", 0
    if count >= 3:
        status, score = "pass", 1
    elif count >= 1:
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"h2Count": count}}


@Signal("lists")
def _lists(ctx):
    with_three = 0
    any_list = 0
    for el in ctx.soup.select("ul, ol"):
        any_list += 1
        if len(el.find_all("li", recursive=False)) >= 3:
            with_three += 1
    status, score = "fail", 0
    if with_three >= 1:
        status, score = "pass", 1
    elif any_list >= 1:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {"lists": any_list, "listsWith3PlusItems": with_three},
    }


@Signal("tables")
def _tables(ctx):
    tables = ctx.soup.select("table")
    structured = sum(1 for t in tables if t.find("thead") and t.find("tbody"))
    status, score = "fail", 0
    if structured >= 1:
        status, score = "pass", 1
    elif len(tables) >= 1:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {"tables": len(tables), "structuredTables": structured},
    }


@Signal("alt-text")
def _alt_text(ctx):
    imgs = ctx.soup.select("img")
    total = len(imgs)
    if total == 0:
        return {"status": "na", "score": None, "evidence": {"images": 0}}
    with_alt = sum(
        1 for el in imgs if isinstance(el.get("alt"), str) and el.get("alt").strip()
    )
    ratio = with_alt / total
    status = "pass" if ratio >= 0.8 else "warn" if ratio >= 0.5 else "fail"
    return {
        "status": status,
        "score": _round2(ratio),
        "evidence": {"images": total, "withAlt": with_alt, "ratio": _round2(ratio)},
    }


@Signal("open-graph")
def _open_graph(ctx):
    tags = ["og:title", "og:description", "og:image", "og:type"]
    present = [t for t in tags if meta_content(ctx.soup, t)]
    n = len(present)
    status, score = "fail", 0
    if n == 4:
        status, score = "pass", 1
    elif n >= 1:
        status, score = "warn", n / 4
    return {
        "status": status,
        "score": _round2(score),
        "evidence": {"present": present, "missing": [t for t in tags if t not in present]},
    }


# -------------------------------------------------------------- authority (7)


@Signal("outbound-authority-links")
def _outbound_authority_links(ctx):
    external = classify_links(ctx)["external"]
    authority = list(dict.fromkeys(h for h in external if _is_authority_host(h)))
    count = len(authority)
    status, score = "fail", 0
    if count >= 2:
        status, score = "pass", 1
    elif count == 1:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {"authorityHosts": authority[:8], "count": count},
    }


@Signal("citation-density")
def _citation_density(ctx):
    external = classify_links(ctx)["external"]
    per_1000 = (len(external) / ctx.word_count) * 1000 if ctx.word_count > 0 else 0
    status, score = "fail", 0
    if per_1000 >= 2:
        status, score = "pass", 1
    elif per_1000 >= 1:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {
            "outboundLinks": len(external),
            "words": ctx.word_count,
            "per1000Words": round(per_1000, 1),
        },
    }


_STATISTIC = re.compile(
    r"(\$|€|£|₺|%)?\s?\d[\d.,]*\s?(%|percent|million|billion|k\b)?", re.IGNORECASE
)


@Signal("statistic-density")
def _statistic_density(ctx):
    # Quantitative tokens: percentages, currency, or standalone numbers.
    matches = list(_STATISTIC.finditer(ctx.text))
    numeric = sum(1 for m in matches if re.search(r"\d", m.group(0)))
    per_1000 = (numeric / ctx.word_count) * 1000 if ctx.word_count > 0 else 0
    status, score = "fail", 0
    if per_1000 >= 3:
        status, score = "pass", 1
    elif per_1000 >= 1.5:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {"numericClaims": numeric, "per1000Words": round(per_1000, 1)},
    }


@Signal("quotations")
def _quotations(ctx):
    count = len(ctx.soup.select("blockquote, q"))
    return {
        "status": "pass" if count >= 1 else "fail",
        "score": 1 if count >= 1 else 0,
        "evidence": {"quotations": count},
    }


@Signal("author-byline")
def _author_byline(ctx):
    nodes = json_ld(ctx.soup)["nodes"]

    def has_author(node):
        a = (node or {}).get("author")
        if not a:
            return False
        candidates = a if isinstance(a, list) else [a]
        return any(
            (isinstance(x, str) and x.strip()) or (isinstance(x, dict) and x.get("name"))
            for x in candidates
        )

    schema_author = any(has_author(n) for n in nodes)
    meta_author = meta_content(ctx.soup, "author") is not None
    rel_author = bool(
        ctx.soup.select('a[rel="author"]')
        or [el for el in ctx.soup.find_all(True)
           if re.search(r"byline|author", (el.get("class") and " ".join(el.get("class")) or "").lower())]
    )
    text_byline = bool(re.search(r"\bby\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", ctx.text[:2000]))
    found = schema_author or meta_author or rel_author or text_byline
    return {
        "status": "pass" if found else "fail",
        "score": 1 if found else 0,
        "evidence": {
            "schemaAuthor": schema_author,
            "metaAuthor": meta_author,
            "relAuthor": rel_author,
            "textByline": text_byline,
        },
    }


@Signal("date-markup")
def _date_markup(ctx):
    dates = _collect_dates(ctx)
    return {
        "status": "pass" if dates else "fail",
        "score": 1 if dates else 0,
        "evidence": {"dateSignals": len(dates)},
    }


@Signal("freshness")
def _freshness(ctx):
    dates = _collect_dates(ctx)
    if not dates:
        return {"status": "fail", "score": 0, "evidence": {"reason": "no date markup"}}
    newest = max(dates)
    age_days = int((ctx.now - newest) // 86400)
    status, score = "fail", 0
    if age_days <= 365:
        status, score = "pass", 1
    elif age_days <= 730:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {
            "ageDays": age_days,
            "newest": datetime.fromtimestamp(newest, tz=timezone.utc).date().isoformat(),
        },
    }


# --------------------------------------------------------- content (4 of 12)


@Signal("readability")
def _readability(ctx):
    flesch = flesch_reading_ease(ctx.text)
    if flesch is None:
        return {"status": "na", "score": None, "evidence": {"reason": "not enough text"}}
    status, score = "fail", 0
    if 50 <= flesch <= 70:
        status, score = "pass", 1
    elif 30 <= flesch < 50 or 70 < flesch <= 80:
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"fleschReadingEase": round(flesch)}}


@Signal("length")
def _length(ctx):
    words = ctx.word_count
    status, score = "fail", 0
    if 800 <= words <= 3000:
        status, score = "pass", 1
    elif (400 <= words < 800) or (3000 < words <= 5000):
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"words": words}}


@Signal("youtube-embed")
def _youtube_embed(ctx):
    has_embed = bool(
        ctx.soup.select(
            'iframe[src*="youtube.com"], iframe[src*="youtube-nocookie.com"], iframe[src*="youtu.be"]'
        )
    )
    return {
        "status": "pass" if has_embed else "fail",
        "score": 1 if has_embed else 0,
        "evidence": {"youtubeEmbed": has_embed},
    }


@Signal("product-description-opening")
def _product_description_opening(ctx):
    nodes = json_ld(ctx.soup)["nodes"]
    product = next(
        (n for n in nodes if any(t in ("product", "productgroup") for t in types_of(n))), None
    )
    if product is None:
        return {"status": "na", "score": None, "evidence": {"reason": "not a product page"}}
    itemprop = ctx.soup.select_one('[itemprop="description"]')
    description = (
        text_of(product.get("description"))
        or (itemprop.get_text() if itemprop is not None else "")
    )
    description = re.sub(r"<[^>]+>", " ", description).strip()
    if not description:
        return {"status": "na", "score": None, "evidence": {"reason": "no product description"}}

    result = check_description_opening(
        description,
        product_name=text_of(product.get("name")),
        brand=text_of(product.get("brand")),
    )
    type_ok = result["names_type"] is not False
    attributes_ok = len(result["attributes"]) >= 2
    if type_ok and attributes_ok:
        status, score = "pass", 1
    elif type_ok or attributes_ok:
        status, score = "warn", 0.5
    else:
        status, score = "fail", 0
    opening_words = result["opening"].split(" ")
    last = " ".join(opening_words[-6:])
    reason = (
        f"ChatGPT appears to stop reading a product description after about {OPENING_WORDS} words "
        f"or {OPENING_CHARS} characters, here at \"…{last}\". "
        f"Product type named there: "
        f"{'unknown' if result['names_type'] is None else ('yes' if result['names_type'] else 'no')}. "
        f"Key attributes there: {', '.join(result['attributes']) if result['attributes'] else 'none'}."
    )
    return {
        "status": status,
        "score": score,
        "evidence": {
            "reason": reason,
            "cut": result["cut"],
            "namesType": result["names_type"],
            "attributes": result["attributes"],
        },
    }


# ---------------------------------------------------------------- trust (7)


@Signal("https")
def _https(ctx):
    is_https = ctx.protocol == "https"
    mixed = 0
    if is_https:
        for el in ctx.soup.select("script[src], link[href], img[src], iframe[src], source[src]"):
            ref = el.get("src") or el.get("href") or ""
            if re.match(r"^http://", ref, re.I):
                mixed += 1
    status, score = "fail", 0
    if is_https and mixed == 0:
        status, score = "pass", 1
    elif is_https:
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"https": is_https, "mixedContentRefs": mixed}}


# registry order == the tokens we check; the bots registry is the source of truth
_AI_BOT_TOKENS = [b.userAgent.lower() for b in ai_bots.all_bots()]


@Signal("ai-bot-access")
def _ai_bot_access(ctx):
    robots_txt = ctx.robots_txt or ""
    if not robots_txt:
        # No robots.txt → still crawlable by default, but no explicit signal.
        return {"status": "warn", "score": 0.5, "evidence": {"robotsTxt": False}}
    lower = robots_txt.lower()
    mentioned = [b for b in _AI_BOT_TOKENS if b in lower]
    groups = robots_groups(robots_txt)
    blocked_bots = [
        b
        for b in mentioned
        if any(g["agents"].__contains__(b) for g in groups if g["disallow_all"])
        and not any(g["agents"].__contains__(b) for g in groups if g["allow_all"])
    ]
    blocked = len(blocked_bots) > 0
    status, score = "warn", 0.5
    if blocked:
        status, score = "fail", 0
    elif mentioned:
        status, score = "pass", 1
    return {
        "status": status,
        "score": score,
        "evidence": {"aiBotsMentioned": mentioned, "blocked": blocked, "blockedBots": blocked_bots},
    }


@Signal("llms-txt-presence")
def _llms_txt_presence(ctx):
    present = bool(ctx.llms_txt and ctx.llms_txt.strip())
    return {
        "status": "pass" if present else "fail",
        "score": 1 if present else 0,
        "evidence": {"llmsTxt": present},
    }


@Signal("canonical")
def _canonical(ctx):
    el = ctx.soup.select_one('link[rel="canonical"]')
    href = (el.get("href") or "").strip() if el is not None else ""
    present = bool(href)
    return {
        "status": "pass" if present else "fail",
        "score": 1 if present else 0,
        "evidence": {"canonical": href or None},
    }


@Signal("viewport")
def _viewport(ctx):
    present = meta_content(ctx.soup, "viewport") is not None
    return {
        "status": "pass" if present else "fail",
        "score": 1 if present else 0,
        "evidence": {"viewport": present},
    }


@Signal("twitter-card")
def _twitter_card(ctx):
    card = meta_content(ctx.soup, "twitter:card")
    image = meta_content(ctx.soup, "twitter:image")
    status, score = "fail", 0
    if card and image:
        status, score = "pass", 1
    elif card:
        status, score = "warn", 0.5
    return {
        "status": status,
        "score": score,
        "evidence": {"card": card, "hasImage": image is not None},
    }


@Signal("meta-description")
def _meta_description(ctx):
    desc = meta_content(ctx.soup, "description")
    length = len(desc) if desc else 0
    status, score = "fail", 0
    if 80 <= length <= 160:
        status, score = "pass", 1
    elif length > 0:
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"length": length}}


# ----------------------------------------------------------- e-e-a-t (3 of 8)

_FIRST_PERSON_PHRASES = [
    "we tested", "we built", "we found", "we measured", "we analyzed",
    "we analysed", "in our experience", "in our testing", "our team",
    "i tested", "i built", "i found", "we ran", "we compared", "when we",
]


@Signal("first-person")
def _first_person(ctx):
    hits = count_phrases(ctx.text, _FIRST_PERSON_PHRASES)
    status, score = "fail", 0
    if hits >= 2:
        status, score = "pass", 1
    elif hits == 1:
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"evidencePhrases": hits}}


@Signal("privacy-terms")
def _privacy_terms(ctx):
    privacy = False
    terms = False
    for a in ctx.soup.select("a[href]"):
        href = (a.get("href") or "").lower()
        text = a.get_text().lower()
        blob = f"{href} {text}"
        if re.search(r"privacy", blob):
            privacy = True
        if re.search(r"terms|tos\b|conditions", blob):
            terms = True
    status, score = "fail", 0
    if privacy and terms:
        status, score = "pass", 1
    elif privacy or terms:
        status, score = "warn", 0.5
    return {"status": status, "score": score, "evidence": {"privacy": privacy, "terms": terms}}


_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)


@Signal("contact-info")
def _contact_info(ctx):
    has_mailto = bool(ctx.soup.select('a[href^="mailto:"]'))
    has_tel = bool(ctx.soup.select('a[href^="tel:"]'))
    contact_link = False
    for a in ctx.soup.select("a[href]"):
        blob = f"{(a.get('href') or '').lower()} {a.get_text().lower()}"
        if re.search(r"contact|iletişim|kontakt", blob):
            contact_link = True
            break
    email_in_text = bool(_EMAIL.search(ctx.text))
    found = has_mailto or has_tel or contact_link or email_in_text
    return {
        "status": "pass" if found else "fail",
        "score": 1 if found else 0,
        "evidence": {"mailto": has_mailto, "tel": has_tel, "contactLink": contact_link, "emailInText": email_in_text},
    }


# --------------------------------------------------------------- registry

STRUCTURE_SIGNALS = [
    _structural_depth, _internal_linking, _page_weight, _json_ld_presence,
    _json_ld_validity, _json_ld_relevance, _faq_schema, _h1_quality,
    _h2_coverage, _lists, _tables, _alt_text, _open_graph,
]

AUTHORITY_SIGNALS = [
    _outbound_authority_links, _citation_density, _statistic_density,
    _quotations, _author_byline, _date_markup, _freshness,
]

CONTENT_SIGNALS = [_readability, _length, _youtube_embed, _product_description_opening]

TRUST_SIGNALS = [
    _https, _ai_bot_access, _llms_txt_presence, _canonical, _viewport,
    _twitter_card, _meta_description,
]

EEAT_SIGNALS = [_first_person, _privacy_terms, _contact_info]

signal_registry = [
    *STRUCTURE_SIGNALS, *AUTHORITY_SIGNALS, *CONTENT_SIGNALS,
    *TRUST_SIGNALS, *EEAT_SIGNALS,
]

# The rubric signals that are NOT implemented here: 13 need an LLM judge (and
# often a target buyer query), brand-entity needs a Wikidata lookup. They land
# with the LLM phase.
DEFERRED_SIGNAL_KEYS = [
    "direct-answer", "bluf-answer", "query-coverage", "sub-query-coverage",
    "entity-coverage", "info-density", "mega-page-coverage", "definitions",
    "technical-terms", "case-study-evidence", "author-credentials",
    "byline-depth", "press-mentions", "brand-entity",
]


# ------------------------------------------------------------------ audit


def audit_url(raw_url, *, query: str | None = None) -> dict:
    """One page's full rubric audit: build the context, run every implemented
    signal, score, and persist a `rubric_audit` report.

    Raises `AuditPageError` when the page can't be fetched into an auditable
    state (bad URL, refused fetch, or an unrenderable status).
    """
    from . import db
    from .rubric import (
        RUBRIC_VERSION,
        SIGNALS_BY_KEY,
        run_signals,
        score_audit,
    )

    ctx = asyncio.run(build_audit_context(raw_url, query=query))
    results = run_signals(ctx)
    score = score_audit(results)

    signals = []
    for row in results:
        descriptor = SIGNALS_BY_KEY.get(row["key"]) or {}
        signals.append(
            {
                "key": row["key"],
                "label": descriptor.get("label", row["key"]),
                "category": descriptor.get("category"),
                "impactTier": descriptor.get("impactTier"),
                "status": row["status"],
                "score": row["score"],
                "evidence": row["evidence"],
            }
        )

    report = {
        "version": RUBRIC_VERSION,
        "url": ctx.url,
        "page": {
            "status": ctx.status_code,
            "words": ctx.word_count,
            "htmlBytes": ctx.html_bytes,
        },
        "score": score,
        "signals": signals,
        "deferred": list(DEFERRED_SIGNAL_KEYS),
    }
    report["report_id"] = db.save_report("rubric_audit", report)
    return report
