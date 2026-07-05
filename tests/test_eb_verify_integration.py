"""
Integration tests for eb_verify: full pipeline from real task.toml parsing
through workspace setup, checkpoint execution, artifact validation, and
scored JSON output.

Tests three distinct task types:
  - err-provenance-01 (single-repo, customer_escalation)
  - dep-traversal-003 (multi-repo, dependency_management)
  - refactor-orchestration-001 (topological ordering, technical_debt)

Also covers error cases: missing workspace, invalid output, missing task.toml.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from eb_verify.task_parser import parse_task, TaskDefinition
from eb_verify.runner import CheckpointRunner
from eb_verify.scoring import VerificationResult

BENCHMARKS = Path(__file__).parent.parent / "benchmarks"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier(checks_dir: Path, name: str, score: float, detail: str) -> None:
    """Create a shell verifier script that outputs a JSON score."""
    script = checks_dir / name
    script.write_text(
        f'#!/bin/bash\n'
        f'echo \'{{"score": {score}, "detail": "{detail}"}}\'\n'
        f'exit 0\n'
    )
    script.chmod(0o755)


def _make_workspace_repos(workspace: Path, task: TaskDefinition) -> None:
    """Create stub repo dirs matching task.repos so health check passes."""
    for repo in task.repos:
        repo_dir = workspace / repo.path
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / ".git").mkdir(exist_ok=True)
        (repo_dir / "go.mod").write_text(f"module {repo.url}\n")


# ===========================================================================
# 1. err-provenance-01 — single-repo customer_escalation
# ===========================================================================


class TestErrProvenance01:
    """Full pipeline for error provenance tracing (customer_escalation)."""

    TASK_TOML = BENCHMARKS / "customer_escalation" / "err-provenance-01" / "task.toml"

    @pytest.fixture
    def env(self, tmp_path: Path) -> tuple[TaskDefinition, Path, Path]:
        task = parse_task(self.TASK_TOML)
        assert task.id == "error-trace-k8s-job-starttime-001"
        assert task.suite == "customer_escalation"

        task_dir = tmp_path / "task"
        task_dir.mkdir()

        # Write a minimal task.toml for the runner (checkpoints reference checks/)
        checks = task_dir / "checks"
        checks.mkdir()

        # Simulate verifier scripts matching the real task checkpoints
        _make_verifier(checks, "check_error_source.sh", 1.0, "Found validation.go")
        _make_verifier(checks, "check_error_chain.sh", 0.8, "Chain partially traced")
        _make_verifier(checks, "check_trigger_conditions.sh", 0.9, "Conditions identified")

        # Rewrite task.toml into task_dir so verifier paths resolve
        (task_dir / "task.toml").write_text(
            (self.TASK_TOML).read_text()
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _make_workspace_repos(workspace, task)

        # Create agent answer artifact
        (workspace / "answer.json").write_text(json.dumps({
            "error_source": "pkg/apis/batch/validation/validation.go",
            "function": "ValidateJobSpec",
            "error_chain": [
                "ValidateJobSpec",
                "validateJobSpecUpdate",
                "Strategy.ValidateUpdate",
            ],
            "trigger_conditions": "startTime field removed during job update",
        }))

        return task, task_dir, workspace

    def test_parse_real_task(self) -> None:
        task = parse_task(self.TASK_TOML)
        assert task.id == "error-trace-k8s-job-starttime-001"
        assert task.suite == "customer_escalation"
        assert task.difficulty_stratum == "large_single"
        assert len(task.checkpoints) == 3
        assert len(task.repos) == 1
        assert task.repos[0].path == "kubernetes"
        assert "answer" in task.artifacts.required
        assert task.ground_truth is not None
        assert len(task.ground_truth.required_files) == 2

    def test_full_pipeline_score(self, env: tuple[TaskDefinition, Path, Path]) -> None:
        task, task_dir, workspace = env
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        reward_path = workspace / "reward.txt"
        result = runner.run_all(output_path=reward_path)

        assert isinstance(result, VerificationResult)
        assert result.task_id == "error-trace-k8s-job-starttime-001"
        assert len(result.checkpoint_results) == 3

        # Weighted: 1.0*0.40 + 0.8*0.30 + 0.9*0.30 = 0.40 + 0.24 + 0.27 = 0.91
        assert result.total_score >= 0.85

        # answer artifact validated
        art_map = {ar["type"]: ar for ar in result.artifact_results}
        assert art_map["answer"]["valid"] is True

        # reward.txt written
        assert reward_path.exists()
        content = reward_path.read_text()
        assert "error-trace-k8s-job-starttime-001" in content

    def test_json_serializable(self, env: tuple[TaskDefinition, Path, Path]) -> None:
        task, task_dir, workspace = env
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_all(output_path=workspace / "reward.txt")

        output = {
            "task_id": result.task_id,
            "total_score": result.total_score,
            "checkpoints": [
                {"name": cr.name, "weight": cr.weight, "passed": cr.passed,
                 "score": cr.score, "detail": cr.detail}
                for cr in result.checkpoint_results
            ],
            "artifacts": result.artifact_results,
        }
        parsed = json.loads(json.dumps(output))
        assert parsed["task_id"] == "error-trace-k8s-job-starttime-001"
        assert 0.0 <= parsed["total_score"] <= 1.0


# ===========================================================================
# 2. dep-traversal-003 — multi-repo dependency_management
# ===========================================================================


class TestDepTraversal003:
    """Full pipeline for dependency graph traversal (multi-repo)."""

    TASK_TOML = BENCHMARKS / "dependency_management" / "dep-traversal-003" / "task.toml"

    @pytest.fixture
    def env(self, tmp_path: Path) -> tuple[TaskDefinition, Path, Path]:
        task = parse_task(self.TASK_TOML)

        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "task.toml").write_text(self.TASK_TOML.read_text())

        checks = task_dir / "checks"
        checks.mkdir()
        _make_verifier(checks, "check_cve_id.sh", 1.0, "CVE-2022-32149 identified")
        _make_verifier(checks, "check_direct_deps.sh", 1.0, "Both repos found")
        _make_verifier(checks, "check_transitive_paths.sh", 0.8, "ParseAcceptLanguage traced")
        _make_verifier(checks, "check_version_analysis.sh", 0.9, "Version classification done")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _make_workspace_repos(workspace, task)

        # Agent output: answer.json
        (workspace / "answer.json").write_text(json.dumps({
            "cve_id": "CVE-2022-32149",
            "affected_package": "golang.org/x/text",
            "affected_versions": "< 0.3.8",
            "consumers": {
                "chi": {"depends": True, "calls_vulnerable_fn": False, "affected": False},
                "hugo": {"depends": True, "calls_vulnerable_fn": True, "affected": True},
            },
        }))

        # Agent output: security_assessment.json
        (workspace / "security_assessment.json").write_text(json.dumps({
            "vulnerabilities": [
                {"cve": "CVE-2022-32149", "severity": "high", "package": "golang.org/x/text"}
            ],
            "severity_summary": "1 high severity vulnerability in transitive dependency",
            "recommendations": [
                "Upgrade golang.org/x/text to >= 0.3.8 in hugo",
                "chi is unaffected — no ParseAcceptLanguage usage",
            ],
        }))

        # BLAST_RADIUS.md
        (workspace / "BLAST_RADIUS.md").write_text(textwrap.dedent("""\
            # Blast Radius: CVE-2022-32149

            ## Affected: hugo
            - Depends on golang.org/x/text < 0.3.8
            - Calls language.ParseAcceptLanguage

            ## Unaffected: chi
            - Depends on golang.org/x/text but does not call vulnerable function
        """))

        return task, task_dir, workspace

    def test_parse_real_task(self) -> None:
        task = parse_task(self.TASK_TOML)
        assert task.id == "dep-traversal-003"
        assert task.suite == "dependency_management"
        assert task.difficulty_stratum == "dual_repo"
        assert len(task.checkpoints) == 4
        assert len(task.repos) == 2
        assert "answer" in task.artifacts.required
        assert "security_assessment" in task.artifacts.required

    def test_full_pipeline_score(self, env: tuple[TaskDefinition, Path, Path]) -> None:
        task, task_dir, workspace = env
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        reward_path = workspace / "reward.txt"
        result = runner.run_all(output_path=reward_path)

        assert result.task_id == "dep-traversal-003"
        assert len(result.checkpoint_results) == 4

        # Weighted: 1.0*0.10 + 1.0*0.30 + 0.8*0.35 + 0.9*0.25
        #         = 0.10 + 0.30 + 0.28 + 0.225 = 0.905
        assert result.total_score >= 0.85

        # Both artifacts validated
        art_map = {ar["type"]: ar for ar in result.artifact_results}
        assert art_map["answer"]["valid"] is True
        assert art_map["security_assessment"]["valid"] is True

        assert reward_path.exists()

    def test_multi_repo_health_check(self, env: tuple[TaskDefinition, Path, Path]) -> None:
        task, task_dir, workspace = env
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        assert runner.sandbox_health_check() is True

        # Remove one repo — health check should fail
        shutil.rmtree(workspace / "chi")
        assert runner.sandbox_health_check() is False


# ===========================================================================
# 3. refactor-orchestration-001 — topological ordering, technical_debt
# ===========================================================================


class TestRefactorOrchestration001:
    """Full pipeline for refactor orchestration (topological ordering)."""

    TASK_TOML = BENCHMARKS / "technical_debt" / "refactor-orchestration-001" / "task.toml"

    @pytest.fixture
    def env(self, tmp_path: Path) -> tuple[TaskDefinition, Path, Path]:
        task = parse_task(self.TASK_TOML)

        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "task.toml").write_text(self.TASK_TOML.read_text())

        checks = task_dir / "checks"
        checks.mkdir()
        _make_verifier(checks, "check_repo_set.sh", 1.0, "All repos identified")
        _make_verifier(checks, "check_topo_order.sh", 0.9, "Valid topological order")
        _make_verifier(checks, "check_parallelism.sh", 0.85, "Parallelism annotated")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _make_workspace_repos(workspace, task)

        # Agent output: answer.json
        (workspace / "answer.json").write_text(json.dumps({
            "repos_to_update": ["etcd", "kubernetes"],
            "ordering": ["etcd", "kubernetes"],
            "rationale": "etcd is upstream; kubernetes consumes etcd client libs",
        }))

        # Agent output: REFACTOR_PLAN.md
        (workspace / "REFACTOR_PLAN.md").write_text(textwrap.dedent("""\
            # Refactor Plan: etcd 3.6 Client Update Cascade

            ## Ordering
            1. etcd — upstream library, update client SDK first
            2. kubernetes — consumer, update after etcd client is stable

            ## Dependency Graph
            kubernetes -> etcd (client library dependency)

            ## Parallelization
            - Step 1 (etcd) must complete first
            - Step 2 (kubernetes) depends on step 1
            - No parallelizable steps in this minimal graph

            ## Risk Assessment
            - etcd: Low risk — library owner controls the release
            - kubernetes: Medium risk — large consumer, many indirect dependents
        """))

        # Agent output: ordering.json for topological_order plugin
        (workspace / "ordering.json").write_text(json.dumps({
            "proposed_order": ["etcd", "kubernetes"],
            "dependency_graph": {
                "etcd": [],
                "kubernetes": ["etcd"],
            },
        }))

        return task, task_dir, workspace

    def test_parse_real_task(self) -> None:
        task = parse_task(self.TASK_TOML)
        assert task.id == "refactor-orch-001"
        assert task.suite == "technical_debt"
        assert task.difficulty_stratum == "dual_repo"
        assert len(task.checkpoints) == 3
        assert len(task.repos) == 2
        assert "answer" in task.artifacts.required
        assert "topological_order" in task.artifacts.required

    def test_full_pipeline_score(self, env: tuple[TaskDefinition, Path, Path]) -> None:
        task, task_dir, workspace = env
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        reward_path = workspace / "reward.txt"
        result = runner.run_all(output_path=reward_path)

        assert result.task_id == "refactor-orch-001"
        assert len(result.checkpoint_results) == 3

        # Weighted: 1.0*0.25 + 0.9*0.45 + 0.85*0.30
        #         = 0.25 + 0.405 + 0.255 = 0.91
        assert result.total_score >= 0.85

        # Both artifacts validated
        art_map = {ar["type"]: ar for ar in result.artifact_results}
        assert art_map["answer"]["valid"] is True
        assert art_map["topological_order"]["valid"] is True

        assert reward_path.exists()

    def test_topological_order_plugin_directly(self, env: tuple[TaskDefinition, Path, Path]) -> None:
        """Verify the topological_order plugin validates ordering.json correctly."""
        _, _, workspace = env
        from eb_verify.plugins import get_validator

        validator = get_validator("topological_order")
        assert validator is not None
        result = validator.validate(workspace)
        assert result.valid is True
        assert "Valid topological ordering" in result.detail

    def test_summary_contains_all_checkpoints(
        self, env: tuple[TaskDefinition, Path, Path]
    ) -> None:
        task, task_dir, workspace = env
        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_all(output_path=workspace / "reward.txt")
        summary = result.summary()

        assert "refactor-orch-001" in summary
        assert "identify_repos" in summary
        assert "topological_order" in summary
        assert "parallelism" in summary


# ===========================================================================
# 4. Error cases
# ===========================================================================


class TestErrorCases:
    """Edge cases and failure modes for the verification pipeline."""

    def test_missing_task_toml(self, tmp_path: Path) -> None:
        bogus = tmp_path / "nonexistent" / "task.toml"
        with pytest.raises((FileNotFoundError, ValueError)):
            parse_task(bogus)

    def test_invalid_task_toml(self, tmp_path: Path) -> None:
        bad_toml = tmp_path / "task.toml"
        bad_toml.write_text("this is [[[not valid toml")
        with pytest.raises(Exception):
            parse_task(bad_toml)

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        incomplete = tmp_path / "task.toml"
        incomplete.write_text(textwrap.dedent("""\
            [task]
            id = "incomplete-001"
            suite = "test"
        """))
        with pytest.raises(ValueError, match="missing required field"):
            parse_task(incomplete)

    def test_missing_workspace_repos(self, tmp_path: Path) -> None:
        """Runner handles missing workspace repos gracefully."""
        task_toml = BENCHMARKS / "customer_escalation" / "err-provenance-01" / "task.toml"
        task = parse_task(task_toml)

        task_dir = tmp_path / "task"
        task_dir.mkdir()
        checks = task_dir / "checks"
        checks.mkdir()
        _make_verifier(checks, "check_error_source.sh", 1.0, "ok")
        _make_verifier(checks, "check_error_chain.sh", 1.0, "ok")
        _make_verifier(checks, "check_trigger_conditions.sh", 1.0, "ok")

        # Empty workspace — no repos cloned
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        assert runner.sandbox_health_check() is False

        # Pipeline still runs (health check is non-fatal)
        result = runner.run_all(output_path=workspace / "reward.txt")
        assert isinstance(result, VerificationResult)

    def test_invalid_agent_answer(self, tmp_path: Path) -> None:
        """answer.json with invalid JSON still produces a result (artifact invalid)."""
        task_toml = BENCHMARKS / "customer_escalation" / "err-provenance-01" / "task.toml"
        task = parse_task(task_toml)

        task_dir = tmp_path / "task"
        task_dir.mkdir()
        checks = task_dir / "checks"
        checks.mkdir()
        _make_verifier(checks, "check_error_source.sh", 0.5, "partial")
        _make_verifier(checks, "check_error_chain.sh", 0.0, "missing")
        _make_verifier(checks, "check_trigger_conditions.sh", 0.0, "missing")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _make_workspace_repos(workspace, task)

        # Write broken answer.json
        (workspace / "answer.json").write_text("{not valid json at all")

        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_all(output_path=workspace / "reward.txt")

        # answer artifact should be invalid
        art_map = {ar["type"]: ar for ar in result.artifact_results}
        assert art_map["answer"]["valid"] is False

    def test_verifier_timeout(self, tmp_path: Path) -> None:
        """A verifier that exceeds timeout returns score 0."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        checks = task_dir / "checks"
        checks.mkdir()

        # Verifier that sleeps forever
        slow_script = checks / "slow.sh"
        slow_script.write_text("#!/bin/bash\nsleep 999\n")
        slow_script.chmod(0o755)

        (task_dir / "task.toml").write_text(textwrap.dedent("""\
            difficulty_stratum = "calibration"
            [task]
            id = "timeout-test-001"
            suite = "test"
            difficulty = "easy"
            session_type = "single"
            description = "Timeout test"
            prompt = "Test"

            [[checkpoints]]
            name = "slow_check"
            weight = 1.0
            verifier = "checks/slow.sh"
            timeout_seconds = 1

            [artifacts]
            required = []
        """))

        task = parse_task(task_dir / "task.toml")
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_checkpoint(task.checkpoints[0])

        assert result.passed is False
        assert result.score == 0.0
        assert "timed out" in result.detail.lower()

    def test_verifier_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Verifier paths that escape task_dir are rejected."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "task.toml").write_text(textwrap.dedent("""\
            difficulty_stratum = "calibration"
            [task]
            id = "traversal-test-001"
            suite = "test"
            difficulty = "easy"
            session_type = "single"
            description = "Path traversal test"
            prompt = "Test"

            [[checkpoints]]
            name = "escape_check"
            weight = 1.0
            verifier = "../../../etc/passwd"
            timeout_seconds = 5

            [artifacts]
            required = []
        """))

        task = parse_task(task_dir / "task.toml")
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        runner = CheckpointRunner(task=task, task_dir=task_dir, workspace=workspace)
        result = runner.run_checkpoint(task.checkpoints[0])

        assert result.passed is False
        assert "escapes" in result.detail.lower() or "not found" in result.detail.lower()


# ===========================================================================
# 5. CLI subprocess invocation
# ===========================================================================


class TestCLISubprocess:
    """Test running eb_verify via subprocess (python -m eb_verify)."""

    def test_cli_run_with_simulated_task(self, tmp_path: Path) -> None:
        """Run the full pipeline via CLI subprocess."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        checks = task_dir / "checks"
        checks.mkdir()

        _make_verifier(checks, "check_a.sh", 1.0, "pass")
        _make_verifier(checks, "check_b.sh", 0.7, "partial")

        (task_dir / "task.toml").write_text(textwrap.dedent("""\
            difficulty_stratum = "calibration"
            [task]
            id = "cli-test-001"
            suite = "test"
            difficulty = "easy"
            session_type = "single"
            description = "CLI integration test"
            prompt = "Test CLI"

            [[repos]]
            url = "github.com/example/repo"
            rev = "v1.0.0"
            path = "repo"
            role = "primary"

            [[checkpoints]]
            name = "check_a"
            weight = 0.6
            verifier = "checks/check_a.sh"
            timeout_seconds = 10

            [[checkpoints]]
            name = "check_b"
            weight = 0.4
            verifier = "checks/check_b.sh"
            timeout_seconds = 10

            [artifacts]
            required = ["answer"]
        """))

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo_dir = workspace / "repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        (workspace / "answer.json").write_text(json.dumps({"answer": "test"}))

        reward_path = tmp_path / "reward.txt"

        result = subprocess.run(
            [
                sys.executable, "-m", "eb_verify", "run",
                str(task_dir / "task.toml"),
                "--workspace", str(workspace),
                "--output", str(reward_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).parent.parent / "lib"),
            },
        )

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        assert reward_path.exists()
        content = reward_path.read_text()
        assert "cli-test-001" in content

    def test_cli_missing_task_file(self, tmp_path: Path) -> None:
        """CLI returns non-zero for missing task.toml."""
        result = subprocess.run(
            [
                sys.executable, "-m", "eb_verify", "run",
                str(tmp_path / "nonexistent.toml"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).parent.parent / "lib"),
            },
        )
        assert result.returncode != 0
        assert "not found" in result.stderr.lower()

    def test_cli_validate_artifact(self, tmp_path: Path) -> None:
        """CLI validate-artifact subcommand works."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "answer.json").write_text(json.dumps({"answer": "42"}))

        result = subprocess.run(
            [
                sys.executable, "-m", "eb_verify", "validate-artifact",
                "answer", str(workspace),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).parent.parent / "lib"),
            },
        )
        assert result.returncode == 0
        assert "VALID" in result.stdout
