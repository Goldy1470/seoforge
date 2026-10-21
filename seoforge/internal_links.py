from __future__ import annotations

import math
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from . import db
from .config import settings

STOP = set("""a above after all also am an and any are as at be because been before being between both but by can could did do does doing down during each few for from further had has have having he her here hers him his i if in into is it its itself just me more most my no nor not of off on or our ours out over own same she should so some such than that the their them then there these they this those through to too under until up very was we were what when where which while who whom why will with you your""".split())


def tokenize(text: str) -> Counter:
    words = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    return Counter(w for w in words if w not in STOP and len(w) > 2)


def _same_site(url: str) -> bool:
    try:
        tgt = urlsplit(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return False
    src = urlsplit(settings.site_url).netloc.lower().removeprefix("www.")
    return bool(tgt) and (tgt == src or tgt.endswith("." + src))


def _same_link(a: str, b: str) -> bool:
    ua, ub = urlsplit(_trailing_slash(a)), urlsplit(_trailing_slash(b))
    return (ua.netloc, ua.path) == (ub.netloc, ub.path)


def _trailing_slash(u: str) -> str:
    return u.rstrip("/") + "/" if u.rstrip("/") else u


def build_graph(job_id: str | None = None) -> dict:
    with db._conn() as conn:
        latest = job_id or _latest_job(conn)
        rows = conn.execute(
            "SELECT url, title, h1, word_count FROM pages WHERE job_id=? AND status=200",
            (latest,),
        ).fetchall()
        edges = conn.execute(
            "SELECT source, target, anchor FROM links WHERE job_id=?", (latest,)
        ).fetchall()
    if latest is None or not rows:
        raise SystemExit("Run a crawl first: python -m seoforge.cli crawl")
    pairs = {(e["source"], e["target"]) for e in edges if _same_site(e["target"])}
    urlset = {p["url"] for p in rows} | {t for _, t in pairs} | {s for s, _ in pairs}
    linked_to = Counter(t for _, t in pairs)
    return {
        "job_id": latest,
        "pages": {p["url"]: dict(p) for p in rows},
        "pairs": pairs,
        "linked_to": linked_to,
        "urlset": urlset,
    }


def _latest_job(conn) -> str | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE kind='crawl' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def pagerank(graph: dict, damping: float = 0.85, iters: int = 30) -> dict:
    urlset, pairs = graph["urlset"], graph["pairs"]
    out = defaultdict(set)
    incoming = defaultdict(int)
    for s, t in pairs:
        if s in urlset and t in urlset:
            out[s].add(t)
            incoming[t] += 1
    n = max(len(urlset), 1)
    rank = {u: 1 / n for u in urlset}
    for _ in range(iters):
        nxt = {u: (1 - damping) / n for u in urlset}
        for s in urlset:
            targets = out.get(s) or (set(urlset) if not out.get(s) else set())
            share = rank[s] / max(len(targets), 1)
            for t in targets:
                nxt[t] += damping * share
        rank = nxt
    return rank


def suggest_links(top_k: int = 10) -> list[dict]:
    graph = build_graph()
    urls = [u for u in graph["urlset"] if u in graph["pages"]]
    docs = {
        u: tokenize(f"{p['title'] or ''} {p['h1'] or ''}")
        for u, p in graph["pages"].items()
    }
    n_docs = max(len(docs), 1)
    df: Counter = Counter()
    for c in docs.values():
        df.update(set(c))
    idf = {w: math.log((n_docs + 1) / (d + 1)) for w, d in df.items()}

    def vec(c: Counter) -> dict:
        return {w: (1 + math.log(n)) * idf.get(w, 0.0) for w, n in c.items()}

    vectors = {u: vec(c) for u, c in docs.items()}
    existing = {f"{s}>{t}" for s, t in graph["pairs"]}
    max_incoming = max(graph["linked_to"].values(), default=10) or 10
    target_cap = max(2, max_incoming // 2)
    suggestions = []
    for tgt in urls:
        if graph["linked_to"][tgt] >= target_cap:
            continue
        tgt_v = vectors[tgt]
        scored = []
        for src in urls:
            if src == tgt or f"{src}>{tgt}" in existing:
                continue
            sim = cosine(vectors[src], tgt_v)
            if sim > 0.12:
                scored.append((sim, src))
        scored.sort(reverse=True)
        anchor = _anchor_for(tgt, graph["pages"])
        for sim, src in scored[:top_k]:
            suggestions.append(
                {
                    "from": src,
                    "to": tgt,
                    "anchor": anchor,
                    "similarity": round(sim, 3),
                }
            )
    return sorted(suggestions, key=lambda x: -x["similarity"])


def cosine(a: dict, b: dict) -> float:
    common = set(a) & set(b)
    if not common or not a or not b:
        return 0.0
    num = sum(a[w] * b[w] for w in common)
    return num / (_norml(a) * _norml(b) or 1.0)


def _norml(v: dict) -> float:
    return math.sqrt(sum(x * x for x in v.values())) or 1.0


def _anchor_for(url: str, pages: dict) -> str:
    p = pages.get(url, {})
    return (p.get("title") or p.get("h1") or url.rsplit("/", 1)[-1] or "this page")[:70]


def build_report() -> dict:
    graph = build_graph()
    rank = pagerank(graph)
    orphans = [u for u in graph["pages"] if graph["linked_to"][u] == 0]
    dead = [t for _, t in graph["pairs"] if t not in graph["pages"]]
    return {
        "job_id": graph["job_id"],
        "pages": len(graph["pages"]),
        "internal_links": len(graph["pairs"]),
        "orphans": orphans,
        "links_to_uncrawled_404": sorted(set(dead)),
        "top_authority": sorted(rank.items(), key=lambda kv: -kv[1])[:15],
        "link_suggestions": suggest_links(),
    }
