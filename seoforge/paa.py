from __future__ import annotations

import asyncio
import csv
import json
import re
import unicodedata
from pathlib import Path

from . import db
from .config import settings


def slugify(q: str) -> str:
    s = unicodedata.normalize("NFKD", q.lower())
    s = re.sub(r"[^a-z0-9\s-]", "", s).strip()
    s = re.sub(r"[\s_]+", "-", s)
    return s[:70].strip("-")


def classify_intent(q: str) -> str:
    ql = q.lower()
    if re.search(r"\b(vs|versus|difference between|or)\b", ql):
        return "comparison"
    if re.search(r"\b(best|top|recommend|alternative)\b", ql):
        return "commercial"
    if re.search(r"\b(how to|how do|how can|how much|when should|steps)\b", ql):
        return "instructional"
    if re.search(r"\b(what is|what does|why|definition|meaning)\b", ql):
        return "informational"
    return "informational"


def _cluster_key(q: str) -> str:
    words = re.sub(r"[^\w\s]", "", q.lower()).split()
    keep = [w for w in words if w not in {"what", "why", "how", "is", "the", "a", "an", "do", "in", "for", "to", "can", "you", "and", "with"}]
    return " ".join(keep[:2]) or q[:30]


def _paa_from_serp(keyword: str) -> list[str]:
    m = settings.serp_method
    if m in ("auto", "dataforseo") and settings.dataforseo_login:
        from .keywords import serp

        data = asyncio.run(serp(keyword))
        return list(dict.fromkeys(data.get("people_also_ask") or []))
    if m in ("auto", "browser") and settings.browser_agent != "off":
        from .browser_agent import paa_expand

        return asyncio.run(paa_expand(keyword))
    raise SystemExit(
        "No SERP source set. Set DATAFORSEO_LOGIN/PASSWORD or SEOFORGE_BROWSER_AGENT=browseruse "
        "with playwright installed."
    )


async def build_paa_tree(
    seeds: list[str],
    max_depth: int = 2,
    per_level: int = 12,
    job_id: str | None = None,
) -> dict:
    job_id = job_id or db.start_job("paa_tree", {"seeds": seeds, "max_depth": max_depth})
    rows = []
    queue = [(s, s, 0) for s in seeds]
    seen = set()
    results = []
    while queue:
        current, current_seed, level = queue.pop(0)
        if current in seen or level >= max_depth:
            continue
        seen.add(current)
        try:
            qs = _paa_from_serp(current)
        except SystemExit:
            raise
        except Exception as exc:
            results.append({"question": current, "error": str(exc)})
            continue
        for q in qs[:per_level]:
            intent = classify_intent(q)
            cluster = _cluster_key(q)
            add_query(q, seed=current_seed, parent=current, depth=level)
            rows.append(
                {
                    "question": q,
                    "seed": current_seed,
                    "parent": current,
                    "depth": level + 1,
                    "cluster": cluster,
                    "intent": intent,
                }
            )
            if q not in seen and level + 1 < max_depth:
                queue.append((q, current_seed, level + 1))
        results.append({"source": current, "expanded": len(qs[:per_level]), "depth": level})

    build_list = generate_build_list(limit=200)
    db.finish_job(job_id, {"questions": len(rows), "unique": len(seen)})
    return {"job_id": job_id, "new_rows": rows, "build_list_preview": build_list[:10]}


def add_query(question: str, seed: str, parent: str | None, depth: int) -> None:
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO paa_questions(question,seed,parent,depth,cluster,intent,created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(question) DO NOTHING""",
            (question.strip(), seed, parent, depth, _cluster_key(question), classify_intent(question), db.now()),
        )


def build_list_export() -> dict:
    return generate_build_list(limit=9999)


def generate_build_list(limit: int = 200) -> dict:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT question, cluster, intent, depth, COUNT(*) OVER (PARTITION BY cluster) AS cluster_size "
            "FROM paa_questions ORDER BY cluster_size DESC, depth, question LIMIT ?",
            (limit,),
        ).fetchall()
    seen_clusters: dict[str, dict] = {}
    for r in rows:
        c = r["cluster"]
        if c not in seen_clusters:
            seen_clusters[c] = {
                "title": c.title(),
                "path": "/" + slugify(c),
                "target_positions": [],
                "faqs": [],
                "intent": r["intent"],
                "questions": 0,
            }
        entry = seen_clusters[c]
        entry["questions"] += 1
        if r["depth"] == 0:
            entry["target_positions"].append(r["question"])
        else:
            entry["faqs"].append(r["question"])
    pages = sorted(seen_clusters.values(), key=lambda p: -p["questions"])
    pages = [finalize(p, i) for i, p in enumerate(pages[:limit])]
    return {"count": len(pages), "pages": pages}


def finalize(p: dict, idx: int) -> dict:
    faqs = [q for q in p["faqs"] if q not in p["target_positions"]]
    return {
        "priority": idx + 1,
        "path": p["path"],
        "title": p["title"],
        "intent": p["intent"],
        "questions": len(faqs),
        "h1": p["title"],
        "meta_desc": f"Clear answers to {len(faqs)} real People Also Ask questions about "
        f"{p['title'].lower()} ({p['intent']} intent).",
        "schema": "FAQPage",
        "interlink": ["hub: /free-guides", "same-cluster prev/next", "related product pages"],
    }


def export_paa_csv() -> str:
    path = Path(settings.exports_dir) / "paa_build_list.csv"
    plan = generate_build_list(limit=500)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["priority", "path", "title", "intent", "question_count", "h1", "meta_desc"])
        for p in plan.get("pages") or []:
            w.writerow(
                [p["priority"], p["path"], p["title"], p["intent"], p["questions"], p.get("h1", ""), p.get("meta_desc", "")]
            )
    return str(path)
