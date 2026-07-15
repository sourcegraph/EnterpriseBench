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

from orchestration.session import (
    SessionConfig,
    SessionFailure,
    SessionResult,
    run_session,
)
from orchestration.milestone import (
    MilestoneResult,
    SessionScore,
    run_session_milestones,
)
from orchestration.runner_cli import add_passthrough_args
from eb_verify.scorer_guard import InfraError

logger = logging.getLogger(__name__)

SESSION_FAILURE_REASON = "session_failed"
NO_AGENT_REASON = "no_agent_configured"


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


def _agent_failure(failure: SessionFailure, session_number: int) -> dict:
    """The MODEL failed the task, so the chain has a score: the 0.0 it earned.

    Deliberately NOT an :class:`InfraError`. That class is the choke point for the
    invalidity channels — everything built through it means "no verdict, re-run" —
    and an agent failure is the opposite claim: a real measurement that belongs in
    the results table. Reusing the shape would put this run one ``is_invalid``
    reader away from the exclusion it exists to avoid.
    """
    return {
        "reason": failure.reason,
        "stage": failure.stage,
        "detail": failure.detail,
        "session_number": session_number,
    }


def _no_agent_configured() -> dict:
    """No agent was wired in, so the chain has no agent work to score.

    Its own cause, not ``session_failure``: the remedy is a harness, not a re-run.
    """
    return InfraError(
        reason=NO_AGENT_REASON,
        stage="chain",
        detail=(
            "chain invoked with no agent_callable and without simulate — no agent "
            "work was produced, so there is nothing to score. Pass simulate to run "
            "the simulation scaffold on purpose."
        ),
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

    ``total_score``/``final_score`` are ``None`` whenever any invalidity channel
    is set, and the run belongs in the re-run channel rather than the results
    table:

    * ``verifier_infra_error`` — a milestone verifier never reached a verdict, so
      there is no score to record.
    * ``session_failure`` — a configured session never completed for a reason that
      is not the model's, so the agent never did the work the checkpoints would be
      scoring.
    * ``agent_not_configured`` — no agent was wired into the chain at all, so no
      session ran and the remedy is a harness, not a re-run.

    They are independent, and each is reported when it occurs.

    ``agent_failure`` is NOT one of them. It records that the MODEL failed the
    task, which is a real measurement worth exactly the 0.0 it earned, so the run
    keeps its score and stays in the results table. Excluding it would be the
    upward bias described on :class:`~orchestration.session.AgentFailure`, which
    is where the failure gets typed (bead jma9y).

    ``simulated`` records that the score came from marker files the scaffold wrote,
    not from agent work; nothing else on the result distinguishes the two.
    """

    task_id: str
    total_sessions: int = 0
    session_results: list[SessionResult] = field(default_factory=list)
    milestone_scores: list[SessionScore] = field(default_factory=list)
    final_score: Optional[float] = None
    total_score: Optional[float] = None
    simulated: bool = False
    verifier_infra_error: Optional[dict] = None
    session_failure: Optional[dict] = None
    agent_not_configured: Optional[dict] = None
    agent_failure: Optional[dict] = None

    @property
    def is_invalid(self) -> bool:
        """True if this chain has no score, for any reason.

        ``agent_failure`` is absent by design: the model failing is a verdict, not
        the absence of one.
        """
        return (
            self.session_failure is not None
            or self.verifier_infra_error is not None
            or self.agent_not_configured is not None
        )

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
                if m.not_reached:
                    # Not "FAIL": no verifier ran. The zero is the work not done,
                    # not a verdict on work that was.
                    lines.append(
                        f"    {m.milestone_name}: NOT REACHED (0.00) {m.message}"
                    )
                    continue
                status = "PASS" if m.passed else "FAIL"
                lines.append(
                    f"    {m.milestone_name}: {status} ({m.score:.2f}) {m.message}"
                )

        if self.agent_not_configured is not None:
            lines.append(
                f"\nINVALID RUN — no agent was wired into this chain, so none of "
                f"its {self.total_sessions} session(s) ran; there is no agent work "
                f"for the checkpoints to score."
            )
            lines.append(f"  {self.agent_not_configured['detail']}")
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

        # Reported alongside the score, never instead of it: the run is valid and
        # the number below is the one the model earned by failing.
        if self.agent_failure is not None:
            lines.append(
                f"\nAGENT FAILURE — the agent failed session "
                f"{self.agent_failure['session_number']} of {self.total_sessions} "
                f"({self.agent_failure['reason']}); the chain is scored on the work "
                f"it completed before then. This is a real result, not a re-run."
            )
            lines.append(f"  {self.agent_failure['detail']}")

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
    if not task_def.final_checkpoints:
        raise ValueError(
            f"chain {task_def.task_id}: no final checkpoints — they are the only "
            f"measure of whether the chain's task got done, and without them an "
            f"agent that dies mid-chain scores only the milestones it passed first, "
            f"which the agent-failure path would publish as its total"
        )


def _log_chain_outcome(chain_result: ChainResult) -> None:
    """Log every invalidity channel that fired, not just the first: an operator who
    fixes only the one they heard about would re-run straight into the others."""
    if chain_result.agent_not_configured is not None:
        logger.error(
            "Chain INVALID — no agent was wired in, so no session ran: %s",
            chain_result.agent_not_configured["detail"],
        )
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
    elif chain_result.agent_failure is not None:
        logger.info(
            "Chain scored. The agent failed session %d (%s) and earned %.2f.",
            chain_result.agent_failure["session_number"],
            chain_result.agent_failure["reason"],
            chain_result.total_score,
        )
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

    Simulation is opt-in, never a fallback: an absent ``agent_callable`` is an
    invalid run, not an invitation to simulate one and score it.

    No agent and no ``simulate`` is an invalid run: returned as a ChainResult with
    ``agent_not_configured`` set, not raised — it is the state every production
    invocation is in, so the CLI has to write it to disk. Both together raises
    ValueError: only a caller with a bug can ask for that.
    """
    _validate_chain_task(task_def)

    if agent_callable is not None and simulate:
        raise ValueError(
            f"chain {task_def.task_id}: agent_callable and simulate are mutually "
            f"exclusive — the agent would be discarded and the simulation scored "
            f"in its place."
        )

    chain_result = ChainResult(
        task_id=task_def.task_id,
        total_sessions=task_def.session_count,
        simulated=simulate,
    )

    # The completed-session gate below cannot catch this: simulated sessions succeed.
    if agent_callable is None and not simulate:
        chain_result.agent_not_configured = _no_agent_configured()
        _log_chain_outcome(chain_result)
        return chain_result

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
            agent_callable=agent_callable,
            simulate=simulate,
        )
        chain_result.session_results.append(session_result)

        # Read the type; never re-derive it from the message. The stage that
        # raised is the only place that knew whose failure it was.
        failure = session_result.failure
        if failure is not None:
            if failure.is_agent:
                logger.error(
                    "Session %d: the agent failed the task (%s) — scoring the chain "
                    "on what it managed before it died.",
                    session_num,
                    failure.reason,
                )
                chain_result.agent_failure = _agent_failure(failure, session_num)
            else:
                logger.error("Session %d failed, aborting chain.", session_num)
                chain_result.session_failure = _session_failure(
                    session_number=session_num, detail=failure.detail
                )
            break

        previous_branch_state = session_result.branch_state

        if session_config.milestones and _milestones_apply(
            session_num, task_def.session_count
        ):
            milestone_score = run_session_milestones(
                milestones=session_config.milestones,
                workspace_path=workspace_root,
                session_number=session_num,
                task_dir=task_dir,
            )
            chain_result.milestone_scores.append(milestone_score)

    if chain_result.agent_failure is not None:
        _zero_fill_unreached_milestones(chain_result, task_def)

    # The checkpoints score whatever is on disk, so they must not run on a
    # workspace whose provenance is unknown: after an INFRA failure what is there
    # is a harness artifact, not the model's work, and scoring it reaches a real
    # verdict on the wrong thing. After an AGENT failure the workspace is exactly
    # what the model managed before it died, which is what it earned.
    #
    # `final_checkpoints` is non-empty by validation, so it is not re-checked here.
    if (
        chain_result.sessions_completed == task_def.session_count
        or chain_result.agent_failure is not None
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


def _milestones_apply(session_num: int, session_count: int) -> bool:
    """Whether a session's milestones count toward the chain's score.

    The last session's do not: the final checkpoints measure that session's work.
    Shared by the run path and the zero-fill so the two cannot drift apart.
    """
    return session_num < session_count


def _zero_fill_unreached_milestones(
    chain_result: ChainResult, task_def: ChainTaskDefinition
) -> None:
    """Score 0.0 for the milestones the agent died before reaching.

    Dropping them instead would shrink the denominator and reweight the milestones
    the agent DID pass — the partial mean ``SessionScore.total_score`` refuses to
    compute, arriving one level up. The agent earned these zeros by not getting to
    them.

    This is a fill, not a verdict: the verifier never ran, so there is no
    measurement here, only the fact that the work it would have measured was never
    done. That is why ``infra_error`` stays ``None`` — nothing broke — and why
    this runs on the AGENT path alone. The identical zero on an infra failure
    would be the false zero of bead s58f: there, the work may well have been done
    and the harness is what broke.
    """
    # What actually ran, read off the record rather than re-derived from the
    # completed-session count: the count only stands in for it because the loop
    # breaks at the failure, and a fill for a session already scored would double
    # it.
    already_scored = {ms.session_number for ms in chain_result.milestone_scores}
    for session_config in task_def.sessions:
        session_num = session_config.session_number
        if not session_config.milestones or not _milestones_apply(
            session_num, task_def.session_count
        ):
            continue
        if session_num in already_scored:
            continue
        chain_result.milestone_scores.append(
            SessionScore(
                session_number=session_num,
                milestones=[
                    MilestoneResult(
                        session_number=session_num,
                        milestone_name=m["name"],
                        passed=False,
                        score=0.0,
                        message="the agent never reached this milestone",
                        not_reached=True,
                    )
                    for m in session_config.milestones
                ],
            )
        )


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


def build_parser() -> argparse.ArgumentParser:
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
    # run_benchmark forwards a fixed flag set to whichever runner handles a task.
    # --mode is consumed above; the rest are accepted-and-ignored here. Declaring
    # the contract once (runner_cli) stops a forwarded flag from being rejected at
    # argparse before a result can be written (EnterpriseBench-nw70h).
    add_passthrough_args(parser, consumed=("--mode",))
    return parser


def main():
    args = build_parser().parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    task_dir = str(Path(args.task_toml).parent.resolve())

    if args.dry_run:
        print(f"[dry-run] would run chain task: {args.task_toml} (mode={args.mode})")
        return 0

    # Unlink before the run, not after: writing only on the way out leaves the
    # previous run's score on disk as this run's answer whenever this one raises.
    result_path = Path(task_dir) / "chain_result.json"
    result_path.unlink(missing_ok=True)

    task_def = parse_chain_task(args.task_toml)

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
    payload = {
        "task_id": result.task_id,
        "total_score": result.total_score,
        "sessions_completed": result.sessions_completed,
        "sessions_total": result.total_sessions,
        "simulated": result.simulated,
    }
    for channel in (
        "verifier_infra_error",
        "session_failure",
        "agent_not_configured",
        # Not an invalidity channel: it travels WITH a score, telling a reader the
        # number is a zero the model earned rather than one it never attempted.
        "agent_failure",
    ):
        cause = getattr(result, channel)
        if cause is not None:
            payload[channel] = cause

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
