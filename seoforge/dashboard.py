from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from . import backlinks, crawler, db, internal_links, orchestrator, paa
from .config import settings

app = FastAPI(title="SEOForge", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
def index():
    with db._conn() as conn:
        jobs = conn.execute("SELECT id,kind,status,started_at FROM jobs ORDER BY started_at DESC LIMIT 15").fetchall()
        paa_n = conn.execute("SELECT COUNT(*) c FROM paa_questions").fetchone()["c"]
        dirs = conn.execute("SELECT status, COUNT(*) c FROM directories GROUP BY status").fetchall()
        pages_n = conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"]
        reports = conn.execute("SELECT id,kind,created_at FROM reports ORDER BY id DESC LIMIT 10").fetchall()
    rows = "".join(
        f"<tr><td>{j['id']}</td><td>{j['kind']}</td><td>{j['status']}</td><td>{j['started_at']}</td></tr>"
        for j in jobs
    )
    dir_els = " · ".join(f"{d['status']}: {d['c']}" for d in dirs) or "no directory data"
    report_els = "".join(f"<li><a href='/reports/{r['id']}'>{r['id']} {r['kind']} {r['created_at']}</a></li>" for r in reports)
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
</p>
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
