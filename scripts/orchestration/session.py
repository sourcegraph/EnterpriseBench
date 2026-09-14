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

    ``success`` is derived, never assigned: assigned independently it could be
    set before a later fallible step and left True beside that step's error, and
    a session claiming completion is what admits the chain's final checkpoints.
    """

    session_number: int
    branch_state: BranchState | None = None
    agent_output: str = ""
    error: str = ""

    @property
    def success(self) -> bool:
        return self.branch_state is not None and not self.error


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

        if branch_state is None:
            # Raised for the real cause: left to the log line below, this
            # surfaces as an AttributeError naming NoneType instead.
            raise ValueError(
                f"run_session: session {session_num} committed no branch state "
                f"({len(repos)} repos configured) — the chain has nothing to hand on."
            )
        result.branch_state = branch_state

        logger.info(
            "Session %d completed. Branch: %s, SHA: %s",
            session_num,
            branch_state.branch_name,
            branch_state.commit_sha[:8],
        )

    except Exception as e:
        # Scrubbed at birth, not at the sinks. `error` is read back by
        # `ChainResult.summary` verbatim as well as wrapped into the
        # `session_failure` channel, so redacting it downstream would leave the
        # summary path — which prints to stdout — carrying the raw exception.
        # The agent runs with ANTHROPIC_API_KEY in its environment and this
        # `except` catches whatever it raises, response bodies included.
        result.error = safe_detail(e)
        logger.error("Session %d failed: %s", session_num, result.error)

    return result
