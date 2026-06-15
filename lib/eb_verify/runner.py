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

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from eb_verify.errors import VerifierInfraError
from eb_verify.task_parser import TaskDefinition, Checkpoint
from eb_verify.scoring import (
    CheckpointResult,
    VerificationResult,
    compute_score,
    write_reward,
    write_score_result,
)
from eb_verify.plugins import get_validator

logger = logging.getLogger(__name__)


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

    def _safe_verifier_path(self, verifier: str) -> Path:
        """Resolve verifier path and assert it stays within task_dir."""
        resolved = (self.task_dir / verifier).resolve()
        if (
            not str(resolved).startswith(str(self.task_dir) + "/")
            and resolved != self.task_dir
        ):
            raise ValueError(
                f"Verifier path escapes task_dir: {verifier!r} -> {resolved}"
            )
        return resolved

    def run_checkpoint(self, checkpoint: Checkpoint) -> CheckpointResult:
        """Execute a single checkpoint verifier script and collect results."""
        try:
            verifier_path = self._safe_verifier_path(checkpoint.verifier)
        except ValueError as e:
            return CheckpointResult(
                name=checkpoint.name,
                weight=checkpoint.weight,
                passed=False,
                score=0.0,
                detail=str(e),
            )

        if not verifier_path.exists():
            return CheckpointResult(
                name=checkpoint.name,
                weight=checkpoint.weight,
                passed=False,
                score=0.0,
                detail=f"Verifier script not found: {verifier_path}",
            )

        env = os.environ.copy()
        env["WORKSPACE"] = str(self.workspace)
        env["TASK_DIR"] = str(self.task_dir)
        env["TASK_ID"] = self.task.id

        try:
            result = subprocess.run(
                ["bash", str(verifier_path)],
                capture_output=True,
                text=True,
                timeout=checkpoint.timeout_seconds,
                cwd=str(self.workspace),
                env=env,
            )

            # Convention: a verifier prints JSON to stdout with
            # {"score": 0.0-1.0, "detail": "..."}. Verifiers that do not follow
            # the JSON convention signal pass/fail purely via exit code
            # (0 = pass / 1.0, nonzero = fail / 0.0).
            #
            # The danger this guards against: a verifier that *meant* to emit the
            # score JSON but crashed mid-output (truncated/malformed JSON) or
            # emitted a non-numeric score, while still exiting 0, must NOT fall
            # through to the exit-code branch — that would silently award a full
            # PASS for a broken verifier. Such output is an infra failure.
            stdout = result.stdout.strip()
            data = None
            if stdout:
                try:
                    data = json.loads(stdout)
                except json.JSONDecodeError as exc:
                    # Output that looks like an attempted JSON emission but does
                    # not parse means the verifier crashed mid-output.
                    if stdout[:1] in ("{", "["):
                        raise VerifierInfraError(
                            f"checkpoint {checkpoint.name!r}: verifier emitted "
                            f"malformed JSON (exit {result.returncode}): "
                            f"{stdout[:200]!r}"
                        ) from exc
                    # Otherwise it is a plain-text exit-code verifier.

            if isinstance(data, dict):
                raw_score = data.get("score", 0.0)
                try:
                    score = max(0.0, min(1.0, float(raw_score)))
                except (TypeError, ValueError) as exc:
                    raise VerifierInfraError(
                        f"checkpoint {checkpoint.name!r}: verifier JSON 'score' "
                        f"is not numeric: {raw_score!r}"
                    ) from exc
                detail = data.get("detail", "")
                passed = score > 0.0
            else:
                # No score JSON object: legitimate exit-code verifier.
                passed = result.returncode == 0
                score = 1.0 if passed else 0.0
                detail = stdout or result.stderr.strip()

            return CheckpointResult(
                name=checkpoint.name,
                weight=checkpoint.weight,
                passed=passed,
                score=score,
                detail=detail,
            )
        except VerifierInfraError:
            # Infra failures must propagate — never coerce to a score.
            raise
        except subprocess.TimeoutExpired:
            return CheckpointResult(
                name=checkpoint.name,
                weight=checkpoint.weight,
                passed=False,
                score=0.0,
                detail=f"Verifier timed out ({checkpoint.timeout_seconds}s)",
            )
        except Exception as e:
            return CheckpointResult(
                name=checkpoint.name,
                weight=checkpoint.weight,
                passed=False,
                score=0.0,
                detail=f"Error running verifier: {e}",
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

    def validate_artifacts(self) -> list[dict]:
        """Validate all required artifacts using plugin validators."""
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
            result = validator.validate(self.workspace)
            results.append(
                {
                    "type": artifact_type,
                    "valid": result.valid,
                    "detail": result.detail,
                }
            )
        return results

    def run_all(
        self,
        output_path: str | Path = "reward.txt",
        *,
        score_result_path: str | Path | None = "score_result.json",
        task_time_seconds: float | None = None,
        token_cost_usd: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> VerificationResult:
        """Run full verification: health check, checkpoints, artifacts, scoring.

        When llm_curator is active, each checkpoint gets two scores:
          - Tier 1 (grep): from the shell script verifier
          - Tier 2 (judge): from the LLM judge against expected_solution.json
        Final score = min(grep, judge) — the judge caps inflated grep scores.

        Args:
            output_path: Path to write the legacy human-readable
                ``reward.txt`` summary.
            score_result_path: Path to write the unified ScoreResult JSON.
                Set to ``None`` to skip emission. Defaults to
                ``score_result.json`` next to ``reward.txt``.
            task_time_seconds: Optional wall-clock duration plumbed in from
                the harness; flows into ``diagnostics.task_time_seconds``.
            token_cost_usd: Optional per-task token cost in USD plumbed in
                from the harness; flows into ``diagnostics.token_cost_usd``.
            input_tokens: Optional raw input-token count plumbed in from the
                harness; flows into ``diagnostics.input_tokens``.
            output_tokens: Optional raw output-token count plumbed in from the
                harness; flows into ``diagnostics.output_tokens``.
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
        for cp in self.task.checkpoints:
            print(f"[runner] Running checkpoint: {cp.name}")

            # Tier 1: grep-based verifier
            grep_result = self.run_checkpoint(cp)
            grep_score = grep_result.score
            detail_parts = [grep_result.detail] if grep_result.detail else []

            # Tier 2: LLM judge (if active and agent output available)
            final_score = grep_score
            if agent_output and self._judge is not None:
                judge_score = self._run_judge_checkpoint(cp, agent_output)
                if judge_score is not None:
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

        # Compute score
        total = compute_score(checkpoint_results)

        verification = VerificationResult(
            task_id=self.task.id,
            checkpoint_results=checkpoint_results,
            artifact_results=artifact_results,
            total_score=total,
        )

        # Write reward.txt (legacy human-readable summary)
        reward_path = write_reward(verification, output_path)
        print(f"[runner] Wrote {reward_path} — total_score={total:.4f}")

        # Write the unified ScoreResult JSON next to reward.txt unless
        # explicitly suppressed by the caller.
        if score_result_path is not None:
            sr_path = Path(score_result_path)
            if not sr_path.is_absolute():
                sr_path = Path(reward_path).resolve().parent / sr_path
            written = write_score_result(
                verification,
                sr_path,
                task_time_seconds=task_time_seconds,
                token_cost_usd=token_cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            print(f"[runner] Wrote {written} — unified ScoreResult")

        return verification

    def run_single(self, checkpoint_name: str) -> CheckpointResult:
        """Run a single checkpoint by name."""
        for cp in self.task.checkpoints:
            if cp.name == checkpoint_name:
                return self.run_checkpoint(cp)
        raise ValueError(f"Checkpoint not found: {checkpoint_name}")
