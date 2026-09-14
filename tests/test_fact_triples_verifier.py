"""Tests for the fact_triples 'codebase cartography' verifier plugin.

Covers:
  1. JSON Schema rejection of malformed facts.json
  2. Groundedness gate: fabricated evidence spans are discarded
  3. Exact triple matching (canonical, case-folded)
  4. Semantic statement fallback matching (TF-IDF char n-grams)
  5. Precision penalty for surviving facts that match no GT fact
  6. perfect > partial > hallucinated score ordering
  7. Workspace containment: evidence paths escaping the workspace are discarded
  8. Plugin validate() integration via workspace files
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from eb_verify.plugins import get_validator
from eb_verify.plugins.fact_triples import (
    DEFAULT_ALPHA,
    FactTriplesValidator,
    TfidfStatementSimilarity,
    schema_errors,
    score_fact_triples,
)


# ── helpers ──────────────────────────────────────────────────────────────────

APP_PY = (
    'DEFAULT_PROMPT = """Extract a knowledge graph from the document."""\n'
    "FACT_SCHEMA = {'type': 'object'}\n"
    "def check_duplicate(new_emb, existing_embs):\n"
    "    sims = existing_embs @ new_emb   # vectorized cosine similarities\n"
    "    return sims\n"
)

JOBS_PY = (
    '"""Single-slot serial queue with foreground preemption and auto-backfill."""\n'
    "PREEMPT_FOREGROUND = True\n"
    "def request_pause(job_id):\n"
    "    pass\n"
)


def _fact(
    subject: str,
    predicate: str,
    obj: str,
    statement: str,
    file: str = "app.py",
    span: str = "DEFAULT_PROMPT = ",
    repo: str = "kge",
    confidence: int = 90,
) -> dict:
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "statement": statement,
        "evidence": {"repo": repo, "file": file, "span": span},
        "confidence": confidence,
    }


GT_FACTS = [
    {
        "subject": "app.py",
        "predicate": "defines",
        "object": "DEFAULT_PROMPT",
        "statement": "app.py defines DEFAULT_PROMPT, the knowledge-graph extraction prompt.",
    },
    {
        "subject": "app.py",
        "predicate": "defines",
        "object": "FACT_SCHEMA",
        "statement": "app.py defines FACT_SCHEMA, the JSON schema for extracted facts.",
    },
    {
        "subject": "jobs.py",
        "predicate": "implements",
        "object": "single-slot scheduler",
        "statement": "jobs.py implements a single-slot serial job queue with foreground preemption.",
    },
    {
        "subject": "check_duplicate",
        "predicate": "computes",
        "object": "cosine similarities",
        "statement": "check_duplicate computes vectorized cosine similarities against existing embeddings.",
    },
]


def make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    repo = ws / "kge"
    repo.mkdir(parents=True)
    (repo / "app.py").write_text(APP_PY)
    (repo / "jobs.py").write_text(JOBS_PY)
    gt_dir = ws / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "expected_facts.json").write_text(json.dumps({"facts": GT_FACTS}))
    return ws


def perfect_facts() -> list[dict]:
    return [
        _fact(
            "app.py", "defines", "DEFAULT_PROMPT",
            "app.py defines DEFAULT_PROMPT, the knowledge-graph extraction prompt.",
            span='DEFAULT_PROMPT = """Extract a knowledge graph',
        ),
        _fact(
            "app.py", "defines", "FACT_SCHEMA",
            "app.py defines FACT_SCHEMA, the JSON schema for extracted facts.",
            span="FACT_SCHEMA = {'type': 'object'}",
        ),
        _fact(
            "jobs.py", "implements", "single-slot scheduler",
            "jobs.py implements a single-slot serial job queue with foreground preemption.",
            file="jobs.py",
            span="Single-slot serial queue with foreground preemption",
        ),
        _fact(
            "check_duplicate", "computes", "cosine similarities",
            "check_duplicate computes vectorized cosine similarities against existing embeddings.",
            span="sims = existing_embs @ new_emb",
        ),
    ]


# ── 1. schema rejection ──────────────────────────────────────────────────────

def test_schema_rejects_missing_facts_key():
    errs = schema_errors({"triples": []})
    assert errs, "missing 'facts' key must produce schema errors"


def test_schema_rejects_fact_missing_evidence():
    fact = _fact("a", "b", "c", "a statement here")
    del fact["evidence"]
    errs = schema_errors({"facts": [fact]})
    assert any("evidence" in e for e in errs)


def test_schema_rejects_out_of_range_confidence():
    fact = _fact("a", "b", "c", "a statement here", confidence=150)
    errs = schema_errors({"facts": [fact]})
    assert errs


def test_schema_rejects_non_integer_confidence():
    fact = _fact("a", "b", "c", "a statement here")
    fact["confidence"] = "high"
    errs = schema_errors({"facts": [fact]})
    assert errs


def test_schema_accepts_valid_facts():
    assert schema_errors({"facts": perfect_facts()}) == []


def test_validate_rejects_malformed_facts_json(tmp_path: Path):
    ws = make_workspace(tmp_path)
    (ws / "facts.json").write_text(json.dumps({"facts": [{"subject": "only"}]}))
    result = FactTriplesValidator().validate(ws)
    assert not result.valid
    assert "schema" in result.detail.lower()


# ── 2. groundedness gate ─────────────────────────────────────────────────────

def test_fabricated_span_is_discarded(tmp_path: Path):
    ws = make_workspace(tmp_path)
    facts = perfect_facts()
    facts[0]["evidence"]["span"] = "this text does not exist anywhere in the repo"
    score = score_fact_triples(facts, GT_FACTS, ws)
    assert score.n_grounded == 3
    assert score.ungrounded_fraction == pytest.approx(0.25)
    # the discarded fact earns no recall credit
    assert score.recall == pytest.approx(0.75)


def test_span_matching_is_whitespace_and_case_insensitive(tmp_path: Path):
    ws = make_workspace(tmp_path)
    facts = [perfect_facts()[2]]
    facts[0]["evidence"]["span"] = "single-slot   SERIAL queue with\nforeground preemption"
    score = score_fact_triples(facts, GT_FACTS, ws)
    assert score.n_grounded == 1


def test_missing_evidence_file_is_discarded_not_crash(tmp_path: Path):
    ws = make_workspace(tmp_path)
    facts = [perfect_facts()[0]]
    facts[0]["evidence"]["file"] = "does_not_exist.py"
    score = score_fact_triples(facts, GT_FACTS, ws)
    assert score.n_grounded == 0


def test_short_span_is_discarded_even_if_verbatim(tmp_path: Path):
    # "DEFAULT_PROMPT = " normalizes to 16 chars — verbatim in app.py, but
    # below the groundedness primitive's MIN_SPAN_CHARS=20, so it is not
    # accepted as evidence.
    ws = make_workspace(tmp_path)
    facts = [perfect_facts()[0]]
    facts[0]["evidence"]["span"] = "DEFAULT_PROMPT = "
    score = score_fact_triples(facts, GT_FACTS, ws)
    assert score.n_grounded == 0
    assert score.discarded[0].reason == "too_short"


def test_span_at_min_length_is_grounded(tmp_path: Path):
    # 20 normalized chars, verbatim in app.py — exactly at the threshold.
    ws = make_workspace(tmp_path)
    facts = [perfect_facts()[0]]
    span = 'DEFAULT_PROMPT = """'
    assert len(span) == 20
    facts[0]["evidence"]["span"] = span
    score = score_fact_triples(facts, GT_FACTS, ws)
    assert score.n_grounded == 1


def test_path_escaping_workspace_is_discarded_not_crash(tmp_path: Path):
    (tmp_path / "secret.txt").write_text("DEFAULT_PROMPT = ")
    ws = make_workspace(tmp_path)
    facts = [perfect_facts()[0]]
    facts[0]["evidence"]["file"] = "../secret.txt"
    score = score_fact_triples(facts, GT_FACTS, ws)
    assert score.n_grounded == 0


# ── 3. exact triple match ────────────────────────────────────────────────────

def test_exact_triple_match_is_case_folded(tmp_path: Path):
    ws = make_workspace(tmp_path)
    fact = perfect_facts()[0]
    fact["subject"] = "APP.PY"
    fact["object"] = "default_prompt"
    fact["statement"] = "completely different wording so semantics cannot match at all"
    score = score_fact_triples([fact], GT_FACTS, ws)
    match = next(m for m in score.gt_matches if m.gt_index == 0)
    assert match.mechanism == "exact"
    assert score.recall == pytest.approx(0.25)


# ── 4. semantic fallback match ───────────────────────────────────────────────

def test_semantic_fallback_matches_paraphrased_statement(tmp_path: Path):
    ws = make_workspace(tmp_path)
    fact = _fact(
        # triple deliberately non-canonical → exact match fails
        "the jobs module", "runs", "a serial queue",
        "jobs.py runs a single-slot serial queue with foreground preemption of jobs.",
        file="jobs.py",
        span="Single-slot serial queue with foreground preemption",
    )
    score = score_fact_triples([fact], GT_FACTS, ws)
    match = next(m for m in score.gt_matches if m.gt_index == 2)
    assert match.mechanism == "semantic"
    assert score.recall == pytest.approx(0.25)


def test_unrelated_statement_does_not_match_semantically(tmp_path: Path):
    ws = make_workspace(tmp_path)
    fact = _fact(
        "PREEMPT_FOREGROUND", "toggles", "preemption",
        "The Dockerfile pins the Python base image version for reproducible builds.",
        file="jobs.py",
        span="PREEMPT_FOREGROUND = True",
    )
    score = score_fact_triples([fact], GT_FACTS, ws)
    assert score.recall == 0.0
    assert score.unmatched_fraction == pytest.approx(1.0)


def test_similarity_backend_is_pluggable(tmp_path: Path):
    class AlwaysMatch:
        def pairwise(self, gt_statements, candidate_statements):
            return np.ones((len(gt_statements), len(candidate_statements)))

    ws = make_workspace(tmp_path)
    fact = _fact(
        "x", "y", "z", "gibberish words entirely unrelated",
        span='DEFAULT_PROMPT = """Extract',
    )
    score = score_fact_triples([fact], GT_FACTS, ws, similarity=AlwaysMatch())
    assert score.n_gt_matched == 1  # one candidate can match at most one GT fact


# ── 5. precision penalty ─────────────────────────────────────────────────────

def test_unmatched_survivor_reduces_score(tmp_path: Path):
    ws = make_workspace(tmp_path)
    facts = perfect_facts() + [
        _fact(
            "PREEMPT_FOREGROUND", "defaults_to", "True",
            "Something true but not in the ground-truth fact set at all.",
            file="jobs.py",
            span="PREEMPT_FOREGROUND = True",
        )
    ]
    score = score_fact_triples(facts, GT_FACTS, ws)
    assert score.recall == pytest.approx(1.0)
    assert score.unmatched_fraction == pytest.approx(1 / 5)
    assert score.total_score == pytest.approx(1.0 * (1 - DEFAULT_ALPHA * (1 / 5)))


def test_alpha_is_configurable(tmp_path: Path):
    ws = make_workspace(tmp_path)
    facts = perfect_facts() + [
        _fact(
            "PREEMPT_FOREGROUND", "defaults_to", "True",
            "Something true but not in the ground-truth fact set at all.",
            file="jobs.py",
            span="PREEMPT_FOREGROUND = True",
        )
    ]
    score = score_fact_triples(facts, GT_FACTS, ws, alpha=1.0)
    assert score.total_score == pytest.approx(1.0 * (1 - 1.0 * (1 / 5)))


# ── 6. ordering: perfect > partial > hallucinated ────────────────────────────

def test_perfect_beats_partial_beats_hallucinated(tmp_path: Path):
    ws = make_workspace(tmp_path)

    perfect = perfect_facts()

    partial = perfect_facts()[:2]
    fabricated = perfect_facts()[2]
    fabricated["evidence"]["span"] = "a span that was never in the file"
    partial.append(fabricated)

    hallucinated = []
    for f in perfect_facts():
        f["evidence"]["span"] = "entirely invented evidence never present " + f["object"]
        hallucinated.append(f)

    s_perfect = score_fact_triples(perfect, GT_FACTS, ws).total_score
    s_partial = score_fact_triples(partial, GT_FACTS, ws).total_score
    s_halluc = score_fact_triples(hallucinated, GT_FACTS, ws).total_score

    assert s_perfect > s_partial > s_halluc
    assert s_perfect == pytest.approx(1.0)
    assert s_halluc == pytest.approx(0.0)


# ── 7/8. plugin registration + validate() integration ───────────────────────

def test_plugin_is_registered():
    assert get_validator("fact_triples") is not None


def test_validate_end_to_end(tmp_path: Path):
    ws = make_workspace(tmp_path)
    (ws / "facts.json").write_text(json.dumps({"facts": perfect_facts()}))
    result = FactTriplesValidator().validate(ws)
    assert result.valid
    assert "score=1.000" in result.detail


def test_validate_missing_facts_json(tmp_path: Path):
    ws = make_workspace(tmp_path)
    result = FactTriplesValidator().validate(ws)
    assert not result.valid
    assert "facts.json" in result.detail


def test_validate_root_decoy_shadowing_pinned_facts_is_ambiguous(tmp_path: Path):
    # Decoy-shadow class: a workspace-root facts.json alongside the real one in
    # a subdir must not be silently graded (sorted(glob)[0] took the root
    # decoy). Reject as ambiguous instead.
    ws = make_workspace(tmp_path)
    (ws / "facts.json").write_text(json.dumps({"facts": []}))  # root decoy
    out = ws / "agent_output"
    out.mkdir()
    (out / "facts.json").write_text(json.dumps({"facts": perfect_facts()}))
    result = FactTriplesValidator().validate(ws)
    assert not result.valid
    assert "ambiguous" in result.detail.lower()


def test_validate_ignores_ground_truth_facts_json(tmp_path: Path):
    # A facts.json planted inside ground_truth/ must not be conflated with the
    # agent artifact; the lone agent file under agent_output/ is graded.
    ws = make_workspace(tmp_path)
    (ws / "ground_truth" / "facts.json").write_text(json.dumps({"facts": []}))
    out = ws / "agent_output"
    out.mkdir()
    (out / "facts.json").write_text(json.dumps({"facts": perfect_facts()}))
    result = FactTriplesValidator().validate(ws)
    assert result.valid
    assert "score=1.000" in result.detail


def test_validate_missing_ground_truth(tmp_path: Path):
    ws = make_workspace(tmp_path)
    (ws / "ground_truth" / "expected_facts.json").unlink()
    (ws / "facts.json").write_text(json.dumps({"facts": perfect_facts()}))
    result = FactTriplesValidator().validate(ws)
    assert not result.valid
    assert "expected_facts.json" in result.detail


def test_validate_hallucinated_output_is_invalid(tmp_path: Path):
    ws = make_workspace(tmp_path)
    hallucinated = []
    for f in perfect_facts():
        f["evidence"]["span"] = "entirely invented evidence never present " + f["object"]
        hallucinated.append(f)
    (ws / "facts.json").write_text(json.dumps({"facts": hallucinated}))
    result = FactTriplesValidator().validate(ws)
    assert not result.valid


def test_validate_require_grounded_citations_kwarg_is_noop(tmp_path: Path):
    """The kwarg exists only for the runner's capability probe: groundedness
    is ALWAYS enforced (layer 1), so passing it changes nothing."""
    ws = make_workspace(tmp_path)
    (ws / "facts.json").write_text(json.dumps({"facts": perfect_facts()}))
    default = FactTriplesValidator().validate(ws)
    flagged = FactTriplesValidator().validate(ws, require_grounded_citations=True)
    assert flagged == default


def test_semantic_threshold_out_of_range_rejected(tmp_path: Path):
    ws = make_workspace(tmp_path)
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError, match="semantic_threshold"):
            score_fact_triples(
                perfect_facts(), GT_FACTS, ws, semantic_threshold=bad
            )


def test_tfidf_similarity_is_deterministic():
    sim = TfidfStatementSimilarity()
    gt = ["app.py defines DEFAULT_PROMPT, the extraction prompt."]
    cand = ["DEFAULT_PROMPT, the extraction prompt, is defined in app.py."]
    a = sim.pairwise(gt, cand)
    b = sim.pairwise(gt, cand)
    assert np.allclose(a, b)
    assert a.shape == (1, 1)
