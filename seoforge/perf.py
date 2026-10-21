from __future__ import annotations

import asyncio
import os

import httpx

from . import db

PSI = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
AUDITS = (
    "largest-contentful-paint",
    "first-contentful-paint",
    "total-blocking-time",
    "cumulative-layout-shift",
    "speed-index",
    "render-blocking-resources",
)


async def _psi(url: str, strategy: str) -> dict:
    params: list[tuple[str, str]] = [
        ("url", url),
        ("strategy", strategy),
        ("category", "performance"),
    ]
    key = os.getenv("PAGESPEED_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if key:
        params.append(("key", key))
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(PSI, params=params)
        r.raise_for_status()
        data = r.json()
    lr = data.get("lighthouseResult") or {}
    audits = lr.get("audits") or {}
    perf = (lr.get("categories") or {}).get("performance", {}).get("score")
    crux = ((data.get("loadingExperience") or {}).get("metrics") or {})
    out = {
        "url": url,
        "strategy": strategy,
        "psi_performance_score": int(perf * 100) if perf is not None else None,
        "field_data_overall": (data.get("loadingExperience") or {}).get("overall_category"),
        "metrics": {},
        "field": {},
    }
    for a in AUDITS:
        node = audits.get(a) or {}
        if node:
            out["metrics"][a] = node.get("displayValue") or node.get("numericValue")
    for k, m in crux.items():
        if isinstance(m, dict) and m.get("percentile"):
            out["field"][k] = {"p75": m["percentile"], "category": m.get("category")}
    rblocking = audits.get("render-blocking-resources") or {}
    items = (rblocking.get("details") or {}).get("items") or []
    if items:
        out["render_blocking"] = items[:5]
    return out


def cwv(url: str, desktop: bool = False) -> dict:
    report = {"url": url}
    strategies = ["mobile"] + (["desktop"] if desktop else [])
    for strat in strategies:
        try:
            report[strat] = asyncio.run(_psi(url, strat))
        except Exception as exc:
            report[strat] = {"error": str(exc)[:200]}
    mobile = report.get("mobile", {})
    if not mobile.get("error"):
        m = mobile.get("metrics") or {}
        lcp = m.get("largest-contentful-paint")
        report["ai_hint"] = _hint(mobile.get("psi_performance_score"), lcp, mobile.get("field_data_overall"))
    else:
        report["ai_hint"] = (
            "PageSpeed API failed - set PAGESPEED_API_KEY env var for a higher quota, or retry."
        )
    db.save_report("cwv", report)
    return report


def _hint(score: int | None, lcp, overall: str | None) -> str:
    lines = []
    if score is not None:
        lines.append(f"PSI mobile score {score}/100")
        if score < 50:
            lines.append("under 50: fix LCP + render-blocking JS/CSS before content work")
        elif score < 80:
            lines.append("50-79: good enough to not block rankings, still worth quick wins")
        else:
            lines.append("core vitals healthy")
    if lcp:
        lines.append(f"LCP {lcp} (target <= 2.5s)")
    if overall:
        lines.append(f"CrUX: {overall}")
    return " | ".join(lines)
