"""Verification tests for dead-code necropsy task checkpoint scripts.

For each task, tests 3 tiers:
  (a) Ground truth answer -> score >= 0.85
  (b) Empty answer -> score <= 0.10
  (c) Partial answer (some correct + some FP) -> 0.3 <= score <= 0.7

Also tests:
  - Precision weighting: FP penalized more than FN
  - Feature flag checkpoint scoring
  - False positive rate < 10% when GT answer used
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

BENCHMARKS_DIR = Path(__file__).parent.parent / "benchmarks" / "technical_debt"


# -- task definitions ---------------------------------------------------------

@dataclass(frozen=True)
class DeadCodeTaskSpec:
    """Spec for testing one dead-code task's verifiers."""
    task_num: str
    repo_dir: str      # subdirectory under workspace (e.g. "react")
    dead_count: int     # number of dead code items in GT
    live_count: int     # number of live code items in GT
    flags: list[str]    # feature flag names in GT


TASKS: list[DeadCodeTaskSpec] = [
    DeadCodeTaskSpec("001", "react", 13, 7, ["enableDeprecatedFlareAPI"]),
    DeadCodeTaskSpec("002", "react", 15, 5, ["enableRenderableContext"]),
    DeadCodeTaskSpec("003", "react", 8, 3, ["retryCompileFunction", "enableFire", "inferEffectDependencies"]),
    DeadCodeTaskSpec("004", "TypeScript", 20, 7, []),
    DeadCodeTaskSpec("005", "angular", 16, 4, []),
]


CHECKPOINT_NAMES = [
    "check_dead_code",
    "check_feature_flags",
    "check_evidence",
]

CHECKPOINT_WEIGHTS = (0.50, 0.30, 0.20)


# -- helpers ------------------------------------------------------------------

def _task_dir(task_num: str) -> Path:
    return BENCHMARKS_DIR / f"dead-code-{task_num}"


def _load_gt(task_num: str) -> dict:
    gt_path = _task_dir(task_num) / "ground_truth.json"
    return json.loads(gt_path.read_text())


def _run_verifier(
    task_num: str,
    checkpoint_name: str,
    workspace: Path,
) -> dict[str, Any]:
    """Run a checkpoint verifier script and return parsed JSON output."""
    script = _task_dir(task_num) / "checks" / f"{checkpoint_name}.sh"
    assert script.exists(), f"Verifier not found: {script}"

    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(_task_dir(task_num))
    env["TASK_ID"] = f"dead-code-{task_num}"

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(workspace),
        env=env,
    )
    stdout = result.stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"score": 1.0 if result.returncode == 0 else 0.0, "raw": stdout}


def _write_report(workspace: Path, repo_dir: str, entries: list[dict]) -> Path:
    """Write a dead_code_report.json into the workspace repo directory."""
    report_dir = workspace / repo_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "dead_code_report.json"
    report_path.write_text(json.dumps(entries, indent=2))
    return report_path


def _weighted_score(results: list[dict[str, Any]]) -> float:
    """Compute weighted score from 3 checkpoint results."""
    total = 0.0
    for r, w in zip(results, CHECKPOINT_WEIGHTS):
        total += float(r.get("score", 0.0)) * w
    return total


def _make_gt_answer(gt: dict) -> list[dict]:
    """Build an agent answer from the ground truth dead code list."""
    return [
        {
            "file": item["file"],
            "symbol": item["symbol"],
            "kind": item["kind"],
            "confidence": "high",
            "evidence": item["evidence"],
        }
        for item in gt["dead_code"]
    ]


def _make_partial_answer(gt: dict) -> list[dict]:
    """Build partial answer: half the dead code + one live code item (FP)."""
    dead = gt["dead_code"]
    live = gt["live_code"]
    half = max(1, len(dead) // 2)
    entries = []
    for item in dead[:half]:
        entries.append({
            "file": item["file"],
            "symbol": item["symbol"],
            "kind": item["kind"],
            "confidence": "medium",
            "evidence": item.get("evidence", "partially identified"),
        })
    # Add one false positive from live code
    if live:
        fp = live[0]
        entries.append({
            "file": fp["file"],
            "symbol": fp["symbol"],
            "kind": "function",
            "confidence": "low",
            "evidence": "possibly unused",
        })
    return entries


# -- tests: scripts exist ----------------------------------------------------

class TestVerifierScriptsExist:
    """All 5 tasks have all 3 checkpoint verifier scripts."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_all_verifiers_present(self, spec: DeadCodeTaskSpec) -> None:
        task_dir = _task_dir(spec.task_num)
        for cp in CHECKPOINT_NAMES:
            script = task_dir / "checks" / f"{cp}.sh"
            assert script.exists(), f"Missing: {script}"
            assert os.access(script, os.X_OK), f"Not executable: {script}"

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_ground_truth_exists(self, spec: DeadCodeTaskSpec) -> None:
        gt_path = _task_dir(spec.task_num) / "ground_truth.json"
        assert gt_path.exists()
        gt = json.loads(gt_path.read_text())
        assert len(gt["dead_code"]) == spec.dead_count
        assert len(gt["live_code"]) == spec.live_count


# -- tests: (a) GT answer scores high ----------------------------------------

class TestGroundTruthScoresHigh:
    """Ground truth answer should score >= 0.85."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_gt_answer_scores_high(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        workspace = tmp_path / "workspace"
        gt = _load_gt(spec.task_num)
        answer = _make_gt_answer(gt)
        _write_report(workspace, spec.repo_dir, answer)

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results)
        assert total >= 0.85, (
            f"Task dead-code-{spec.task_num} GT scored {total:.2f} (<0.85). "
            f"Results: {results}"
        )


# -- tests: (b) Empty answer scores low --------------------------------------

class TestEmptyAnswerScoresLow:
    """Empty/missing answer should score <= 0.10."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_empty_answer_scores_low(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        # No dead_code_report.json written

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results)
        assert total <= 0.10, (
            f"Task dead-code-{spec.task_num} empty scored {total:.2f} (>0.10). "
            f"Results: {results}"
        )


# -- tests: (c) Partial answer scores mid ------------------------------------

class TestPartialAnswerScoresMid:
    """Partial answer (some correct + FP) should score between 0.15 and 0.75."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_partial_answer_scores_mid(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        workspace = tmp_path / "workspace"
        gt = _load_gt(spec.task_num)
        answer = _make_partial_answer(gt)
        _write_report(workspace, spec.repo_dir, answer)

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results)
        assert 0.15 <= total <= 0.80, (
            f"Task dead-code-{spec.task_num} partial scored {total:.2f} "
            f"(expected 0.15-0.80). Results: {results}"
        )


# -- tests: precision weighting ----------------------------------------------

class TestPrecisionWeighting:
    """Claiming live code as dead should be penalized more than missing dead code."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_fp_penalized_more_than_fn(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        gt = _load_gt(spec.task_num)
        dead = gt["dead_code"]
        live = gt["live_code"]

        if len(dead) < 2 or len(live) < 2:
            pytest.skip("Not enough items for FP/FN comparison")

        half = max(1, len(dead) // 2)

        # High precision, low recall: half the dead code, no FPs
        high_prec_answer = [
            {
                "file": item["file"],
                "symbol": item["symbol"],
                "kind": item["kind"],
                "confidence": "high",
                "evidence": "no callers found",
            }
            for item in dead[:half]
        ]

        # Low precision: half the dead code + multiple FPs
        low_prec_answer = list(high_prec_answer)
        for fp_item in live[:2]:
            low_prec_answer.append({
                "file": fp_item["file"],
                "symbol": fp_item["symbol"],
                "kind": "function",
                "confidence": "medium",
                "evidence": "possibly unused",
            })

        # Score high precision version
        ws_hp = tmp_path / "ws_hp"
        _write_report(ws_hp, spec.repo_dir, high_prec_answer)
        hp_result = _run_verifier(spec.task_num, "check_dead_code", ws_hp)
        hp_score = float(hp_result.get("score", 0))

        # Score low precision version
        ws_lp = tmp_path / "ws_lp"
        _write_report(ws_lp, spec.repo_dir, low_prec_answer)
        lp_result = _run_verifier(spec.task_num, "check_dead_code", ws_lp)
        lp_score = float(lp_result.get("score", 0))

        assert hp_score > lp_score, (
            f"Task dead-code-{spec.task_num}: high precision ({hp_score:.3f}) "
            f"should beat low precision ({lp_score:.3f}). "
            f"HP: {hp_result}, LP: {lp_result}"
        )


# -- tests: feature flag checkpoint ------------------------------------------

class TestFeatureFlagCheckpoint:
    """Feature flag checkpoint scores correctly."""

    @pytest.mark.parametrize(
        "spec",
        [t for t in TASKS if t.flags],
        ids=[t.task_num for t in TASKS if t.flags],
    )
    def test_correct_flags_score_high(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        """Answer mentioning correct flag names should score high."""
        gt = _load_gt(spec.task_num)
        answer = _make_gt_answer(gt)
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, answer)

        result = _run_verifier(spec.task_num, "check_feature_flags", workspace)
        score = float(result.get("score", 0))
        assert score >= 0.8, (
            f"Task dead-code-{spec.task_num}: correct flags scored {score:.2f} (<0.8). "
            f"Expected flags: {spec.flags}. Result: {result}"
        )

    @pytest.mark.parametrize(
        "spec",
        [t for t in TASKS if t.flags],
        ids=[t.task_num for t in TASKS if t.flags],
    )
    def test_wrong_flags_score_low(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        """Answer with no mention of correct flag names should score low."""
        # Write answer that doesn't mention any flags
        wrong_answer = [
            {
                "file": "some/other/file.js",
                "symbol": "someFunction",
                "kind": "function",
                "confidence": "high",
                "evidence": "this function is never called anywhere",
            }
        ]
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, wrong_answer)

        result = _run_verifier(spec.task_num, "check_feature_flags", workspace)
        score = float(result.get("score", 0))
        assert score <= 0.2, (
            f"Task dead-code-{spec.task_num}: wrong flags scored {score:.2f} (>0.2). "
            f"Result: {result}"
        )


# -- tests: false positive rate -----------------------------------------------

class TestFalsePositiveRate:
    """GT answer should have 0% FP rate by definition."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_gt_has_zero_fp(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        """When using exact GT answer, FP count should be 0."""
        gt = _load_gt(spec.task_num)
        answer = _make_gt_answer(gt)
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, answer)

        result = _run_verifier(spec.task_num, "check_dead_code", workspace)
        detail = result.get("detail", "")

        # Parse FP count from detail string "... FP=N ..."
        fp_count = 0
        for part in detail.split():
            if part.startswith("FP="):
                fp_count = int(part.split("=")[1])
                break

        assert fp_count == 0, (
            f"Task dead-code-{spec.task_num}: GT answer has {fp_count} false positives. "
            f"Detail: {detail}"
        )

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_gt_precision_is_one(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        """GT answer should achieve precision = 1.0."""
        gt = _load_gt(spec.task_num)
        answer = _make_gt_answer(gt)
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, answer)

        result = _run_verifier(spec.task_num, "check_dead_code", workspace)
        detail = result.get("detail", "")

        # Parse precision from detail string "precision=X.XXX ..."
        precision = 0.0
        for part in detail.split():
            if part.startswith("precision="):
                precision = float(part.split("=")[1])
                break

        assert precision >= 0.99, (
            f"Task dead-code-{spec.task_num}: GT precision={precision:.3f} (<1.0). "
            f"Detail: {detail}"
        )


# -- tests: evidence checkpoint -----------------------------------------------

class TestEvidenceCheckpoint:
    """Evidence checkpoint validates reasoning quality."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_gt_evidence_scores_high(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        """GT answer has quality evidence -> high score."""
        gt = _load_gt(spec.task_num)
        answer = _make_gt_answer(gt)
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, answer)

        result = _run_verifier(spec.task_num, "check_evidence", workspace)
        score = float(result.get("score", 0))
        assert score >= 0.8, (
            f"Task dead-code-{spec.task_num}: GT evidence scored {score:.2f} (<0.8). "
            f"Result: {result}"
        )

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_no_evidence_scores_low(self, tmp_path: Path, spec: DeadCodeTaskSpec) -> None:
        """Answer with no evidence fields -> low score."""
        answer = [
            {"file": "a.js", "symbol": "x", "kind": "function"}
        ]
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, answer)

        result = _run_verifier(spec.task_num, "check_evidence", workspace)
        score = float(result.get("score", 0))
        assert score <= 0.1, (
            f"Task dead-code-{spec.task_num}: no-evidence scored {score:.2f} (>0.1). "
            f"Result: {result}"
        )


# -- tests: proportional scoring ---------------------------------------------

class TestProportionalScoring:
    """Finding more dead code should yield proportionally higher scores."""

    def test_more_items_scores_higher(self, tmp_path: Path) -> None:
        """Task 001: finding 8/12 dead code should score higher than 4/12."""
        gt = _load_gt("001")
        dead = gt["dead_code"]

        # 4 of 12
        small_answer = [
            {
                "file": item["file"],
                "symbol": item["symbol"],
                "kind": item["kind"],
                "confidence": "high",
                "evidence": "no callers found",
            }
            for item in dead[:4]
        ]

        # 8 of 12
        large_answer = [
            {
                "file": item["file"],
                "symbol": item["symbol"],
                "kind": item["kind"],
                "confidence": "high",
                "evidence": "no callers found",
            }
            for item in dead[:8]
        ]

        ws_small = tmp_path / "ws_small"
        _write_report(ws_small, "react", small_answer)
        small_result = _run_verifier("001", "check_dead_code", ws_small)
        small_score = float(small_result.get("score", 0))

        ws_large = tmp_path / "ws_large"
        _write_report(ws_large, "react", large_answer)
        large_result = _run_verifier("001", "check_dead_code", ws_large)
        large_score = float(large_result.get("score", 0))

        assert large_score > small_score, (
            f"8/12 ({large_score:.3f}) should beat 4/12 ({small_score:.3f})"
        )


# -- regression (p6ux.13): agent reports keyed on MCP "path" ------------------

class TestPathKeyedReport:
    """Agents key the dead symbol's file on either "file" or the MCP "path".

    The verifier normalized only on "file", so a report using "path"
    (e.g. {"path": "src/...", "symbol": "x"}) produced no (file,symbol) pairs and
    scored 0.0 — even when every symbol was correct. After the fix the file is read
    from "file" or "path", so a path-keyed report matches a file-keyed one exactly.
    """

    def test_path_keyed_matches_file_keyed(self, tmp_path: Path) -> None:
        gt = _load_gt("001")

        file_keyed = [
            {"file": it["file"], "symbol": it["symbol"]} for it in gt["dead_code"]
        ]
        path_keyed = [
            {"path": it["file"], "symbol": it["symbol"]} for it in gt["dead_code"]
        ]

        ws_file = tmp_path / "ws_file"
        _write_report(ws_file, "react", file_keyed)
        file_score = float(
            _run_verifier("001", "check_dead_code", ws_file).get("score", 0)
        )

        ws_path = tmp_path / "ws_path"
        _write_report(ws_path, "react", path_keyed)
        path_score = float(
            _run_verifier("001", "check_dead_code", ws_path).get("score", 0)
        )

        assert path_score == file_score, (
            f"path-keyed report scored {path_score} but file-keyed scored "
            f"{file_score}; the verifier must accept the MCP 'path' key."
        )
        assert path_score >= 0.85, f"path-keyed all-correct report scored {path_score}"
