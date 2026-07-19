#!/usr/bin/env python3
"""Blinded PR-review-like judging pilot (EnterpriseBench-rryas.10).

Runs the existing Tier-2 LLM judge (``lib/eb_verify/judge/``) as a SECONDARY,
blinded reviewer over already-scored headline-run artifacts, and measures how
often it agrees with the deterministic reward. It never blends into the
deterministic score; it emits a parallel ledger and an agreement report.

Design (see docs/internal/rryas10_blinded_judge_pilot.md for the full protocol):

  * Blinding. The judge sees only the arm-neutral final answer (answer.json)
    and the curated expected_solution per checkpoint. Arm identity (baseline /
    mcp_only / hybrid / cli) is withheld from the judge entirely -- it is
    carried alongside each unit for post-hoc analysis only, never into the
    prompt. Units are shuffled under a fixed seed so ordering leaks nothing.
  * Reuse, not reinvention. Rubric = the repo's CHECKPOINT_EVAL_PROMPT; engine
    = LLMJudge. The only net-new pieces are (a) reading arm-neutral artifacts,
    (b) randomization, (c) persisting the reasoning/evidence/confidence that the
    production runner discards (eb_verify/runner.py returns only result.score).
  * No primary-reward contamination. This script reads run_log.json's
    deterministic total_score read-only and writes a separate ledger. It does
    not touch any scoring path.

Usage:
  PYTHONPATH=lib python3 scripts/analysis/rryas10_blinded_judge_pilot.py \
      --runs-root results/sample_runs --model cc:haiku --seed 20260719 \
      --out results/analysis/rryas10_pilot

The deterministic reward is authoritative for the benchmark. This pilot only
asks a separate question: does an independent blinded reviewer corroborate it?
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import random
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# eb_verify is importable via PYTHONPATH=lib (see conftest / CLAUDE.md).
from eb_verify.judge import CheckpointJudgeInput, LLMJudge
from eb_verify.judge.backends import JudgeBackendError
from eb_verify.judge.models import JudgeScoreError

# Default repo root = two levels up from scripts/analysis/. Overridable via
# --repo-root so the pilot can read artifacts/fixtures from a sibling checkout
# (e.g. run the worktree's script against the base tree's not-yet-committed
# expected_solution.json fixtures) without copying anything.
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Unit:
    """One (task, arm) artifact to be judged, plus its held-out arm label."""

    task_id: str
    arm: str  # held out from the judge; used only for post-hoc analysis
    answer_path: str
    task_dir: str  # benchmark dir holding expected_solution.json
    deterministic_total: Optional[float]
    deterministic_checkpoints: dict = field(default_factory=dict)  # name -> {score,weight}


@dataclass
class CheckpointVerdict:
    blind_id: str
    checkpoint: str
    judge_score: float
    judge_passed: bool
    deterministic_score: Optional[float]
    weight: float
    reasoning: str
    evidence: str
    confidence: str


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _find_task_dir(task_id: str) -> Optional[str]:
    """Resolve a task_id to its benchmark dir (holds expected_solution.json)."""
    hits = glob.glob(
        str(REPO_ROOT / "benchmarks" / "**" / task_id / "expected_solution.json"),
        recursive=True,
    )
    hits = [h for h in hits if "_archived" not in h]
    return str(Path(hits[0]).parent) if hits else None


def discover_units(runs_root: Path) -> list[Unit]:
    """Find every answer.json under runs_root and pair it with its arm + scores.

    Layout handled: <runs_root>/<group>/<arm>/<task_id>/workspace/agent_output/
    answer.json, with a sibling run_log.json carrying the deterministic score.
    """
    units: list[Unit] = []
    for answer_path in sorted(runs_root.glob("**/agent_output/answer.json")):
        # .../<arm>/<task_id>/workspace/agent_output/answer.json
        task_run_dir = answer_path.parents[2]
        task_id = task_run_dir.name
        arm = task_run_dir.parent.name
        run_log = _read_json(str(task_run_dir / "run_log.json")) or {}
        # Prefer the arm recorded in the artifact; fall back to the path.
        arm = run_log.get("mode", arm)
        det_cps = {
            cp["name"]: {"score": cp.get("score"), "weight": cp.get("weight", 1.0)}
            for cp in run_log.get("checkpoints", [])
            if "name" in cp
        }
        task_dir = _find_task_dir(task_id)
        if task_dir is None:
            print(f"[skip] no expected_solution.json for {task_id}", file=sys.stderr)
            continue
        units.append(
            Unit(
                task_id=task_id,
                arm=arm,
                answer_path=str(answer_path),
                task_dir=task_dir,
                deterministic_total=run_log.get("total_score"),
                deterministic_checkpoints=det_cps,
            )
        )
    return units


def _assert_arm_neutral(payload: str) -> None:
    """Fail closed if the blinded payload names the arm the judge must not see.

    The final answer.json is arm-neutral by construction, but a task author
    could in principle echo the mode into it. Refuse rather than silently leak.
    """
    needles = {"baseline", "mcp_only", "hybrid", "cli", "sgx"}
    low = payload.lower()
    leaked = [n for n in needles if f'"mode": "{n}"' in low or f"mode={n}" in low]
    if leaked:
        raise ValueError(
            f"arm tell {leaked!r} present in blinded payload -- refusing to judge"
        )


def build_blinded_inputs(unit: Unit) -> list[tuple[str, CheckpointJudgeInput]]:
    """Per checkpoint: (checkpoint name, blinded judge input).

    The CheckpointJudgeInput carries only answer text + expected solution +
    criteria. No arm field exists on it (verified against models.py), so
    blinding here is: (1) read the arm-neutral answer, (2) never attach mode.
    """
    answer_raw = Path(unit.answer_path).read_text()
    _assert_arm_neutral(answer_raw)
    expected = _read_json(
        str(Path(unit.task_dir) / "expected_solution.json")
    ) or {}
    out: list[tuple[str, CheckpointJudgeInput]] = []
    for name, cp in expected.get("checkpoints", {}).items():
        out.append(
            (
                name,
                CheckpointJudgeInput(
                    task_id=unit.task_id,
                    checkpoint_name=name,
                    agent_output=answer_raw,
                    expected_solution=cp.get("expected_solution", ""),
                    evaluation_criteria=cp.get("evaluation_criteria", []),
                    checkpoint_weight=unit.deterministic_checkpoints.get(name, {}).get(
                        "weight", 1.0
                    ),
                ),
            )
        )
    return out


def run_pilot(units: list[Unit], model: str, seed: int, out_dir: Path) -> dict:
    """Judge every unit in shuffled order, blind to arm; write ledger + report."""
    judge = LLMJudge(model=model, temperature=0.0)
    rng = random.Random(seed)

    # Assign opaque blind ids and shuffle so processing order leaks no arm.
    blind_map: dict[str, Unit] = {}
    for i, unit in enumerate(units):
        blind_map[f"U{i:04d}"] = unit
    order = list(blind_map.keys())
    rng.shuffle(order)

    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "verdicts.jsonl"
    verdicts: list[CheckpointVerdict] = []
    infra_errors: list[dict] = []

    with open(ledger_path, "w") as ledger:
        for blind_id in order:
            unit = blind_map[blind_id]
            for name, judge_input in build_blinded_inputs(unit):
                det = unit.deterministic_checkpoints.get(name, {})
                try:
                    res = judge.evaluate_checkpoint(
                        judge_input, task_description="", checkpoint_description=name
                    )
                except (JudgeBackendError, JudgeScoreError) as exc:
                    # No verdict reached. Do NOT coerce to a score (scorer_guard
                    # invariant): record as infra error and move on.
                    infra_errors.append(
                        {"blind_id": blind_id, "checkpoint": name, "error": str(exc)}
                    )
                    continue
                v = CheckpointVerdict(
                    blind_id=blind_id,
                    checkpoint=name,
                    judge_score=res.score,
                    judge_passed=res.passed,
                    deterministic_score=det.get("score"),
                    weight=det.get("weight", 1.0),
                    reasoning=res.reasoning,
                    evidence=res.evidence,
                    confidence=res.confidence,
                )
                verdicts.append(v)
                # Ledger row reveals arm ONLY here (post-hoc), never to the judge.
                row = asdict(v) | {"task_id": unit.task_id, "arm": unit.arm}
                ledger.write(json.dumps(row) + "\n")

    report = _agreement_report(blind_map, verdicts, model, seed, infra_errors)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report


def _weighted_total(pairs: list[tuple[float, float]]) -> Optional[float]:
    tot_w = sum(w for _, w in pairs)
    if tot_w == 0:
        return None
    return sum(s * w for s, w in pairs) / tot_w


def _agreement_report(
    blind_map: dict[str, Unit],
    verdicts: list[CheckpointVerdict],
    model: str,
    seed: int,
    infra_errors: list[dict],
) -> dict:
    """Report judge-vs-deterministic agreement. No blending: parallel view."""
    paired = [
        v for v in verdicts if v.deterministic_score is not None
    ]
    cp_abs_diffs = [abs(v.judge_score - v.deterministic_score) for v in paired]
    # Binary pass-agreement at the deterministic 0.5-pass line.
    pass_agree = sum(
        1
        for v in paired
        if (v.judge_score >= 0.5) == (v.deterministic_score >= 0.5)
    )
    # Per (task, arm) weighted totals: judge vs deterministic run_log total.
    by_unit: dict[str, list[CheckpointVerdict]] = {}
    for v in verdicts:
        by_unit.setdefault(v.blind_id, []).append(v)
    unit_rows = []
    for blind_id, vs in by_unit.items():
        unit = blind_map[blind_id]
        judge_total = _weighted_total(
            [(v.judge_score, v.weight) for v in vs if v.deterministic_score is not None]
        )
        unit_rows.append(
            {
                "task_id": unit.task_id,
                "arm": unit.arm,
                "judge_total": judge_total,
                "deterministic_total": unit.deterministic_total,
                "delta": (
                    None
                    if judge_total is None or unit.deterministic_total is None
                    else round(judge_total - unit.deterministic_total, 3)
                ),
            }
        )
    unit_deltas = [r["delta"] for r in unit_rows if r["delta"] is not None]

    def _stats(xs: list[float]) -> dict:
        if not xs:
            return {"n": 0}
        return {
            "n": len(xs),
            "mean": round(statistics.fmean(xs), 3),
            "median": round(statistics.median(xs), 3),
            "max": round(max(xs), 3),
        }

    return {
        "config": {
            "model": model,
            "seed": seed,
            "rubric": "eb_verify.judge.prompts.CHECKPOINT_EVAL_PROMPT",
            "rubric_sha256": _rubric_hash(),
            "pass_threshold": 0.5,
            "blending": "none (secondary outcome, parallel to deterministic reward)",
        },
        "sample": {
            "units": len(blind_map),
            "arms": sorted({u.arm for u in blind_map.values()}),
            "checkpoints_judged": len(verdicts),
            "checkpoints_paired_with_deterministic": len(paired),
            "infra_errors": len(infra_errors),
        },
        "checkpoint_agreement": {
            "abs_diff": _stats(cp_abs_diffs),
            "pass_agreement_rate": (
                round(pass_agree / len(paired), 3) if paired else None
            ),
        },
        "unit_total_agreement": {"abs_delta": _stats([abs(d) for d in unit_deltas])},
        "units": sorted(unit_rows, key=lambda r: (r["task_id"], r["arm"])),
        "infra_errors_detail": infra_errors,
    }


def _rubric_hash() -> str:
    from eb_verify.judge.prompts import CHECKPOINT_EVAL_PROMPT, CHECKPOINT_EVAL_SYSTEM

    h = hashlib.sha256()
    h.update(CHECKPOINT_EVAL_SYSTEM.encode())
    h.update(CHECKPOINT_EVAL_PROMPT.encode())
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo-root",
        default=None,
        help="resolve benchmarks/ + runs-root here (default: this checkout)",
    )
    ap.add_argument("--runs-root", default="results/sample_runs")
    ap.add_argument("--model", default="cc:haiku")
    ap.add_argument("--seed", type=int, default=20260719)
    ap.add_argument("--out", default="results/analysis/rryas10_pilot")
    ap.add_argument(
        "--limit", type=int, default=0, help="cap number of units (0 = all)"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="discover + blind-check units, but do not call the judge",
    )
    args = ap.parse_args()

    if args.repo_root:
        global REPO_ROOT
        REPO_ROOT = Path(args.repo_root).resolve()

    runs_root = (REPO_ROOT / args.runs_root).resolve()
    units = discover_units(runs_root)
    if args.limit:
        # Keep arms balanced under the cap: interleave by arm.
        by_arm: dict[str, list[Unit]] = {}
        for u in units:
            by_arm.setdefault(u.arm, []).append(u)
        interleaved: list[Unit] = []
        while len(interleaved) < args.limit and any(by_arm.values()):
            for arm in sorted(by_arm):
                if by_arm[arm] and len(interleaved) < args.limit:
                    interleaved.append(by_arm[arm].pop(0))
        units = interleaved

    print(
        f"[pilot] {len(units)} units across arms "
        f"{sorted({u.arm for u in units})} from {runs_root}",
        file=sys.stderr,
    )
    if args.dry_run:
        for u in units:
            build_blinded_inputs(u)  # exercises the arm-neutral assertion
        print("[pilot] dry-run OK: units discovered and blind-checked", file=sys.stderr)
        return 0

    report = run_pilot(units, args.model, args.seed, (REPO_ROOT / args.out).resolve())
    print(json.dumps(report["checkpoint_agreement"], indent=2))
    print(json.dumps(report["unit_total_agreement"], indent=2))
    print(f"[pilot] ledger + report written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
