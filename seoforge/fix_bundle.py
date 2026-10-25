"""Fix bundle generator: packages all actionable fixes into a ZIP.

Generates ready-to-deploy files from the current crawl + audit state:
  - robots_suggested.txt  - corrected AI-bot directives
  - llms.txt + llms-full.txt  - AI-engine discovery files
  - schemas/faq_*.json  - FAQPage JSON-LD per question cluster
  - schemas/organization.json  - Organization + sameAs
  - preferred_sources_button.html  - Google Preferred Sources embed
  - titles_metas.csv  - rewritten titles + metas for striking-distance pages
  - content_briefs/  - PAA page scaffolds (markdown)
  - REPORT.md  - human-readable summary of all fixes
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from . import aeo, ai_bots, db, geo, onpage, paa, preferred, struct
from .config import ROOT, settings


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def robots_suggested() -> str:
    """Generate a clean robots.txt snippet explicitly welcoming AI search and user bots."""
    bots = ai_bots.all_bots()
    search_user_bots = [b for b in bots if b.type in ("search", "user")]
    training_bots = [b for b in bots if b.type == "training"]

    lines = [
        "# ===========================================================================",
        "# SEOForge Suggested robots.txt for AI Search & Agent Readiness",
        f"# Generated: {dt.date.today().isoformat()} for {settings.site_url}",
        "# ===========================================================================",
        "",
        "# Standard Search Crawlers",
        "User-agent: Googlebot",
        "Allow: /",
        "",
        "User-agent: Bingbot",
        "Allow: /",
        "",
        "# ---------------------------------------------------------------------------",
        "# AI Search & Assistive Agents (ChatGPT, Claude, Perplexity, etc.)",
        "# ALLOW these so answer engines can read, cite, and recommend your site.",
        "# ---------------------------------------------------------------------------",
    ]

    for b in search_user_bots:
        lines.append(f"User-agent: {b.userAgent}")
        lines.append("Allow: /")
        lines.append("")

    if training_bots:
        lines.append("# ---------------------------------------------------------------------------")
        lines.append("# AI Model Training Scrapers (Disallow if you do not want data scraping)")
        lines.append("# ---------------------------------------------------------------------------")
        for b in training_bots:
            lines.append(f"User-agent: {b.userAgent}")
            lines.append("Disallow: /")
            lines.append("")

    sitemap_url = f"{settings.site_url.rstrip('/')}/sitemap.xml"
    lines.append(f"Sitemap: {sitemap_url}")
    return "\n".join(lines) + "\n"


def schemas_bundle() -> list[tuple[str, str]]:
    """Generate JSON-LD schemas (Organization and PAA cluster FAQs)."""
    items: list[tuple[str, str]] = []

    # 1. Organization schema
    org_schema = _safe(lambda: struct.organization())
    if org_schema:
        items.append(("schemas/organization.json", struct.render(org_schema)))

    # 2. FAQ schemas from PAA database
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT cluster, question FROM paa_questions WHERE cluster IS NOT NULL ORDER BY cluster, id"
        ).fetchall()

    clusters: dict[str, list[dict]] = {}
    for r in rows:
        c = (r["cluster"] or "").strip()
        if c:
            clusters.setdefault(c, []).append({"question": r["question"], "answer": "Answer will be populated from page copy."})

    for c_name, q_list in list(clusters.items())[:15]:
        if len(q_list) >= 2:
            faq_schema = _safe(lambda: struct.faq(q_list[:10]))
            if faq_schema:
                slug = paa.slugify(c_name)[:40] or "cluster"
                items.append((f"schemas/faq_{slug}.json", struct.render(faq_schema)))

    return items


def titles_metas_csv() -> str:
    """Generate CSV of pages that have title or meta warnings with fix hints."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["url", "title", "meta_description", "warnings", "recommended_fix"])

    audit_res = _safe(lambda: onpage.audit(limit_rows=150), default={})
    findings = audit_res.get("findings", []) if isinstance(audit_res, dict) else []

    with db._conn() as conn:
        pages_map = {
            r["url"]: {"title": r["title"] or "", "meta": r["meta_desc"] or ""}
            for r in conn.execute("SELECT url, title, meta_desc FROM pages").fetchall()
        }

    for item in findings:
        url = item.get("url", "")
        f_list = item.get("findings", [])
        warns = [f.get("detail", "") for f in f_list if f.get("level") == "warn" and f.get("rule") in ("1-title", "2-meta")]
        if not warns:
            continue
        p = pages_map.get(url, {})
        fix_pack = _safe(lambda: onpage.fix_pack(p.get("title", "") or settings.brand_name))
        rec_title = ""
        rec_meta = ""
        if isinstance(fix_pack, dict):
            for b in fix_pack.get("blocks", []):
                if b.get("rule") == "1-title":
                    rec_title = b.get("deliver", "")
                elif b.get("rule") == "2-meta":
                    rec_meta = b.get("deliver", "")

        fix_str = f"Title: {rec_title} | Meta: {rec_meta}" if rec_title else "; ".join(warns)
        writer.writerow([url, p.get("title", ""), p.get("meta", ""), " | ".join(warns), fix_str])

    return out.getvalue()


def preferred_button_html() -> str:
    """HTML helper page containing Google Preferred Sources embeds in light and dark mode."""
    res = preferred.embed_snippet(theme="light")
    res_dark = preferred.embed_snippet(theme="dark")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Google Preferred Sources Embed - {settings.brand_name}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; line-height: 1.6; }}
    pre {{ background: #f4f4f5; padding: 16px; border-radius: 8px; overflow-x: auto; }}
    .card {{ border: 1px solid #e4e4e7; border-radius: 8px; padding: 20px; margin-bottom: 24px; }}
    .dark-preview {{ background: #18181b; color: #fff; padding: 20px; border-radius: 8px; margin-top: 10px; }}
  </style>
</head>
<body>
  <h1>Google Preferred Sources Button Code</h1>
  <p>Add this button to your homepage or article footers. When readers click it, your publication is added to their Google Preferred Sources (displaying badges in Top Stories, AI Overviews, and AI Mode).</p>

  <div class="card">
    <h2>1. Light Theme Embed (Standard)</h2>
    <pre><code>{res.get('standard', '')}</code></pre>
  </div>

  <div class="card">
    <h2>2. Dark Theme Embed</h2>
    <pre><code>{res_dark.get('standard', '')}</code></pre>
  </div>

  <div class="card">
    <h2>3. Direct Script Tags</h2>
    <p>Include Google's Publisher JS in your head or footer:</p>
    <pre><code>&lt;script async src="https://news.google.com/swg/js/v1/publisher.js"&gt;&lt;/script&gt;</code></pre>
  </div>
</body>
</html>
"""


def generate_report_md(stats: dict) -> str:
    """Markdown manifest explaining the fix bundle."""
    return f"""# SEOForge Fix Bundle Manifest

- **Target Site**: `{settings.site_url}`
- **Brand**: `{settings.brand_name}`
- **Generated**: `{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}`

---

## Bundle Contents

| File | Purpose | Action Required |
|---|---|---|
| `robots_suggested.txt` | Directives allowing AI search bots (GPTBot, ClaudeBot, PerplexityBot) | Compare with your `/robots.txt` and merge |
| `llms.txt` | Standard AI engine markdown site summary | Deploy to `{settings.site_url}/llms.txt` |
| `llms-full.txt` | Full AI agent corpus index | Deploy to `{settings.site_url}/llms-full.txt` |
| `preferred_sources_button.html` | Google Preferred Sources button embed code | Copy snippet into article footer / homepage |
| `titles_metas.csv` | Scored page title and description fixes | Apply suggested titles & meta descriptions |
| `schemas/organization.json` | Organization schema with sameAs links | Inject into homepage `<head>` |
| `schemas/faq_*.json` | FAQPage schemas for question clusters | Inject into corresponding guide pages |
| `content_briefs/` | Scaffolds for PAA question pages | Build out pages under `/guides/` or `/faq/` |

---

## Summary Statistics

- **Schemas Generated**: {stats.get('schemas_count', 0)}
- **Titles & Metas to Optimize**: {stats.get('titles_count', 0)}
- **Content Briefs Generated**: {stats.get('briefs_count', 0)}
- **AI Bots Whitelisted**: {stats.get('bots_allowed', 0)}

---
*Generated by SEOForge Autonomous Execution Engine.*
"""


def generate(output_dir: str | None = None) -> str:
    """Creates a complete fix bundle ZIP file and returns its path."""
    settings.ensure_dirs()
    target_dir = Path(output_dir or settings.exports_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    site_slug = urlsplit(settings.site_url).netloc.replace(".", "_").replace(":", "_")
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = target_dir / f"fix_bundle_{site_slug}_{timestamp}.zip"

    stats = {
        "schemas_count": 0,
        "titles_count": 0,
        "briefs_count": 0,
        "bots_allowed": len([b for b in ai_bots.all_bots() if b.type in ("search", "user")]),
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. robots.txt
        zf.writestr("robots_suggested.txt", robots_suggested())

        # 2. llms.txt & llms-full.txt
        llms_txt = _safe(lambda: geo.llms_txt_from_crawl(), default=f"# {settings.brand_name}\nSite summary for AI agents.\n")
        zf.writestr("llms.txt", llms_txt)

        llms_full = _safe(lambda: aeo.llms_full_txt_from_crawl(), default=f"# {settings.brand_name} Full Corpus\n")
        zf.writestr("llms-full.txt", llms_full)

        # 3. Schemas
        schemas = schemas_bundle()
        stats["schemas_count"] = len(schemas)
        for rel_path, content in schemas:
            zf.writestr(rel_path, content)

        # 4. Preferred Sources button
        zf.writestr("preferred_sources_button.html", preferred_button_html())

        # 5. Titles & Metas CSV
        csv_content = titles_metas_csv()
        stats["titles_count"] = max(0, len(csv_content.strip().splitlines()) - 1)
        zf.writestr("titles_metas.csv", csv_content)

        # 6. Content briefs from PAA
        scaffolds = _safe(lambda: paa.page_scaffold(min_questions=2, limit=10), default={})
        if isinstance(scaffolds, dict):
            files = scaffolds.get("files", {})
            stats["briefs_count"] = len(files)
            for path_key, info in files.items():
                md_path = info.get("markdown")
                if md_path and Path(md_path).exists():
                    name = Path(md_path).name
                    zf.writestr(f"content_briefs/{name}", Path(md_path).read_text(encoding="utf-8"))

        # 7. Summary report
        zf.writestr("REPORT.md", generate_report_md(stats))

    return str(zip_path)
