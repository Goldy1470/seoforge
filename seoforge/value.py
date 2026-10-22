from __future__ import annotations

from .config import settings

SEMRUSH_PRO_USD = 139.95
SEMRUSH_GURU_USD = 249.95
OPENSEO_HOSTED_USD = 10.0
DATAFORSEO_LIGHT_USD = 15.0

MATRIX = [
    ("Keyword research (volume/difficulty/CPC)", "yes", "OpenSEO MCP skill or DataForSEO (keywords.py)"),
    ("Rank tracking", "yes, 250 keywords", "seoforge serp_watch + watch_add (DataForSEO SERP)"),
    ("Site audit / crawl", "yes, limited projects", "seoforge crawler.py (Screaming-Frog-style, unlimited runs)"),
    ("Backlinks analytics", "yes", "DataForSEO backlinks + tracker"),
    ("Competitor analysis", "yes", "OpenSEO MCP + competitor_gap"),
    ("Content post-audit (position tracking)", "yes", "rank_snapshots history"),
    ("AI visibility / GEO", "guru-tier only, add-on-ish", "seoforge aeo.py funnel + ai_citation_check + OpenSEO AI-visibility"),
    ("Question-pages build list (PAA)", "manual", "seoforge paa.py: PAA tree -> scaffold + FAQPage JSON-LD"),
    ("Internal linking engine", "pillars only", "seoforge internal_links.py (PageRank + TF-IDF anchors)"),
    ("Faceted navigation rules", "not really", "seoforge facets.py"),
    ("Review/FAQ/Product schema generator", "separate tools", "seoforge struct.py"),
    ("Merchant Center feed", "separate product", "seoforge merchant.py"),
    ("Directory backlink automation (browser agent)", "no", "seoforge browser_agent.py + 73-directory tracker"),
    ("IndexNow push / new-page indexing", "no", "seoforge indexing.py"),
    ("Nightly delta + scored work queue", "dashboards, no queue", "seoforge scheduler + integrations.py"),
    ("Open-source / you own it", "no", "yes - MIT-style freedom, self-host anything"),
]


def coverage() -> list[dict]:
    return [
        {"module": m, "semrush_pro": s, "seoforge_stack": v}
        for m, s, v in MATRIX
    ]


def cost_model(openseo_hosted: bool = False) -> dict:
    openseo = OPENSEO_HOSTED_USD if openseo_hosted else 0.0
    total = openseo + DATAFORSEO_LIGHT_USD
    semrush = SEMRUSH_PRO_USD
    return {
        "semrush_pro_per_month": semrush,
        "seoforge_stack_per_month": {
            "openseo_hosted_usd": openseo,
            "dataforseo_typical_usage_usd": DATAFORSEO_LIGHT_USD,
            "gsc_pagespeed_indexnow": 0.0,
            "seoforge_selfhosted": 0.0,
        },
        "seoforge_stack_total": round(total, 2),
        "savings_usd_month": round(semrush - total, 2),
        "savings_pct": round(100 * (semrush - total) / semrush, 1),
        "note": "DataForSEO usage varies with volume; light-agency usage ≈ $10-30/mo. Self-host OpenSEO = $0 hosting on Cloudflare free plan.",
    }


def value_report(openseo_hosted: bool = False) -> dict:
    return {
        "headline": f"Semrush Pro ${SEMRUSH_PRO_USD}/mo vs SEOForge stack ≈ "
        f"${round(OPENSEO_HOSTED_USD + DATAFORSEO_LIGHT_USD if openseo_hosted else DATAFORSEO_LIGHT_USD)}/mo "
        "for the data that matters, with execution (agent browser, schema, question pages) Semrush never does",
        "coverage_matrix": coverage(),
        "cost": cost_model(openseo_hosted),
        "site": settings.site_url,
    }


def render_markdown(report: dict | None = None) -> str:
    report = report or value_report()
    lines = ["# SEOForge vs Semrush", "", report["headline"], "",
             "| Module | Semrush Pro | SEOForge stack |", "|---|---|---|"]
    for row in report["coverage_matrix"]:
        lines.append(f"| {row['module']} | {row['semrush_pro']} | {row['seoforge_stack']} |")
    c = report["cost"]
    lines += [
        "",
        f"**Cost:** Semrush Pro ${c['semrush_pro_per_month']}/mo → SEOForge stack ${c['seoforge_stack_total']}/mo "
        f"(save ${c['savings_usd_month']}/mo, {c['savings_pct']}%)",
        "",
        "## AEO/GEO funnel (found → answered → cited → recommended → chosen)",
        "Semrush covers 'be found' partly; the remaining four stages are what SEOForge + OpenSEO",
        "AI-visibility tooling run end-to-end, category-wide (see aeo.py funnel_audit).",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(render_markdown())
