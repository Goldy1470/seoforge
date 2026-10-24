"""The 48-signal AEO/GEO rubric — does a page get cited by AI answer engines?

Ported from the reference Site-Audit implementation (derivation in
THIRD_PARTY_NOTICES.md). Pieces:

* `data/rubric.json` — the rubric metadata: 5 weighted categories, 48 signal
  descriptors (label / what / why / howToFix / source / impactTier).
* `run_signals(ctx)` — the engine: every implemented evaluator runs against an
  audit context; one that raises is recorded `na` (not applicable / errored)
  so a bad check never sinks the whole audit.
* `score_audit(results)` — the scorer: a category's score is the mean of its
  *evaluated* signals only (status `na` doesn't drag it down); the total
  re-normalizes over the weight of categories that had at least one evaluated
  signal, so the headline number stays a true 0..1 even mid-rollout. Each
  category reports `evaluated / total` for coverage.

The reference ships 34 deterministic signals today (Structure 13, Trust 7,
Authority 7, Content 4, E-E-A-T 3); the 13 LLM-judged + the Wikidata-backed
brand-entity signal land with the LLM phase. The scorer's partial-coverage
rule keeps partial implementation honest in the meantime.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT

RUBRIC_PATH = ROOT / "data" / "rubric.json"


def _load() -> dict:
    return json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))


_RUBRIC = _load()

RUBRIC_VERSION: str = _RUBRIC["version"]
CATEGORIES: list[dict] = _RUBRIC["categories"]
TOTAL_SIGNALS: int = len(_RUBRIC["signals"])
SIGNALS_BY_KEY: dict[str, dict] = {s["key"]: s for s in _RUBRIC["signals"]}


def describe_signal(key: str) -> dict | None:
    """The full descriptor (label, what, why, howToFix, source, impactTier)."""
    return SIGNALS_BY_KEY.get(key)


def score_audit(results: list[dict]) -> dict:
    """Aggregate per-signal verdicts into category scores and a total.

    `results` items: {key, status, score, evidence}. Signals not in the
    results (or `na`, or without a numeric score) count as not evaluated.
    """
    by_key = {r.get("key"): r for r in results}
    category_scores: dict[str, dict] = {}

    weight_sum = 0.0
    weighted_sum = 0.0

    for cat in CATEGORIES:
        cat_keys = [k for k, s in SIGNALS_BY_KEY.items() if s["category"] == cat["key"]]
        evaluated = [
            by_key[k]
            for k in cat_keys
            if by_key.get(k)
            and by_key[k].get("status") != "na"
            and isinstance(by_key[k].get("score"), (int, float))
        ]
        if not evaluated:
            category_scores[cat["key"]] = {
                "score": None,
                "evaluated": 0,
                "total": len(cat_keys),
            }
            continue

        score = sum(r["score"] for r in evaluated) / len(evaluated)
        category_scores[cat["key"]] = {
            "score": score,
            "evaluated": len(evaluated),
            "total": len(cat_keys),
        }
        weight_sum += cat["weight"]
        weighted_sum += cat["weight"] * score

    total = weighted_sum / weight_sum if weight_sum > 0 else None
    return {"total_score": total, "category_scores": category_scores}


def run_signals(ctx, signals=None) -> list[dict]:
    """Run every implemented evaluator over an audit context.

    `signals` defaults to the registry in `seoforge.rubric_signals` (the 34
    deterministic signals). A raising evaluator becomes a `na` result with the
    error in evidence, never an abort. Returns rows shaped for `score_audit`.
    """
    if signals is None:
        from .rubric_signals import signal_registry as signals

    results: list[dict] = []
    for signal in signals:
        key = getattr(signal, "key", "?")
        try:
            verdict = signal.evaluate(ctx) or {}
            results.append(
                {
                    "key": key,
                    "status": verdict.get("status", "na"),
                    "score": verdict.get("score"),
                    "evidence": verdict.get("evidence") or {},
                }
            )
        except Exception as err:  # noqa: BLE001 — one bad check never sinks the audit
            results.append(
                {
                    "key": key,
                    "status": "na",
                    "score": None,
                    "evidence": {"error": str(err)[:300]},
                }
            )
    return results
