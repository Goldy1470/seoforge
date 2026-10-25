"""Autonomous execution engine: one URL in, complete execution out.

Chains every phase of the SEO + AEO + GEO + AAO pipeline:
  Phase 1 - DISCOVER: crawl + GSC pull + PAA expansion
  Phase 2 - DIAGNOSE: 4-engine score (SEO / AEO / GEO / AAO)
  Phase 3 - FIX: generate all code drops (fix_bundle.py)
  Phase 4 - DISTRIBUTE: batch-submit to 75 directories (auto_submit.py)
  Phase 5 - INDEX: push IndexNow + sitemap ping (indexing.py)
  Phase 6 - MONITOR: arm the daily monitoring loop (scheduler.py)

Returns a complete execution report with scores, fixes generated,
submissions attempted, and monitoring schedule.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from . import aeo, auto_submit, crawler, db, fix_bundle, geo, indexing, integrations, internal_links, onpage, paa, scheduler, technical
from .config import settings


def _safe(fn, default=None, label: str = "") -> tuple[Any, str | None]:
    """Execute fn(), return (result, None) or (default, error_message)."""
    try:
        return fn(), None
    except Exception as exc:
        err = f"{label or 'error'}: {type(exc).__name__} - {str(exc)[:200]}"
        return default, err


def discover(site_url: str | None = None) -> dict:
    """Phase 1: Run comprehensive site discovery (crawl)."""
    target = site_url or settings.site_url
    print(f"[autopilot] Phase 1/6: DISCOVER - crawling {target}...", flush=True)

    crawl_res, err = _safe(lambda: asyncio.run(crawler.crawl_site(target)), default=None, label="crawl")
    return {
        "site": target,
        "crawl_summary": crawl_res.get("summary") if isinstance(crawl_res, dict) else {},
        "error": err,
    }


def diagnose(site_url: str | None = None) -> dict:
    """Phase 2: Calculate 4-Engine scores (SEO, AEO, GEO, AAO)."""
    target = site_url or settings.site_url
    print(f"[autopilot] Phase 2/6: DIAGNOSE - scoring SEO, AEO, GEO, AAO...", flush=True)

    # 1. SEO score calculation
    onpage_res, _ = _safe(lambda: onpage.audit(limit_rows=100), default={})
    tech_res, _ = _safe(lambda: technical.report(), default={})
    links_res, _ = _safe(lambda: internal_links.build_report(), default={})

    pages_audited = onpage_res.get("pages_audited", 0) if isinstance(onpage_res, dict) else 0
    worst_count = len(onpage_res.get("worst_pages", [])) if isinstance(onpage_res, dict) else 0
    clean_ratio = max(0.0, 1.0 - (worst_count / max(pages_audited, 1)))
    seo_onpage_pts = round(clean_ratio * 40)

    tech_violations = sum(tech_res.get("summary", {}).values()) if isinstance(tech_res, dict) else 5
    seo_tech_pts = max(5, 35 - min(30, tech_violations * 3))

    orphans = len(links_res.get("orphans", [])) if isinstance(links_res, dict) else 0
    seo_links_pts = max(5, 25 - min(20, orphans * 2))

    seo_score = min(100, max(10, seo_onpage_pts + seo_tech_pts + seo_links_pts))

    # 2. AEO, GEO, AAO scores from funnel
    funnel_res, _ = _safe(lambda: aeo.funnel_audit(), default={})
    stages = funnel_res.get("stages", {}) if isinstance(funnel_res, dict) else {}

    def _norm(raw):
        val = float(raw or 0)
        return val / 100.0 if val > 100 else val

    found_score = _norm(stages.get("found", {}).get("score", 30))
    answered_score = _norm(stages.get("answered", {}).get("score", 25))
    cited_score = _norm(stages.get("cited", {}).get("score", 15))
    rec_score = _norm(stages.get("recommended", {}).get("score", 20))
    chosen_score = _norm(stages.get("chosen", {}).get("score", 35))

    aeo_score = min(100, max(0, round((found_score + answered_score) / 2)))
    geo_score = min(100, max(0, round((cited_score + rec_score) / 2)))
    aao_score = min(100, max(0, round(chosen_score)))

    overall_score = round(
        (seo_score * 0.30) + (aeo_score * 0.25) + (geo_score * 0.25) + (aao_score * 0.20)
    )

    issues = []
    if aeo_score < 50:
        issues.append("Low AEO score: Question coverage and FAQ schema are incomplete.")
    if geo_score < 40:
        issues.append("Low GEO score: Brand has low citation frequency and missing llms.txt.")
    if aao_score < 50:
        issues.append("Low AAO score: Assistive agents cannot find clear pricing or product schema.")
    if seo_score < 60:
        issues.append("SEO opportunities: Multiple pages have title/meta length or phrase mismatches.")

    return {
        "site": target,
        "overall_score": overall_score,
        "scores": {
            "seo": {"score": seo_score, "weight": "30%", "label": "Search Engine Optimization"},
            "aeo": {"score": aeo_score, "weight": "25%", "label": "Answer Engine Optimization (PAA/FAQs)"},
            "geo": {"score": geo_score, "weight": "25%", "label": "Generative Engine Optimization (AI citations)"},
            "aao": {"score": aao_score, "weight": "20%", "label": "Assistive Agent Optimization (Action/Checkout)"},
        },
        "critical_issues": issues,
        "funnel_stages": stages,
    }


def fix(site_url: str | None = None) -> dict:
    """Phase 3: Generate the complete downloadable fix bundle."""
    print(f"[autopilot] Phase 3/6: FIX - generating schemas, llms.txt, and code bundle...", flush=True)
    zip_path, err = _safe(lambda: fix_bundle.generate(), default=None, label="fix_bundle")
    return {
        "bundle_zip_path": zip_path,
        "status": "ready" if zip_path else "failed",
        "error": err,
    }


def distribute(skip_browser: bool = True) -> dict:
    """Phase 4: Submit to 75 directories."""
    print(f"[autopilot] Phase 4/6: DISTRIBUTE - backlink directory execution...", flush=True)
    if skip_browser:
        status_info = auto_submit.submit_status()
        return {
            "mode": "plan_only",
            "message": "Browser submission paused (skip_browser=True). Ready for batch execution.",
            "directory_overview": status_info,
        }

    batch_res = auto_submit.batch_submit(max_per_session=5, pause_seconds=10)
    return {
        "mode": "executed",
        "batch_results": batch_res,
    }


def index_push() -> dict:
    """Phase 5: Push updated URLs via IndexNow and ping sitemaps."""
    print(f"[autopilot] Phase 5/6: INDEX - notifying search engines (IndexNow + sitemap)...", flush=True)

    urls = []
    with db._conn() as conn:
        rows = conn.execute("SELECT url FROM pages WHERE status=200 LIMIT 20").fetchall()
        urls = [r["url"] for r in rows if r["url"]]

    idx_res, err1 = _safe(lambda: indexing.indexnow_submit(urls), default=None, label="indexnow")
    ping_res, err2 = _safe(lambda: indexing.ping_sitemap(), default=None, label="sitemap_ping")

    return {
        "urls_submitted": len(urls),
        "indexnow": idx_res,
        "sitemap_ping": ping_res,
        "errors": [e for e in (err1, err2) if e],
    }


def arm_monitoring() -> dict:
    """Phase 6: Ensure scheduler jobs are armed for 24/7 background monitoring."""
    print(f"[autopilot] Phase 6/6: MONITOR - arming daily scheduler & alerts...", flush=True)
    scheduler.ensure_defaults()
    jobs = scheduler.list_schedule()
    return {
        "active_jobs": len(jobs),
        "schedule": jobs,
        "slack_alert_ready": bool(scheduler.notify.os.getenv("SLACK_WEBHOOK_URL")),
    }


def run(
    site_url: str | None = None,
    skip_browser: bool = True,
    phases: list[str] | None = None,
) -> dict:
    """Master entry point: executes the complete end-to-end autopilot pipeline."""
    target = site_url or settings.site_url
    allowed_phases = {"discover", "diagnose", "fix", "distribute", "index", "monitor"}
    active_phases = [p for p in (phases or list(allowed_phases)) if p in allowed_phases]

    t0 = time.time()
    timing: dict[str, float] = {}
    report: dict[str, Any] = {
        "site": target,
        "started_at": db.now(),
        "phases_requested": active_phases,
    }

    print(f"==================================================================", flush=True)
    print(f"  SEOFORGE AUTONOMOUS EXECUTION ENGINE RUNNING FOR: {target}", flush=True)
    print(f"==================================================================", flush=True)

    if "discover" in active_phases:
        st = time.time()
        report["discover"] = discover(target)
        timing["discover"] = round(time.time() - st, 2)

    if "diagnose" in active_phases:
        st = time.time()
        report["diagnose"] = diagnose(target)
        timing["diagnose"] = round(time.time() - st, 2)

    if "fix" in active_phases:
        st = time.time()
        report["fix"] = fix(target)
        timing["fix"] = round(time.time() - st, 2)

    if "distribute" in active_phases:
        st = time.time()
        report["distribute"] = distribute(skip_browser=skip_browser)
        timing["distribute"] = round(time.time() - st, 2)

    if "index" in active_phases:
        st = time.time()
        report["index"] = index_push()
        timing["index"] = round(time.time() - st, 2)

    if "monitor" in active_phases:
        st = time.time()
        report["monitor"] = arm_monitoring()
        timing["monitor"] = round(time.time() - st, 2)

    total_time = round(time.time() - t0, 2)
    report["total_time_seconds"] = total_time
    report["timing"] = timing

    report_id = db.save_report("autopilot_execution", report)
    report["report_id"] = report_id

    print(f"==================================================================", flush=True)
    overall = report.get("diagnose", {}).get("overall_score", "N/A")
    print(f"  AUTOPILOT COMPLETE in {total_time}s | Visibility Score: {overall}/100", flush=True)
    print(f"  Report saved: #{report_id} | Fix bundle: {report.get('fix', {}).get('bundle_zip_path')}", flush=True)
    print(f"==================================================================", flush=True)

    return report
