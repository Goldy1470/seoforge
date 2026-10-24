"""Task 3.1 tests: rubric data shape, scorer partial-coverage semantics,
engine error isolation."""
import types

from seoforge import rubric


def verdict(key, status="pass", score=1.0, evidence=None):
    return types.SimpleNamespace(
        key=key, evaluate=lambda ctx: {"status": status, "score": score, "evidence": evidence or {}}
    )


# ------------------------------------------------------------ rubric metadata

def test_rubric_shape():
    assert rubric.RUBRIC_VERSION == "v1.1"
    assert rubric.TOTAL_SIGNALS == 48
    assert len(rubric.SIGNALS_BY_KEY) == 48
    weights = {c["key"]: c["weight"] for c in rubric.CATEGORIES}
    assert set(weights) == {"structure", "authority", "content", "trust", "eeat"}
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    for cat in rubric.CATEGORIES:
        in_cat = [s for s in rubric.SIGNALS_BY_KEY.values() if s["category"] == cat["key"]]
        assert len(in_cat) == cat["signalCount"], cat["key"]


def test_every_signal_fully_described_and_debranded():
    required = ("key", "category", "label", "what", "why", "howToFix", "source", "impactTier")
    for s in rubric.SIGNALS_BY_KEY.values():
        for field in required:
            assert s.get(field), (s["key"], field)
        assert s["impactTier"] in ("high", "medium", "standard"), s["key"]
        # derived from a reference whose copy pitched its own tools — gone here
        blob = f"{s['what']} {s['why']} {s['howToFix']} {s['source']}".lower()
        assert "/tools/" not in blob and "via our" not in blob, s["key"]


def test_describe_signal():
    d = rubric.describe_signal("faq-schema")
    assert d["category"] == "structure" and d["impactTier"] == "medium"
    assert rubric.describe_signal("nope") is None


def test_every_signal_belongs_to_a_listed_category():
    keys = {c["key"] for c in rubric.CATEGORIES}
    assert all(s["category"] in keys for s in rubric.SIGNALS_BY_KEY.values())


# ------------------------------------------------------------------- scorer

def _full_results(score_map=None):
    out = []
    for key, sig in rubric.SIGNALS_BY_KEY.items():
        status, score = (score_map or {}).get(key, ("pass", 1.0))
        out.append({"key": key, "status": status, "score": score, "evidence": {}})
    return out


def test_perfect_scores_total_one():
    scored = rubric.score_audit(_full_results())
    assert scored["total_score"] == 1.0
    assert all(c["score"] == 1.0 and c["evaluated"] == c["total"] for c in scored["category_scores"].values())
    assert scored["category_scores"]["trust"]["total"] == 7


def test_category_means_without_renormalization_bias():
    # every structure signal at 0, everything else perfect:
    # structure mean 0 (weight .25), others 1 → total = .75/.75 → 1.0? No:
    res = _full_results()
    for s in rubric.SIGNALS_BY_KEY.values():
        if s["category"] == "structure":
            for r in res:
                if r["key"] == s["key"]:
                    r["status"], r["score"] = "fail", 0.0
    scored = rubric.score_audit(res)
    assert scored["category_scores"]["structure"]["score"] == 0.0
    # weights stay complete on a fully evaluated rubric: the zero category drags
    # the total to (0.2+0.25+0.1+0.2)*1 / 1.0 = 0.75
    assert scored["total_score"] == 0.75


def test_partial_coverage_only_evaluated_categories_count():
    only_structure = [r for r in _full_results() if r["key"].startswith(("structural", "h1", "json"))]
    scored = rubric.score_audit(only_structure)
    assert scored["category_scores"]["structure"]["score"] == 1.0
    assert scored["category_scores"]["eeat"] == {"score": None, "evaluated": 0, "total": 8}
    assert scored["total_score"] == 1.0  # re-normalized over evaluated weight only


def test_na_signals_do_not_drag_their_category_down():
    res = _full_results()
    for s in rubric.SIGNALS_BY_KEY.values():
        if s["category"] != "eeat":
            for r in res:
                if r["key"] == s["key"]:
                    r["status"], r["score"] = "na", None
    res = [r for r in res if r["status"] != "na"]  # engine emits na rows; drop them
    res.append({"key": "first-person", "status": "na", "score": None, "evidence": {}})
    scored = rubric.score_audit(res)
    assert scored["category_scores"]["eeat"]["evaluated"] == 7
    assert scored["category_scores"]["eeat"]["score"] == 1.0
    assert scored["total_score"] == 1.0


def test_no_evaluated_signals_total_none():
    scored = rubric.score_audit([])
    assert scored["total_score"] is None
    assert all(c["score"] is None for c in scored["category_scores"].values())


def test_unknown_and_malformed_rows_ignored():
    scored = rubric.score_audit([
        {"key": "not-a-signal", "status": "pass", "score": 1.0, "evidence": {}},
        {"key": "quotations", "status": "pass", "score": None, "evidence": {}},
        {"key": "listz", "status": "pass", "score": 1.0},
    ])
    assert scored["total_score"] is None
    assert scored["category_scores"]["authority"]["evaluated"] == 0


# ------------------------------------------------------------------- engine

def _signal(key, outcome):
    if isinstance(outcome, Exception):
        def evaluate(ctx):
            raise outcome
    else:
        def evaluate(ctx):
            return outcome
    return types.SimpleNamespace(key=key, evaluate=evaluate)


CTX = object()


def test_engine_collects_verdicts_and_defaults_evidence():
    signals = [
        _signal("a", {"status": "pass", "score": 1.0, "evidence": {"x": 1}}),
        _signal("b", {"status": "fail", "score": 0}),
        _signal("c", None),
    ]
    rows = rubric.run_signals(CTX, signals=signals)
    assert rows[0] == {"key": "a", "status": "pass", "score": 1.0, "evidence": {"x": 1}}
    assert rows[1] == {"key": "b", "status": "fail", "score": 0, "evidence": {}}
    assert rows[2]["status"] == "na" and rows[2]["score"] is None


def test_engine_isolates_raising_evaluators():
    signals = [
        verdict("good"),
        _signal("broken", RuntimeError("soup exploded")),
    ]
    rows = rubric.run_signals(CTX, signals=signals)
    assert rows[0]["status"] == "pass"
    assert rows[1]["status"] == "na" and rows[1]["score"] is None
    assert "soup exploded" in rows[1]["evidence"]["error"]


def test_engine_rows_score_alongside_rubric():
    signals = [verdict(key) for key in ("faq-schema", "lists", "tables", "viewport")]
    rows = rubric.run_signals(CTX, signals=signals)
    scored = rubric.score_audit(rows)
    assert scored["category_scores"]["structure"]["evaluated"] == 3
    assert scored["category_scores"]["trust"]["evaluated"] == 1
    assert scored["total_score"] == 1.0
