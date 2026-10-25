"""Tracked prompts and their AI answers — citation tracking.

A tracked prompt is a question people paste into AI assistants; each
recorded answer is parsed into deterministic metrics the moment it is
stored — mentions, citations, where the brand lands in the answer's
mention order, a 0-100 visibility score — with no LLM in the parse.
Per-platform states over a window and the before/after verdict mirror
the reference's Positions panel and its Improve-outcome kernel.

Answers are recorded by paste-in until a live answer-fetch provider is
decided (deferred user decision); the parse, the states and the verdicts
run identically once a collector exists — only `record_answer`'s caller
changes.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import unicodedata
from urllib.parse import urlsplit

from . import db
from .config import settings

# ----------------------------------------------------- platform vocabulary

# The platforms answers are read on, as stored in prompt_results.platform
# (the reference's tracked scraper platforms, shopping surfaces excluded
# from its Insights per its own decision — kept as pure data vocabulary:
# naming a platform never fetches one).
PLATFORMS = [
    "chatgpt-web",
    "google-aio",
    "google-aimode",
    "copilot-web",
    "grok-web",
    "perplexity-web",
    "gemini-web",
]

# Below this share of runs mentioning the brand, the platform counts as
# not mentioned: a brand in 8 of 120 answers is mostly missing there and
# needs coverage advice, not positioning advice, however high those 8
# ranked.
MIN_MENTION_RATE = 0.5

# A mention position must move at least this far to count as a change
# (positions average halves quite often — 1.5 over two runs — so the
# floor is well below 1).
POSITION_CHANGE = 0.5

# The outcome window: one read covers this many days, ending at the day
# of the read; a prompt improvement is tracked this many days after the
# work closes, and the read on the last day is final.
OUTCOME_WINDOW_DAYS = 7
OUTCOME_TRACKING_DAYS = 30


# --------------------------------------------------- ChatGPT entity markup

# ChatGPT tags the products, companies and places it names, and markdown
# pasted from an answer can keep the tag where the name should be:
# `<Entity category="software" value="Quanta" disambig="Nimbus AI search
# optimization platform"/>` for "Quanta". The tag reads as literal text
# in the answer, and its attributes would count as mentions: a competitor
# named only in `disambig` (or one called "Software") looked mentioned,
# and the extra names shifted every mention position. Paste-ins are
# cleaned at record time and again at parse time.

# A self-closing `<Entity …/>` tag; group 1 is its attribute list.
_ENTITY_TAG = re.compile(r"<Entity((?:\s+[\w:-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*)\s*/>")
_ATTRIBUTE = re.compile(r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")

_NAMED_ESCAPES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_XML_ESCAPE = re.compile(r"&(?:(amp|lt|gt|quot|apos)|#(\d+)|#x([0-9a-fA-F]+));")


def _unescape_xml(value: str) -> str:
    """Attribute values are XML-escaped ("AT&amp;T"); undo the common escapes."""

    def repl(match: re.Match) -> str:
        name, dec, hx = match.group(1), match.group(2), match.group(3)
        if name:
            return _NAMED_ESCAPES[name.lower()]
        code = int(dec) if dec else int(hx, 16)
        # NUL and lone surrogates would not survive a text column.
        if 0 < code <= 0x10FFFF and not (0xD800 <= code <= 0xDFFF):
            return chr(code)
        return match.group(0)

    return _XML_ESCAPE.sub(repl, value)


def strip_entity_markup(text: str) -> str:
    """Replaces each `<Entity … value="X" …/>` tag with X, the name the
    answer reads; a tag without a `value` is left as it is."""
    text = str(text or "")
    if "<Entity" not in text:
        return text

    def repl(match: re.Match) -> str:
        attributes = match.group(1) or ""
        for name, double, single in _ATTRIBUTE.findall(attributes):
            if name == "value":
                return _unescape_xml(double if double is not None else (single or ""))
        return match.group(0)

    return _ENTITY_TAG.sub(repl, text)


# ------------------------------------------------------------ name matching

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]+\)")
_BARE_URL = re.compile(r"https?://[^\s)>\]]+")


def strip_urls(text: str) -> str:
    """Strip markdown link URLs so only display text remains
    ("[label](https://example.com/path)" → "label"); bare URLs go too."""
    cleaned = _MD_LINK.sub(r"\1", str(text or ""))
    cleaned = _BARE_URL.sub("", cleaned)
    return cleaned


def matchable_text(text: str) -> str:
    """The answer as its reader sees it, for matching names: entity tags
    replaced by the names they stand for, URLs stripped."""
    return strip_urls(strip_entity_markup(text))


def _word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _unspaced_script(ch: str) -> bool:
    """Scripts written without spaces between words (Han, Thai…) and
    Korean, whose particles attach to the noun: a name there is always
    flanked by other letters, so a boundary check would never let it
    match."""
    try:
        name = unicodedata.name(ch, "")
    except ValueError:
        return False
    return name.startswith(
        (
            "CJK UNIFIED IDEOGRAPH",
            "CJK COMPATIBILITY IDEOGRAPH",
            "HIRAGANA",
            "KATAKANA",
            "HANGUL",
            "THAI",
            "LAO",
            "KHMER",
            "MYANMAR",
        )
    )


def name_regex(term: str, flags: int = 0) -> re.Pattern:
    """Case-insensitive regex matching a name as a whole word.

    A plain ``\\b`` only knows ASCII word characters, so a name starting
    or ending with an accented letter ("Ümit") or punctuation ("C++")
    never matched. The boundary is a Unicode letter/number lookaround,
    applied only on a side where the name ends in a word character of a
    space-separated script."""
    term = str(term or "")
    if not term:
        return re.compile(r"(?!)")  # matches nothing
    chars = list(term)
    before = after = ""
    if _word_char(chars[0]) and not _unspaced_script(chars[0]):
        before = r"(?<!\w)"
    if _word_char(chars[-1]) and not _unspaced_script(chars[-1]):
        after = r"(?!\w)"
    return re.compile(before + re.escape(term) + after, flags | re.IGNORECASE)


def count_occurrences(text: str, term: str) -> int:
    """Count case-insensitive whole-word occurrences of a term in text."""
    if not term:
        return 0
    return len(name_regex(term, 0).findall(text))


def first_occurrence_index(text: str, term: str) -> int:
    """First whole-word occurrence index of a term (case-insensitive), or -1."""
    if not term:
        return -1
    match = name_regex(term, 0).search(text)
    return match.start() if match else -1


# ------------------------------------------------------- hosts and domains


def extract_hostname(raw_url) -> str | None:
    """Normalized hostname of a URL ("https://www.FOO.com/bar" → "foo.com").

    A relative reference ("foo.com/bar", "//foo.com/bar") has none — the
    bare-path fallback then reads the host-looking prefix the same way the
    reference's URL parser fallback did."""
    if not raw_url:
        return None
    text = str(raw_url).strip()
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", text):
        # A parsed URL is trusted (the reference's URL parser): an empty
        # host ('mailto:a@b') is None, not a fallback candidate. The
        # reference's WHATWG corner ('https:foo.com' re-hosts as
        # 'foo.com') is unreachable here — answer citations are absolute.
        try:
            host = (urlsplit(text).hostname or "").lower()
        except ValueError:
            return None
        if host.startswith("www."):
            host = host[4:]
        return host or None
    # No scheme: a relative reference ('foo.com/bar', '//foo.com/bar') —
    # read the host-looking prefix the same way the reference's fallback did.
    match = re.match(r"^(?:https?://)?(?:www\.)?([^/\s?#]+)", text, re.IGNORECASE)
    return match.group(1).lower() if match else None


def own_domain_matcher(brand_domains):
    """Whether a URL is on one of the brand's own domains: exact host or
    subdomain match. The old substring check (url.includes(domain)) also
    matched the brand's domain inside another site's path or query."""
    domains = []
    for d in brand_domains or []:
        host = extract_hostname(d) or str(d or "").strip().lower()
        if host:
            domains.append(host)

    def is_own(url) -> bool:
        host = extract_hostname(url or "")
        return bool(host) and any(host == d or host.endswith("." + d) for d in domains)

    return is_own


def count_own_domain_citations(citations, brand_domains) -> int:
    """Count the citations that point at one of the brand's own domains
    (one count per citation, even when several domains match it)."""
    is_own = own_domain_matcher(brand_domains)
    return sum(1 for cite in citations or [] if isinstance(cite, dict) and is_own(cite.get("url")))


def brand_citation_rank(citations, brand_domains) -> dict | None:
    """The brand's first source in an answer, 1-based among the citations
    that carry a URL; None when the brand is not cited at all."""
    if not isinstance(citations, list):
        return None
    domains = []
    for d in brand_domains or []:
        host = extract_hostname(d) or str(d or "").strip().lower()
        if host:
            domains.append(host)
    if not domains:
        return None
    total = 0
    rank = None
    for cite in citations:
        url = cite.get("url") if isinstance(cite, dict) else None
        url = url.strip() if isinstance(url, str) else ""
        if not url:
            continue
        total += 1
        if rank is None:
            host = extract_hostname(url)
            if host and any(host == d or host.endswith("." + d) for d in domains):
                rank = total
    return {"rank": rank, "total": total} if rank is not None else None


# -------------------------------------------------------------- the parser


def brand() -> dict:
    """The single-tenant brand: the configured name plus the site's own
    registrable domain — the matcher covers exact hosts and subdomains,
    so one registrable domain carries what the reference's brand_domains
    list did."""
    from .outcomes import site_domain  # lazy: outcomes imports prompts later

    return {
        "name": (settings.brand_name or "").strip(),
        "domains": [d for d in [site_domain()] if d],
    }


def compute_mention_position(
    text: str, brand_dict: dict, competitors: list | None = None
) -> dict:
    """Where each tracked entity (the brand and its competitors) lands in
    the answer's mention order. Deterministic: earliest whole-word
    occurrence of each entity's name/domain in the URL-stripped answer
    text; an entity's rank is 1 + the number of other mentioned entities
    named before it.

    Returns mention_position (1-based rank of the brand's first mention,
    None when the brand is not mentioned), mentioned_entity_count (how
    many tracked entities the answer names; 0 marks "computed, nothing
    found") and competitor_positions (competitor id → rank, only for
    competitors the answer actually mentions)."""
    clean = matchable_text(text)

    def entity_first_index(names) -> int:
        best = -1
        for name in names:
            idx = first_occurrence_index(clean, name)
            if idx >= 0 and (best == -1 or idx < best):
                best = idx
        return best

    brand_index = entity_first_index([brand_dict.get("name"), *(brand_dict.get("domains") or [])])
    named_competitors = [
        {"id": comp.get("id"), "index": entity_first_index([comp.get("name"), *( [comp.get("domain")] if comp.get("domain") else [] )])}
        for comp in competitors or []
    ]
    named_competitors = [c for c in named_competitors if c["index"] >= 0]

    all_indexes = [c["index"] for c in named_competitors]
    if brand_index >= 0:
        all_indexes.append(brand_index)

    def rank_of(index: int) -> int:
        return sum(1 for other in all_indexes if other < index) + 1

    return {
        "mention_position": rank_of(brand_index) if brand_index >= 0 else None,
        "mentioned_entity_count": len(all_indexes),
        "competitor_positions": {c["id"]: rank_of(c["index"]) for c in named_competitors},
    }


def compute_visibility_score(
    mention_count: int, citation_count: int, total_citations: int, sentiment: str
) -> int:
    """0-100 from mention and citation counts:

    mentions (max 40) + own citations (max 30) + the share of the answer's
    citations that are the brand's (max 15) + sentiment (positive 15;
    neutral but mentioned at all 7). Sentiment is an external input — an
    LLM's verdict — never computed here."""
    score = min(mention_count * 10, 40)
    score += min(citation_count * 15, 30)
    if total_citations > 0:
        score += _round_half_up((citation_count / total_citations) * 15)
    if sentiment == "positive":
        score += 15
    elif sentiment == "neutral" and mention_count > 0:
        score += 7
    return min(score, 100)


def parse_response(
    text: str,
    citations: list | None,
    brand_dict: dict,
    sentiment: str = "neutral",
    competitors: list | None = None,
) -> dict:
    """Parse an AI answer into visibility metrics for the brand.

    Mentions and citations are computed locally (deterministic, no LLM);
    sentiment comes from outside (an external judgment)."""
    cites = [c for c in (citations or []) if isinstance(c, dict)]
    clean = matchable_text(text)

    # --- Brand mention count (on URL-stripped text to not double-count) ---
    mention_count = 0
    for term in [brand_dict.get("name"), *(brand_dict.get("domains") or [])]:
        mention_count += count_occurrences(clean, term)

    # --- Brand citation count (hostname-based, see count_own_domain_citations) ---
    citation_count = count_own_domain_citations(cites, brand_dict.get("domains") or [])

    # --- Visibility score (0-100) ---
    visibility_score = compute_visibility_score(
        mention_count, citation_count, len(cites), sentiment
    )

    # --- Mention order among brand + competitors (position signal) ---
    positioned = compute_mention_position(text, brand_dict, competitors)

    # --- Competitor mentions (on URL-stripped text; their citations match by
    # substring — deliberate reference legacy, a domain in a host counts) ---
    competitor_mentions = []
    for comp in competitors or []:
        comp_mentions = sum(
            count_occurrences(clean, term)
            for term in [comp.get("name"), comp.get("domain")]
            if term
        )
        comp_citations = 0
        if comp.get("domain"):
            like = str(comp["domain"]).lower()
            comp_citations = sum(
                1
                for cite in cites
                if like in str(cite.get("url") or "").lower()
            )
        competitor_mentions.append(
            {
                "competitor_id": comp.get("id"),
                "name": comp.get("name"),
                "domain": comp.get("domain") or "",
                "mention_count": comp_mentions,
                "citation_count": comp_citations,
                "visibility_score": compute_visibility_score(
                    comp_mentions, comp_citations, len(cites), "neutral"
                ),
                "mention_position": positioned["competitor_positions"].get(
                    comp.get("id")
                ),
            }
        )

    return {
        "mention_count": mention_count,
        "citation_count": citation_count,
        "sentiment": sentiment,
        "visibility_score": visibility_score,
        "competitor_mentions": competitor_mentions,
        "mention_position": positioned["mention_position"],
        "mentioned_entity_count": positioned["mentioned_entity_count"],
    }


# --------------------------------------------------- platform state + verdict


def _round1(n) -> float | None:
    """One decimal place, like the reference's Math.round — always
    floor(x + 0.5), so an average of exactly 1.25 reads 1.3, not 1.2;
    only non-negative numbers flow through here (positions and totals)."""
    if n is None:
        return None
    return math.floor(n * 10 + 0.5) / 10


def _round_half_up(n) -> int:
    """The reference's bare Math.round."""
    return math.floor(n + 0.5)


def position_tone(rank: int | float, total: int | float) -> str:
    """First third of the citation list reads Early, last third Late,
    anything between Mid: first place and a final out-of-N place must
    mean different things even in short lists."""
    ratio = (rank - 1) / (total - 1) if total > 1 else 0
    if ratio <= 1 / 3:
        return "Early"
    if ratio >= 2 / 3:
        return "Late"
    return "Mid"


def classify_platform(rows: list, brand_domains) -> dict:
    """One platform's state over a window. The brand appearing in fewer
    than MIN_MENTION_RATE of the runs is not mentioned; otherwise
    positions average over the runs that mention the brand, so one lucky
    #1 among many #4s still reads "late".

    Rows are the window's prompt results for the platform."""
    runs = len(rows)
    if runs == 0:
        return {
            "state": "no_runs",
            "runs": 0,
            "mentioned_runs": 0,
            "cited_runs": 0,
            "mention_position": None,
            "mention_total": None,
            "citation_rank": None,
        }

    mentioned_rows = [r for r in rows if (r.get("mention_count") or 0) > 0]
    positions = [
        r.get("mention_position")
        for r in mentioned_rows
        if isinstance(r.get("mention_position"), (int, float)) and r["mention_position"] > 0
    ]
    totals = [
        r.get("mentioned_entity_count")
        for r in mentioned_rows
        if isinstance(r.get("mentioned_entity_count"), (int, float))
        and r["mentioned_entity_count"] > 0
    ]

    def avg(xs):
        return sum(xs) / len(xs) if xs else None

    citation_ranks = [
        cr
        for cr in (
            brand_citation_rank(r.get("citations"), brand_domains) for r in rows
        )
        if cr is not None
    ]
    citation_rank = (
        {
            "rank": _round1(avg([cr["rank"] for cr in citation_ranks])),
            "total": _round1(avg([cr["total"] for cr in citation_ranks])),
        }
        if citation_ranks
        else None
    )

    mention_position = _round1(avg(positions))
    mention_total = _round1(avg(totals))

    state: str
    if len(mentioned_rows) < runs * MIN_MENTION_RATE:
        state = "not_mentioned"
    elif mention_position is None or _round_half_up(mention_position) > 1:
        state = "mentioned_late"
    elif citation_rank is None or position_tone(citation_rank["rank"], citation_rank["total"]) != "Early":
        state = "not_cited"
    else:
        state = "doing_well"

    return {
        "state": state,
        "runs": runs,
        "mentioned_runs": len(mentioned_rows),
        "cited_runs": len(citation_ranks),
        "mention_position": mention_position,
        "mention_total": mention_total,
        "citation_rank": citation_rank,
    }


def platform_snapshot(platform: str, state: dict) -> dict:
    """What one read keeps per platform (the History-facing shape)."""
    return {
        "platform": platform,
        "state": state["state"],
        "runs": state["runs"],
        "mentioned_runs": state["mentioned_runs"],
        "cited_runs": state["cited_runs"],
        "mention_position": state["mention_position"],
        "citation_rank": state["citation_rank"],
    }


def platform_mentioned(snapshot: dict) -> bool:
    """Mentioned or not, by the Improve tab's rule: a brand in fewer than
    MIN_MENTION_RATE of the runs is not_mentioned, so 1 mention in 10
    runs is still not mentioned."""
    return (
        (snapshot.get("runs") or 0) > 0
        and snapshot.get("state") not in ("no_runs", "not_mentioned")
    )


def platform_verdict(before: dict | None, after: dict | None) -> str:
    """The outcome one platform's two reads speak:

    - not_enough_data: no runs before or after
    - improved:         now mentioned, or named at least POSITION_CHANGE earlier
    - declined:         no longer mentioned, or named at least POSITION_CHANGE later
    - unchanged:        anything smaller"""
    if not (before and before.get("runs")) or not (after and after.get("runs")):
        return "not_enough_data"
    if not platform_mentioned(before) and platform_mentioned(after):
        return "improved"
    if platform_mentioned(before) and not platform_mentioned(after):
        return "declined"
    if not platform_mentioned(before):
        return "unchanged"
    b = before.get("mention_position")
    a = after.get("mention_position")
    if b is None or a is None:
        return "unchanged"
    if b - a >= POSITION_CHANGE:
        return "improved"
    if a - b >= POSITION_CHANGE:
        return "declined"
    return "unchanged"


def compare_outcome(baseline: list, current: list) -> dict:
    """Baseline next to the latest read, per platform, with the counts
    the outcome History reports. The one-line delta is the mean position
    over the platforms mentioned and ranked on both sides, so a
    not-mentioned side's stray position is left out."""
    current_by_platform = {c.get("platform"): c for c in current or []}
    platforms = [
        {
            "platform": b.get("platform"),
            "before": b,
            "after": current_by_platform.get(b.get("platform")),
            "verdict": platform_verdict(b, current_by_platform.get(b.get("platform"))),
        }
        for b in baseline or []
    ]

    def count(v: str) -> int:
        return sum(1 for p in platforms if p["verdict"] == v)

    ranked = [
        p
        for p in platforms
        if p["verdict"] != "not_enough_data"
        and platform_mentioned(p["before"])
        and platform_mentioned(p["after"])
        and p["before"].get("mention_position") is not None
        and p["after"].get("mention_position") is not None
    ]

    def mean(xs):
        return _round1(sum(xs) / len(xs)) if xs else None

    return {
        "platforms": platforms,
        "summary": {
            "total": len(platforms),
            "measurable": len(platforms) - count("not_enough_data"),
            "improved": count("improved"),
            "declined": count("declined"),
            "avg_before": mean([p["before"]["mention_position"] for p in ranked]),
            "avg_after": mean([p["after"]["mention_position"] for p in ranked]),
        },
    }


def outcome_window(end: dt.date | str) -> dict:
    """The OUTCOME_WINDOW_DAYS days ending at `end`, as day strings.

    The one read over a window: baseline windows end the day before the
    work was raised, daily re-reads end today, and the final read ends on
    the last tracked day — never earlier (a half-open window reads low)."""
    to = end if isinstance(end, dt.date) else dt.date.fromisoformat(str(end)[:10])
    from_ = to - dt.timedelta(days=OUTCOME_WINDOW_DAYS)
    return {"from": from_.isoformat(), "to": to.isoformat()}


# ------------------------------------------------------------- storage API


def _add_prompt(conn, text: str, category: str, source: str) -> int | None:
    """One prompt by its text; an existing (or blank) text re-inserts nothing."""
    text = str(text or "").strip()
    if not text:
        return None
    have = conn.execute("SELECT id FROM prompts WHERE text = ?", (text,)).fetchone()
    if have is not None:
        return None
    cur = conn.execute(
        "INSERT INTO prompts(text, category, source, created_at) VALUES (?,?,?,?)",
        (text, category or "", source, db.now()),
    )
    return cur.lastrowid


def create_prompt(text: str, category: str = "", source: str = "manual") -> int | None:
    """Track a prompt verbatim (the question as a person would paste it).
    Idempotent: the same text never creates a second row."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("prompt text is required")
    with db._conn() as conn:
        have = conn.execute("SELECT id FROM prompts WHERE text = ?", (text,)).fetchone()
        if have is not None:
            return have["id"]
        return _add_prompt(conn, text, category, source)


def list_prompts(active_only: bool = True) -> list:
    """Tracked prompts, oldest first."""
    where = "WHERE is_active = 1" if active_only else ""
    with db._conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, text, category, source, is_active, created_at"
                f" FROM prompts {where} ORDER BY id"
            )
        ]


def seed_prompts(paa_limit: int = 12, watch_limit: int = 12, keywords: list | None = None) -> dict:
    """Seed tracked prompts from what SEOForge already tracks: the PAA
    tree's questions (verbatim questions — the closest thing to a prompt
    we own) and the keyword watchlist. Idempotent: already-tracked text
    is left alone, re-running adds nothing."""
    added = {"paa": 0, "watchlist": 0}
    with db._conn() as conn:
        if paa_limit > 0:
            for row in conn.execute(
                "SELECT question FROM paa_questions WHERE question IS NOT NULL AND question <> ''"
                " ORDER BY id DESC LIMIT ?",
                (int(paa_limit),),
            ):
                if _add_prompt(conn, row["question"], "paa", "seed_paa") is not None:
                    added["paa"] += 1
        texts = []
        if watch_limit > 0:
            for row in conn.execute(
                "SELECT keyword FROM watchlist ORDER BY added_at DESC LIMIT ?",
                (int(watch_limit),),
            ):
                texts.append(row["keyword"])
        for kw in keywords or []:
            texts.append(kw)
        for text in texts:
            if _add_prompt(conn, text, "watchlist", "seed_watchlist") is not None:
                added["watchlist"] += 1
    return added


def get_prompt_results(
    prompt_id: int, platform: str | None = None, since: str | None = None, until: str | None = None
) -> list:
    """A prompt's recorded answers, oldest first, citations decoded."""
    sql = (
        "SELECT id, prompt_id, platform, at, response, citations, mention_count,"
        " citation_count, sentiment, visibility_score, mention_position,"
        " mentioned_entity_count, competitor_mentions, competitors, source"
        " FROM prompt_results WHERE prompt_id = ?"
    )
    params: list = [prompt_id]
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    if since:
        sql += " AND substr(at, 1, 10) >= ?"
        params.append(str(since)[:10])
    if until:
        sql += " AND substr(at, 1, 10) <= ?"
        params.append(str(until)[:10])
    sql += " ORDER BY at, id"
    with db._conn() as conn:
        return [_row_with_json(r) for r in conn.execute(sql, params)]


def _row_with_json(row) -> dict:
    d = dict(row)
    for key in ("citations", "competitor_mentions", "competitors"):
        try:
            d[key] = json.loads(d[key]) if d.get(key) else []
        except (TypeError, ValueError):
            d[key] = []
    return d


def _result_rows(prompt_id: int, platform: str | None, since: str | None, until: str | None) -> list:
    return [
        {
            "mention_count": r["mention_count"],
            "mention_position": r["mention_position"],
            "mentioned_entity_count": r["mentioned_entity_count"],
            "citations": r["citations"],
        }
        for r in get_prompt_results(prompt_id, platform=platform, since=since, until=until)
    ]


def platform_states(
    prompt_id: int,
    platforms: list | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list:
    """One prompt's state per platform over a window, from the same rows
    and rules as the Positions panel. Every requested platform answers,
    even one with no runs (no_runs)."""
    wanted = [p for p in (platforms or PLATFORMS) if p in PLATFORMS]
    if not wanted:
        return []
    domains = brand()["domains"]
    snapshots = []
    for platform in wanted:
        state = classify_platform(_result_rows(prompt_id, platform, since, until), domains)
        snapshots.append(platform_snapshot(platform, state))
    return snapshots


def record_answer(
    prompt_id: int,
    platform: str,
    response: str,
    citations: list | None = None,
    sentiment: str = "neutral",
    competitors: list | None = None,
    at: str | None = None,
) -> dict:
    """Record one pasted-in answer to a prompt: parse it at write time
    (deterministic, no LLM) and store the metrics next to the text —
    append-only; a recorded answer never changes.

    Returns the new row's parsed values."""
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}: expect one of {PLATFORMS}")
    text = str(response or "")
    cites = [c for c in (citations or []) if isinstance(c, dict)]
    parsed = parse_response(text, cites, brand(), sentiment, competitors or [])
    stamp = at or db.now()
    with db._conn() as conn:
        if conn.execute("SELECT 1 FROM prompts WHERE id = ?", (prompt_id,)).fetchone() is None:
            raise ValueError(f"unknown prompt id {prompt_id}")
        cur = conn.execute(
            "INSERT INTO prompt_results(prompt_id, platform, at, response, citations,"
            " mention_count, citation_count, sentiment, visibility_score, mention_position,"
            " mentioned_entity_count, competitor_mentions, competitors, source)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                prompt_id,
                platform,
                stamp,
                text,
                json.dumps(cites),
                parsed["mention_count"],
                parsed["citation_count"],
                parsed["sentiment"],
                parsed["visibility_score"],
                parsed["mention_position"],
                parsed["mentioned_entity_count"],
                json.dumps(parsed["competitor_mentions"]),
                json.dumps(competitors or []),
                "manual",
            ),
        )
    return {
        "id": cur.lastrowid,
        "prompt_id": prompt_id,
        "platform": platform,
        "at": stamp,
        "source": "manual",
        **parsed,
    }
