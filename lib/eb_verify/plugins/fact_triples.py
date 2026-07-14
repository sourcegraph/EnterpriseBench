"""
fact_triples validator — 'codebase cartography' comprehension verifier.

The agent under evaluation writes facts.json in its output dir: a set of
knowledge-graph triples about the codebase, each backed by a verbatim
evidence span from a workspace file:

    {"facts": [{"subject": str, "predicate": str, "object": str,
                "statement": str,
                "evidence": {"repo": str, "file": str, "span": str},
                "confidence": 0-100}]}

Three-layer scoring:
  1. Groundedness gate — each fact's evidence span must be a verbatim
     (whitespace-normalized, case-insensitive) substring of
     workspace/{repo}/{file}, checked by eb_verify.groundedness (which also
     enforces workspace containment and its MIN_SPAN_CHARS=20 minimum span
     length). Ungrounded facts are DISCARDED before any credit; the
     ungrounded fraction is reported.
  2. Coverage — recall of the task's ground-truth fact set
     (workspace/ground_truth/expected_facts.json) by surviving facts.
     A GT fact matches by exact canonical triple first, then by
     statement-level semantic similarity as fallback. Matching is
     one-to-one: a surviving fact can satisfy at most one GT fact.
  3. Precision penalty — surviving facts matching no GT fact reduce the
     score: score = recall * (1 - alpha * unmatched_fraction).

The semantic matcher sits behind a small Protocol (StatementSimilarity) so
a real embedding model can be plugged in later; the default delegates to
eb_verify.fact_coverage's deterministic TF-IDF char n-gram embedder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Protocol, Sequence

from jsonschema import Draft202012Validator

if TYPE_CHECKING:  # numpy is imported lazily at its one runtime use site below.
    import numpy as np

from eb_verify.fact_coverage import (
    Embedder,
    ScoringDepsUnavailable,
    TfidfCharNgramEmbedder,
    load_scoring_deps,
)
from eb_verify.groundedness import Citation, check_groundedness
from eb_verify.plugins import ValidationResult, safe_read

DEFAULT_ALPHA = 0.3
# Chosen operating point for one-to-one greedy triple matching — NOT calibrated
# like fact_coverage.DEFAULT_THRESHOLD (0.40, swept in fact_coverage_calibration.py).
# Recalibrate when a real embedding backend lands.
DEFAULT_SEMANTIC_THRESHOLD = 0.5

_EVIDENCE_SCHEMA = {
    "type": "object",
    "required": ["repo", "file", "span"],
    "additionalProperties": False,
    "properties": {
        "repo": {"type": "string", "minLength": 1},
        "file": {"type": "string", "minLength": 1},
        "span": {"type": "string", "minLength": 1},
    },
}

FACTS_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facts"],
    "additionalProperties": False,
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "subject", "predicate", "object",
                    "statement", "evidence", "confidence",
                ],
                "additionalProperties": False,
                "properties": {
                    "subject": {"type": "string", "minLength": 1},
                    "predicate": {"type": "string", "minLength": 1},
                    "object": {"type": "string", "minLength": 1},
                    "statement": {"type": "string", "minLength": 1},
                    "evidence": _EVIDENCE_SCHEMA,
                    "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                },
            },
        },
    },
}

GT_FACTS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facts"],
    "properties": {
        "facts": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["subject", "predicate", "object", "statement"],
                "properties": {
                    "subject": {"type": "string", "minLength": 1},
                    "predicate": {"type": "string", "minLength": 1},
                    "object": {"type": "string", "minLength": 1},
                    "statement": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


def schema_errors(data: object, schema: dict | None = None) -> list[str]:
    """Return human-readable schema violations for a facts document (empty = valid)."""
    validator = Draft202012Validator(schema or FACTS_JSON_SCHEMA)
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    ]


class StatementSimilarity(Protocol):
    """Pluggable statement-similarity backend for the semantic fallback matcher."""

    def pairwise(
        self, gt_statements: Sequence[str], candidate_statements: Sequence[str]
    ) -> np.ndarray:
        """Return an (n_gt, n_candidates) matrix of similarities in [0, 1]."""
        ...


class TfidfStatementSimilarity:
    """Deterministic local default: cosine similarity over embeddings from
    eb_verify.fact_coverage's TfidfCharNgramEmbedder (char 3-5-gram TF-IDF).

    GT and candidate statements are embedded in a single call so they share a
    vector space (the embedder is fit per call). Any ``fact_coverage.Embedder``
    can be substituted; a full-matrix backend (e.g. a cross-encoder) can
    instead implement StatementSimilarity directly.
    """

    def __init__(self, embedder: Optional[Embedder] = None):
        self._embedder = embedder if embedder is not None else TfidfCharNgramEmbedder()

    def pairwise(
        self, gt_statements: Sequence[str], candidate_statements: Sequence[str]
    ) -> np.ndarray:
        emb = self._embedder.embed(list(gt_statements) + list(candidate_statements))
        n_gt = len(gt_statements)
        # rows are L2-normalized, so the dot product is cosine similarity
        return emb[:n_gt] @ emb[n_gt:].T


@dataclass(frozen=True)
class GtMatch:
    """How (or whether) one ground-truth fact was matched by a surviving fact."""
    gt_index: int
    mechanism: Optional[str]  # "exact" | "semantic" | None (unmatched)
    candidate_index: Optional[int]
    similarity: Optional[float]


@dataclass(frozen=True)
class DiscardedFact:
    """A fact removed by the groundedness gate.

    reason is the eb_verify.groundedness reason code:
    span_not_found | file_missing | path_escape | too_short.
    """
    index: int
    reason: str


@dataclass(frozen=True)
class FactTriplesScore:
    """Full scoring breakdown for a fact-triples submission."""
    total_score: float
    recall: float
    ungrounded_fraction: float
    unmatched_fraction: float
    n_facts: int
    n_grounded: int
    n_gt: int
    n_gt_matched: int
    gt_matches: tuple[GtMatch, ...]
    discarded: tuple[DiscardedFact, ...]


def _normalize(text: str) -> str:
    """Collapse whitespace and case-fold for verbatim-span comparison."""
    return re.sub(r"\s+", " ", text).casefold().strip()


def _canonical_triple(fact: dict) -> tuple[str, str, str]:
    return (
        _normalize(fact["subject"]),
        _normalize(fact["predicate"]),
        _normalize(fact["object"]),
    )


def score_fact_triples(
    agent_facts: Sequence[dict],
    gt_facts: Sequence[dict],
    workspace: Path,
    *,
    alpha: float = DEFAULT_ALPHA,
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    similarity: Optional[StatementSimilarity] = None,
) -> FactTriplesScore:
    """Three-layer score: groundedness gate, GT coverage, precision penalty.

    Both fact lists must already be schema-valid (see schema_errors);
    structural problems raise KeyError/TypeError rather than being scored.
    """
    if not gt_facts:
        raise ValueError("ground truth fact set is empty — task is misconfigured")
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")
    if not 0.0 <= semantic_threshold <= 1.0:
        raise ValueError(
            f"semantic_threshold must be in [0, 1], got {semantic_threshold}"
        )

    # Layer 1 — groundedness gate (eb_verify.groundedness: verbatim span
    # matching, workspace containment, MIN_SPAN_CHARS=20; one call so the
    # per-file content cache is shared across facts).
    citations = [
        Citation(
            repo=fact["evidence"]["repo"],
            file=fact["evidence"]["file"],
            evidence_span=fact["evidence"]["span"],
        )
        for fact in agent_facts
    ]
    grounding = check_groundedness(citations, workspace)
    survivors: list[tuple[int, dict]] = []
    discarded: list[DiscardedFact] = []
    for i, (fact, result) in enumerate(zip(agent_facts, grounding.results)):
        if result.grounded:
            survivors.append((i, fact))
        else:
            discarded.append(DiscardedFact(index=i, reason=result.reason))

    n_facts = len(agent_facts)
    n_grounded = len(survivors)
    ungrounded_fraction = (n_facts - n_grounded) / n_facts if n_facts else 0.0

    # Layer 2 — coverage (one-to-one matching, exact triples first)
    matches: dict[int, GtMatch] = {}
    used_candidates: set[int] = set()

    triple_index: dict[tuple[str, str, str], int] = {}
    for survivor_pos, (_, fact) in enumerate(survivors):
        triple_index.setdefault(_canonical_triple(fact), survivor_pos)

    for gt_i, gt in enumerate(gt_facts):
        cand_pos = triple_index.get(_canonical_triple(gt))
        if cand_pos is not None and cand_pos not in used_candidates:
            used_candidates.add(cand_pos)
            matches[gt_i] = GtMatch(
                gt_index=gt_i,
                mechanism="exact",
                candidate_index=survivors[cand_pos][0],
                similarity=None,
            )

    unmatched_gt = [i for i in range(len(gt_facts)) if i not in matches]
    free_candidates = [p for p in range(n_grounded) if p not in used_candidates]
    if unmatched_gt and free_candidates:
        import numpy as np

        backend = similarity if similarity is not None else TfidfStatementSimilarity()
        sims = np.asarray(
            backend.pairwise(
                [gt_facts[i]["statement"] for i in unmatched_gt],
                [survivors[p][1]["statement"] for p in free_candidates],
            ),
            dtype=float,
        )
        expected_shape = (len(unmatched_gt), len(free_candidates))
        if sims.shape != expected_shape:
            raise ValueError(
                f"similarity backend returned shape {sims.shape}, expected {expected_shape}"
            )
        # Greedy one-to-one assignment by descending similarity
        pairs = sorted(
            ((sims[r, c], r, c) for r in range(sims.shape[0]) for c in range(sims.shape[1])),
            key=lambda t: -t[0],
        )
        taken_rows: set[int] = set()
        taken_cols: set[int] = set()
        for sim_val, r, c in pairs:
            if sim_val < semantic_threshold:
                break
            if r in taken_rows or c in taken_cols:
                continue
            taken_rows.add(r)
            taken_cols.add(c)
            gt_i = unmatched_gt[r]
            cand_pos = free_candidates[c]
            used_candidates.add(cand_pos)
            matches[gt_i] = GtMatch(
                gt_index=gt_i,
                mechanism="semantic",
                candidate_index=survivors[cand_pos][0],
                similarity=round(float(sim_val), 4),
            )

    gt_matches = tuple(
        matches.get(i, GtMatch(gt_index=i, mechanism=None, candidate_index=None, similarity=None))
        for i in range(len(gt_facts))
    )
    n_gt_matched = len(matches)
    recall = n_gt_matched / len(gt_facts)

    # Layer 3 — precision penalty
    unmatched_fraction = (
        (n_grounded - len(used_candidates)) / n_grounded if n_grounded else 0.0
    )
    total_score = recall * (1 - alpha * unmatched_fraction)

    return FactTriplesScore(
        total_score=total_score,
        recall=recall,
        ungrounded_fraction=ungrounded_fraction,
        unmatched_fraction=unmatched_fraction,
        n_facts=n_facts,
        n_grounded=n_grounded,
        n_gt=len(gt_facts),
        n_gt_matched=n_gt_matched,
        gt_matches=gt_matches,
        discarded=tuple(discarded),
    )


class FactTriplesValidator:
    """Plugin validator for codebase-comprehension fact-triple tasks."""

    artifact_type = "fact_triples"

    def validate(
        self, workspace: Path, *, require_grounded_citations: bool = False
    ) -> ValidationResult:
        """Validate a facts.json submission against workspace + ground truth.

        require_grounded_citations is a documented no-op: this validator
        ALWAYS enforces per-fact groundedness (layer 1 of score_fact_triples).
        The kwarg exists so the runner's capability probe recognizes it and
        tasks can set ground_truth.require_grounded_citations with a
        fact_triples artifact without a false 'unsupported' failure.
        """
        # Agent artifact: facts.json anywhere in the workspace except ground_truth/
        gt_dir = (workspace / "ground_truth").resolve()
        candidates = [
            p for p in sorted(workspace.glob("**/facts.json"))
            if not p.resolve().is_relative_to(gt_dir)
        ]
        if not candidates:
            return ValidationResult(valid=False, detail="No facts.json found")

        try:
            data = json.loads(safe_read(candidates[0], workspace))
        except (json.JSONDecodeError, ValueError) as e:
            return ValidationResult(valid=False, detail=f"Invalid facts.json: {e}")

        errs = schema_errors(data)
        if errs:
            preview = "; ".join(errs[:5])
            return ValidationResult(
                valid=False, detail=f"facts.json failed schema validation: {preview}"
            )

        # Ground truth
        gt_path = workspace / "ground_truth" / "expected_facts.json"
        if not gt_path.exists():
            return ValidationResult(
                valid=False, detail="Ground truth expected_facts.json not found"
            )
        try:
            gt_data = json.loads(safe_read(gt_path, workspace))
        except (json.JSONDecodeError, ValueError) as e:
            return ValidationResult(valid=False, detail=f"Invalid ground truth JSON: {e}")

        gt_errs = schema_errors(gt_data, GT_FACTS_SCHEMA)
        if gt_errs:
            preview = "; ".join(gt_errs[:5])
            return ValidationResult(
                valid=False,
                detail=f"expected_facts.json failed schema validation: {preview}",
            )

        # Registration probes the deps with find_spec, which resolves without executing,
        # so a present-but-broken install (ABI mismatch, truncated wheel) survives it and
        # fails here instead. Load the stack behind one guard before scoring: callers
        # (runner.validate_artifacts, cli) invoke validate() unguarded, so letting this
        # escape would abort the whole verification run over a dependency fault that is
        # not the agent's doing.
        try:
            load_scoring_deps()
        except ScoringDepsUnavailable as exc:
            return ValidationResult(
                valid=False,
                detail=f"fact scoring unavailable (dependency: {exc})",
            )

        score = score_fact_triples(data["facts"], gt_data["facts"], workspace)

        mechanisms = ",".join(
            f"gt{m.gt_index}={m.mechanism or 'unmatched'}" for m in score.gt_matches
        )
        detail = (
            f"score={score.total_score:.3f} recall={score.recall:.3f} "
            f"ungrounded={score.ungrounded_fraction:.3f} "
            f"unmatched={score.unmatched_fraction:.3f} "
            f"facts={score.n_facts} grounded={score.n_grounded} "
            f"gt_matched={score.n_gt_matched}/{score.n_gt} [{mechanisms}]"
        )
        return ValidationResult(valid=score.total_score > 0.0, detail=detail)
