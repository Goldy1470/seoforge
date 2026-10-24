from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx

from . import ai_bots, db, geo, safe_fetch
from .config import ROOT, settings

CAT_FILE = ROOT / "data" / "aeo_categories.json"
# Derived from the registry in data/ai_bots.json (which is the source of truth);
# the alias stays so existing imports keep working.
AI_AGENTS_EXTENDED = [b.userAgent for b in ai_bots.all_bots()]

ANSWER_SENTENCE = re.compile(r"\b(is|are|means|refers to|works by)\b")
PRICE_HINT = re.compile(r"(₹|\$|pricing|price|plans? start|book a call|get a quote)", re.I)
ORIGINAL_DATA = re.compile(r"(\b\d{1,3}% |\b\d+x\b|\bn\s*=\s*\d+\b|\bour data\b|\bwe tested\b)", re.I)
CTA_HINT = re.compile(r"(book|talk to|get started|contact|enquiry|inquire|whatsapp|free consult)", re.I)
CONTACT = re.compile(r"(tel:|mailto:|wa\.me/|contact-us|/inquire)", re.I)

FUNNEL = {
    "found": "AI agents and user agents can reach, read and parse you (no JS wall)",
    "answered": "the exact buying questions get a short, quotable answer block",
    "cited": "LLM answers actually carry your brand or domain",
    "recommended": "you appear when agents compose 'best X' shortlists",
    "chosen": "an assistive agent (human or AI) can ACT on you: price, proof, CTA, contact",
}


def load_categories() -> list[dict]:
    if CAT_FILE.exists():
        data = json.loads(CAT_FILE.read_text(encoding="utf-8"))
        return data.get("categories", [])
    return [
        {
            "id": "default",
            "name": settings.brand_name,
            "key_pages": ["/"],
            "money_patterns": ["best {cat}", "how to choose {cat}", "{cat} pricing"],
            "seed_questions": ["What does {brand} do?".format(brand=settings.brand_name)],
            "directories": [],
        }
    ]


def category_page(category_id: str) -> dict:
    for c in load_categories():
        if c["id"] == category_id:
            return c
    raise KeyError(f"category '{category_id}' not in data/aeo_categories.json")


def _live_pages(category: dict, limit: int = 6) -> list[str]:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT url FROM pages WHERE status=200 AND (url LIKE '%faq%' OR url LIKE '%guides%') LIMIT 12"
        ).fetchall()
        crawled = [r["url"] for r in rows]
    picks = []
    for prefix in category.get("key_pages", []):
        full = urljoin(settings.site_url, prefix)
        crawled_match = next((u for u in crawled if prefix in u), None)
        picks.append(crawled_match or full)
    return list(dict.fromkeys(picks))[:limit]


def _fetch(url: str) -> str | None:
    try:
        r = safe_fetch.fetch_sync(url, accept=None, truncate=True, timeout=25)
        return r.text if r.status == 200 else None
    except Exception:
        return None


def _visible_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def stage_found(site: str | None = None) -> dict:
    site = (site or settings.site_url).rstrip("/")
    geox = asyncio.run(geo.check_ai_readiness(site))
    checks = geox.get("checks", {})
    robots = checks.get("robots_ai_agents", {})
    named = sum(1 for b in robots.values() if b.get("explicitly_named"))
    registry = ai_bots.all_bots()
    total = max(len(registry), 1)
    llms = checks.get("llms_txt", {}).get("present", False)
    sitemap = checks.get("sitemap_xml", {}).get("present", False)
    ten_sec = checks.get("ten_second_test", {}).get("pages", {})
    rendered = [t for t in ten_sec.values() if t.get("server_rendered")]
    try:
        r = safe_fetch.fetch_sync(urljoin(site, "/llms-full.txt"), accept=None, truncate=True, timeout=15)
        llms_full = r.status == 200
    except Exception:
        llms_full = False
    score = round(
        100 * (
            (named / total) * 25
            + 20 * llms
            + 10 * llms_full
            + 15 * sitemap
            + 30 * (len(rendered) / max(len(ten_sec), 1) if ten_sec else 0)
        )
    )
    by_type = checks.get("robots_by_type", {})
    unnamed = [ua for ua, e in robots.items() if not e.get("explicitly_named")]
    training_blocked = by_type.get("training", {}).get("site_blocked", 0)
    training_total = by_type.get("training", {}).get("total", 0)
    search_or_user_blocked = (
        by_type.get("search", {}).get("site_blocked", 0) + by_type.get("user", {}).get("site_blocked", 0)
    )
    fixes = []
    if unnamed:
        fixes.append(
            "add explicit robots.txt sections for the unnamed agents ("
            + ", ".join(unnamed[:4]) + ("..." if len(unnamed) > 4 else "") + ")"
        )
    if training_blocked and not search_or_user_blocked and training_total:
        fixes.append(
            f"training crawlers blocked ({training_blocked}/{training_total}) while search/user agents are allowed "
            "- a common deliberate choice; make sure it is one"
        )
    fixes.extend([
        *(["write /llms-full.txt (full markdown corpus for agents)"] if not llms_full else []),
        *(["self-host / pre-render FAQ blocks - GPTBot only sees raw HTML"] if ten_sec and not rendered else []),
    ])
    return {
        "stage": "found",
        "score": score,
        "explicit_bot_sections": f"{named}/{total}",
        "bots_by_type": by_type,
        "llms_txt": llms,
        "llms_full_txt": llms_full,
        "sitemap": sitemap,
        "server_rendered_faqs": f"{len(rendered)}/{len(ten_sec)}",
        "fixes": fixes,
    }


def stage_answered(category: dict, pages: list[str]) -> dict:
    from bs4 import BeautifulSoup

    checks, llm_blocks = [], []
    for url in pages:
        html = _fetch(url)
        if not html:
            checks.append({"url": url, "reachable_bot_ua": False})
            continue
        text = _visible_text(html)
        fq_ok = 0
        for q in category.get("seed_questions", [])[:3]:
            qw = [w for w in q.lower().split() if len(w) > 3]
            hit = sum(1 for w in qw if w in text.lower())
            fq_ok += 1 if hit / max(len(qw), 1) >= 0.6 else 0
        soup = BeautifulSoup(html, "lxml")
        has_faq_schema = '"@type": "FAQPage"' in html or '"@type":"FAQPage"' in html
        first_1k = text[:1000].lower()
        has_def = bool(ANSWER_SENTENCE.search(first_1k))
        heads = " | ".join(h.get_text(" ").lower() for h in soup.find_all(["h2", "h3"])[:10])
        q_shaped_h = sum(1 for w in ("what", "how", "why", "cost", "best") if w in heads)
        checks.append(
            {
                "url": url,
                "reachable_bot_ua": True,
                "questions_matched_on_page": f"{fq_ok}/3",
                "faq_jsonld": has_faq_schema,
                "definition_in_first_1k": has_def,
                "question_shaped_headings": q_shaped_h,
            }
        )
    ok = [c for c in checks if c.get("reachable_bot_ua")]
    if not ok:
        return {"stage": "answered", "score": 0, "checks": checks, "fixes": ["pages unreachable to GPTBot agent UA - fix robots/rendering first"]}
    matched = sum(1 for c in ok if c["questions_matched_on_page"] != "0/3")
    schema = sum(1 for c in ok if c["faq_jsonld"])
    defs = sum(1 for c in ok if c["definition_in_first_1k"])
    heads = sum(1 for c in ok if c["question_shaped_headings"] >= 2)
    n = len(ok)
    return {
        "stage": "answered",
        "score": round(100 * (matched / n * 40 + schema / n * 25 + defs / n * 20 + heads / n * 15)),
        "checks": checks,
        "fixes": [
            *(["lead each page with a 40-70 word literal answer to the page's main question (the part AI quotes)"] if matched < n else []),
            *(["add FAQPage JSON-LD (struct.faq tool generates it)"] if schema < n else []),
            *(["put a 'X is/means...' definition sentence in the first paragraph for entity clarity"] if defs < n else []),
            *(["headings should mirror the real question phrasing from PAA"] if heads < n else []),
        ],
    }


def stage_cited() -> dict:
    with db._conn() as conn:
        row = conn.execute("SELECT payload FROM reports WHERE kind='ai_citation_check' ORDER BY id DESC LIMIT 1").fetchone()
        pages_n = conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"]
    cited_ratio = None
    if row:
        p = json.loads(row["payload"])
        cited_ratio = round(100 * p.get("cited", 0) / max(p.get("total", 1), 1))
    fixes = [] if row else [
        "run ai_citation_check on your money questions (the actual citation test)"
    ]
    return {
        "stage": "cited",
        "score": cited_ratio or 0,
        "cited_ratio_pct": cited_ratio,
        "pages_in_db": pages_n,
        "openseo_bridge": (
            "attach OpenSEO MCP - their 'Research questions with ChatGPT answers and cited sources' tool "
            "re-runs this test across models and logs competitor citations"
        ),
        "fixes": fixes or [
            "publish original data (n=..., %) - LLMs prefer citing first-party stats",
            "name your methodology on guides so engines can attribute",
            "keep author entity + sameAs schema so citations resolve to one entity",
        ],
    }


def stage_recommended(category: dict) -> dict:
    with db._conn() as conn:
        rank = conn.execute(
            "SELECT position FROM rank_snapshots ORDER BY searched_at DESC LIMIT 60"
        ).fetchall()
        dirs_ai = [r["name"] for r in conn.execute(
            "SELECT name FROM directories WHERE category='ai-directory' AND status='live'"
        ).fetchall()]
    best = min((r["position"] for r in rank), default=None)
    scored = round(60 * min(30 - best, 30) / 30) if best is not None and best <= 30 else 0
    return {
        "stage": "recommended",
        "score": min(100, scored + 10 * len(dirs_ai)),
        "best_rank_seen": best,
        "ai_directories_live": dirs_ai,
        "ask_in_agent": f"ask ChatGPT: 'best {category['name']} for a D2C brand' -> count you vs competitors",
        "fixes": [
            f"create the '{category['name']} how to choose / pricing' comparison page - agents cite selection criteria",
            *(["get listed in the AI directories from the 73-list (There's an AI for That, Toolify, Fazier)"] if not dirs_ai else []),
            *(["watch_add your money keywords so position trends feed this stage"] if not rank else []),
        ],
    }


def stage_chosen(category: dict, pages: list[str]) -> dict:
    checks = []
    for url in pages[:3]:
        html = _fetch(url)
        if not html:
            continue
        has_product = '"@type": "Product"' in html or '"@type":"Product"' in html
        has_review = '"aggregateRating"' in html or '"@type": "Review"' in html
        text = _visible_text(html)
        checks.append(
            {
                "url": url,
                "offer_schema": has_product,
                "review_schema": has_review,
                "price_or_cta_visible": bool(PRICE_HINT.search(text[:2500])),
                "clear_cta": bool(CTA_HINT.search(text[:2500])),
                "contact_readable": bool(CONTACT.search(html)),
            }
        )
    if not checks:
        return {"stage": "chosen", "score": 0, "fixes": ["category key pages unreachable - cannot verify act-on-it signals"]}
    n = len(checks)
    score = round(100 * (
        sum(1 for c in checks if c["offer_schema"]) / n * 20
        + sum(1 for c in checks if c["review_schema"]) / n * 20
        + sum(1 for c in checks if c["price_or_cta_visible"]) / n * 25
        + sum(1 for c in checks if c["clear_cta"]) / n * 20
        + sum(1 for c in checks if c["contact_readable"]) / n * 15
    ))
    return {
        "stage": "chosen",
        "score": score,
        "checks": checks,
        "fixes": [
            *(["add Product+Offer schema with price (struct.product tool) to service/product pages"] if any(not c["offer_schema"] for c in checks) else []),
            *(["show AggregateRating/Review schema from real reviews"] if any(not c["review_schema"] for c in checks) else []),
            *(["make pricing or 'book a call' visible in first screen so assistive agents resolve intent"] if any(not c["price_or_cta_visible"] for c in checks) else []),
            *(["machine-readable contact (tel:, mailto:, wa.me) so agents can act on your behalf"] if any(not c["contact_readable"] for c in checks) else []),
        ],
    }


def funnel_audit(category_id: str | None = None, persist: bool = True) -> dict:
    category = category_page(category_id) if category_id else (load_categories()[0])
    pages = _live_pages(category)
    stages = {
        "found": stage_found(),
        "answered": stage_answered(category, pages),
        "cited": stage_cited(),
        "recommended": stage_recommended(category),
        "chosen": stage_chosen(category, pages),
    }
    overall = round(sum(s["score"] for s in stages.values()) / 5)
    report = {
        "category": category["id"],
        "name": category["name"],
        "funnel": FUNNEL,
        "stages": stages,
        "overall_score": overall,
        "llms_txt_status": "llms.txt alone is outdated - ship llms.txt (index) + llms-full.txt (corpus) + per-agent robots",
    }
    if persist:
        report["report_id"] = db.save_report("aeo_funnel", report)
    return report


def funnel_matrix() -> list[dict]:
    rows = []
    for c in load_categories():
        r = funnel_audit(c["id"], persist=False)
        rows.append(
            {
                "category": c["id"],
                "name": c["name"],
                "overall": r["overall_score"],
                "found": r["stages"]["found"]["score"],
                "answered": r["stages"]["answered"]["score"],
                "cited": r["stages"]["cited"]["score"],
                "recommended": r["stages"]["recommended"]["score"],
                "chosen": r["stages"]["chosen"]["score"],
            }
        )
    return rows


def llms_full_txt_from_crawl(top_n: int = 200) -> str:
    """llms-full.txt: index + long-form corpus. Validate found,llms_full will pass."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT url, title, meta_desc, h1 FROM pages WHERE status=200 AND word_count > 250 "
            "AND job_id=(SELECT id FROM jobs WHERE kind='crawl' ORDER BY started_at DESC LIMIT 1) LIMIT ?",
            (top_n,),
        ).fetchall()
    head = [
        f"# {settings.brand_name}",
        "",
        f"> {settings.brand_tagline or settings.brand_name}",
        "",
        "## Categories we serve",
    ]
    body = []
    for c in load_categories():
        head.append(f"- {c['name']}: {'; '.join(c.get('money_patterns', [])[:2])}")
        pages = [r for r in rows if any(p in r["url"] for p in c.get("key_pages", []))]
        for r in pages[:6]:
            body += [
                f'### {r["title"] or r["h1"] or r["url"]}',
                f"Source: {r['url']}",
                (r["meta_desc"] or "")[:180],
                "",
            ]
    head += ["", "## How to cite us", f"- cite {settings.site_url} with the page title above", "- direct answers live in each page's first 200 words"]
    rest = [f"- [{r['title'] or r['url']}]({r['url']}): {(r['meta_desc'] or '')[:140]}" for r in rows]
    return "\n".join(head) + "\n\n" + "\n".join(body) + "\n### All pages\n" + "\n".join(rest[:100]) + "\n"
