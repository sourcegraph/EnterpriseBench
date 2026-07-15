"""
Single session lifecycle management.

A session represents one agent invocation:
  setup -> run agent (or simulate) -> commit -> teardown

In chain mode, each session is a fresh environment that starts from
the previous session's committed branch.
"""

import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
from eb_verify.redact import safe_detail

from .branch_manager import (
    BranchState,
    create_session_branch,
    checkout_session_branch,
    commit_session_state,
    _run_git,
)

logger = logging.getLogger(__name__)


# Who earned a session's failure. The chain's whole disposition turns on this:
# an AGENT failure is scored, an INFRA failure is routed to the re-run channel.
AGENT_FAILURE = "agent"
INFRA_FAILURE = "infra"


class AgentFailure(Exception):
    """The MODEL failed the task — an earned 0.0, not an infrastructure fault.

    An agent adapter raises this to declare that the failure is the agent's own:
    it exhausted its context or turn budget, gave up, produced nothing, or
    crashed its own loop. The chain SCORES such a session instead of routing it
    to the re-run channel. Excluding it would compute a model's reported mean
    only over the runs its agent happened to survive, so a model that reliably
    blows its budget on hard chains would never receive the zeros it earned —
    an upward bias, and the same over-credit family the scorer guard exists to
    close, arriving through the exclusion channel instead of the scoring one.

    Nothing infers this. Every other exception out of the agent is UNCLASSIFIED
    and routes to the re-run channel, because the alternative — reading budget
    exhaustion out of an arbitrary traceback — is a guess, and a wrong guess in
    that direction blames the model for a broken harness. That is the false-zero
    bug (beads s58f, hktt/pt0n) this codebase already closed once. Only the
    adapter driving the model knows which failure it saw, so only the adapter
    may say so.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason  # machine key, e.g. "context_budget_exhausted"
        self.detail = detail or reason
        super().__init__(self.detail)


@dataclass(frozen=True)
class SessionFailure:
    """Why a session did not complete, and who earned it.

    ``kind`` is set at the only place that can know it — the stage that raised —
    and is never re-derived downstream from a message string.

    ``detail`` and ``reason`` are scrubbed on construction, the same rule and for
    the same reason as :class:`~eb_verify.scorer_guard.InfraError` (bead otgzo):
    they are built from text this harness does not author — the agent's own
    exception, declared or not — and reach three sinks that keep them verbatim
    (``chain_result.json``, stdout via ``ChainResult.summary``, and
    ``logger.error``). The agent runs with ``ANTHROPIC_API_KEY`` in its
    environment. Scrubbing here rather than at each sink is what makes it
    impossible for a caller to forget, and an ``AgentFailure`` is exactly as
    untrusted as an undeclared one — declaring whose fault the failure was says
    nothing about what text it carries.

    ``reason`` is scrubbed too, where ``InfraError`` leaves its own alone: that
    class's reasons are harness-authored constants, but an ``AgentFailure``'s
    comes from the adapter, and an adapter that passes a response body as its
    machine key would otherwise publish it unbounded.
    """

    kind: str  # AGENT_FAILURE | INFRA_FAILURE
    reason: str  # machine key, e.g. "context_budget_exhausted" | "commit_error"
    detail: str
    stage: str  # setup | agent | commit

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", safe_detail(self.detail))
        object.__setattr__(self, "reason", safe_detail(self.reason))

    @property
    def is_agent(self) -> bool:
        return self.kind == AGENT_FAILURE


@dataclass
class SessionConfig:
    """Configuration for a single session within a chain."""

    session_number: int
    prompt: str
    milestones: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)  # Extra context passed to agent
    mode: str = "baseline"  # Tool access mode: baseline, mcp_only, hybrid


@dataclass
class SessionResult:
    """Result of running a single session.

    ``success`` and ``error`` are derived from ``failure`` rather than stored
    beside it: a session either completed or carries the reason it did not, and
    two fields that can disagree let a caller read one and miss the other.
    """

    session_number: int
    branch_state: BranchState | None = None
    agent_output: str = ""
    failure: SessionFailure | None = None

    @property
    def success(self) -> bool:
        return self.failure is None

    @property
    def error(self) -> str:
        return self.failure.detail if self.failure is not None else ""


def setup_workspace(
    repos: list[dict],
    workspace_root: str,
    task_id: str,
    session_number: int,
    previous_branch_state: BranchState | None = None,
) -> str:
    """Set up the workspace for a session.

    For session 1: Initialize repos from their pinned revisions.
    For session N>1: Start from the previous session's branch.

    Returns the workspace path.
    """
    workspace = Path(workspace_root)
    workspace.mkdir(parents=True, exist_ok=True)

    for repo in repos:
        repo_path = workspace / repo["path"]
        resolved = repo_path.resolve()
        ws_resolved = workspace.resolve()
        if not str(resolved).startswith(str(ws_resolved) + "/"):
            raise ValueError(f"Repo path escapes workspace: {resolved}")

        if session_number == 1:
            # First session: simulate repo clone by creating a git repo
            # In production, this would clone from the actual URL at the pinned rev
            if not repo_path.exists():
                repo_path.mkdir(parents=True)
                _run_git(str(repo_path), "init")
                # Create an initial commit so we have a valid repo
                readme = repo_path / "README.md"
                readme.write_text(f"# {repo['path']}\nSimulated repo for {task_id}\n")
                _run_git(str(repo_path), "add", "-A")
                _run_git(str(repo_path), "commit", "-m", "Initial simulated state")
                logger.info(
                    "Initialized simulated repo: %s (would clone %s@%s)",
                    repo_path,
                    repo.get("url", "?"),
                    repo.get("rev", "?"),
                )

            # Create session 1 branch
            create_session_branch(str(repo_path), task_id, session_number)
        else:
            # Subsequent sessions: checkout the previous session's branch, then create new
            checkout_session_branch(str(repo_path), task_id, session_number - 1)
            create_session_branch(str(repo_path), task_id, session_number)

    return str(workspace)


def simulate_agent_work(
    workspace_path: str,
    session_config: SessionConfig,
    repos: list[dict],
    task_id: str,
    simulation_actions: list[dict] | None = None,
) -> str:
    """Simulate agent work for testing the orchestrator.

    In simulation mode, we create predetermined file changes to test
    the chain handoff mechanism without an actual agent.

    Args:
        simulation_actions: List of {"repo": str, "file": str, "content": str} dicts.
    """
    workspace = Path(workspace_path)
    output_lines = []

    if simulation_actions:
        for action in simulation_actions:
            repo_path = workspace / action["repo"]
            file_path = repo_path / action["file"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(action["content"])
            output_lines.append(f"Created/modified: {action['repo']}/{action['file']}")
            logger.info("Simulated: wrote %s/%s", action["repo"], action["file"])
    else:
        # Default simulation: create a session marker file in the first repo
        repo_path = workspace / repos[0]["path"]
        marker = repo_path / f"session_{session_config.session_number}_output.md"
        marker.write_text(
            f"# Session {session_config.session_number} Output\n\n"
            f"Prompt: {session_config.prompt[:200]}\n\n"
            f"[Simulated agent work for session {session_config.session_number}]\n"
        )
        output_lines.append(f"Created marker: {marker.name}")

    return "\n".join(output_lines)


def run_session(
    session_config: SessionConfig,
    repos: list[dict],
    task_id: str,
    workspace_root: str,
    previous_branch_state: BranchState | None = None,
    simulation_actions: list[dict] | None = None,
    agent_callable=None,
    simulate: bool = False,
) -> SessionResult:
    """Execute a single session lifecycle.

    1. Setup workspace (clone or branch checkout)
    2. Run agent (or simulate)
    3. Commit results to session branch
    4. Return result with branch state

    A failure is classified by the stage that raised it, because that is the only
    place that knows whose failure it is. Setup and commit are the harness's own
    code, so anything they raise is INFRA. Only the agent stage can produce an
    :class:`AgentFailure`, and only if the adapter declares one — an undeclared
    exception out of the agent is INFRA too, so an unrecognised failure fails
    closed into the re-run channel rather than into a zero the model may not have
    earned.

    Args:
        agent_callable: callable(workspace_path, prompt) -> str. Required unless
                       ``simulate`` is set.
        simulate: run ``simulate_agent_work`` instead of an agent. Never inferred
                 from a missing ``agent_callable`` — that would score simulated
                 work as the agent's.

    Raises:
        ValueError: neither an agent nor ``simulate``, or both.
    """
    if agent_callable is not None and simulate:
        raise ValueError(
            "run_session: agent_callable and simulate are mutually exclusive — the "
            "agent would be discarded and the simulation scored in its place."
        )
    if agent_callable is None and not simulate:
        raise ValueError(
            "run_session: no agent_callable and simulate is not set — pass an agent, "
            "or simulate=True to run the simulation scaffold on purpose."
        )

    session_num = session_config.session_number
    logger.info("=== Starting session %d for task %s ===", session_num, task_id)

    result = SessionResult(session_number=session_num)
    stage = "setup"

    try:
        # 1. Setup
        workspace_path = setup_workspace(
            repos=repos,
            workspace_root=workspace_root,
            task_id=task_id,
            session_number=session_num,
            previous_branch_state=previous_branch_state,
        )

        # 2. Run agent or simulate
        stage = "agent"
        if simulate:
            agent_output = simulate_agent_work(
                workspace_path=workspace_path,
                session_config=session_config,
                repos=repos,
                task_id=task_id,
                simulation_actions=simulation_actions,
            )
        else:
            agent_output = agent_callable(workspace_path, session_config.prompt)
        result.agent_output = agent_output

        # 3. Commit to session branch in ALL repos
        stage = "commit"
        branch_state = None
        for repo in repos:
            repo_path = str(Path(workspace_root) / repo["path"])
            bs = commit_session_state(
                repo_path=repo_path,
                task_id=task_id,
                session_number=session_num,
                message=f"Session {session_num}: agent work completed",
            )
            if branch_state is None:
                branch_state = bs  # Use first repo as primary branch state
        result.branch_state = branch_state

        logger.info(
            "Session %d completed. Branch: %s, SHA: %s",
            session_num,
            branch_state.branch_name,
            branch_state.commit_sha[:8],
        )

    except Exception as exc:
        # An AgentFailure counts as the agent's only from the agent stage, and only
        # when a real agent ran. The claim "the model failed" is the adapter's
        # alone: the same exception out of setup or commit is harness code wearing
        # the agent's name, and out of `simulate_agent_work` it is a zero charged
        # to a model that never ran at all. Both would score a session the model
        # may well have completed.
        if isinstance(exc, AgentFailure) and stage == "agent" and not simulate:
            result.failure = SessionFailure(
                kind=AGENT_FAILURE, reason=exc.reason, detail=exc.detail, stage=stage
            )
        else:
            # Everything else fails closed to INFRA. The detail carries the
            # exception type because `str(exc)` is empty for a bare `raise
            # IndexError`, and an operator sent to re-run needs more than a blank.
            result.failure = SessionFailure(
                kind=INFRA_FAILURE,
                reason=f"{stage}_error",
                detail=f"{type(exc).__name__}: {exc}",
                stage=stage,
            )
        # Log off the constructed failure, never `exc` — the raw text is what
        # carries the credential.
        logger.error(
            "Session %d failed during %s (%s): %s",
            session_num,
            stage,
            result.failure.kind,
            result.failure.detail,
        )

    return result
