"""
Checkpoint runner: parses task.toml, executes verifiers in order, collects scores.

Supports two verification tiers:
  Tier 1 (deterministic): grep-based shell script verifiers (fast, cheap)
  Tier 2 (llm_curator): LLM judge against curated expected_solution.json
    (semantic, catches domain-knowledge-only answers)

When both tiers are active, the final score is min(grep, judge) — the
LLM judge acts as a ceiling on the grep score.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, Protocol, cast

from eb_verify.task_parser import TaskDefinition, Checkpoint
from eb_verify.scorer_guard import INFRA_SENTINEL
from eb_verify.scoring import (
    CheckpointResult,
    VerificationResult,
    compute_score,
    write_reward,
)
from eb_verify.scorer_guard import InfraError, run_verifier_subprocess
from eb_verify.plugins import ValidationResult, get_validator

logger = logging.getLogger(__name__)

# The directory holding the eb_verify package, i.e. what has to be on PYTHONPATH
# for a checkpoint script to `python -m eb_verify.…`.
_LIB_DIR = Path(__file__).resolve().parents[1]


def checkpoint_env(workspace: Path, task_dir: Path, task_id: str) -> dict[str, str]:
    """The environment a checkpoint script runs with.

    One definition, because tests that re-derive it are not a guard on it. The
    sandbox builds the same keys against its staged copy of the harness
    (scripts/orchestration/run_task.py). PYTHONPATH is absolute and prepended:
    checkpoints run with cwd=workspace and exec scorers as `python -m eb_verify.…`,
    so an inherited relative value would resolve against the workspace and the
    import would fail.
    """
    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(task_dir)
    env["TASK_ID"] = task_id
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(_LIB_DIR), env.get("PYTHONPATH", "")) if p
    )
    return env


class _GroundednessCapableValidator(Protocol):
    """An artifact validator whose validate() accepts the groundedness flag.

    Membership is established at runtime by validate_artifacts()'s
    inspect.signature probe, not statically — this Protocol exists so the
    probed call site is typed instead of an untyped reflection call.
    """

    def validate(
        self, workspace: Path, require_grounded_citations: bool = ...
    ) -> ValidationResult: ...


def _load_expected_solution(task_dir: Path) -> dict[str, Any]:
    """Load expected_solution.json if it exists, else return empty dict."""
    path = task_dir / "expected_solution.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _load_agent_output(workspace: Path) -> str:
    """Load agent output from workspace (answer.json or INCIDENT_REPORT.md)."""
    # Try answer.json first
    answer_path = workspace / "agent_output" / "answer.json"
    if answer_path.exists():
        return answer_path.read_text()

    # Try INCIDENT_REPORT.md in any repo dir
    for child in workspace.iterdir():
        report = child / "INCIDENT_REPORT.md"
        if report.exists():
            return report.read_text()

    # Try SUPPORT_MAPPING.md
    for child in workspace.iterdir():
        mapping = child / "SUPPORT_MAPPING.md"
        if mapping.exists():
            return mapping.read_text()

    return ""


class CheckpointRunner:
    """Runs checkpoints for a task definition and produces a VerificationResult.

    When the task's verification_modes includes 'llm_curator' and an
    expected_solution.json exists, each checkpoint is also evaluated by
    the LLM judge. The final score per checkpoint is min(grep, judge).
    """

    def __init__(
        self,
        task: TaskDefinition,
        task_dir: Optional[Path] = None,
        workspace: Optional[Path] = None,
        judge_model: Optional[str] = None,
    ):
        self.task = task
        # task_dir is where the task.toml lives (verifier paths are relative to this)
        self.task_dir = (task_dir or Path(".")).resolve()
        # workspace is where repos are cloned
        self.workspace = (workspace or task.workspace_root).resolve()

        # Tier 2: LLM judge setup
        self._judge = None
        self._expected_solution: dict[str, Any] = {}
        self._agent_output: Optional[str] = None

        if "llm_curator" in task.verification_modes:
            self._expected_solution = _load_expected_solution(self.task_dir)
            if self._expected_solution:
                self._warn_unmapped_checkpoints()
                try:
                    from eb_verify.judge import LLMJudge

                    self._judge = LLMJudge(
                        model=judge_model or "cc:haiku",
                    )
                    logger.info(
                        "LLM judge enabled (model=%s)", judge_model or "cc:haiku"
                    )
                except Exception as exc:
                    logger.warning("Failed to init LLM judge: %s", exc)

    def _warn_unmapped_checkpoints(self) -> None:
        """Log a WARNING for any task.toml checkpoint absent from expected_solution.

        Without this, a partial expected_solution.json silently disables Tier 2
        capping for unmapped checkpoints — they fall back to grep_score with no
        operator-visible signal.
        """
        mapped = set((self._expected_solution.get("checkpoints") or {}).keys())
        declared = {cp.name for cp in self.task.checkpoints}
        missing = sorted(declared - mapped)
        for name in missing:
            logger.warning(
                "expected_solution.json missing checkpoint %r — LLM judge will be "
                "skipped for it, score falls back to grep_score (run "
                "scripts/validation/validate_expected_solutions.py to catch this "
                "before runtime)",
                name,
            )

    def sandbox_health_check(self) -> bool:
        """Check that all required repos exist under workspace."""
        for repo in self.task.repos:
            repo_path = self.workspace / repo.path
            if not repo_path.is_dir():
                print(f"[health] MISSING repo: {repo_path}")
                return False
            print(f"[health] OK: {repo_path}")
        return True

    def _infra_result(
        self, checkpoint: Checkpoint, error: InfraError
    ) -> CheckpointResult:
        """A checkpoint whose verifier never reached a verdict.

        `score` is a placeholder, never a measurement: `run_all` refuses to
        report a numeric total once any checkpoint carries an infra_error.
        """
        logger.error(
            "Checkpoint %r: verifier did not run (%s) — %s",
            checkpoint.name,
            error.cause,
            error.detail,
        )
        return CheckpointResult(
            name=checkpoint.name,
            weight=checkpoint.weight,
            passed=False,
            score=0.0,
            detail=error.detail,
            infra_error=error,
        )

    def run_checkpoint(self, checkpoint: Checkpoint) -> CheckpointResult:
        """Execute a single checkpoint verifier script and collect results.

        A verifier that does not reach a verdict (missing, escaping task_dir,
        timing out, crashing, or emitting no parseable score) yields an
        InfraError — never a fabricated score. See
        :func:`eb_verify.scorer_guard.run_verifier_subprocess`, which owns both
        halves of that rule.
        """
        env = checkpoint_env(self.workspace, self.task_dir, self.task.id)

        verdict = run_verifier_subprocess(
            checkpoint.verifier,
            base_dir=self.task_dir,
            argv_prefix=("bash",),
            cwd=self.workspace,
            env=env,
            timeout=checkpoint.timeout_seconds,
            checkpoint=checkpoint.name,
        )
        if isinstance(verdict, InfraError):
            return self._infra_result(checkpoint, verdict)

        return CheckpointResult(
            name=checkpoint.name,
            weight=checkpoint.weight,
            passed=verdict["passed"],
            score=verdict["score"],
            detail=verdict.get("detail", ""),
        )

    def _run_judge_checkpoint(
        self, checkpoint: Checkpoint, agent_output: str
    ) -> Optional[float]:
        """Run LLM judge for a checkpoint. Returns score or None if not applicable."""
        if self._judge is None:
            return None

        cp_data = self._expected_solution.get("checkpoints", {}).get(checkpoint.name)
        if cp_data is None:
            return None

        from eb_verify.judge import CheckpointJudgeInput

        judge_input = CheckpointJudgeInput(
            task_id=self.task.id,
            checkpoint_name=checkpoint.name,
            agent_output=agent_output,
            expected_solution=cp_data["expected_solution"],
            evaluation_criteria=cp_data.get("evaluation_criteria", []),
            checkpoint_weight=checkpoint.weight,
        )

        result = self._judge.evaluate_checkpoint(
            judge_input,
            task_description=self.task.description or self.task.prompt[:500],
            checkpoint_description=checkpoint.description,
        )

        logger.info(
            "  LLM judge: %s score=%.2f (%s) — %s",
            checkpoint.name,
            result.score,
            result.confidence,
            result.reasoning[:100],
        )
        return result.score

    def _grounding_required(self) -> bool:
        """True when the task's ground truth demands grounded citations."""
        return bool(
            self.task.ground_truth is not None
            and self.task.ground_truth.require_grounded_citations
        )

    def validate_artifacts(self) -> list[dict]:
        """Validate all required artifacts using plugin validators.

        When the task's ground truth sets require_grounded_citations, the flag
        is forwarded to validators whose validate() declares that keyword.
        The ArtifactValidator protocol is validate(workspace) only, so
        inspect.signature is the capability check: a validator that cannot
        enforce groundedness must fail the artifact explicitly rather than
        run without the gate the task demands.
        """
        require_grounded = self._grounding_required()
        results = []
        for artifact_type in self.task.artifacts.required:
            validator = get_validator(artifact_type)
            if validator is None:
                results.append(
                    {
                        "type": artifact_type,
                        "valid": False,
                        "detail": f"No validator registered for type: {artifact_type}",
                    }
                )
                continue
            if require_grounded:
                params = inspect.signature(validator.validate).parameters
                if "require_grounded_citations" not in params:
                    results.append(
                        {
                            "type": artifact_type,
                            "valid": False,
                            "detail": (
                                "require_grounded_citations is set but the "
                                f"'{artifact_type}' validator does not support "
                                "grounded citations"
                            ),
                        }
                    )
                    continue
                # The signature probe above verified the kwarg exists; the
                # cast records that runtime fact for the type checker (the
                # base ArtifactValidator protocol is validate(workspace) only).
                capable = cast(_GroundednessCapableValidator, validator)
                result = capable.validate(
                    self.workspace, require_grounded_citations=True
                )
            else:
                result = validator.validate(self.workspace)
            results.append(
                {
                    "type": artifact_type,
                    "valid": result.valid,
                    "detail": result.detail,
                }
            )
        return results

    def run_all(self, output_path: str | Path = "reward.txt") -> VerificationResult:
        """Run full verification: health check, checkpoints, artifacts, scoring.

        When llm_curator is active, each checkpoint gets two scores:
          - Tier 1 (grep): from the shell script verifier
          - Tier 2 (judge): from the LLM judge against expected_solution.json
        Final score = min(grep, judge) — the judge caps inflated grep scores.
        """
        # Health check (non-fatal in prototype — repos may not be cloned)
        healthy = self.sandbox_health_check()
        if not healthy:
            print("[runner] WARNING: sandbox health check failed, continuing anyway")

        # Load agent output once for LLM judge (if active)
        agent_output = ""
        if self._judge is not None:
            agent_output = _load_agent_output(self.workspace)
            if not agent_output:
                logger.warning(
                    "LLM judge active but no agent output found in workspace"
                )

        # Run checkpoints in order
        checkpoint_results = []
        # Set once the judge reaches no verdict. The first such failure already
        # routes the whole run to the re-run channel, so every later judge call
        # would return a number nobody reads — and a backend outage fails all of
        # them anyway, at a 120s timeout plus retries each.
        judge_failure: Optional[str] = None
        for cp in self.task.checkpoints:
            print(f"[runner] Running checkpoint: {cp.name}")

            # Tier 1: grep-based verifier
            grep_result = self.run_checkpoint(cp)

            # A verifier that never reached a verdict has no score to cap.
            # Running the judge here would min() a real judgement against a
            # placeholder 0.0 and launder the result into a plausible number.
            if grep_result.infra_error is not None:
                checkpoint_results.append(grep_result)
                print(f"[runner]   INFRA {grep_result.detail}")
                continue

            grep_score = grep_result.score
            detail_parts = [grep_result.detail] if grep_result.detail else []

            # Tier 2: LLM judge (if active and agent output available)
            final_score = grep_score
            if agent_output and self._judge is not None:
                judge_score = None
                if judge_failure is None:
                    try:
                        judge_score = self._run_judge_checkpoint(cp, agent_output)
                    except Exception as exc:
                        logger.warning("LLM judge failed for %s: %s", cp.name, exc)
                        judge_failure = str(exc)

                if judge_failure is not None:
                    # No verdict, so the Tier-2 ceiling cannot be applied. Grep
                    # alone would stand as an un-capped score, so declare the
                    # infra failure: INFRA_SENTINEL in the detail is what routes
                    # the run, not the score below.
                    detail_parts.append(
                        f"{INFRA_SENTINEL}: LLM judge failed: {judge_failure}"
                    )
                    final_score = 0.0
                elif judge_score is not None:
                    final_score = min(grep_score, judge_score)
                    detail_parts.append(
                        f"grep={grep_score:.2f} judge={judge_score:.2f} final={final_score:.2f}"
                    )

            result = CheckpointResult(
                name=cp.name,
                weight=cp.weight,
                passed=final_score > 0.0,
                score=final_score,
                detail="; ".join(detail_parts),
            )
            checkpoint_results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"[runner]   {status} score={result.score:.2f}")

        # Validate artifacts
        artifact_results = self.validate_artifacts()

        # A checkpoint that never reached a verdict has no score, so the run has
        # no total: averaging its placeholder against the checkpoints that DID
        # run yields a plausible number indistinguishable from a real one. Every
        # failure is in checkpoint_results (and reward.txt); the routed dict
        # names the first, which is what the re-run channel triages on.
        infra_errors = [
            cr.infra_error for cr in checkpoint_results if cr.infra_error is not None
        ]

        total = 0.0 if infra_errors else compute_score(checkpoint_results)
        score_gates: list[str] = []

        # Grounded-citations gate: when the task demands grounded citations,
        # a required artifact failing validation zeroes the total — otherwise
        # the flag would be a side channel with no effect on the score or
        # exit code. Tasks without the flag keep legacy scoring untouched.
        # Skipped on an invalid run: there is no score for a gate to force down.
        if not infra_errors and self._grounding_required():
            failed_types = [
                ar["type"] for ar in artifact_results if not ar["valid"]
            ]
            if failed_types:
                gate_msg = (
                    "require_grounded_citations: required artifact(s) failed "
                    f"validation ({', '.join(failed_types)}); total_score "
                    "forced to 0.0"
                )
                score_gates.append(gate_msg)
                print(f"[runner] SCORE GATE: {gate_msg}")
                total = 0.0

        verification = VerificationResult(
            task_id=self.task.id,
            checkpoint_results=checkpoint_results,
            artifact_results=artifact_results,
            total_score=total,
            score_gates=score_gates,
            verifier_infra_error=(
                infra_errors[0].as_verifier_error() if infra_errors else None
            ),
        )

        # Write reward.txt
        reward_path = write_reward(verification, output_path)
        if infra_errors:
            print(
                f"[runner] Wrote {reward_path} — INVALID RUN: "
                f"{len(infra_errors)} verifier(s) did not run"
            )
        else:
            print(f"[runner] Wrote {reward_path} — total_score={total:.4f}")

        return verification

    def run_single(self, checkpoint_name: str) -> CheckpointResult:
        """Run a single checkpoint by name."""
        for cp in self.task.checkpoints:
            if cp.name == checkpoint_name:
                return self.run_checkpoint(cp)
        raise ValueError(f"Checkpoint not found: {checkpoint_name}")
