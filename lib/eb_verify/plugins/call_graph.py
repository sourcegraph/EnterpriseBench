"""
call_graph validator — checks that claimed dead code is truly unreachable.

Precision-weighted: false positives (claiming live code is dead) are penalized
more heavily than false negatives (missing actual dead code).

Input via JSON files in workspace:
  - dead_code_report.json: agent's claimed dead code list
  - ground_truth/dead_code.json: actual dead code (ground truth)
  - ground_truth/live_code.json: functions with known callers

Each entry is {"file": str, "symbol": str, "kind": "function"|"class"|"method"}.
Optionally includes "reachability": "direct"|"dynamic"|"reflection"|"conditional".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Sequence

from eb_verify.artifact_io import safe_read, select_unique
from eb_verify.plugins import ValidationResult


@dataclass(frozen=True)
class CodeSymbol:
    """A uniquely identified code symbol."""
    file: str
    symbol: str

    @classmethod
    def from_dict(cls, d: dict) -> CodeSymbol:
        return cls(file=d["file"], symbol=d["symbol"])


@dataclass(frozen=True)
class CallGraphScore:
    """Detailed scoring breakdown for call graph reachability checks."""
    precision: float  # TP / (TP + FP)
    recall: float     # TP / (TP + FN)
    f_score: float    # precision-weighted F-score
    true_positives: int
    false_positives: int
    false_negatives: int
    dynamic_flags: int  # claims on dynamic/reflection-reachable code
    total_score: float  # final score after all adjustments


# Symbols reachable only through these mechanisms get lower-confidence penalties
DYNAMIC_REACHABILITY = frozenset({"dynamic", "reflection", "conditional"})

# Precision weight in F-score (beta < 1 means precision matters more)
PRECISION_BETA = 0.5

# Penalty multiplier for false positives involving dynamic dispatch
DYNAMIC_FP_DISCOUNT = 0.5


def _parse_symbols(raw: list[dict]) -> FrozenSet[CodeSymbol]:
    """Parse a list of symbol dicts into a frozen set of CodeSymbol."""
    return frozenset(CodeSymbol.from_dict(d) for d in raw)


def _get_reachability_map(raw: list[dict]) -> dict[CodeSymbol, str]:
    """Build a map from symbol to reachability type."""
    result: dict[CodeSymbol, str] = {}
    for d in raw:
        sym = CodeSymbol.from_dict(d)
        result[sym] = d.get("reachability", "direct")
    return result


def score_dead_code_claims(
    claimed: Sequence[dict],
    gt_dead: Sequence[dict],
    gt_live: Sequence[dict],
) -> CallGraphScore:
    """Score agent's dead code claims against ground truth.

    Args:
        claimed: Agent's list of claimed dead code symbols.
        gt_dead: Ground truth dead code symbols.
        gt_live: Ground truth live code symbols (known callers exist).

    Returns:
        CallGraphScore with precision-weighted scoring.
    """
    claimed_set = _parse_symbols(list(claimed))
    dead_set = _parse_symbols(list(gt_dead))
    live_reachability = _get_reachability_map(list(gt_live))
    live_set = frozenset(live_reachability.keys())

    # True positives: claimed dead AND actually dead
    true_positives = claimed_set & dead_set

    # False positives: claimed dead BUT actually live
    false_positives = claimed_set & live_set

    # Count how many FPs are dynamic/reflection reachable
    dynamic_flags = sum(
        1 for sym in false_positives
        if live_reachability.get(sym, "direct") in DYNAMIC_REACHABILITY
    )

    # False negatives: actually dead but not claimed
    false_negatives = dead_set - claimed_set

    tp = len(true_positives)
    fp = len(false_positives)
    fn = len(false_negatives)

    # Discount FPs for dynamic dispatch (lower confidence penalty)
    effective_fp = fp - dynamic_flags + dynamic_flags * DYNAMIC_FP_DISCOUNT

    # Precision-weighted F-score (F_beta with beta < 1)
    beta_sq = PRECISION_BETA ** 2
    if tp + effective_fp == 0:
        precision = 0.0
    else:
        precision = tp / (tp + effective_fp)

    if tp + fn == 0:
        recall = 0.0
    else:
        recall = tp / (tp + fn)

    if precision + recall == 0:
        f_score = 0.0
    else:
        f_score = (1 + beta_sq) * (precision * recall) / (beta_sq * precision + recall)

    # Empty submission gets near-zero
    if len(claimed_set) == 0:
        total_score = 0.0
    else:
        total_score = f_score

    return CallGraphScore(
        precision=precision,
        recall=recall,
        f_score=f_score,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        dynamic_flags=dynamic_flags,
        total_score=total_score,
    )


class CallGraphValidator:
    """Plugin validator for dead code / call graph reachability tasks."""

    artifact_type = "call_graph"

    def validate(self, workspace: Path) -> ValidationResult:
        """Validate dead code claims in the workspace."""
        # Find agent's report (decoy-shadow-safe: reject >1 candidate as
        # ambiguous). Only the agent report is globbed; the sealed ground_truth/
        # reads below stay direct so a planted file cannot shadow the truth.
        report_path, sel_err = select_unique(workspace, ["dead_code_report.json"])
        if sel_err is not None:
            return ValidationResult(valid=False, detail=sel_err)
        if report_path is None:
            return ValidationResult(
                valid=False, detail="No dead_code_report.json found"
            )

        try:
            claimed = json.loads(safe_read(report_path, workspace))
        except (json.JSONDecodeError, ValueError) as e:
            return ValidationResult(valid=False, detail=f"Invalid report JSON: {e}")

        if not isinstance(claimed, list):
            return ValidationResult(
                valid=False, detail="dead_code_report.json must be a JSON array"
            )

        # Find ground truth files
        gt_dir = workspace / "ground_truth"
        gt_dead_path = gt_dir / "dead_code.json"
        gt_live_path = gt_dir / "live_code.json"

        if not gt_dead_path.exists():
            return ValidationResult(
                valid=False, detail="Ground truth dead_code.json not found"
            )
        if not gt_live_path.exists():
            return ValidationResult(
                valid=False, detail="Ground truth live_code.json not found"
            )

        try:
            gt_dead = json.loads(safe_read(gt_dead_path, workspace))
            gt_live = json.loads(safe_read(gt_live_path, workspace))
        except (json.JSONDecodeError, ValueError) as e:
            return ValidationResult(
                valid=False, detail=f"Invalid ground truth JSON: {e}"
            )

        result = score_dead_code_claims(claimed, gt_dead, gt_live)

        detail = (
            f"score={result.total_score:.3f} "
            f"precision={result.precision:.3f} recall={result.recall:.3f} "
            f"f={result.f_score:.3f} "
            f"TP={result.true_positives} FP={result.false_positives} "
            f"FN={result.false_negatives} dynamic_flags={result.dynamic_flags}"
        )

        return ValidationResult(
            valid=result.total_score > 0.0,
            detail=detail,
        )
