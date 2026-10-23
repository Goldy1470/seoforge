from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from . import backlinks, crawler, db, internal_links, integrations, orchestrator, paa
from .config import settings

app = FastAPI(title="SEOForge", version="0.2.0")


@app.get("/", response_class=HTMLResponse)
def index():
    with db._conn() as conn:
        jobs = conn.execute("SELECT id,kind,status,started_at FROM jobs ORDER BY started_at DESC LIMIT 15").fetchall()
        paa_n = conn.execute("SELECT COUNT(*) c FROM paa_questions").fetchone()["c"]
        dirs = conn.execute("SELECT status, COUNT(*) c FROM directories GROUP BY status").fetchall()
        pages_n = conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"]
        reports = conn.execute("SELECT id,kind,created_at FROM reports ORDER BY id DESC LIMIT 10").fetchall()
        opps = conn.execute(
            "SELECT type,target,impact,effort,score FROM opportunities WHERE status='open' ORDER BY score DESC LIMIT 10"
        ).fetchall()
        sched = conn.execute("SELECT name,kind,interval_hours,last_status FROM schedule WHERE enabled=1 ORDER BY interval_hours").fetchall()
        import json as _json

        value_row = conn.execute(
            "SELECT payload FROM reports WHERE kind='value_report' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        value_summary = _json.loads(value_row["payload"])["cost"] if value_row else None
        pref_row = conn.execute(
            "SELECT payload FROM reports WHERE kind='preferred_sources' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        pref = _json.loads(pref_row["payload"]) if pref_row else None
        funnel_rows = conn.execute(
            "SELECT payload FROM reports WHERE kind='aeo_funnel' ORDER BY id DESC LIMIT 5"
        ).fetchall()
        funnel_els = "".join(
            f"<li>{_json.loads(f['payload'])['name']}: overall {_json.loads(f['payload'])['overall_score']}/100 "
            f"({', '.join(f'{k}:{_json.loads(f['payload'])['stages'][k]['score']}' for k in ('found','answered','cited','recommended','chosen'))})</li>"
            for f in funnel_rows
        )
    rows = "".join(
        f"<tr><td>{j['id']}</td><td>{j['kind']}</td><td>{j['status']}</td><td>{j['started_at']}</td></tr>"
        for j in jobs
    )
    dir_els = " · ".join(f"{d['status']}: {d['c']}" for d in dirs) or "no directory data"
    report_els = "".join(f"<li><a href='/reports/{r['id']}'>{r['id']} {r['kind']} {r['created_at']}</a></li>" for r in reports)
    opp_els = "".join(
        f"<tr><td>{o['type']}</td><td>{o['target'][:70]}</td><td>{o['impact']}</td><td>{o['effort']}</td><td><b>{o['score']}</b></td></tr>"
        for o in opps
    ) or "<tr><td colspan=5>no opportunities - press Opps-refresh or configure GSC/DataForSEO</td></tr>"
    sched_els = " · ".join(f"{s['name']}({s['kind']}/{s['interval_hours']}h:{s['last_status'] or 'never'})" for s in sched)
    value_els = (
        f"Semrush Pro: ${value_summary['semrush_pro_per_month']}/mo → SEOForge stack: ${value_summary['seoforge_stack_total']}/mo "
        f"(save ${value_summary['savings_usd_month']}/mo)"
        if value_summary
        else "run the <em>save-value</em> button once"
    )
    if pref:
        e = pref["eligible"]
        pref_stat = (
            f"fresh content: {'yes' if e['publishes_fresh_content'] else 'NO - weekly cadence needed'} · "
            f"button on site: {'yes' if e['button_on_site'] else 'no'}"
        )
        pref_els = "".join(f"<li>{c}</li>" for c in pref["checklist"])
    else:
        pref_stat = "not audited yet"
        pref_els = "<li>run <em>Preferred Sources</em> — Google's newest free lever (Top Stories + AI Overviews/AI Mode badge, 2× CTR)</li>"
    return f"""<html><head><meta charset=utf-8><title>SEOForge</title>
<style>body{{font-family:ui-monospace;font-size:14px;max-width:900px;margin:30px auto}}
table{{border-collapse:collapse}}td{{border:1px solid #ccc;padding:4px 10px}}
button{{padding:6px 14px;margin:4px 2px}}</style></head><body>
<h1>SEOForge</h1>
<p>{settings.site_url} — pages: {pages_n} · PAA questions: {paa_n}<br>{dir_els}</p>
<p>
<form method=post action=/run/crawl style=display:inline><button>Crawl</button></form>
<form method=post action=/run/facets style=display:inline><button>Facets</button></form>
<form method=post action=/run/links style=display:inline><button>Internal links</button></form>
<form method=post action=/run/content style=display:inline><button>PAA plan</button></form>
<form method=post action=/run/audit style=display:inline><button>Full audit</button></form>
<form method=post action=/run/opps style=display:inline><button>Opps-refresh</button></form>
<form method=post action=/run/delta style=display:inline><button>GSC delta</button></form>
<form method=post action=/run/value style=display:inline><button>Value report</button></form>
<form method=post action=/run/preferred style=display:inline><button>Preferred Sources</button></form>
<form method=post action=/run/research style=display:inline><button>Keyword research</button></form>
</p>
<p><b>Cost:</b> {value_els}</p>
<p><b>Preferred Sources</b> (Google's newest free lever — Top Stories + AI Overviews/AI Mode badge, 2× CTR): {pref_stat}</p>
<ul>{pref_els}</ul>
<p><b>AEO funnel per category</b> (found → answered → cited → recommended → chosen):</p>
<ul>{funnel_els or '<li>run <code>aeo &lt;category&gt;</code> or the MCP tool aeo_funnel</li>'}</ul>
<h2>Work queue (impact/effort scored)</h2>
<table><tr><th>type</th><th>target</th><th>impact</th><th>effort</th><th>score</th></tr>{opp_els}</table>
<p><b>Schedule:</b> {sched_els}</p>
<h2>Jobs</h2><table><tr><th>id</th><th>kind</th><th>status</th><th>started</th></tr>{rows}</table>
<h2>Reports</h2><ul>{report_els}</ul>
<h2>Exports</h2>
<ul><li><a href=/export/paa>paa_build_list.csv</a></li>
<li><a href=/export/tracker>backlink_tracker.csv</a></li>
<li><a href=/export/feed>merchant_center.xml</a></li></ul>
</body></html>"""


@app.post("/run/{kind}")
def run_job(kind: str):
    fns = {
        "crawl": lambda: crawler.crawl_site(settings.site_url),
        "facets": lambda: paa_none(),
        "links": lambda: internal_links.build_report(),
        "content": lambda: orchestrator.content_plan([settings.brand_name], 2),
        "audit": lambda: orchestrator.seo_audit(),
        "opps": lambda: integrations.recompute(persist=True),
        "delta": lambda: _scheduler_delta(),
        "value": lambda: _value_save(),
        "preferred": lambda: _preferred_save(),
        "research": lambda: _research_save(),
    }
    fn = fns.get(kind)
    if not fn:
        return JSONResponse({"error": "unknown"}, 400)
    try:
        import asyncio

        if kind == "crawl":
            result = asyncio.run(fn())
        elif kind == "facets":
            from . import facets

            result = facets.analyze()
        else:
            result = fn()
        return {"ok": True, "result": _clip(result)}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


def paa_none():
    from . import facets

    return facets.analyze()


def _scheduler_delta():
    from . import scheduler

    try:
        return scheduler.nightly_delta()
    except SystemExit as exc:
        return {"skipped": str(exc)}
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _preferred_save():
    from . import preferred

    return preferred.audit()


def _research_save():
    from . import research

    return research.workflow()


def _value_save():
    from . import integrations, value

    report = value.value_report()
    rid = db.save_report("value_report", report)
    report["report_id"] = rid
    integrations.recompute(persist=True)
    return report


def _clip(o, n=4000):
    s = json.dumps(o, default=str)
    return o if len(s) < n else {"preview": s[:n]}


@app.get("/reports/{rid}")
def report(rid: int):
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    return JSONResponse(json.loads(row["payload"])) if row else JSONResponse({"error": "not found"}, 404)


@app.get("/export/{kind}")
def exports(kind: str):
    ex = Path(settings.exports_dir)
    if kind == "paa":
        paa.export_paa_csv()
        return FileResponse(ex / "paa_build_list.csv", filename="paa_build_list.csv")
    if kind == "tracker":
        backlinks.export_csv()
        return FileResponse(ex / "backlink_tracker.csv", filename="backlink_tracker.csv")
    if kind == "feed":
        from . import merchant

        merchant.build_feed()
        return FileResponse(ex / "merchant_center.xml", filename="merchant_center.xml")
    return JSONResponse({"error": "unknown"}, 404)


def serve(host="127.0.0.1", port=8808):
    import uvicorn

    uvicorn.run(app, host=host, port=port)
