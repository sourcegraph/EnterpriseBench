"""
answer validator — oracle matching (keywords, files, symbols).

Citations convention (require_grounded_citations=True):
    Citations live in a top-level ``citations`` list in answer.json, each
    entry ``{"repo": ..., "file": ..., "evidence_span": ...}`` — the same
    schema and seam as incident_report, so agents learn one format. The
    answer artifact is otherwise free-form JSON (there is no guaranteed
    per-claim structure to attach evidence to), which makes the top level
    the only stable seam. answer.txt cannot carry a structured list, so a
    txt-only workspace fails the gate with an explicit reason.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from eb_verify.groundedness import CitationParseError, ground_citations
from eb_verify.artifact_io import safe_read, select_unique
from eb_verify.plugins import ValidationResult
from eb_verify.plugins.path_match import extract_claimed_paths, path_match_score

# The graded answer is pinned to /workspace/agent_output/ — the file the check
# scripts (test_runner.sh:373) and run_task's grounding gate
# (ANSWER_ARTIFACT_PATH) actually read. Preferring it here stops a
# workspace-root or other decoy from shadowing the real artifact, since
# pathlib.glob yields the anchor directory before subdirectories.
PINNED_ANSWER_SUBDIR = "agent_output"


def _select_answer_file(workspace: Path, name: str) -> tuple[Optional[Path], Optional[str]]:
    """Choose the answer file to grade for *name* (``answer.json`` / ``answer.txt``).

    Prefers the pinned ``agent_output/<name>`` so a decoy elsewhere in the
    workspace cannot shadow the file every check actually grades. Falls back to
    a workspace-wide glob only when the pinned file is absent, and in that
    fallback rejects ambiguity (>1 candidate) instead of silently grading the
    first glob hit.

    Returns ``(path, error)``: at most one is non-None. Both are None when no
    candidate exists (so the caller can try the other artifact type).
    """
    pinned = workspace / PINNED_ANSWER_SUBDIR / name
    if pinned.is_file():
        return pinned, None
    # No pinned file: fall back to the shared decoy-shadow-safe selector, which
    # rejects >1 glob candidate as ambiguous rather than grading candidates[0].
    path, err = select_unique(workspace, [name])
    if err is not None:
        # Restore the pinned-subdir remedy the generic message omits: for the
        # answer artifact the fix is "write it to agent_output/", discoverable
        # from the pin convention, not merely "write exactly one".
        err = f"{err} (write the graded answer to {PINNED_ANSWER_SUBDIR}/{name})"
    return path, err


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace for comparison."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _extract_text(data: Any) -> str:
    """Extract all text content from an answer (dict, str, or nested)."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        parts = []
        for value in data.values():
            parts.append(_extract_text(value))
        return " ".join(parts)
    if isinstance(data, list):
        return " ".join(_extract_text(item) for item in data)
    return str(data)


def keyword_match_score(answer_text: str, keywords: list[str]) -> float:
    """Return fraction of keywords found in answer_text (case-insensitive)."""
    if not keywords:
        return 1.0
    normalized = _normalize(answer_text)
    hits = sum(1 for kw in keywords if _normalize(kw) in normalized)
    return hits / len(keywords)


def symbol_match_score(answer_text: str, symbols: list[str]) -> float:
    """Return fraction of symbol names (functions, classes) found in answer."""
    if not symbols:
        return 1.0
    hits = 0
    for sym in symbols:
        # Exact word-boundary match for symbol names
        pattern = re.compile(r"\b" + re.escape(sym) + r"\b")
        if pattern.search(answer_text):
            hits += 1
    return hits / len(symbols)


def file_path_match_score(claimed_paths: list[str], expected_files: list[str]) -> float:
    """Precision-aware score for *claimed_paths* against *expected_files*.

    Delegates to the path_match primitive. *claimed_paths* is a STRUCTURED list
    of path strings (see :func:`extract_claimed_paths`), not flattened answer
    text — recall-only substring matching over flattened text is the defect this
    replaces. A bare string first argument is a caller using the old free-text
    convention; fail loud rather than silently scoring 0.
    """
    if isinstance(claimed_paths, str):
        raise TypeError(
            "file_path_match_score expects a structured list of claimed paths, "
            "not flattened answer text (EnterpriseBench-6py4v)"
        )
    return path_match_score(claimed_paths, expected_files)


def fuzzy_match_score(answer_text: str, expected: str, threshold: float = 0.6) -> float:
    """Return SequenceMatcher ratio between answer and expected text.

    Returns 0.0 if the ratio is below *threshold*, otherwise the raw ratio.
    This avoids giving partial credit for completely unrelated answers.
    """
    ratio = SequenceMatcher(None, _normalize(answer_text), _normalize(expected)).ratio()
    return ratio if ratio >= threshold else 0.0


def oracle_score(
    answer_text: str,
    ground_truth: dict[str, Any],
    thresholds: Optional[dict[str, float]] = None,
    claimed_paths: Optional[list[str]] = None,
) -> tuple[float, dict[str, float]]:
    """Score an answer against ground_truth oracle data.

    ground_truth may contain:
        keywords: list[str]
        symbols: list[str]
        expected_files: list[str]
        expected_answer: str  (for fuzzy matching)

    *claimed_paths* is the agent's STRUCTURED claimed-file list (see
    :func:`extract_claimed_paths`). The file_path dimension scores against this
    list, not flattened *answer_text* — flattening is the recall-only defect
    (EnterpriseBench-6py4v). A missing/empty list scores the file dimension 0
    when expected_files are required.

    Returns (aggregate_score, per_dimension_scores).
    """
    if thresholds is None:
        thresholds = {}

    scores: dict[str, float] = {}
    weights: dict[str, float] = {}

    if "keywords" in ground_truth:
        scores["keyword"] = keyword_match_score(answer_text, ground_truth["keywords"])
        weights["keyword"] = thresholds.get("keyword_weight", 0.3)

    if "symbols" in ground_truth:
        scores["symbol"] = symbol_match_score(answer_text, ground_truth["symbols"])
        weights["symbol"] = thresholds.get("symbol_weight", 0.3)

    if "expected_files" in ground_truth:
        scores["file_path"] = file_path_match_score(
            claimed_paths or [], ground_truth["expected_files"]
        )
        weights["file_path"] = thresholds.get("file_path_weight", 0.2)

    if "expected_answer" in ground_truth:
        fuzzy_threshold = thresholds.get("fuzzy_threshold", 0.6)
        scores["fuzzy"] = fuzzy_match_score(
            answer_text, ground_truth["expected_answer"], threshold=fuzzy_threshold,
        )
        weights["fuzzy"] = thresholds.get("fuzzy_weight", 0.2)

    if not scores:
        return 1.0, {}

    total_weight = sum(weights.values())
    if total_weight == 0:
        return 1.0, scores

    aggregate = sum(scores[k] * weights[k] for k in scores) / total_weight
    return aggregate, scores


class AnswerValidator:
    artifact_type = "answer"

    def validate(
        self,
        workspace: Path,
        ground_truth: Optional[dict[str, Any]] = None,
        thresholds: Optional[dict[str, float]] = None,
        require_grounded_citations: bool = False,
    ) -> ValidationResult:
        """
        Validate an answer artifact exists, has valid structure, and optionally
        score it against oracle ground_truth data.

        When *ground_truth* is provided, oracle matching is performed:
        - keyword matching against expected values
        - symbol matching (function/class names)
        - file path matching
        - fuzzy string matching for near-matches

        When *require_grounded_citations* is True, answer.json must carry a
        top-level ``citations`` list (see module docstring) and every
        evidence_span must appear verbatim in the cited workspace file; the
        gate runs before oracle matching, and missing/malformed citations or
        any ungrounded span fail validation with per-citation reasons.
        """
        json_path, json_err = _select_answer_file(workspace, "answer.json")
        if json_err:
            return ValidationResult(valid=False, detail=json_err)

        answer_text: Optional[str] = None
        data: Optional[dict[str, Any]] = None

        if json_path is not None:
            try:
                data = json.loads(safe_read(json_path, workspace))
                if not isinstance(data, dict):
                    return ValidationResult(valid=False, detail="answer.json should be a JSON object")
                answer_text = _extract_text(data)
            except (json.JSONDecodeError, ValueError) as e:
                return ValidationResult(valid=False, detail=f"answer.json invalid: {e}")
        else:
            txt_path, txt_err = _select_answer_file(workspace, "answer.txt")
            if txt_err:
                return ValidationResult(valid=False, detail=txt_err)
            if txt_path is None:
                return ValidationResult(valid=False, detail="No answer file found")
            try:
                content = safe_read(txt_path, workspace).strip()
            except ValueError as e:
                return ValidationResult(valid=False, detail=str(e))
            if not content:
                return ValidationResult(valid=False, detail="answer.txt is empty")
            answer_text = content

        # Optional groundedness gate (task ground truth: require_grounded_citations)
        if require_grounded_citations:
            if data is None:
                return ValidationResult(
                    valid=False,
                    detail=(
                        "grounded citations require answer.json with a top-level "
                        "'citations' list; answer.txt cannot carry structured citations"
                    ),
                )
            try:
                grounded = ground_citations(data, workspace)
            except CitationParseError as e:
                # Malformed/missing citations surface as explicit issues -> invalid.
                return ValidationResult(valid=False, detail="; ".join(e.issues))
            if not grounded.all_grounded:
                return ValidationResult(
                    valid=False, detail=f"ungrounded citations: {grounded.summary()}"
                )

        # Structure-only validation (no oracle)
        if ground_truth is None:
            source = "answer.json" if json_path is not None else "answer.txt"
            return ValidationResult(valid=True, detail=f"{source} found and valid")

        # Oracle matching. The file_path dimension scores against the agent's
        # STRUCTURED claimed-file list, not the flattened answer text.
        claimed_paths = extract_claimed_paths(data)
        score, dimension_scores = oracle_score(
            answer_text, ground_truth, thresholds, claimed_paths=claimed_paths
        )
        min_score = (thresholds or {}).get("min_score", 0.3)

        dims = ", ".join(f"{k}={v:.2f}" for k, v in dimension_scores.items())
        detail = f"oracle_score={score:.2f} ({dims})"
        return ValidationResult(valid=score >= min_score, detail=detail)
