"""Six-step keyword research playbook (the SEO curriculum, as tools).

1 Understand your audience  -> audience_problems
2 Find seed keywords        -> seed_ideas
3 Use one tool              -> provider (difficulty scores differ between tools)
4 Check search intent       -> check_intent (teach / compare / sell)
5 Check the numbers         -> worth_a_page (volume, competition, trend)
6 Group your keywords       -> keyword_groups (one page per cluster, never compete with yourself)
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter

from . import aeo, db, keywords
from .config import settings

_PROVIDER = {
    "tool": "DataForSEO (labs keyword_ideas + organic SERP live)",
    "why": "difficulty scores differ between tools - pick ONE and stick with it so every score is comparable. DataForSEO is the one wired into seoforge (same numbers in keyword_ideas, SERP, backlinks, rank tracking).",
    "config": "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD in .env",
    "alternative": "attach OpenSEO instead (mcp tool openseo_setup) - same rule, never mix two difficulty scales",
}

_TEACH = re.compile(r"/blog|/guide|/how|/tutorial|/faq|what\s+is|how\s+to|tip|example|definition|explained", re.I)
_COMPARE = re.compile(r"\bbest\b|\bvs\b|\bversus\b|alternativ|compar|\btop\s*\d|\breview|\brated\b", re.I)
_SELL = re.compile(r"/product|/pricing|/price|/shop|/store|/service|/contact|/offer|/demo|/cart|buy|hire|book\b|order", re.I)
_PAGE_TYPE = {
    "teach": "guide / foundational answer page",
    "compare": "comparison or listicle page",
    "sell": "product or service landing page",
}


def provider() -> dict:
    return dict(_PROVIDER)


def _offered(explicit: list[str] | None) -> list[str]:
    if explicit:
        return list(dict.fromkeys(explicit))
    with db._conn() as conn:
        rows = conn.execute("SELECT title FROM products").fetchall()
    names = [r["title"] for r in rows if r["title"]]
    if not names:
        names = [c["id"].replace("-", " ") for c in aeo.load_categories()]
    return names


def _problems_for(name: str) -> list[str]:
    n = name.strip().lower()
    return [
        f"how does {n} work",
        f"why do {n} projects fail",
        f"best {n}",
        f"{n} alternatives",
        f"{n} for d2c brands",
        f"is {n} worth it",
        f"{n} cost",
        f"how to choose a {n}",
    ]


def audience_problems(products: list[str] | None = None, limit: int = 40) -> dict:
    """Problems your customers search about, in their words: real GSC queries + PAA + category + generated."""
    src: dict[str, list[str]] = {}
    with db._conn() as conn:
        gsc = conn.execute(
            "SELECT query, SUM(impressions) imp FROM gsc_cache GROUP BY query ORDER BY imp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        paa = conn.execute(
            "SELECT question, cluster FROM paa_questions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    src["gsc_real_queries"] = [r["query"] for r in gsc]
    src["paa_questions"] = [r["question"] for r in paa]
    src["category_questions"] = [
        q.format(brand=settings.brand_name)
        for c in aeo.load_categories()
        for q in c.get("seed_questions", [])
    ]
    src["generated_problem_drills"] = [p for n in _offered(products) for p in _problems_for(n)]
    seen: set[str] = set()
    problems = []
    for kind, items in src.items():
        for text in items:
            key = text.strip().lower()
            if key and key not in seen:
                seen.add(key)
                problems.append({"text": text.strip(), "source": kind})
    return {"provider": provider(), "counts": {k: len(v) for k, v in src.items()}, "problems": problems}


def seed_ideas(products: list[str] | None = None) -> dict:
    """Start broad with how a customer would describe what you sell (rule 2)."""
    seeds = []
    for n in _offered(products):
        n = n.lower()
        seeds += [
            n,
            f"best {n}",
            f"{n} for d2c brands",
            f"{n} pricing",
            f"is {n} worth it",
            f"how to choose a {n}",
            f"affordable {n}",
            f"{n} vs",
        ]
    return {
        "provider": provider(),
        "seeds": list(dict.fromkeys(seeds)),
        "note": "start broad - narrow only after you see what buyers actually type",
    }


def _classify_text(text: str) -> str:
    if _SELL.search(text):
        return "sell"
    if _COMPARE.search(text):
        return "compare"
    if _TEACH.search(text):
        return "teach"
    return "teach"


def _heuristic_intent(keyword: str) -> dict:
    k = keyword.lower()
    if re.search(r"\bbest\b|\bvs\b|\btop\b|alternativ|review", k):
        dominant = "compare"
    elif re.search(r"price|pricing|cost|buy|hire|agency|service", k):
        dominant = "sell"
    else:
        dominant = "teach"
    return {
        "keyword": keyword,
        "mode": "keyword-heuristic (no DataForSEO creds - google the keyword manually and ask: do the results teach, compare or sell?)",
        "intent": {dominant: 1},
        "dominant": dominant,
        "page_type": _PAGE_TYPE[dominant],
    }


def check_intent(keyword: str) -> dict:
    """Google it for real and see whether the results teach, compare or sell (rule 4)."""
    try:
        s = asyncio.run(keywords.serp(keyword))
    except SystemExit as exc:
        out = _heuristic_intent(keyword)
        out["how_to_get_real_serps"] = str(exc)
        return out
    except Exception as exc:
        out = _heuristic_intent(keyword)
        out["how_to_get_real_serps"] = f"serp fetch failed: {exc}"[:120]
        return out
    counts: Counter = Counter()
    evidence = []
    for r in s["organic"]:
        bucket = _classify_text(f"{r.get('title', '')} {r.get('url', '')}")
        counts[bucket] += 1
        evidence.append({"rank": r.get("rank"), "title": (r.get("title") or "")[:60], "bucket": bucket})
    if not counts:
        out = _heuristic_intent(keyword)
        out["how_to_get_real_serps"] = "SERP returned no organic rows"
        return out
    dominant = max(counts, key=counts.get)
    return {
        "keyword": keyword,
        "mode": "live-serp",
        "intent": dict(counts),
        "dominant": dominant,
        "page_type": _PAGE_TYPE[dominant],
        "evidence": evidence[:10],
        "paa_questions": s["people_also_ask"][:5],
    }


def judge(volume, competition=None, trend=None) -> dict:
    """Volume + difficulty + trend -> is it worth a page (rule 5). Deterministic, offline."""
    comp = competition
    if isinstance(competition, str):
        comp = {"low": 0.2, "medium": 0.5, "high": 0.85}.get(competition.lower())
    if volume is None:
        return {"verdict": "unknown", "reason": "no numbers yet - add DataForSEO creds (worth_a_page fills these)"}
    if volume < 100:
        return {"verdict": "skip", "reason": "only ~{v}/mo demand - not worth its own page (unless it is your brand name)".format(v=volume)}
    if volume >= 1000 and (comp is None or comp < 0.6):
        return {"verdict": "page", "reason": f"strong demand and {'manageable' if comp is not None else 'unmeasured'} competition - build the page", "trend": trend}
    if volume >= 1000:
        return {"verdict": "needs-edge", "reason": "heavy demand but crowded - go only if you can say something the pages above you don't (rule: your comparison/FAQ page must differ)", "trend": trend}
    return {"verdict": "page", "reason": "long-tail: low demand but low competition wins fastest", "trend": trend}


async def _numbers_for(kws: list[str]) -> dict:
    out = {}
    for kw in kws:
        out[kw] = await keywords.keyword_suggestions(kw)
    return out


def worth_a_page(keywords_in: list[str]) -> dict:
    """Rule 5 for a keyword batch: volume / competition / trend -> page, skip or needs-edge."""
    kws = list(dict.fromkeys(keywords_in))[:8]
    try:
        data = asyncio.run(_numbers_for(kws))
    except SystemExit as exc:
        return {
            "provider": provider(),
            "skipped": str(exc),
            "results": [{"keyword": kw, **judge(None)} for kw in kws],
        }
    except Exception as exc:
        return {"provider": provider(), "skipped": f"dataforseo fetch failed: {exc}"[:120], "results": [{"keyword": kw, **judge(None)} for kw in kws]}
    results = []
    for kw in kws:
        rows = data.get(kw) or []
        exact = next((r for r in rows if r["keyword"].lower() == kw.lower()), None)
        near = next((r for r in rows if set(kw.lower().split()) <= set(r["keyword"].lower().split())), None)
        pick = exact or near
        if pick:
            j = judge(pick.get("volume"), pick.get("competition"), pick.get("monthly_trend"))
            j.update(
                keyword=kw,
                matched=pick["keyword"] if not exact else "exact",
                volume=pick.get("volume"),
                competition=pick.get("competition"),
                difficulty_hint=pick.get("difficulty"),
            )
        else:
            j = judge(None)
            j["keyword"] = kw
        results.append(j)
    return {"provider": provider(), "results": results}


def _pool_from_db() -> list[str]:
    with db._conn() as conn:
        gsc = [r["query"] for r in conn.execute("SELECT DISTINCT query FROM gsc_cache LIMIT 100")]
        paa = [r["question"] for r in conn.execute("SELECT DISTINCT question FROM paa_questions LIMIT 100")]
        watch = [r["keyword"] for r in conn.execute("SELECT keyword FROM watchlist")]
    return list(dict.fromkeys(gsc + paa + watch))


def _tokens(kw: str) -> frozenset:
    return frozenset(re.findall(r"[a-z0-9]+", kw.lower()))


def group_keywords(keywords_in: list[str] | None = None, min_overlap: float = 0.6, max_pages: int = 30) -> dict:
    """Similar keywords share one page so you never compete with yourself (rule 6)."""
    pool = list(dict.fromkeys(keywords_in)) if keywords_in else _pool_from_db()
    clusters: list[list[str]] = []
    for kw in pool:
        tok = _tokens(kw)
        home = None
        for cl in clusters:
            if any(len(tok & _tokens(m)) / max(min(len(tok), len(_tokens(m))), 1) >= min_overlap for m in cl):
                home = cl
                break
        if home is not None:
            home.append(kw)
        else:
            clusters.append([kw])
    groups = []
    for cl in clusters:
        cl.sort(key=lambda k: (len(_tokens(k)), k))
        primary = cl[0]
        groups.append(
            {
                "primary": primary,
                "slug": re.sub(r"[^a-z0-9]+", "-", primary.lower()).strip("-")[:60],
                "members": cl,
            }
        )
    groups.sort(key=lambda g: -len(g["members"]))
    return {
        "keyword_count": len(pool),
        "page_count": len(groups),
        "rule": "one page per cluster: similar keywords share a page so you never compete with yourself (live check after publishing: gsc_cannibalization)",
        "groups": groups[:max_pages],
    }


def workflow(products: list[str] | None = None, deep_on: int = 4, limit: int = 40) -> dict:
    """All six rules in one pass; steps needing creds degrade to skipped, never crash."""
    problems = audience_problems(products, limit)
    step2 = seed_ideas(products)
    step3 = provider()
    probe = step2["seeds"][:deep_on]
    intents = [check_intent(s) for s in probe]
    numbers = worth_a_page(probe)
    everything = [p["text"] for p in problems["problems"]] + step2["seeds"]
    step6 = group_keywords(everything)
    report = {
        "site": settings.site_url,
        "rule_1_audience": problems,
        "rule_2_seeds": step2,
        "rule_3_one_tool": step3,
        "rule_4_intent": {"checked": [i for i in intents], "note": "teach -> guide, compare -> comparison page, sell -> landing"},
        "rule_5_numbers": numbers,
        "rule_6_groups": step6,
    }
    rid = db.save_report("keyword_research", report)
    report["report_id"] = rid
    return report
