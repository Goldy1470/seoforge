from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response

import re

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
        auto_row = conn.execute(
            "SELECT payload FROM reports WHERE kind='autopilot_execution' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        auto_data = _json.loads(auto_row["payload"]) if auto_row else None
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

    if auto_data and "diagnose" in auto_data:
        diag = auto_data["diagnose"]
        scores = diag.get("scores", {})
        overall = diag.get("overall_score", 0)
        auto_els = f"""
<div style="background:#0f172a;color:#f8fafc;padding:18px 20px;border-radius:10px;margin:18px 0">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">
    <div>
      <span style="font-size:22px;font-weight:bold;color:#38bdf8">⚡ Visibility Score: {overall}/100</span>
      <div style="margin-top:6px;color:#94a3b8">
        SEO: <b>{scores.get('seo', {}).get('score', 'N/A')}</b> ·
        AEO: <b>{scores.get('aeo', {}).get('score', 'N/A')}</b> ·
        GEO: <b>{scores.get('geo', {}).get('score', 'N/A')}</b> ·
        AAO: <b>{scores.get('aao', {}).get('score', 'N/A')}</b>
      </div>
    </div>
    <div style="margin-top:10px">
      <form method=post action=/run/autopilot style=display:inline><button style="background:#38bdf8;color:#0f172a;font-weight:bold;padding:8px 16px;border:none;border-radius:6px;cursor:pointer">🚀 Re-Run Autopilot</button></form>
      <a href=/export/bundle style="display:inline-block;background:#22c55e;color:#fff;font-weight:bold;padding:8px 16px;border-radius:6px;text-decoration:none;margin-left:6px">📦 Download Fix ZIP</a>
    </div>
  </div>
</div>"""
    else:
        auto_els = """
<div style="background:#0f172a;color:#f8fafc;padding:18px 20px;border-radius:10px;margin:18px 0">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">
    <div>
      <span style="font-size:20px;font-weight:bold;color:#38bdf8">⚡ Autonomous Execution Engine</span>
      <div style="margin-top:4px;color:#94a3b8">One-click SEO + AEO + GEO + AAO diagnosis, fix bundle generation, directory submission, and search push.</div>
    </div>
    <div style="margin-top:10px">
      <form method=post action=/run/autopilot style=display:inline><button style="background:#38bdf8;color:#0f172a;font-weight:bold;padding:8px 16px;border:none;border-radius:6px;cursor:pointer">🚀 Run Complete Autopilot</button></form>
      <a href=/export/bundle style="display:inline-block;background:#22c55e;color:#fff;font-weight:bold;padding:8px 16px;border-radius:6px;text-decoration:none;margin-left:6px">📦 Generate & Download Fix ZIP</a>
    </div>
  </div>
</div>"""

    return f"""<html><head><meta charset=utf-8><title>SEOForge</title>
<style>body{{font-family:ui-monospace;font-size:14px;max-width:900px;margin:30px auto}}
table{{border-collapse:collapse}}td{{border:1px solid #ccc;padding:4px 10px}}
button{{padding:6px 14px;margin:4px 2px}}</style></head><body>
<h1>SEOForge</h1>
<p>{settings.site_url} — pages: {pages_n} · PAA questions: {paa_n}<br>{dir_els}</p>
{auto_els}
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
<form method=post action=/run/onpage style=display:inline><button>On-page audit</button></form>
<form method=post action=/run/technical style=display:inline><button>Technical audit</button></form>
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
<ul><li><a href=/export/bundle><b>fix_bundle.zip (Complete AI & SEO Fix Package)</b></a></li>
<li><a href=/export/paa>paa_build_list.csv</a></li>
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
        "onpage": lambda: _onpage_save(),
        "technical": lambda: _technical_save(),
        "autopilot": lambda: _autopilot_run(),
        "bundle": lambda: _bundle_gen(),
        "submit": lambda: _submit_batch(),
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


def _onpage_save():
    from . import onpage, db

    rep = onpage.audit()
    rid = db.save_report("onpage_audit", rep)
    rep["report_id"] = rid
    integrations.recompute(persist=True)
    return rep


def _technical_save():
    from . import technical

    return technical.report()


def _value_save():
    from . import integrations, value

    report = value.value_report()
    rid = db.save_report("value_report", report)
    report["report_id"] = rid
    integrations.recompute(persist=True)
    return report


def _autopilot_run():
    from . import autopilot

    return autopilot.run(skip_browser=True)


def _bundle_gen():
    from . import fix_bundle

    return {"bundle_zip": fix_bundle.generate()}


def _submit_batch():
    from . import auto_submit

    return auto_submit.batch_submit(max_per_session=5)


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
    if kind == "bundle":
        from . import fix_bundle

        zip_p = fix_bundle.generate()
        return FileResponse(zip_p, filename=Path(zip_p).name)
    return JSONResponse({"error": "unknown"}, 404)


@app.get("/scan", response_class=HTMLResponse)
def scan_page():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Free AI Visibility Audit — SEOForge</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #090d16; color: #f8fafc; line-height: 1.5; padding: 40px 20px; }
    .container { max-width: 680px; margin: 0 auto; }
    .badge { display: inline-block; background: #1e293b; color: #38bdf8; border: 1px solid #38bdf844; padding: 4px 12px; border-radius: 999px; font-size: 13px; font-weight: 600; margin-bottom: 20px; }
    h1 { font-size: 36px; line-height: 1.2; font-weight: 800; margin-bottom: 16px; letter-spacing: -0.5px; }
    h1 span { color: #38bdf8; }
    p.sub { font-size: 18px; color: #94a3b8; margin-bottom: 32px; }
    .card { background: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 28px; }
    label { display: block; font-size: 14px; font-weight: 600; color: #cbd5e1; margin-bottom: 6px; }
    input[type=text], input[type=email] { width: 100%; padding: 12px 14px; background: #1e293b; border: 1px solid #334155; border-radius: 8px; color: #fff; font-size: 15px; margin-bottom: 18px; outline: none; }
    input:focus { border-color: #38bdf8; }
    button.btn { width: 100%; background: #38bdf8; color: #090d16; font-size: 16px; font-weight: 700; padding: 14px; border: none; border-radius: 8px; cursor: pointer; transition: background 0.2s; }
    button.btn:hover { background: #7dd3fc; }
    .features { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; margin-top: 28px; }
    .feat { background: #1e293b44; border: 1px solid #1e293b; padding: 14px; border-radius: 8px; font-size: 13px; color: #cbd5e1; }
    .feat b { color: #38bdf8; display: block; margin-bottom: 4px; font-size: 14px; }
  </style>
</head>
<body>
  <div class="container">
    <div class="badge">AI Visibility Engine</div>
    <h1>Is Your Brand Invisible to <span>ChatGPT & Google AI</span>?</h1>
    <p class="sub">Traditional SEO tools only track blue links. We audit your site across SEO, AEO, GEO, and AAO in 15 seconds.</p>
    
    <div class="card">
      <form method="POST" action="/scan">
        <label for="url">Your Website URL</label>
        <input type="text" id="url" name="url" placeholder="https://yourbrand.com" required>
        
        <label for="email">Work Email (to receive fix report)</label>
        <input type="email" id="email" name="email" placeholder="you@company.com" required>
        
        <button type="submit" class="btn">🚀 Run Free 4-Engine Visibility Audit</button>
      </form>
    </div>

    <div class="features">
      <div class="feat"><b>1. SEO Score</b>Title, meta, speed, and 3-click link depth.</div>
      <div class="feat"><b>2. AEO Score</b>People Also Ask demand & FAQPage schema.</div>
      <div class="feat"><b>3. GEO Score</b>ChatGPT, Claude, and Perplexity citation audit.</div>
      <div class="feat"><b>4. AAO Score</b>AI shopping bot extraction & price visibility.</div>
    </div>
  </div>
</body>
</html>"""


@app.post("/scan", response_class=HTMLResponse)
def run_scan(url: str = Form(...), email: str = Form(...)):
    from . import autopilot

    target = url if url.startswith(("http://", "https://")) else f"https://{url}"
    diag = autopilot.diagnose(target)
    scores = diag.get("scores", {})
    overall = diag.get("overall_score", 42)
    issues = diag.get("critical_issues", [])

    # Save lead
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO leads(email, site_url, score, details, created_at) VALUES (?,?,?,?,?)",
            (email, target, overall, json.dumps(diag), db.now()),
        )

    issue_items = "".join(f"<li style='margin-bottom:8px'>⚠️ {iss}</li>" for iss in issues) or "<li>No critical violations detected.</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Your Visibility Score: {overall}/100 — SEOForge</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #090d16; color: #f8fafc; line-height: 1.5; padding: 40px 20px; }}
    .container {{ max-width: 680px; margin: 0 auto; }}
    .score-box {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 32px; text-align: center; margin-bottom: 24px; }}
    .score-circle {{ font-size: 56px; font-weight: 800; color: #38bdf8; margin: 10px 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 24px 0; text-align: left; }}
    .bar-item {{ background: #1e293b; padding: 14px; border-radius: 8px; }}
    .bar-item b {{ display: block; font-size: 14px; margin-bottom: 6px; }}
    .issues {{ background: #1e1b4b; border: 1px solid #3730a3; padding: 20px; border-radius: 8px; margin-bottom: 24px; }}
    .cta-btn {{ display: block; width: 100%; background: #22c55e; color: #fff; text-align: center; font-size: 17px; font-weight: 700; padding: 16px; border-radius: 8px; text-decoration: none; }}
    .cta-btn:hover {{ background: #16a34a; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="score-box">
      <p style="color:#94a3b8">Visibility Audit for</p>
      <h2 style="font-size:24px;margin-bottom:8px">{target}</h2>
      <div class="score-circle">{overall}<span style="font-size:24px;color:#64748b">/100</span></div>
      <p style="color:#cbd5e1">Diagnosed across traditional search, generative engines, and AI shopping assistants.</p>
      
      <div class="grid">
        <div class="bar-item"><b>SEO: {scores.get('seo', {}).get('score', 0)}/100</b>Search Engine Optimization</div>
        <div class="bar-item"><b>AEO: {scores.get('aeo', {}).get('score', 0)}/100</b>Answer Engine (PAA)</div>
        <div class="bar-item"><b>GEO: {scores.get('geo', {}).get('score', 0)}/100</b>Generative AI Citations</div>
        <div class="bar-item"><b>AAO: {scores.get('aao', {}).get('score', 0)}/100</b>Assistive Shopping Agents</div>
      </div>
    </div>

    <div class="issues">
      <h3 style="color:#a5b4fc;margin-bottom:12px">🚨 Critical AI Visibility Roadblocks:</h3>
      <ul style="padding-left:20px;color:#e0e7ff">{issue_items}</ul>
    </div>

    <a href="/export/bundle" class="cta-btn">📦 Download Your Complete Fix Bundle (ZIP)</a>
    <p style="text-align:center;font-size:13px;color:#64748b;margin-top:10px">Includes llms.txt, schema JSON-LD, robots directives, and Preferred Sources badge.</p>
  </div>
</body>
</html>"""


@app.get("/leads")
def list_leads():
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 50").fetchall()
    return JSONResponse([dict(r) for r in rows])


# ------------------------------------------------------------ bot report


@app.post("/bots/ingest")
async def bots_ingest(request: Request):
    """POST /bots/ingest — AI-bot requests from the site's own edge. Public,
    outside the dashboard: the sender is the site's Cloudflare or Vercel,
    authenticated by its connection secret (Authorization: Bearer) before its
    body is read, so a caller without one can't make the server take in
    20 MB. The body is read by the connection's kind (bot_log_formats): a
    Worker's JSON batch, a Logpush gzipped NDJSON file, or a Vercel drain's
    log entries."""
    import asyncio
    import zlib

    from . import bot_ingest, bot_log_formats

    auth = request.headers.get("authorization") or ""
    secret = re.sub(r"^Bearer\s+", "", auth, flags=re.I)
    connection = bot_ingest.connection_for_secret(secret)
    if not connection:
        return JSONResponse({"error": "unknown_connection"}, status_code=401)
    body = await request.body()
    hash_ = None
    try:
        # Answered 200 so the sender stops resending what was read once.
        if connection["kind"] != "cloudflare_worker":
            hash_ = bot_ingest.log_file_hash(body)
            with db._conn() as conn:
                if bot_ingest.file_was_read(conn, connection["id"], hash_):
                    return JSONResponse(
                        {"received": 0, "verified": 0, "stored": 0, "duplicate": True}
                    )
        try:
            parsed = bot_log_formats.read_sender_body(connection["kind"], body)
        except bot_log_formats.LogTooLargeError as exc:
            return JSONResponse(
                {"error": "too_large", "message": str(exc)}, status_code=413
            )
        except (ValueError, zlib.error, UnicodeError):
            return JSONResponse({"error": "unreadable_body"}, status_code=400)
        # A capped or locked file is answered 200 too: a retry changes nothing.
        if connection["kind"] == "cloudflare_worker":
            result = await bot_ingest.ingest_bot_requests(connection, parsed["requests"])
        else:
            result = await bot_ingest.ingest_log_file(connection, hash_, parsed)
        return JSONResponse({**result, "records": parsed["records"]})
    except Exception:
        return JSONResponse({"error": "ingest_failed"}, status_code=500)


@app.get("/bots/connections")
def bots_connections():
    from . import bot_ingest

    with db._conn() as conn:
        connections = bot_ingest.list_connections(conn)
        month = db.now()[:7] + "-01"
        usage_row = conn.execute(
            "SELECT stored_events, counted_events, capped_at, raw_records,"
            " raw_capped_at FROM bot_usage WHERE month = ?",
            (month,),
        ).fetchone()
    caps = bot_ingest.current_caps()
    return JSONResponse(
        {
            "connections": connections,
            "usage": {
                "month": month,
                "stored": usage_row["stored_events"] if usage_row else 0,
                "counted": usage_row["counted_events"] if usage_row else 0,
                "capped_at": usage_row["capped_at"] if usage_row else None,
                "event_cap": caps["event_cap"],
                # Log senders' records: past the raw cap the files are counted
                # but not read, so the report stops until the month ends.
                "raw": usage_row["raw_records"] if usage_row else 0,
                "raw_capped_at": usage_row["raw_capped_at"] if usage_row else None,
                "raw_cap": caps["raw_cap"],
            },
        }
    )


@app.post("/bots/connections")
async def bots_create_connection(request: Request):
    from . import bot_ingest, bot_sender_setup, bot_worker_script

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    kind = payload.get("kind")
    if kind not in bot_ingest.CONNECTION_KINDS:
        return JSONResponse({"error": "Invalid request"}, status_code=400)
    if bot_ingest.current_caps()["event_cap"] == 0:
        return JSONResponse(
            {
                "error": "locked",
                "message": "The bot report is off on this server (SEOFORGE_BOT_EVENT_CAP=0).",
            },
            status_code=403,
        )
    # Checked before a connection exists: one the sender can't reach is no use.
    endpoint = bot_ingest.public_ingest_endpoint()
    if not endpoint:
        return JSONResponse(
            {
                "error": "not_set_up",
                "message": "AI-bot connections are not set up on this server yet"
                " (SEOFORGE_PUBLIC_SERVER_URL is missing or not https).",
            },
            status_code=503,
        )
    row = bot_ingest.create_connection(kind, payload.get("domains") or [])
    return JSONResponse(
        {
            "connection": {
                "id": row["id"],
                "kind": row["kind"],
                "domains": row["domains"],
                "created_at": row["created_at"],
            },
            "secret": row["secret"],  # shown once; only its SHA-256 is stored
            "endpoint": endpoint,
            # what the person copies, with the secret in it once
            "worker_script": (
                bot_worker_script.build_worker_script(endpoint)
                if kind == "cloudflare_worker"
                else None
            ),
            "worker_secret_name": (
                bot_worker_script.WORKER_SECRET_NAME if kind == "cloudflare_worker" else None
            ),
            "logpush_job": (
                bot_sender_setup.logpush_job(endpoint, row["secret"])
                if kind == "cloudflare_logpush"
                else None
            ),
            "vercel_drain": (
                bot_sender_setup.vercel_drain(endpoint, row["secret"])
                if kind == "vercel_drain"
                else None
            ),
        },
        status_code=201,
    )


@app.delete("/bots/connections/{connection_id}")
def bots_revoke_connection(connection_id: int):
    from . import bot_ingest

    if not bot_ingest.revoke_connection(connection_id):
        return JSONResponse({"error": "Connection not found"}, status_code=404)
    return Response(status_code=204)


# --------------------------------------------------------- tenant data plane
#
# /api/v1/* is the SaaS shell's face: Bearer sfk_ keys, a plan gate and a
# monthly quota, answered before any engine work runs. The operator's own
# dashboard routes above are unchanged - same-process, local tool.

API_PREFIX = "/api/v1"

# The v1 surface runs engine jobs under the requesting tenant's own scope,
# allow-listed. 'opps' (the cross-source value recalculation) ships first;
# every further kind lands here with its own origin verification.
_V1_RUNS = {
    "opps": lambda: integrations.recompute(persist=True),
}


@app.middleware("http")
async def tenant_api_guard(request: Request, call_next):
    if not request.url.path.startswith(API_PREFIX + "/"):
        return await call_next(request)
    from . import tenants

    parts = request.headers.get("authorization", "").split(None, 1)
    secret = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    tenant = tenants.authenticate(secret)
    if tenant is None:
        return JSONResponse(
            {
                "error": "unauthorized",
                "message": "no valid API key (Authorization: Bearer sfk_...)",
            },
            401,
        )
    opened = tenants.gate(tenant)
    if opened["gate"] != "open":
        return JSONResponse(
            {
                "error": "plan_gate",
                "reason": opened["reason"],
                "message": f"plan status {opened['status']!r} gates engine work",
            },
            402,
        )
    # the auth row is flattened (tenant_id, plan); the gate helpers read
    # a tenant row (id, plan) - build the seat explicitly
    seat = {"id": tenant["tenant_id"], "plan": tenant["plan"]}
    if tenants.over_quota(seat):
        return JSONResponse(
            {
                "error": "quota_exhausted",
                "message": "the plan's monthly request budget is spent",
                "limits": tenants.plan_limits(tenant["plan"]),
            },
            429,
        )
    request.state.tenant = tenant
    return await call_next(request)


@app.get(API_PREFIX + "/whoami")
def api_whoami(request: Request):
    from . import tenants

    t = request.state.tenant
    return {
        "tenant_id": t["tenant_id"],
        "name": t["tenant_name"],
        "plan": t["plan"],
        "status": t["status"],
        "limits": tenants.plan_limits(t["plan"]),
        "usage": tenants.usage(t["tenant_id"]),
        "remaining": tenants.remaining(
            {"id": t["tenant_id"], "plan": t["plan"]}
        ),
    }


@app.post(API_PREFIX + "/run/{kind}")
def api_run(kind: str, request: Request):
    from . import tenancy, tenants

    fn = _V1_RUNS.get(kind)
    if fn is None:
        return JSONResponse(
            {
                "error": "unknown",
                "message": f"the v1 surface runs these kinds: {sorted(_V1_RUNS)}",
            },
            400,
        )
    t = request.state.tenant
    try:
        with tenancy.scoped(t["tenant_id"]):
            result = fn() or {}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, 500)
    tenants.meter(t["tenant_id"], "requests", 1)
    return {"ok": True, "tenant_id": t["tenant_id"], "result": _clip(result)}


def serve(host="127.0.0.1", port=8808):
    import uvicorn

    uvicorn.run(app, host=host, port=port)
