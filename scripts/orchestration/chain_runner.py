#!/usr/bin/env python3
"""
Session-chain orchestrator for EnterpriseBench.

Reads a chain task definition and executes N sessions sequentially,
with git-branch handoff and milestone verification between sessions.

Usage:
    python -m scripts.orchestration.chain_runner benchmarks/chain_example/task.toml
    python -m scripts.orchestration.chain_runner benchmarks/chain_example/task.toml --simulate
"""

import argparse
import json
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Support both toml (Python 3.11+) and tomli
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

from orchestration.session import SessionConfig, SessionResult, run_session
from orchestration.milestone import SessionScore, run_session_milestones
from eb_verify.scorer_guard import InfraError

logger = logging.getLogger(__name__)

SESSION_FAILURE_REASON = "session_failed"


def _session_failure(session_number: int, detail: str) -> dict:
    """A configured session never completed, so the chain has no score.

    A distinct CAUSE from a verifier failure, carried in the same shape and kept
    in its own ``ChainResult`` field: an operator sent to re-run must be told the
    agent died, not that a verifier broke.
    """
    return InfraError(
        reason=SESSION_FAILURE_REASON,
        stage="session",
        detail=detail,
        context={"session_number": session_number},
    ).as_verifier_error()


@dataclass
class ChainTaskDefinition:
    """Parsed chain task with per-session configuration."""

    task_id: str
    suite: str
    difficulty: str
    session_count: int
    repos: list[dict]
    sessions: list[SessionConfig]
    final_checkpoints: list[dict]
    metadata: dict = field(default_factory=dict)
    simulation: dict = field(default_factory=dict)


@dataclass
class ChainResult:
    """Complete result of running a chain task.

    ``total_score``/``final_score`` are ``None`` whenever either invalidity
    channel is set, and the run belongs in the re-run channel rather than the
    results table:

    * ``verifier_infra_error`` — a milestone verifier never reached a verdict, so
      there is no score to record.
    * ``session_failure`` — a configured session never completed, so the agent
      never did the work the checkpoints would be scoring.

    They are independent, and both are reported when both occur.
    """

    task_id: str
    total_sessions: int = 0
    session_results: list[SessionResult] = field(default_factory=list)
    milestone_scores: list[SessionScore] = field(default_factory=list)
    final_score: Optional[float] = None
    total_score: Optional[float] = None
    verifier_infra_error: Optional[dict] = None
    session_failure: Optional[dict] = None

    @property
    def is_invalid(self) -> bool:
        """True if this chain has no score, for either reason."""
        return self.session_failure is not None or self.verifier_infra_error is not None

    @property
    def sessions_completed(self) -> int:
        return sum(1 for sr in self.session_results if sr.success)

    def summary(self) -> str:
        lines = [
            f"Chain Result: {self.task_id}",
            f"Sessions: {len(self.session_results)} / {self.total_sessions}",
            "",
        ]
        for sr in self.session_results:
            status = "OK" if sr.success else f"FAIL ({sr.error})"
            branch = sr.branch_state.branch_name if sr.branch_state else "N/A"
            lines.append(f"  Session {sr.session_number}: {status} -> {branch}")

        lines.append("")
        for ms in self.milestone_scores:
            total = "INVALID" if ms.total_score is None else f"{ms.total_score:.2f}"
            lines.append(f"  Session {ms.session_number} milestones: {total}")
            for m in ms.milestones:
                if m.infra_error is not None:
                    lines.append(
                        f"    {m.milestone_name}: INVALID "
                        f"(no verdict: {m.infra_error.cause}) {m.infra_error.detail}"
                    )
                    continue
                status = "PASS" if m.passed else "FAIL"
                lines.append(
                    f"    {m.milestone_name}: {status} ({m.score:.2f}) {m.message}"
                )

        if self.session_failure is not None:
            lines.append(
                f"\nINVALID RUN — session "
                f"{self.session_failure['session_number']} of {self.total_sessions} "
                f"never completed; the agent did not do the work the checkpoints "
                f"would score, so this chain has no score and must be re-run."
            )
            lines.append(f"  {self.session_failure['detail']}")
        if self.verifier_infra_error is not None:
            lines.append(
                f"\nINVALID RUN — a milestone verifier never reached a verdict "
                f"({self.verifier_infra_error['cause']}); this chain has no score "
                f"and must be re-run."
            )
            lines.append(f"  {self.verifier_infra_error['detail']}")

        if self.is_invalid:
            return "\n".join(lines)

        # A None score with no infra error means no milestone ran at all. Render it
        # as unscored — the fields used to default to 0.0 and print that as earned.
        final = "NOT SCORED" if self.final_score is None else f"{self.final_score:.2f}"
        total = "NOT SCORED" if self.total_score is None else f"{self.total_score:.2f}"
        lines.append(f"\nFinal score: {final}")
        lines.append(f"Total score: {total}")
        return "\n".join(lines)


def parse_chain_task(toml_path: str) -> ChainTaskDefinition:
    """Parse a chain task.toml file into a ChainTaskDefinition."""
    path = Path(toml_path)
    if not path.exists():
        raise FileNotFoundError(f"Task file not found: {toml_path}")

    if tomllib is None:
        raise ImportError(
            "No TOML parser available. Install tomli (pip install tomli) "
            "or use Python 3.11+."
        )

    with open(path, "rb") as f:
        data = tomllib.load(f)

    task = data["task"]
    if task.get("session_type") != "chain":
        raise ValueError(
            f"Task {task['id']} is not a chain task (session_type={task.get('session_type')})"
        )

    session_count = task.get("session_count", 2)

    # Parse per-session configs from [[sessions]] array
    sessions_data = data.get("sessions", [])
    sessions = []
    for i, s in enumerate(sessions_data):
        sessions.append(
            SessionConfig(
                session_number=i + 1,
                prompt=s["prompt"],
                milestones=s.get("milestones", []),
                context=s.get("context", {}),
            )
        )

    task_def = ChainTaskDefinition(
        task_id=task["id"],
        suite=task["suite"],
        difficulty=task["difficulty"],
        session_count=session_count,
        repos=data.get("repos", []),
        sessions=sessions,
        final_checkpoints=data.get("checkpoints", []),
        metadata=data.get("metadata", {}),
        simulation=data.get("simulation", {}),
    )
    _validate_chain_task(task_def)
    return task_def


def _validate_chain_task(task_def: ChainTaskDefinition) -> None:
    """Reject a task whose ``session_count`` is not the number of sessions.

    The final-checkpoint gate compares completed sessions against ``session_count``,
    so a count that lies defeats it silently: ``session_count == 0`` satisfies the
    gate vacuously and scores a workspace no agent touched, and a count higher than
    the sessions defined gates every healthy chain to no score.
    """
    if task_def.session_count < 1:
        raise ValueError(
            f"chain {task_def.task_id}: session_count={task_def.session_count} — a "
            f"chain with no sessions has no agent work to score"
        )
    if len(task_def.sessions) != task_def.session_count:
        raise ValueError(
            f"chain {task_def.task_id}: session_count={task_def.session_count} but "
            f"{len(task_def.sessions)} session(s) defined — the chain cannot be scored "
            f"against a session count it does not have"
        )


def _log_chain_outcome(chain_result: ChainResult) -> None:
    """Log every invalidity channel that fired, not just the first: an operator who
    fixes only the one they heard about would re-run straight into the other."""
    if chain_result.session_failure is not None:
        logger.error(
            "Chain INVALID — session %d never completed, so it has no score: %s",
            chain_result.session_failure["session_number"],
            chain_result.session_failure["detail"],
        )
    if chain_result.verifier_infra_error is not None:
        logger.error(
            "Chain INVALID — a milestone verifier never reached a verdict: %s",
            chain_result.verifier_infra_error["detail"],
        )
    if chain_result.is_invalid:
        return
    if chain_result.total_score is None:
        logger.info("Chain complete. No milestones scored.")
    else:
        logger.info("Chain complete. Total score: %.2f", chain_result.total_score)


def run_chain(
    task_def: ChainTaskDefinition,
    workspace_root: str | None = None,
    simulate: bool = False,
    agent_callable=None,
    task_dir: str = "",
    mode: str = "baseline",
) -> ChainResult:
    """Execute a full session chain.

    1. For each session:
       a. Set up workspace (fresh env with previous session's branch)
       b. Run agent or simulation
       c. Commit to session branch
       d. Run milestone verifiers (if not the last session)
    2. Only once EVERY configured session has succeeded: run the final checkpoints
       and compute the total score.
    """
    _validate_chain_task(task_def)

    chain_result = ChainResult(
        task_id=task_def.task_id, total_sessions=task_def.session_count
    )

    if workspace_root is None:
        workspace_root = tempfile.mkdtemp(prefix=f"eb-chain-{task_def.task_id}-")

    logger.info(
        "Starting chain: %s (%d sessions) in %s",
        task_def.task_id,
        task_def.session_count,
        workspace_root,
    )

    previous_branch_state = None

    # Propagate mode to all session configs
    for sc in task_def.sessions:
        sc.mode = mode

    for session_config in task_def.sessions:
        session_num = session_config.session_number

        # Determine simulation actions for this session
        sim_actions = None
        if simulate and task_def.simulation:
            session_key = f"session_{session_num}"
            sim_actions = task_def.simulation.get(session_key, {}).get("actions", None)

        # Run the session
        session_result = run_session(
            session_config=session_config,
            repos=task_def.repos,
            task_id=task_def.task_id,
            workspace_root=workspace_root,
            previous_branch_state=previous_branch_state,
            simulation_actions=sim_actions,
            agent_callable=None if simulate else agent_callable,
        )
        chain_result.session_results.append(session_result)

        if not session_result.success:
            logger.error("Session %d failed, aborting chain.", session_num)
            chain_result.session_failure = _session_failure(
                session_number=session_num,
                detail=session_result.error or "session did not complete",
            )
            break

        previous_branch_state = session_result.branch_state

        # Run milestones between sessions (not after the final one)
        if session_config.milestones and session_num < task_def.session_count:
            milestone_score = run_session_milestones(
                milestones=session_config.milestones,
                workspace_path=workspace_root,
                session_number=session_num,
                task_dir=task_dir,
            )
            chain_result.milestone_scores.append(milestone_score)

    # Gated on every session: the checkpoints score whatever is on disk, so on a
    # workspace the agent never worked in they reach real verdicts, not failures.
    if (
        task_def.final_checkpoints
        and chain_result.sessions_completed == task_def.session_count
    ):
        final_milestone = run_session_milestones(
            milestones=[
                {"name": cp["name"], "verifier": cp["verifier"]}
                for cp in task_def.final_checkpoints
            ],
            workspace_path=workspace_root,
            session_number=task_def.session_count,
            task_dir=task_dir,
        )
        chain_result.milestone_scores.append(final_milestone)

    _compute_total_score(chain_result, task_def)
    _log_chain_outcome(chain_result)
    return chain_result


def _compute_total_score(chain_result: ChainResult, task_def: ChainTaskDefinition):
    """Compute weighted total score from milestone results and final checkpoints.

    No score at all if either invalidity channel is set: a partial chain's
    surviving milestones are still in ``milestone_scores``, and a milestone with no
    verdict has no number to fold into the weighted sum.
    """
    all_milestone_results = [
        mr for ms in chain_result.milestone_scores for mr in ms.milestones
    ]

    infra = next(
        (mr.infra_error for mr in all_milestone_results if mr.infra_error is not None),
        None,
    )
    if infra is not None:
        chain_result.verifier_infra_error = infra.as_verifier_error()

    if chain_result.is_invalid or not all_milestone_results:
        chain_result.total_score = None
        chain_result.final_score = None
        return

    # Final checkpoints have explicit weights; milestones are equal-weighted
    final_cp_weights = {
        cp["name"]: cp.get("weight", 1.0) for cp in task_def.final_checkpoints
    }

    weighted_sum = 0.0
    weight_sum = 0.0

    for mr in all_milestone_results:
        if mr.milestone_name in final_cp_weights:
            w = final_cp_weights[mr.milestone_name]
        else:
            # Inter-session milestones: small fixed weight
            w = 0.1
        weighted_sum += mr.score * w
        weight_sum += w

    chain_result.total_score = weighted_sum / weight_sum if weight_sum > 0 else 0.0
    chain_result.final_score = chain_result.total_score


def main():
    parser = argparse.ArgumentParser(description="Run a session-chain task")
    parser.add_argument("task_toml", help="Path to the chain task.toml file")
    parser.add_argument(
        "--simulate", action="store_true", help="Run in simulation mode (no real agent)"
    )
    parser.add_argument(
        "--mode",
        choices=["baseline", "mcp_only", "hybrid"],
        default="baseline",
        help="Tool access mode (default: baseline)",
    )
    parser.add_argument(
        "--workspace", default=None, help="Workspace root directory (default: temp dir)"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    # Passthrough args forwarded by run_benchmark.py (accepted but not used here)
    parser.add_argument("--source", choices=["mirror", "upstream"])
    parser.add_argument("--agent", type=str)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--account", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    task_def = parse_chain_task(args.task_toml)
    task_dir = str(Path(args.task_toml).parent.resolve())

    result = run_chain(
        task_def=task_def,
        workspace_root=args.workspace,
        simulate=args.simulate,
        task_dir=task_dir,
        mode=args.mode,
    )

    print("\n" + "=" * 60)
    print(result.summary())
    print("=" * 60)

    # Write result JSON. `total_score` is null for an invalid run — a reader must
    # never be handed a number that came from a verifier which did not score, or
    # from a chain whose sessions never ran.
    result_path = Path(task_dir) / "chain_result.json"
    payload = {
        "task_id": result.task_id,
        "total_score": result.total_score,
        "sessions_completed": result.sessions_completed,
        "sessions_total": result.total_sessions,
    }
    if result.verifier_infra_error is not None:
        payload["verifier_infra_error"] = result.verifier_infra_error
    if result.session_failure is not None:
        payload["session_failure"] = result.session_failure

    with open(result_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nResult written to: {result_path}")

    if result.is_invalid:
        # Nonzero exit routes the run to run_benchmark's error channel, so an
        # unscoreable chain is never recorded as a completed one.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
