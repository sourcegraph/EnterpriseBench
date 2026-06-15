"""
answer validator — oracle matching (keywords, files, symbols).
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from eb_verify.plugins import ValidationResult, safe_read


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


def _referenced(needle: str, haystack: str) -> bool:
    """Whether *needle* appears in *haystack* as a whole token (word-boundary
    anchored). Anchoring kills unanchored-substring inflation — e.g. the
    basename ``id.py`` must not be credited for appearing inside ``valid.python``
    — in the same spirit as the suffix policy in file_extraction.py."""
    return re.search(r"\b" + re.escape(needle) + r"\b", haystack) is not None


def file_path_match_score(answer_text: str, expected_files: list[str]) -> float:
    """Return fraction of expected file paths referenced in answer."""
    if not expected_files:
        return 1.0
    hits = 0
    for fp in expected_files:
        # Match the full path or just the basename, anchored on word boundaries.
        basename = fp.rsplit("/", 1)[-1] if "/" in fp else fp
        if _referenced(fp, answer_text) or _referenced(basename, answer_text):
            hits += 1
    return hits / len(expected_files)


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
) -> tuple[float, dict[str, float]]:
    """Score an answer against ground_truth oracle data.

    ground_truth may contain:
        keywords: list[str]
        symbols: list[str]
        expected_files: list[str]
        expected_answer: str  (for fuzzy matching)

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
        scores["file_path"] = file_path_match_score(answer_text, ground_truth["expected_files"])
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
    ) -> ValidationResult:
        """
        Validate an answer artifact exists, has valid structure, and optionally
        score it against oracle ground_truth data.

        When *ground_truth* is provided, oracle matching is performed:
        - keyword matching against expected values
        - symbol matching (function/class names)
        - file path matching
        - fuzzy string matching for near-matches
        """
        json_candidates = list(workspace.glob("**/answer.json"))
        txt_candidates = list(workspace.glob("**/answer.txt"))

        answer_text: Optional[str] = None

        if json_candidates:
            try:
                data = json.loads(safe_read(json_candidates[0], workspace))
                if not isinstance(data, dict):
                    return ValidationResult(valid=False, detail="answer.json should be a JSON object")
                answer_text = _extract_text(data)
            except (json.JSONDecodeError, ValueError) as e:
                return ValidationResult(valid=False, detail=f"answer.json invalid: {e}")
        elif txt_candidates:
            try:
                content = safe_read(txt_candidates[0], workspace).strip()
            except ValueError as e:
                return ValidationResult(valid=False, detail=str(e))
            if not content:
                return ValidationResult(valid=False, detail="answer.txt is empty")
            answer_text = content
        else:
            return ValidationResult(valid=False, detail="No answer file found")

        # Structure-only validation (no oracle)
        if ground_truth is None:
            source = "answer.json" if json_candidates else "answer.txt"
            return ValidationResult(valid=True, detail=f"{source} found and valid")

        # Oracle matching
        score, dimension_scores = oracle_score(answer_text, ground_truth, thresholds)
        min_score = (thresholds or {}).get("min_score", 0.3)

        dims = ", ".join(f"{k}={v:.2f}" for k, v in dimension_scores.items())
        detail = f"oracle_score={score:.2f} ({dims})"
        return ValidationResult(valid=score >= min_score, detail=detail)
