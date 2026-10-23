"""On-page SEO: the six rules as per-page checks and a one-query build pack.

1 Title tag         - match how people phrase the search, short enough to show in full
2 Meta description  - tell searchers why your page beats the results above it
3 Headings          - headings ask the question first, details follow
4 First sentences   - answer the heading straight away, then add the details
5 Images            - real photos, alt text describing what is in them
6 Internal links    - link related pages, anchor text that matches their titles
"""
from __future__ import annotations

import json
import re

from . import db
from .config import settings

_TITLE_MAX = 60
_META_MAX = 155
_QUESTION = re.compile(r"^(what|why|how|when|which|who|whom|whose|where|is|are|was|were|can|could|do|does|did|should|will|would)\b|[\?$]", re.I)
_GENERIC_ANCHOR = re.compile(r"^(click here|read more|learn more|here|this|more|link|details|info)$", re.I)


def _tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def rule_title(title: str, query: str = "") -> list:
    out = []
    if not title:
        return [{"rule": "1-title", "level": "warn", "detail": "no title tag at all"}]
    if len(title) > _TITLE_MAX:
        out.append({"rule": "1-title", "level": "warn", "detail": f"{len(title)} chars - cut past {_TITLE_MAX} will be hidden; shorten while keeping the search phrase"})
    elif len(title) < 30:
        out.append({"rule": "1-title", "level": "warn", "detail": "too thin - room to add the phrase + a differentiator"})
    if query:
        qtok, ttok = _tokens(query), _tokens(title)
        recall = len(qtok & ttok) / max(len(qtok), 1)
        if recall < 0.6:
            out.append({"rule": "1-title", "level": "warn", "detail": f"phrase mismatch: shares {int(recall * 100)}% of '{query}' words - title must match how people phrase the search"})
        else:
            out.append({"rule": "1-title", "level": "ok", "detail": f"covers '{query}' ({int(recall * 100)}%)"})
        first_word_hit = bool(qtok & set(_tokens(title[:15])))
        if not first_word_hit:
            out.append({"rule": "1-title", "level": "warn", "detail": "search phrase not leading - move it to the front"})
    return out


def rule_meta(meta: str, title: str) -> list:
    out = []
    if not meta:
        return [{"rule": "2-meta", "level": "warn", "detail": "no meta description - Google invents one and it won't say why you win"}]
    if len(meta) > _META_MAX:
        out.append({"rule": "2-meta", "level": "warn", "detail": f"{len(meta)} chars - cut at ~{_META_MAX}; frontload the differentiator"})
    elif len(meta) < 70:
        out.append({"rule": "2-meta", "level": "warn", "detail": "too short to sell the 'why us' - use the space"})
    diff_words = {"free", "guarantee", "days", "price", "pricing", "fast", "speed", "proof", "why", "save", "affordable", "cheap", "results", "certified", "trusted", "expert", "years", "clients", "rating", "demo", "book", "compare"}
    ttok, mtok = _tokens(title), _tokens(meta)
    overlap = len(ttok & mtok) / max(len(ttok), 1)
    has_diff = bool(diff_words & mtok)
    if has_diff:
        out.append({"rule": "2-meta", "level": "ok", "detail": "says why it beats the results above (differentiator present)"})
    elif overlap > 0.7:
        out.append({"rule": "2-meta", "level": "warn", "detail": "echoes the title - add why it beats the pages above (number, price, guarantee, speed, proof)"})
    else:
        out.append({"rule": "2-meta", "level": "ok", "detail": "differs from title - has room for a differentiator"})
    return out


def rule_headings(headings: list | None) -> list:
    if headings is None:
        return [{"rule": "3-headings", "level": "skip", "detail": "captured before v0.6 - recrawl to audit headings"}]
    out = []
    if not headings:
        return [{"rule": "3-headings", "level": "warn", "detail": "no h2/h3 - wall of text; headings ask the question, details follow"}]
    non_q = [h for h in headings if not _QUESTION.search(h)]
    if non_q:
        out.append({"rule": "3-headings", "level": "warn", "detail": f"{len(non_q)}/{len(headings)} not in the customer's question form, first: \"{non_q[0][:50]}\" - phrase headings as the questions people search"})
    else:
        out.append({"rule": "3-headings", "level": "ok", "detail": f"all {len(headings)} headings are question-shaped"})
    long_h = [h for h in headings if len(h) > 70]
    if long_h:
        out.append({"rule": "3-headings", "level": "warn", "detail": f"heading over 70 chars: \"{long_h[0][:60]}\""})
    return out


def rule_first(first_text: str | None, h1: str = "", query: str = "") -> list:
    if first_text is None:
        return [{"rule": "4-first-sentence", "level": "skip", "detail": "captured before v0.6 - recrawl to audit"}]
    qtok = _tokens(query) or _tokens(h1)
    if not qtok:
        return [{"rule": "4-first-sentence", "level": "skip", "detail": "no target phrase known (gsc or h1)"}]
    ftok = _tokens(first_text[:280])
    recall = len(qtok & ftok) / max(len(qtok), 1)
    if recall >= 0.5:
        return [{"rule": "4-first-sentence", "level": "ok", "detail": f"answers straight away ({int(recall * 100)}% of '{query or h1}' in the first words)"}]
    return [{"rule": "4-first-sentence", "level": "warn", "detail": f"answer not in the opening words ({int(recall * 100)}% coverage of '{query or h1}') - answer immediately, then details"}]


def rule_images(missing_alt: int | None) -> list:
    if not missing_alt:
        return [{"rule": "5-images", "level": "ok", "detail": "alt text present on captured images"}]
    return [{"rule": "5-images", "level": "warn", "detail": f"{missing_alt} images without alt - describe what is in the photo (subject + what is happening), not keywords"}]


def _anchor_findings(anchor: str, target_title: str) -> list:
    a = (anchor or "").strip()
    title = (target_title or "").strip()
    if not a:
        return [{"rule": "6-internal-links", "level": "warn", "detail": "empty anchor text"}]
    if _GENERIC_ANCHOR.match(a.lower()):
        return [{"rule": "6-internal-links", "level": "warn", "detail": f"generic anchor '{a}' - use the target's title instead"}]
    if not title:
        return []
    recall = len(_tokens(a) & _tokens(title)) / max(len(_tokens(title)), 1)
    if recall < 0.4:
        return [{"rule": "6-internal-links", "level": "warn", "detail": f"anchor '{a[:40]}' vs title '{title[:40]}' - anchor text should match the destination title"}]
    return [{"rule": "6-internal-links", "level": "ok", "detail": "anchor matches destination title"}]


def _target_query(conn, page_url: str) -> str:
    row = conn.execute(
        "SELECT query FROM gsc_cache WHERE page=? GROUP BY query ORDER BY SUM(impressions) DESC LIMIT 1",
        (page_url,),
    ).fetchone()
    return row["query"] if row else ""


def audit(url: str | None = None, limit_rows: int = 200) -> dict:
    """Six-rule audit over the crawl DB; skips uncaptured fields on pre-v0.6 crawls."""
    findings = []
    pages = []
    rule_counts = {"1-title": 0, "2-meta": 0, "3-headings": 0, "4-first-sentence": 0, "5-images": 0, "6-internal-links": 0}
    with db._conn() as conn:
        if url:
            rows = conn.execute(
                "SELECT * FROM pages WHERE status=200 AND url LIKE ? ORDER BY harvested_at DESC LIMIT ?",
                (f"%{url}%", limit_rows),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pages WHERE status=200 AND title IS NOT NULL ORDER BY harvested_at DESC LIMIT ?",
                (limit_rows,),
            ).fetchall()
        meta_dups = {
            r["meta_desc"]: c
            for r, c in conn.execute(
                "SELECT meta_desc, COUNT(*) c FROM pages WHERE status=200 AND meta_desc IS NOT NULL AND meta_desc<>'' GROUP BY meta_desc HAVING c>1"
            ).fetchall()
        }
        titles_map = {r["url"]: r["title"] for r in conn.execute("SELECT url,title FROM pages")}
        for r in rows:
            page_findings = []
            query = _target_query(conn, r["url"])
            headings = json.loads(r["headings"]) if r["headings"] else None
            page_findings += rule_title(r["title"] or "", query)
            page_findings += rule_meta(r["meta_desc"] or "", r["title"] or "")
            if r["meta_desc"] and meta_dups.get(r["meta_desc"]):
                page_findings.append({"rule": "2-meta", "level": "warn", "detail": f'duplicate meta across {meta_dups[r["meta_desc"]]} pages'})
            page_findings += rule_headings(headings)
            page_findings += rule_first(r["first_text"], r["h1"] or "", query)
            page_findings += rule_images(r["images_missing_alt"])
            anchors = conn.execute(
                "SELECT anchor,target FROM links WHERE source=? LIMIT 60", (r["url"],)
            ).fetchall()
            anchor_warns = 0
            for l in anchors:
                if _registrable_ok(l["target"], r["url"]):
                    for f in _anchor_findings(l["anchor"], titles_map.get(l["target"], "")):
                        if f["level"] == "warn":
                            anchor_warns += 1
                        page_findings.append({**f, "target": l["target"]})
            if anchor_warns > 3:
                page_findings.append({"rule": "6-internal-links", "level": "warn", "detail": f"{anchor_warns} links with off-title anchors on this page"})
            for f in page_findings:
                if f["level"] == "warn":
                    rule_counts[f["rule"]] += 1
            pages.append({"url": r["url"], "title": (r["title"] or "")[:70], "warnings": sum(1 for f in page_findings if f["level"] == "warn")})
            findings.append({"url": r["url"], "findings": page_findings})
    return {
        "site": settings.site_url,
        "pages_audited": len(rows),
        "warnings_by_rule": rule_counts,
        "worst_pages": sorted(pages, key=lambda p: -p["warnings"])[:10],
        "findings": findings if url else [w for w in findings if any(f["level"] == "warn" for f in w["findings"])],
    }


def _registrable_ok(target: str, source: str) -> bool:
    if not target:
        return False
    try:
        from urllib.parse import urlsplit

        return urlsplit(target).netloc == urlsplit(source).netloc
    except Exception:
        return False


def _smart_title(query: str) -> str:
    brand = settings.brand_name
    q = query.strip().title()
    t = f"{q} | {brand}"
    if len(t) > _TITLE_MAX:
        words = q.split()
        while words and len(f"{' '.join(words)} | {brand}") > _TITLE_MAX:
            words.pop()
        t = f"{' '.join(words)} | {brand}"
        if len(t) > _TITLE_MAX:
            t = t[:_TITLE_MAX].rstrip()
    if len(t) < 30 and len(f"{t} - Free Consultation") <= _TITLE_MAX:
        t = f"{t} - Free Consultation"
    return t


def _question_headings(query: str, fallbacks: list[str]) -> list:
    qtok = _tokens(query)
    with db._conn() as conn:
        rows = conn.execute("SELECT question FROM paa_questions").fetchall()
    scored = sorted(
        (r["question"] for r in rows),
        key=lambda q: -len(qtok & _tokens(q)),
    )
    picked = [q for q in scored if len(qtok & _tokens(q)) > 0][:4]
    return (picked + fallbacks)[:6]


def fix_pack(query: str, usp: str = "") -> dict:
    """One query -> the six on-page rules pre-complied: title, meta, headings, first line, images, internal links."""
    usp = usp or "done for you in days, with a money-back guarantee"
    brand = settings.brand_name
    title = _smart_title(query)
    meta = f"{title.split('|')[0].strip()} without the agency price tag. {usp.title()}. Talk to {brand} today."
    if len(meta) > _META_MAX:
        meta = meta[: _META_MAX - 1].rstrip() + "."
    fallbacks = [
        f"What does {query.lower()} cost?",
        f"How long does {query.lower()} take?",
        f"How to choose a {query.lower()} provider",
        f"Is {query.lower()} worth it?",
    ]
    headings = _question_headings(query, fallbacks)
    first = f"{title.split('|')[0].strip()} is {usp.split(',')[0].lower()}. The next two sentences answer the question straight away - details come after."
    with db._conn() as conn:
        qt = _tokens(query)
        rows = conn.execute(
            "SELECT url,title FROM pages WHERE status=200 AND title IS NOT NULL LIMIT 300"
        ).fetchall()
    related = sorted(
        (r for r in rows if len(qt & _tokens(r["title"]))> 0),
        key=lambda r: -len(qt & _tokens(r["title"])),
    )[:3]
    links = [{"anchor": r["title"], "to": r["url"]} for r in related]
    checks = {
        "1-title": {f["level"] for f in rule_title(title, query)},
        "2-meta": {f["level"] for f in rule_meta(meta, title)},
    }
    return {
        "query": query,
        "blocks": [
            {"rule": "1-title", "deliver": title, "length": len(title)},
            {"rule": "2-meta", "deliver": meta, "length": len(meta)},
            {"rule": "3-headings", "deliver": headings, "note": "each heading becomes a question first, details below it"},
            {"rule": "4-first-sentence", "deliver": first},
            {"rule": "5-images", "deliver": "real photos only; alt = what is literally in the photo, e.g. '<subject> - <what is happening>'"},
            {"rule": "6-internal-links", "deliver": links or "no matching crawled pages yet - link pages whose titles share the query words", },
        ],
        "self_check": checks,
    }


def opportunities() -> list:
    """Rule violations as scored work-queue items (fix_onpage)."""
    out = []
    try:
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT url,title,meta_desc,headings,word_count,images_missing_alt FROM pages WHERE status=200 LIMIT 400"
            ).fetchall()
        for r in rows:
            title = r["title"] or ""
            if (title and len(title) > _TITLE_MAX) or not title:
                out.append(
                    {
                        "type": "fix_onpage",
                        "target": f"title|{r['url']}",
                        "impact": 3,
                        "effort": 1,
                        "sources": ["crawler"],
                        "evidence": {"title": title[:70], "length": len(title), "rule": "1"},
                    }
                )
            if not (r["meta_desc"] or "").strip() or len(r["meta_desc"] or "") > _META_MAX:
                out.append(
                    {
                        "type": "fix_onpage",
                        "target": f"meta|{r['url']}",
                        "impact": 2.5,
                        "effort": 1,
                        "sources": ["crawler"],
                        "evidence": {"meta": (r["meta_desc"] or "")[:70], "rule": "2"},
                    }
                )
            if (r["images_missing_alt"] or 0) > 0:
                out.append(
                    {
                        "type": "fix_onpage",
                        "target": f"alt|{r['url']}",
                        "impact": 2,
                        "effort": 1,
                        "sources": ["crawler"],
                        "evidence": {"missing_alt": r["images_missing_alt"], "rule": "5"},
                    }
                )
    except Exception:
        return out
    return out
