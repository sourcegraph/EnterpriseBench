"""Verification tests for monorepo-boundary task checkpoint scripts.

For each task, tests 3 tiers:
  (a) Ground truth answer → score ≥0.85
  (b) Empty answer → score ≤0.10
  (c) Partial answer → 0.3 ≤ score ≤ 0.7

Runs the actual bash verifier scripts via subprocess, matching the
eb_verify runner convention (WORKSPACE env var, JSON stdout).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

BENCHMARKS_DIR = Path(__file__).parent.parent / "benchmarks" / "feature_delivery"

# ── task definitions ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaskVerifierSpec:
    """Spec for testing one task's verifiers."""
    task_num: str
    repo_dir: str  # subdirectory under workspace (e.g. "babel")
    # Ground truth answer content — what a perfect agent would write
    gt_answer: str
    # Partial answer — some but not all packages/files found
    partial_answer: str
    # Expected semver classification keyword
    classification: str


TASKS: list[TaskVerifierSpec] = [
    TaskVerifierSpec(
        task_num="001",
        repo_dir="babel",
        gt_answer="""\
# Impact Report

## Affected Packages

### @babel/parser
- **Impact**: patch
- **Files**: `packages/babel-parser/src/types.d.ts` updated
- References `definitions/typescript` type changes
- Parser types.d.ts updated for TSPropertySignature

### @babel/generator
- **Impact**: patch
- **Files**: `packages/babel-generator/src/generators/typescript.ts` updated
- Generator typescript generation code updated
""",
        partial_answer="""\
# Impact Report

## @babel/parser
- Impact: patch
- Parser types.d.ts needs update
""",
        classification="patch",
    ),
    TaskVerifierSpec(
        task_num="002",
        repo_dir="babel",
        gt_answer="""\
# Impact Report

## @babel/helpers
- **Impact**: minor
- `packages/babel-helpers/src/helpers-generated.ts` — new applyDecs2311 helper
- `packages/babel-helpers/src/helpers/applyDecs2311.ts`

## @babel/plugin-proposal-decorators
- **Impact**: minor
- `packages/babel-plugin-proposal-decorators/src/index.ts` — transform updated
""",
        partial_answer="""\
# Impact Report

## @babel/helpers
- Impact: minor
- helpers-generated.ts updated
""",
        classification="minor",
    ),
    TaskVerifierSpec(
        task_num="003",
        repo_dir="babel",
        # Refuting the release-notes attribution is now part of a correct answer:
        # instruction.md states the claim that the decorator core lands in
        # @babel/helper-create-class-features-plugin and asks the agent to test
        # it. Neither PR #15895 nor #15896 touches that package
        # (EnterpriseBench-jn73.2.7.3.1.2).
        gt_answer="""\
# Impact Report

## @babel/helpers
- **Impact**: minor
- `packages/babel-helpers/src/helpers/applyDecs2305.js`
- New decorator metadata helper

## @babel/plugin-proposal-decorators
- **Impact**: minor
- `packages/babel-plugin-proposal-decorators/src/transformer-2023-05.ts`

## @babel/parser
- **Impact**: minor
- `packages/babel-parser/src/plugins/typescript/index.ts` — tuple label relaxation

## Not affected
- `@babel/helper-create-class-features-plugin` is not affected — neither PR
  contains a file from it, so it is not the core of the decorator change.
""",
        partial_answer="""\
# Impact Report

## @babel/helpers
- Impact: minor
- applyDecs2305 updated
""",
        classification="minor",
    ),
    TaskVerifierSpec(
        task_num="004",
        repo_dir="babel",
        gt_answer="""\
# Impact Report

## @babel/plugin-transform-for-of
- **Impact**: patch
- `packages/babel-plugin-transform-for-of/src/index.ts`
- Fixed iterableIsArray assumption

## @babel/preset-env
- **Impact**: patch
- Integration test: `for-of-array-block-scoping` fixtures updated
- `packages/babel-generator/src/printer.ts` — retainLines fix
""",
        partial_answer="""\
# Impact Report

## @babel/plugin-transform-for-of
- Impact: patch
- src/index.ts fixed
""",
        classification="patch",
    ),
    TaskVerifierSpec(
        task_num="005",
        repo_dir="pnpm",
        gt_answer="""\
# Impact Report

## @pnpm/lockfile-file
- **Impact**: major — lockfile read/write format changed
- `lockfile/lockfile-file/src/write.ts`

## @pnpm/lockfile-utils
- **Impact**: major
- `lockfile/lockfile-utils/test/nameVerFromPkgSnapshot.ts`

## @pnpm/lockfile-to-pnp
- **Impact**: major

## @pnpm/merge-lockfile-changes
- **Impact**: major — merge logic updated

## @pnpm/prune-lockfile
- **Impact**: major

## @pnpm/filter-lockfile
- **Impact**: major — filter updated

## @pnpm/calc-dep-state
- **Impact**: major
- Dependency state calculation

## @pnpm/plugin-commands-rebuild
- **Impact**: major

## @pnpm/audit
- **Impact**: major

Source changes: `packages/constants/src/index.ts`, `packages/dependency-path/src/index.ts`
""",
        partial_answer="""\
# Impact Report

## @pnpm/lockfile-file
- Impact: major
- lockfile-file/src/write.ts

## @pnpm/lockfile-utils
- Impact: major

## @pnpm/calc-dep-state
- Impact: major
- packages/constants/src/index.ts
""",
        classification="major",
    ),
    TaskVerifierSpec(
        task_num="006",
        repo_dir="pnpm",
        gt_answer="""\
# Impact Report

## @pnpm/plugin-commands-installation
- **Impact**: major
- `exec/plugin-commands-rebuild/test/index.ts`

## pnpm CLI
- **Impact**: major
- `pnpm/test/install/lifecycleScripts.ts`
- `pnpm/test/monorepo/index.ts`

Source: `config/config/src/getOptionsFromRootManifest.ts`
""",
        partial_answer="""\
# Impact Report

## pnpm CLI
- Impact: major
- pnpm/test/install/lifecycleScripts.ts
""",
        classification="major",
    ),
    TaskVerifierSpec(
        task_num="007",
        repo_dir="pnpm",
        gt_answer="""\
# Impact Report

## @pnpm/pnpmfile
- **Impact**: major
- `hooks/pnpmfile/src/requireHooks.ts`

## @pnpm/dependency-path
- **Impact**: major
- `packages/dependency-path/src/index.ts`

## pnpm CLI
- **Impact**: major
- `pnpm/test/hooks.ts`

## @pnpm/core
- **Impact**: major
- `pkg-manager/core/test/install/fromRepo.ts`

Source: `crypto/hash/src/index.ts`
""",
        partial_answer="""\
# Impact Report

## @pnpm/pnpmfile
- Impact: significant breaking change
- requireHooks.ts updated
""",
        classification="major",
    ),
    TaskVerifierSpec(
        task_num="008",
        repo_dir="next.js",
        gt_answer="""\
# Impact Report

## @next/swc (next-core crate)
- **Impact**: major
- `packages/next-swc/crates/next-core/src/next_import_map.rs`
- Rust SWC import map must resolve next/og instead of next/server

## @vercel/og (compiled)
- **Impact**: major
- `packages/next/src/compiled/@vercel/og/package.json`
""",
        partial_answer="""\
# Impact Report

## @next/swc
- Impact: major
- next-core crate needs update
""",
        classification="major",
    ),
    TaskVerifierSpec(
        task_num="009",
        repo_dir="rust",
        gt_answer="""\
# Impact Report

## rustc_ast_passes
- **Impact**: major
- `compiler/rustc_ast_passes/Cargo.toml` — dependency on rustc_attr must change
- Source uses `rustc_attr` imports

## rustc_builtin_macros
- **Impact**: major
- `compiler/rustc_builtin_macros/Cargo.toml`

## rustc_codegen_llvm
- **Impact**: major
- `compiler/rustc_codegen_llvm/Cargo.toml`

## rustc_codegen_gcc
- **Impact**: major
- `compiler/rustc_codegen_gcc/src/lib.rs`
""",
        partial_answer="""\
# Impact Report

## rustc_ast_passes
- Impact: major — needs Cargo.toml update
- Uses the old crate name in imports
""",
        classification="major",
    ),
    TaskVerifierSpec(
        task_num="010",
        repo_dir="rust",
        gt_answer="""\
# Impact Report

## rustc_const_eval
- **Impact**: minor
- `compiler/rustc_const_eval/src/const_eval/eval_queries.rs`

## rustc_metadata
- **Impact**: minor
- `compiler/rustc_metadata/src/rmeta/encoder.rs`

## rustc_mir_transform
- **Impact**: minor
- `compiler/rustc_mir_transform/src/gvn.rs`

## miri
- **Impact**: minor
- `src/tools/miri/src/shims/intrinsics/mod.rs`

Source: `compiler/rustc_middle/src/mir/mod.rs`
""",
        partial_answer="""\
# Impact Report

## rustc_const_eval
- Impact: minor — internal representation change
- eval_queries.rs updated
""",
        classification="minor",
    ),
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _task_dir(task_num: str) -> Path:
    return BENCHMARKS_DIR / f"monorepo-boundary-{task_num}"


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
    env["TASK_ID"] = f"monorepo-boundary-{task_num}"

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


def _write_report(workspace: Path, repo_dir: str, content: str) -> Path:
    """Write IMPACT_REPORT.md to the agent's deliverable drop.

    *repo_dir* is accepted for call-site compatibility (historically the
    report lived under the repo tree) but is unused: deliverables were
    migrated to /workspace/agent_output/ (mode_gate locks repo dirs in gated
    arms; agent_output is the one location every arm leaves writable) --
    scripts/authoring/migrate_deliverable_to_agent_output.py.
    """
    report_dir = workspace / "agent_output"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "IMPACT_REPORT.md"
    report_path.write_text(content)
    return report_path


def _weighted_score(results: list[dict[str, Any]], weights: tuple[float, ...] = (0.25, 0.45, 0.30)) -> float:
    """Compute weighted score from 3 checkpoint results."""
    total = 0.0
    for r, w in zip(results, weights):
        total += float(r.get("score", 0.0)) * w
    return total


CHECKPOINT_NAMES = [
    "check_affected_packages",
    "check_impact_classification",
    "check_boundary_violations",
]


# ── tests ────────────────────────────────────────────────────────────────────

class TestGroundTruthScoresHigh:
    """(a) Ground truth answer should score ≥0.85."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_gt_answer_scores_high(self, tmp_path: Path, spec: TaskVerifierSpec) -> None:
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, spec.gt_answer)

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results)
        assert total >= 0.85, (
            f"Task {spec.task_num} GT scored {total:.2f} (<0.85). "
            f"Results: {results}"
        )


class TestEmptyAnswerScoresLow:
    """(b) Empty/missing answer should score ≤0.10."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_empty_answer_scores_low(self, tmp_path: Path, spec: TaskVerifierSpec) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        # No IMPACT_REPORT.md written — verifiers should return 0

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results)
        assert total <= 0.10, (
            f"Task {spec.task_num} empty scored {total:.2f} (>0.10). "
            f"Results: {results}"
        )


class TestPartialAnswerScoresMid:
    """(c) Partial answer should score between 0.3 and 0.7."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_partial_answer_scores_mid(self, tmp_path: Path, spec: TaskVerifierSpec) -> None:
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, spec.partial_answer)

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results)
        assert 0.15 <= total <= 0.75, (
            f"Task {spec.task_num} partial scored {total:.2f} (expected 0.15-0.75). "
            f"Results: {results}"
        )


class TestPartialCreditProportional:
    """Finding 3 of 5 affected packages scores proportionally, not binary."""

    def test_pnpm_lockfile_partial_credit(self, tmp_path: Path) -> None:
        """Task 005 has 9 packages — finding 3 should score ~0.33."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "pnpm", """\
# Impact Report
## @pnpm/lockfile-file
- major
## @pnpm/lockfile-utils
- major
## @pnpm/filter-lockfile
- major
packages/constants/src/index.ts
""")

        result = _run_verifier("005", "check_affected_packages", workspace)
        score = float(result.get("score", 0))
        # 3/9 = 0.33
        assert 0.25 <= score <= 0.45, (
            f"3/9 packages scored {score:.2f} (expected ~0.33): {result}"
        )

    def test_rust_attr_partial_credit(self, tmp_path: Path) -> None:
        """Task 009 has 4 crates — finding 2 should score ~0.50."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "rust", """\
# Impact Report
## rustc_ast_passes
- major, Cargo.toml
## rustc_codegen_llvm
- major, Cargo.toml
""")

        result = _run_verifier("009", "check_affected_packages", workspace)
        score = float(result.get("score", 0))
        # 2/4 = 0.50
        assert 0.40 <= score <= 0.60, (
            f"2/4 crates scored {score:.2f} (expected ~0.50): {result}"
        )

    def test_babel_all_vs_partial_boundary(self, tmp_path: Path) -> None:
        """Task 001 boundary check: 2/3 files should score ~0.67, not 0 or 1."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "babel", """\
# Impact Report
## @babel/parser — patch
- `packages/babel-types/src/definitions/typescript.ts`
- `packages/babel-parser/src/types.d.ts`
""")

        result = _run_verifier("001", "check_boundary_violations", workspace)
        score = float(result.get("score", 0))
        # 2/3 boundary files found
        assert 0.55 <= score <= 0.75, (
            f"2/3 boundaries scored {score:.2f} (expected ~0.67): {result}"
        )


class TestVerifierScriptsExist:
    """All 10 tasks have all 3 checkpoint verifier scripts."""

    @pytest.mark.parametrize("task_num", [f"{i:03d}" for i in range(1, 11)])
    def test_all_verifiers_present(self, task_num: str) -> None:
        task_dir = _task_dir(task_num)
        for cp in CHECKPOINT_NAMES:
            script = task_dir / "checks" / f"{cp}.sh"
            assert script.exists(), f"Missing: {script}"
            assert os.access(script, os.X_OK), f"Not executable: {script}"
