from __future__ import annotations

import json
from collections import defaultdict

from . import backlinks, db, paa
from .config import settings

# blending rules: every opportunity lists the sources it merged.
SOURCE_WEIGHT = {"gsc": 3, "paa": 2, "crawl": 2, "dataforseo": 2, "rank": 2, "cwv": 2, "backlinks": 1, "geo": 1}


def _gsc_striking(rows: list[dict], lo=5, hi=20, min_impr=50) -> list[dict]:
    seen = {}
    for r in rows:
        if lo <= r["position"] <= hi and r["impressions"] >= min_impr:
            seen.setdefault((r["query"], r["page"]), r)
    out = []
    for (q, p), r in seen.items():
        impact = min(10, (r["impressions"] / 500) + (hi - r["position"]) / 2)
        out.append(
            {
                "type": "promote_to_top5",
                "target": f'{p} [{q}]',
                "impact": round(impact, 1),
                "effort": 2,
                "sources": ["gsc", "crawl"],
                "evidence": {
                    "query": q,
                    "page": p,
                    "position": round(r["position"], 1),
                    "impressions": int(r["impressions"]),
                    "why": "ranks 5-20 with real impressions - top-5 wins traffic disproportionately",
                    "action": "refresh the section matching the query, add internal links using the query as anchor, extend with 2 related PAA questions",
                },
            }
        )
    return out


def _gsc_lowctr(rows: list[dict], min_impr=500, max_ctr=0.02) -> list[dict]:
    seen = {}
    for r in rows:
        if r["impressions"] >= min_impr and r["ctr"] < max_ctr:
            seen.setdefault((r["query"], r["page"]), r)
    out = []
    for (q, p), r in seen.items():
        out.append(
            {
                "type": "fix_title_meta",
                "target": p,
                "impact": min(10, r["impressions"] / 400),
                "effort": 1,
                "sources": ["gsc", "crawl"],
                "evidence": {
                    "query": q,
                    "impressions": int(r["impressions"]),
                    "ctr": round(r["ctr"], 4),
                    "why": "people see the snippet and do not click - query intent vs title mismatch",
                    "action": f'rewrite title/h1 to lead with "{q}", put the promise in the meta description',
                },
            }
        )
    return out


def _gsc_cannibal(rows: list[dict]) -> list[dict]:
    by_query: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["clicks"] > 0:
            by_query[r["query"]].append(r)
    out = []
    for q, pages in by_query.items():
        if len(pages) > 1:
            primary = max(pages, key=lambda r: r["clicks"])
            others = [r["page"] for r in pages if r["page"] != primary["page"]]
            if not others:
                continue
            out.append(
                {
                    "type": "repair_cannibalization",
                    "target": primary["page"] or q,
                    "impact": min(10, sum(r["clicks"] for r in pages) / 20),
                    "effort": 2,
                    "sources": ["gsc"],
                    "evidence": {
                        "query": q,
                        "primary_page": primary["page"],
                        "split_over": others,
                        "why": "two URLs split ranking power for the same intent",
                        "action": "pick primary, rewrite others toward different sub-intent or 301/merge; canonical to primary",
                    },
                }
            )
    return out


def _paa_build_list(min_questions=3) -> list[dict]:
    plan = paa.generate_build_list(limit=200)
    out = []
    for p in plan.get("pages", []):
        if p["questions"] < min_questions:
            continue
        out.append(
            {
                "type": "build_question_page",
                "target": p["path"],
                "impact": min(10, p["questions"] * 1.2 + (3 if p["intent"] in ("commercial", "comparison") else 1)),
                "effort": 3,
                "sources": ["paa", "gsc", "dataforseo"],
                "evidence": {
                    "title": p["title"],
                    "questions": p["questions"],
                    "intent": p["intent"],
                    "why": "real PAA demand cluster; question pages capture long-tail and are quoted by AI engines (FAQPage)",
                    "action": f"ship page at {p['path']} - brief available via page_scaffolds; link from /free-guides hub",
                },
            }
        )
    return out


def _crawl_thin() -> list[dict]:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT url, word_count FROM pages WHERE status=200 AND word_count < 250 ORDER BY word_count LIMIT 50"
        ).fetchall()
    thresh = settings.site_url.split("//")[-1].removeprefix("www.")
    out = []
    for r in rows:
        if "/" not in r["url"] or thresh not in r["url"]:
            continue
        out.append(
            {
                "type": "fix_thin_content",
                "target": r["url"],
                "impact": 3,
                "effort": 2,
                "sources": ["crawl"],
                "evidence": {
                    "words": r["word_count"],
                    "why": "thin servers-rendered text ranks worse and gives AI engines nothing to cite",
                    "action": "expand to 600+ words with a PAA-derived FAQ block",
                },
            }
        )
    return out


def _internal_orphans() -> list[dict]:
    try:
        from . import internal_links

        rep = internal_links.build_report()
    except SystemExit:
        return []
    orphans = rep.get("orphans") or []
    out = []
    for u in orphans[:30]:
        out.append(
            {
                "type": "internal_link_orphan",
                "target": u,
                "impact": 4,
                "effort": 1,
                "sources": ["crawl"],
                "evidence": {
                    "why": "zero internal links - crawl equity cannot reach it",
                    "suggested_links": [s for s in rep.get("link_suggestions", []) if s["to"] == u][:3],
                    "action": "add 2-3 contextual internal links from top-authority pages (suggestions attached)",
                },
            }
        )
    return out


def _noindex_in_sitemap() -> list[dict]:
    with db._conn() as conn:
        row = conn.execute(
            "SELECT payload FROM reports WHERE kind='sitemap_crosscheck' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return []
    payload = json.loads(row["payload"])
    problems = payload.get("problems") or []
    grouped = defaultdict(list)
    for p in problems:
        if p["severity"] in ("high", "medium"):
            grouped[p["issue"].rsplit(" ", 2)[0]].append(p["url"])
    out = []
    for issue, urls in grouped.items():
        out.append(
            {
                "type": "sitemap_hygiene",
                "target": issue[:60],
                "impact": 7,
                "effort": 1,
                "sources": ["crawl", "indexnow"],
                "evidence": {"urls": urls[:8], "count": len(urls), "why": "index signals contradict each other (sitemap says yes, robots/meta says no)"},
                "action": "reconcile: remove from sitemap or remove noindex, then re-push via indexing_push",
            }
        )
    return out


def _cwv_slow() -> list[dict]:
    with db._conn() as conn:
        row = conn.execute("SELECT payload FROM reports WHERE kind='cwv' ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return []
    data = json.loads(row["payload"])
    mob = data.get("mobile") or {}
    if mob.get("error"):
        return []
    score = mob.get("psi_performance_score") or 100
    if score >= 70:
        return []
    overall = mob.get("field_data_overall") or ""
    return [
        {
            "type": "cwv_speed",
            "target": data.get("url", settings.site_url),
            "impact": min(10, (100 - score) / 8),
            "effort": 3,
            "sources": ["cwv"],
            "evidence": {
                "psi_score": score,
                "field_data": overall,
                "lcp": (mob.get("metrics") or {}).get("largest-contentful-paint"),
                "render_blocking": mob.get("render_blocking"),
                "why": "mobile speed is a confirmed ranking + conversion factor",
                "action": "kill render-blocking scripts, compress hero image, lazy-load below fold",
            },
        }
    ]


def _backlink_directories(min_dr=70) -> list[dict]:
    out = []
    pending = [d for d in backlinks.list_directories(min_dr=min_dr) if d["status"] == "todo"]
    pending = pending[:10]
    for d in pending:
        out.append(
            {
                "type": "backlink_submission",
                "target": f'{d["name"]} (DR {d["dr"]})',
                "impact": min(10, d["dr"] / 10),
                "effort": 2,
                "sources": ["backlinks"],
                "evidence": {
                    "category": d["category"],
                    "gauged_url": d["url"],
                    "why": f"DR {d['dr']} placement still unpaid-for effort growth",
                    "action": f'submit via browser agent: submit_to_directory("{d["name"]}")',
                },
            }
        )
    return out


def _ai_visibility_gap() -> list[dict]:
    """One `improve_ai_visibility` item per tracked prompt whose 7-day
    state is weak: not_mentioned, mentioned_late or not_cited. The target
    is the prompt's text — the prompt-positions sweep then owns these
    rows' outcome, so the fix is prompt-shaped work it can measure.

    no_runs raises nothing: with no recorded answers there is nothing to
    improve yet, only pastes to catch up on."""
    import datetime as dt

    from . import prompts

    today = prompts._today()
    window = prompts.outcome_window(today)
    items: list[dict] = []
    for prompt in prompts.list_prompts():
        states = prompts.platform_states(
            prompt["id"], since=window["from"], until=today.isoformat()
        )
        weak = [
            s for s in states
            if s.get("state") in ("not_mentioned", "mentioned_late", "not_cited")
        ]
        if not weak:
            continue
        unmentioned = any(s["state"] == "not_mentioned" for s in states)
        items.append(
            {
                "type": "improve_ai_visibility",
                "target": prompt["text"],
                "impact": 8 if unmentioned else 6,
                "effort": 2,
                "sources": ["geo"],
                "evidence": {
                    "prompt": prompt["text"],
                    "platforms": {
                        s["platform"]: {"state": s["state"], "runs": s.get("runs")}
                        for s in weak
                    },
                    "why": "; ".join(
                        f'{s["platform"]}: {s["state"]}' for s in weak
                    ),
                    "action": (
                        "build a question page for this prompt, refresh llms.txt,"
                        " collect fresh answers, re-verify the positions"
                    ),
                },
            }
        )
    return items


def _dedupe_score(items: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for it in items:
        key = (it["type"], it["target"])
        it["score"] = round(it["impact"] / max(it["effort"], 0.5), 2)
        it["evidence"] = it.get("evidence", {})
        if key not in best or it["score"] > best[key]["score"]:
            best[key] = it
    return sorted(best.values(), key=lambda x: -x["score"])


def _volume_lookup(queries: list[str], top: int = 3) -> dict:
    try:
        import asyncio

        from . import keywords

        if not settings.dataforseo_login:
            return {}
        out = {}
        for q in queries[:top]:
            try:
                ideas = asyncio.run(keywords.keyword_suggestions(q))
                out[q] = max((i.get("volume") or 0) for i in ideas) if ideas else 0
            except SystemExit:
                return {}
            except Exception:
                continue
        return out
    except Exception:
        return {}


def recompute(include_volume: bool = True, persist: bool = True) -> dict:
    """Blend every source into one scored work queue. This is where integrations pay out."""
    rows, has_gsc = db_cached_gsc()
    items: list = []
    if has_gsc:
        items += _gsc_striking(rows)
        items += _gsc_lowctr(rows)
        items += _gsc_cannibal(rows)
    items += _paa_build_list()
    items += _crawl_thin()
    items += _internal_orphans()
    items += _noindex_in_sitemap()
    items += _cwv_slow()
    try:
        from . import onpage, technical

        items += onpage.opportunities()
        items += technical.opportunities()
    except Exception:
        pass
    items += _backlink_directories()
    items += _ai_visibility_gap()

    if include_volume:
        seed_queries = [i["evidence"].get("query", "") for i in items if i["type"] == "promote_to_top5"]
        seed_queries += [i["evidence"].get("title", "") for i in items if i["type"] == "build_question_page"]
        volmap = _volume_lookup([q for q in seed_queries if q], top=4)
        for i in items:
            key = (i["evidence"].get("query") or (i["evidence"].get("title") if i["type"] == "build_question_page" else None))
            if key and key in volmap and volmap[key]:
                i["evidence"]["search_volume"] = volmap[key]
                if i["type"] == "build_question_page":
                    i["impact"] = min(10, i["impact"] + volmap[key] / 2000)
                i["sources"] = sorted(set(i["sources"]) | {"dataforseo"})

    items = _dedupe_score(items)

    if persist:
        persist_opps(items)
    top = items[:25]
    return {
        "count": len(items),
        "quick_wins": [i for i in items if i["effort"] <= 1][:10],
        "top": top,
        "source_mix": {"gsc": has_gsc},
    }


def db_cached_gsc() -> tuple[list, bool]:
    from . import gsc

    rows, has = gsc.cache_rows()
    return rows, bool(rows)


def persist_opps(items: list) -> None:
    stamp = db.now()
    with db._conn() as conn:
        for i in items:
            conn.execute(
                """INSERT INTO opportunities(type,target,impact,effort,score,evidence,sources,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(type,target) DO UPDATE SET impact=excluded.impact,
                     effort=excluded.effort, score=excluded.score, evidence=excluded.evidence,
                     sources=excluded.sources, updated_at=excluded.updated_at""",
                (
                    i["type"],
                    i["target"],
                    i["impact"],
                    i["effort"],
                    i["score"],
                    json.dumps(i["evidence"], default=str),
                    ",".join(i.get("sources", [])),
                    stamp,
                    stamp,
                ),
            )


def queue(status: str = "open", kind: str = "", limit: int = 25) -> list[dict]:
    with db._conn() as conn:
        q = "SELECT * FROM opportunities WHERE status=? ORDER BY score DESC LIMIT ?"
        params: list = [status, limit]
        if kind:
            rows = conn.execute(
                "SELECT * FROM opportunities WHERE status=? AND type=? ORDER BY score DESC LIMIT ?",
                (status, kind, limit),
            ).fetchall()
        else:
            rows = conn.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["evidence"] = json.loads(d.pop("evidence") or "{}")
        except Exception:
            pass
        d["sources"] = (d.get("sources") or "").split(",")
        out.append(d)
    return out


def mark_done(type_target: str) -> str:
    t, _, target = type_target.partition("|")
    with db._conn() as conn:
        prior = conn.execute(
            "SELECT id, status FROM opportunities WHERE type=? AND target=?", (t, target)
        ).fetchone()
        conn.execute(
            "UPDATE opportunities SET status='done',"
            " completed_at=COALESCE(completed_at, ?),"
            " outcome=COALESCE(outcome, 'pending_measurement')"
            " WHERE type=? AND target=?",
            (db.now(), t, target),
        )
        # The close event marks the transition, not the repeat: re-stamping
        # an already-done row stays an idempotent write.
        if prior is not None and prior["status"] != "done":
            from . import outcomes

            outcomes.record_event(
                conn, prior["id"], "opportunity_done", {"closedBy": "mark_done"}
            )
    return f"{t} {target} -> done"
