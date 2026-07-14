#!/usr/bin/env python3
"""Single-session task runner for EnterpriseBench.

Builds a Docker sandbox from a task.toml, optionally runs an agent inside it,
then scores the result using checkpoint verifiers.

Usage:
    python3 scripts/orchestration/run_task.py benchmarks/.../task.toml --dry-run
    python3 scripts/orchestration/run_task.py benchmarks/.../task.toml --agent "claude -p"
    python3 scripts/orchestration/run_task.py benchmarks/.../task.toml --timeout 900
"""

import argparse
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Auto-load .env.local for Sourcegraph credentials if not already in env
_env_local = REPO_ROOT / ".env.local"
if _env_local.is_file() and not os.environ.get("SOURCEGRAPH_ACCESS_TOKEN"):
    for _line in _env_local.read_text().splitlines():
        _line = _line.strip()
        if _line.startswith("#") or not _line or "=" not in _line:
            continue
        # Handle 'export KEY=VALUE' and 'KEY=VALUE'
        if _line.startswith("export "):
            _line = _line[7:]
        _key, _, _val = _line.partition("=")
        _val = _val.strip().strip("\"'")
        os.environ.setdefault(_key.strip(), _val)

# Reuse the TOML parser from create_sg_mirrors
sys.path.insert(0, str(REPO_ROOT / "scripts" / "infra"))
from create_sg_mirrors import parse_toml

sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
from validation import validate_repo_entry

# Sourcegraph MCP preamble builder
sys.path.insert(0, str(REPO_ROOT))
from agents.harnesses.claude.mcp.sourcegraph import (
    build_system_prompt as _build_mcp_preamble,
)

# Scorer trust boundary — single definition of "infra failure vs real score",
# shared by every scoring entry point in this file and by code_patch.validate.
sys.path.insert(0, str(REPO_ROOT / "lib"))
from eb_verify.scorer_guard import InfraError, guard_verifier_output

logger = logging.getLogger(__name__)

# Paths to sandbox scripts (relative to repo root)
DOCKERFILE_GENERATOR = REPO_ROOT / "scripts" / "sandbox" / "dockerfile_generator.py"
TEST_RUNNER_SH = REPO_ROOT / "scripts" / "sandbox" / "test_runner.sh"
HEALTH_CHECK_SH = REPO_ROOT / "scripts" / "sandbox" / "health_check.sh"
EB_VERIFY_LIB = REPO_ROOT / "lib" / "eb_verify"


VALID_MODES = ("baseline", "mcp_only", "hybrid")

_DEFAULT_MCP_URL = "https://demo.sourcegraph.com/.api/mcp/all"
_raw_mcp_url = os.environ.get("SOURCEGRAPH_MCP_URL", _DEFAULT_MCP_URL)
# Ensure /all suffix for full tool set (13 tools vs 8 on base endpoint)
SOURCEGRAPH_MCP_ENDPOINT = (
    _raw_mcp_url if _raw_mcp_url.endswith("/all") else f"{_raw_mcp_url.rstrip('/')}/all"
)
# Note: MCP is configured via .mcp.json files, not `claude mcp add`
# (the CLI command had race conditions causing intermittent needs-auth)


@dataclass(frozen=True)
class TaskRunConfig:
    """Immutable configuration for a single task run."""

    task_toml: Path
    source: str = "mirror"
    agent_command: str = ""
    timeout: int = 1800
    build_timeout: int = 1800
    verifier_timeout: int = 600
    memory_mb: int = 8192
    output_dir: Path | None = None
    dry_run: bool = False
    no_build: bool = False
    keep_container: bool = False
    verbose: bool = False
    account: int | None = None
    mode: str = "baseline"
    rep: int | None = None
    ablation_variant: str | None = None
    min_disk_gb: float = 10.0


# Status marker for runs that must not be scored (e.g. MCP pre-flight failure,
# a broken grading-asset seal). Whether such a run is re-runnable is decided by
# failure_class, not by this marker: verifier_infra_error earns a fresh attempt,
# integrity_violation never does.
RUN_STATUS_INVALID = "invalid"

# The directory holding both the agent's tree and the grading assets. It must be
# root-owned so the agent cannot unlink/rename the sealed entries inside it.
WORKSPACE_DIR = "/workspace"

# The grader, the answer key, and the harness the checks import. The agent under
# test has no legitimate need for any of them (no task instruction references
# them), so they are sealed root-only rather than merely read-only: that closes
# the ground_truth.json read leak as well as the tamper vector.
VERIFIER_DIR = f"{WORKSPACE_DIR}/.verifiers"
TASK_DIR = f"{WORKSPACE_DIR}/.task"
EB_VERIFY_DIR = f"{WORKSPACE_DIR}/.eb_verify"
TEST_SH = f"{WORKSPACE_DIR}/test.sh"
GROUND_TRUTH = f"{TASK_DIR}/ground_truth.json"
GRADING_PATHS = [VERIFIER_DIR, TASK_DIR, EB_VERIFY_DIR, TEST_SH]

# Pre-quoted for the `for f in ...` loops that seal and re-verify these paths.
_GRADING_PATHS_SH = " ".join(shlex.quote(p) for p in GRADING_PATHS)

# The two things the agent legitimately owns. instruction.md is deliberately NOT
# a grading asset: the s58f readability gate depends on the agent reading it.
AGENT_INSTRUCTION = f"{WORKSPACE_DIR}/instruction.md"
AGENT_OUTPUT_DIR = f"{WORKSPACE_DIR}/agent_output"

# Phases whose scores cannot be trusted. The judge is skipped for these (there
# is nothing trustworthy left to put a ceiling on), and none of them may be
# overwritten with phase="complete", success=True at save time. agent_infra_error
# is scoreable-but-failed, so it blocks the save override without blocking the
# judge — hence two sets rather than one.
UNTRUSTED_SCORE_PHASES = frozenset({"verifier_infra_error", "integrity_violation"})
NON_COMPLETE_PHASES = UNTRUSTED_SCORE_PHASES | {"agent_infra_error"}

# The grader's identity. Scoring must not run as the agent (it would own the
# grader) and must not run as root either: several checks execute code the agent
# controls, BY DESIGN — pytest auto-loads a planted /workspace/conftest.py, git
# runs a .gitattributes-selected textconv driver, and check_test_fails.sh runs
# the agent's own test suite. A root grader turns any of those into a rewrite of
# the root-owned seal for a not-yet-run checkpoint, forging the very score the
# seal exists to protect.
#
# So the grading assets are owned by root and *read* by a third identity that
# runs the checks. It can execute the grader; it cannot modify it. Agent code
# that executes during scoring therefore gains an unprivileged uid with no path
# back to the verifiers, the answer key, or the harness.
SCORING_USER = "ebscorer"
SCORING_GROUP = "ebscorer"
SCORING_UID = 2000

# Root-owned, scorer-unwritable cwd for the scoring exec. Ownership alone would
# not close the hole: the checks shell out to `python3 -c`, whose sys.path[0] is
# the cwd, so scoring from agent-owned /workspace lets a planted
# /workspace/json.py hijack the grader. The cwd must stay unwritable by the
# scoring user too, or planted code would simply hijack the *next* check's
# import from there.
SCORING_WORKDIR = "/opt/eb_scoring"

# The one scorer-writable path: test_runner.sh's results file. Deliberately NOT
# the cwd (see above) and deliberately not under /workspace, which is sealed
# shut for the scoring window.
SCORING_OUT_DIR = f"{SCORING_WORKDIR}/out"
SCORING_RESULTS_FILE = f"{SCORING_OUT_DIR}/results.json"

# /workspace during the agent's session: root-owned + sticky, world-writable so
# the agent can create its own artifacts (it cannot unlink root-owned entries).
WORKSPACE_MODE_SESSION = "1777"
# /workspace during the scoring window: the agent is no longer running, so
# nothing legitimate creates entries here. Closing the write bit denies planted
# code, executing as the scorer, the one shared drop point it could use to plant
# an answer artifact for a later checkpoint after reading the answer key.
WORKSPACE_MODE_SCORING = "1755"

# Git config for the scoring exec. The repos under /workspace are agent-owned
# (cloned as USER agent), so git run by the scorer refuses them as "dubious
# ownership" — which a check like chain-err-flask-import-001's `git diff ...
# 2>/dev/null || true` silently swallows into a false "no code changes".
# safe.directory fixes that, but it alone would let the agent's own repo-local
# config execute code as the scorer (core.fsmonitor / core.hooksPath run
# commands during `git diff`), which would hand the agent the grader by another
# door. GIT_CONFIG_* env entries carry `git -c` precedence — above repo-local
# config — so pinning those two knobs here neutralizes them. Verified: a planted
# core.fsmonitor payload does not execute under this env.
_GIT_SCORING_CONFIG = (
    ("safe.directory", "*"),
    ("core.fsmonitor", "false"),
    ("core.hooksPath", "/dev/null"),
)
GIT_SCORING_ENV = " ".join(
    [
        "GIT_CONFIG_NOSYSTEM=1",
        f"GIT_CONFIG_COUNT={len(_GIT_SCORING_CONFIG)}",
        *(
            f"GIT_CONFIG_KEY_{i}={shlex.quote(key)} "
            f"GIT_CONFIG_VALUE_{i}={shlex.quote(value)}"
            for i, (key, value) in enumerate(_GIT_SCORING_CONFIG)
        ),
    ]
)

# Where Claude Code writes session JSONL inside the container. The agent
# controls this filesystem, so any trace path that claims to sit outside this
# root is rejected rather than copied out.
TRACE_ROOT = "/home/agent/.claude/projects"


@dataclass
class TaskRunResult:
    """Result of running a single task."""

    task_id: str
    phase: str = ""
    success: bool = False
    error: str = ""
    image_tag: str = ""
    container_id: str = ""
    scores: dict = field(default_factory=dict)
    timing: dict = field(default_factory=dict)
    output_dir: str = ""
    tool_usage: dict = field(default_factory=dict)
    failure_class: Optional[str] = None
    # "" for normal runs; RUN_STATUS_INVALID for runs that must be re-run,
    # never scored (see the MCP pre-flight gate).
    status: str = ""


def _load_oauth_token(account: int) -> str:
    """Load and validate an OAuth access token for the given account number.

    Reads credentials from ~/.claude-homes/account{N}/.claude/.credentials.json,
    checks the token has not expired, and returns the access token string.

    Raises:
        FileNotFoundError: If the credentials file does not exist.
        ValueError: If the token is expired or the credentials are malformed.
    """
    real_home = Path(os.environ.get("HOME", str(Path.home())))
    creds_path = (
        real_home
        / ".claude-homes"
        / f"account{account}"
        / ".claude"
        / ".credentials.json"
    )

    if not creds_path.is_file():
        raise FileNotFoundError(
            f"Credentials file not found for account {account}: {creds_path}"
        )

    try:
        creds = json.loads(creds_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"Failed to read credentials for account {account}: {exc}"
        ) from exc

    oauth = creds.get("claudeAiOauth")
    if not oauth or not isinstance(oauth, dict):
        raise ValueError(f"Missing or invalid claudeAiOauth section in {creds_path}")

    access_token = oauth.get("accessToken")
    if not access_token:
        raise ValueError(f"No accessToken found in {creds_path}")

    expires_at_ms = oauth.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)
    if expires_at_ms <= now_ms:
        expired_at = datetime.fromtimestamp(expires_at_ms / 1000, tz=timezone.utc)
        raise ValueError(
            f"OAuth token for account {account} expired at {expired_at.isoformat()}. "
            f"Run: python3 scripts/infra/headless_login.py --account {account}"
        )

    remaining_min = (expires_at_ms - now_ms) // 60000
    logger.info("Account %d: token valid, %d minutes remaining", account, remaining_min)
    return access_token


DEFAULT_OAUTH_AGENT_COMMAND = "claude --dangerously-skip-permissions --max-turns 50 --verbose --output-format stream-json -p"


def _parse_task(toml_path: Path) -> dict:
    """Parse and validate a task.toml file."""
    if not toml_path.exists():
        raise FileNotFoundError(f"Task file not found: {toml_path}")

    data = parse_toml(toml_path)

    task_info = data.get("task", {})
    if not task_info.get("id"):
        raise ValueError(f"Task file missing [task].id: {toml_path}")

    session_type = task_info.get("session_type", "single")
    if session_type != "single":
        raise ValueError(
            f"run_task.py only handles single-session tasks, "
            f"got session_type={session_type!r}. Use chain_runner.py for chains."
        )

    for repo in data.get("repos", []):
        validate_repo_entry(repo)

    return data


def _generate_dockerfile(task_toml: Path, source: str) -> Path:
    """Generate Dockerfile using the existing dockerfile_generator and return its path."""
    # Import the generator function directly to avoid subprocess overhead
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "sandbox"))
    from dockerfile_generator import generate_for_task

    results = generate_for_task(task_toml, source=source)
    dockerfile_path = results.get("standard")
    if dockerfile_path is None or not dockerfile_path.exists():
        raise RuntimeError(
            "Dockerfile generation failed: no standard Dockerfile produced"
        )

    logger.info("Generated Dockerfile: %s", dockerfile_path)
    return dockerfile_path


def _docker_build(dockerfile_path: Path, image_tag: str, timeout: int = 1800) -> None:
    """Build a Docker image from the generated Dockerfile."""
    context_dir = str(dockerfile_path.parent)
    cmd = [
        "docker",
        "build",
        "-f",
        str(dockerfile_path),
        "-t",
        image_tag,
        context_dir,
    ]
    logger.info("Building Docker image: %s (timeout=%ds)", image_tag, timeout)
    logger.debug("Command: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        # Check for disk space errors before reporting generic build failure
        if "No space left on device" in result.stderr:
            raise RuntimeError(
                f"Docker build failed: no disk space left\n{result.stderr[-2000:]}"
            )
        raise RuntimeError(
            f"Docker build failed (exit {result.returncode}):\n{result.stderr[-2000:]}"
        )
    logger.info("Docker image built: %s", image_tag)


def _docker_create_container(
    image_tag: str,
    container_name: str,
    memory_mb: int = 8192,
) -> str:
    """Create (but do not start) a container, returning the container ID."""
    cmd = [
        "docker",
        "create",
        "--name",
        container_name,
        f"--memory={memory_mb}m",
        f"--memory-swap={memory_mb * 2}m",
        image_tag,
        "sleep",
        "infinity",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"docker create failed: {result.stderr.strip()}")

    container_id = result.stdout.strip()
    logger.info("Created container: %s (%s)", container_name, container_id[:12])
    return container_id


def _docker_start(container_id: str) -> None:
    """Start an existing container."""
    result = subprocess.run(
        ["docker", "start", container_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker start failed: {result.stderr.strip()}")


def _docker_exec(
    container_id: str,
    cmd: list[str],
    timeout: int = 120,
    workdir: str = "/workspace",
    user: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a command inside the container, optionally as a specific user."""
    full_cmd = ["docker", "exec", "-w", workdir]
    if user is not None:
        full_cmd += ["-u", user]
    full_cmd += [container_id] + cmd
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _docker_cp(src: str, dest: str) -> None:
    """Copy files into or out of a container."""
    result = subprocess.run(
        ["docker", "cp", src, dest],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker cp failed: {result.stderr.strip()}")


def _docker_stop_rm(container_id: str) -> None:
    """Stop and remove a container."""
    subprocess.run(
        ["docker", "stop", "-t", "5", container_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    subprocess.run(
        ["docker", "rm", "-f", container_id],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _build_instruction_text(
    task_dir: Path,
    mode: str,
    repos: list[dict] | None = None,
    require_grounded_citations: bool = False,
) -> str | None:
    """Build the full instruction text with optional MCP preamble and output appendix.

    For mcp_only/hybrid modes, prepends the Sourcegraph MCP preamble (from
    agents.harnesses.claude.mcp.sourcegraph) and any task-specific
    instruction_mcp.md. For baseline mode, uses instruction.md as-is.

    When require_grounded_citations is True, the answer.json appendix's
    example JSON includes a top-level `citations` field (schema:
    {repo, file, evidence_span}), matching what
    lib/eb_verify/plugins/answer.py's groundedness gate requires.

    Returns the combined text, or None if instruction.md does not exist.
    """
    instruction = task_dir / "instruction.md"
    if not instruction.exists():
        return None

    instruction_text = instruction.read_text()

    # Build MCP preamble for non-baseline modes
    preamble_parts: list[str] = []
    if mode in ("mcp_only", "hybrid"):
        # Mode-specific preamble from the sourcegraph module
        mcp_preamble = _build_mcp_preamble(mode=mode, repos=repos)
        if mcp_preamble:
            preamble_parts.append(mcp_preamble)

        # Append instruction_mcp.md content if it exists
        instruction_mcp = task_dir / "instruction_mcp.md"
        if instruction_mcp.exists():
            preamble_parts.append(instruction_mcp.read_text())

    if require_grounded_citations:
        sys.path.insert(0, str(REPO_ROOT / "lib"))
        from eb_verify.groundedness import MIN_SPAN_CHARS

        citations_block = (
            ',\n  "citations": [\n'
            '    {"repo": "repo-name", "file": "relative/path", '
            f'"evidence_span": "verbatim excerpt, >={MIN_SPAN_CHARS} characters, copied exactly from the file"}}\n'
            "  ]\n"
        )
        closing_sentence = (
            "Include only the fields relevant to this task, but `citations` is "
            "required. Every entry in `citations` must quote an exact, verbatim "
            "span from the cited file — not a paraphrase or summary. "
        )
    else:
        citations_block = "\n"
        closing_sentence = "Include only the fields relevant to this task. "

    output_appendix = (
        "\n\n---\n\n## Output Requirements\n\n"
        "Write your findings as a JSON file to `/workspace/agent_output/answer.json`.\n"
        "Create the directory first: `mkdir -p /workspace/agent_output`\n\n"
        "All file paths MUST be absolute and anchored at `/workspace/<repo>/...` "
        "(the repo roots are the directories under `/workspace`). "
        "Repo-relative paths will not match the oracle and score 0.\n\n"
        "Include all relevant fields for this task type. Example structure:\n"
        "```json\n"
        "{\n"
        '  "source_files": [{"path": "/workspace/<repo>/path/to/file"}],\n'
        '  "error_chain": ["Step 1", "Step 2"],\n'
        '  "trigger_conditions": ["Condition 1"],\n'
        '  "code_paths": [{"path": "/workspace/<repo>/path/to/file"}],\n'
        '  "ownership": "subsystem description",\n'
        '  "severity": {"level": "high", "rationale": "..."},\n'
        '  "related_issues": ["/workspace/<repo>/path/to/related/file.go", "description of related component"]'
        + citations_block
        + "}\n```\n"
        + closing_sentence
        + "Your answer is evaluated against a closed-world oracle — completeness matters.\n"
    )

    if preamble_parts:
        preamble = "\n\n".join(preamble_parts)
        return preamble + "\n\n---\n\n" + instruction_text + output_appendix
    return instruction_text + output_appendix


def _chown_to_agent(container_id: str, paths: list[str]) -> None:
    """chown the given container paths to agent:agent (recursively), as root.

    This is the ONLY sanctioned way to grant the agent ownership of anything in
    the container, because it is the one place that can refuse: handing the agent
    the workspace root, a grading asset, or any parent of one puts the grader
    back in the hands of the party being graded (bead EnterpriseBench-8krz5).
    Two separate call sites did exactly that before the seal, so the invariant is
    enforced here rather than trusted to every future caller.

    Only paths that exist are chowned (missing ones are skipped, not treated as
    errors — some are created by later steps). A genuine chown failure is logged
    loudly, never silently swallowed: a swallowed failure is what produced
    unreadable instruction.md / .mcp.json files and fake-0 no-op runs
    (bead EnterpriseBench-s58f).

    Raises:
        ValueError: if any path would grant the agent ownership of a grading
            asset, or of a directory it could unlink one from.
    """
    for path in paths:
        target = PurePosixPath(path)
        for sealed in GRADING_PATHS:
            sealed_path = PurePosixPath(sealed)
            # Refuse the asset itself, anything under it (a single check script,
            # ground_truth.json), and any ancestor — chowning a parent hands the
            # agent the write bit it needs to unlink the sealed entry out of it.
            if (
                target == sealed_path
                or target.is_relative_to(sealed_path)
                or sealed_path.is_relative_to(target)
            ):
                raise ValueError(
                    f"refusing to chown {path} to agent: it is, contains, or sits "
                    f"inside the sealed grading asset {sealed} — the agent under "
                    "test must never own the grader (bead EnterpriseBench-8krz5)"
                )

    quoted = " ".join(shlex.quote(p) for p in paths)
    script = (
        f"rc=0; for f in {quoted}; do "
        'if [ -e "$f" ]; then chown -R agent:agent "$f" || rc=1; fi; '
        "done; exit $rc"
    )
    result = _docker_exec(container_id, ["bash", "-c", script], user="root")
    if result.returncode != 0:
        logger.error(
            "chown to agent FAILED for one or more of %s: %s",
            paths,
            result.stderr.strip(),
        )


def _seal_grading_assets(container_id: str) -> None:
    """Seal the grading assets: owned by root, read by the scorer, agent locked out.

    The agent under test previously owned its own verifiers, ground truth and
    harness library, so it could overwrite a check with a forged verdict, read
    the answer key, or delete the harness to convert a scored 0.0 into a free
    re-run (beads EnterpriseBench-8krz5, -g5k5s).

    Three parties, three privileges, and no two of them collapse into one:

      root         owns the assets; the only identity that can modify them
      SCORING_USER runs the checks; reads and executes the assets, writes none
      agent        no access at all

    That split is what makes the seal survive code execution during scoring:
    some checks run agent-controlled code by design, so the identity running
    them is assumed compromised, and the assets must not be writable by it.

    Also hardens /workspace itself and creates the scoring cwd, so the scoring
    exec never starts in a directory the agent — or the scorer — can write to.

    Raises:
        RuntimeError: if the seal cannot be applied. An unsealed run is
            unscoreable, so this fails loud rather than silently producing a
            number nobody can trust (the s58f masked-chown lesson).
    """
    # /workspace becomes root-owned + sticky + world-writable, exactly like /tmp.
    # Sealing the assets alone is not enough: POSIX governs unlink/rename by the
    # PARENT directory's write bit, so an agent-owned /workspace lets the agent
    # move a sealed file aside and drop in a forgery without ever writing to it.
    # Sticky alone does not close it either — sticky still permits the DIRECTORY's
    # owner to unlink — so ownership must move to root as well. 1777 keeps the
    # agent able to create and delete its OWN entries.
    #
    # `useradd` is guaranteed present: the image built the agent user with it
    # (dockerfile_generator). The -u/-g variants fall back to auto-allocated ids
    # if 2000 is already taken in some base image; _assert_scoring_identity is
    # what decides whether whatever we ended up with is actually safe.
    script = (
        f"set -e; "
        f"if ! id -u {shlex.quote(SCORING_USER)} >/dev/null 2>&1; then "
        f"  groupadd -g {SCORING_UID} {shlex.quote(SCORING_GROUP)} 2>/dev/null "
        f"    || groupadd {shlex.quote(SCORING_GROUP)}; "
        f"  useradd -u {SCORING_UID} -g {shlex.quote(SCORING_GROUP)} -M -s /bin/bash "
        f"    {shlex.quote(SCORING_USER)} 2>/dev/null "
        f"    || useradd -g {shlex.quote(SCORING_GROUP)} -M -s /bin/bash "
        f"    {shlex.quote(SCORING_USER)}; "
        f"fi; "
        f"mkdir -p {shlex.quote(SCORING_OUT_DIR)}; "
        f"chown root:root {shlex.quote(SCORING_WORKDIR)}; "
        f"chmod 755 {shlex.quote(SCORING_WORKDIR)}; "
        f"chown root:{shlex.quote(SCORING_GROUP)} {shlex.quote(SCORING_OUT_DIR)}; "
        f"chmod 770 {shlex.quote(SCORING_OUT_DIR)}; "
        f"chown root:root {shlex.quote(WORKSPACE_DIR)}; "
        f"chmod {WORKSPACE_MODE_SESSION} {shlex.quote(WORKSPACE_DIR)}; "
        f"for f in {_GRADING_PATHS_SH}; do "
        f'if [ -e "$f" ]; then chown -R root:{shlex.quote(SCORING_GROUP)} "$f"; '
        'chmod -R u=rwX,g=rX,o= "$f"; fi; '
        "done"
    )
    result = _docker_exec(container_id, ["bash", "-c", script], user="root")
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to seal grading assets {GRADING_PATHS}: "
            f"{result.stderr.strip() or 'unknown error'}"
        )
    logger.info(
        "Sealed grading assets root-owned, %s-readable, agent-inaccessible: %s",
        SCORING_USER,
        ", ".join(GRADING_PATHS),
    )


def _assert_grading_assets_sealed(container_id: str) -> tuple[bool, str]:
    """Verify the agent could not have tampered with, or read, the grader.

    Returns (ok, error_message). Checks three independent breaches: a /workspace
    the agent could unlink/rename the sealed entries out of, any grading path
    that is not root-owned or is group/other-writable, and a ground truth the
    agent user can read. Cheap enough to re-run immediately before scoring, which
    is what makes a reused or hand-modified container fail loud instead of
    producing a forged score.
    """
    # The parent must be root-owned and sticky, else the sealed entries can be
    # renamed or unlinked wholesale regardless of their own mode.
    parent = _docker_exec(
        container_id,
        ["bash", "-c", f"find {shlex.quote(WORKSPACE_DIR)} -maxdepth 0 "
                       "\\( ! -user root -o ! -perm -1000 \\) -print"],
        user="root",
    )
    if parent.returncode != 0:
        return False, (
            f"could not verify the {WORKSPACE_DIR} seal: "
            f"{parent.stderr.strip() or 'unknown error'}"
        )
    if parent.stdout.strip():
        return False, (
            f"{WORKSPACE_DIR} is agent-owned or not sticky — the agent can "
            "unlink/rename the sealed grading assets out of it"
        )

    # A MISSING grading asset is a breach, not a pass. _setup_container creates
    # all four unconditionally, so absence at scoring time means something
    # removed them. Skipping absent paths would fail OPEN: a deleted test.sh
    # yields empty verifier output, which guard_verifier_output classifies as a
    # verifier_infra_error — handing the saboteur the re-run mulligan this seal
    # exists to deny (bead g5k5s).
    script = (
        f"for f in {_GRADING_PATHS_SH}; do "
        'if [ ! -e "$f" ]; then echo "MISSING:$f"; '
        'else find "$f" \\( ! -user root -o -perm /go+w \\) -print; fi; '
        "done"
    )
    result = _docker_exec(container_id, ["bash", "-c", script], user="root")
    if result.returncode != 0:
        return False, (
            f"could not verify the grading-asset seal: "
            f"{result.stderr.strip() or 'unknown error'}"
        )
    breached = result.stdout.strip()
    if breached:
        return False, (
            "grading assets are missing, agent-owned, or agent-writable: "
            + ", ".join(breached.splitlines())
        )

    if _can_read(container_id, GROUND_TRUTH, user="agent"):
        return False, f"{GROUND_TRUTH} is readable by the agent user"

    identity_ok, identity_err = _assert_scoring_identity(container_id)
    if not identity_ok:
        return False, identity_err

    # The mirror image of the seal: assets the scorer cannot READ score every
    # checkpoint 0.0 with no crash and no diagnostic — a silent false zero, the
    # same failure class as the ${1:-.} workspace bug this seal already caused
    # once. A seal that grades everything 0.0 is not a seal, it is an outage.
    for path in (TEST_SH, GROUND_TRUTH, VERIFIER_DIR, EB_VERIFY_DIR):
        if not _can_read(container_id, path, user=SCORING_USER):
            return False, (
                f"{path} is not readable by the {SCORING_USER} user — every "
                "checkpoint would score a false 0.0"
            )

    return True, ""


def _assert_scoring_identity(container_id: str) -> tuple[bool, str]:
    """Verify the identity that will run the checks cannot rewrite them.

    The seal is only worth the identity that runs against it. Scoring as root
    voids it outright (root ignores the mode bits it just set), and scoring as
    the agent hands the grader to the graded. Both were live: the first shipped
    in the original seal, the second is the bug the seal was written for.

    Returns (ok, error_message).
    """
    scoring_user = shlex.quote(SCORING_USER)
    script = (
        f"suid=$(id -u {scoring_user} 2>/dev/null) "
        f'|| {{ echo "the {SCORING_USER} user does not exist"; exit 0; }}; '
        f'[ "$suid" = "0" ] && echo "the scoring user is root — it can rewrite '
        f'the sealed grading assets it is grading against"; '
        f"auid=$(id -u agent 2>/dev/null) || true; "
        f'[ -n "$auid" ] && [ "$suid" = "$auid" ] '
        f'&& echo "the scoring user IS the agent user — the graded party owns the grader"; '
        f"id -nG agent 2>/dev/null | tr ' ' '\\n' | grep -qx {shlex.quote(SCORING_GROUP)} "
        f'&& echo "the agent belongs to the {SCORING_GROUP} group — it can read the answer key"; '
        "exit 0"
    )
    # workdir="/": who the grader IS is a property of the container, not of the
    # workspace. Probing from /workspace would make this gate fail closed for a
    # reason that has nothing to do with the identity it is checking.
    result = _docker_exec(
        container_id, ["bash", "-c", script], user="root", workdir="/"
    )
    if result.returncode != 0:
        return False, (
            f"could not verify the scoring identity: "
            f"{result.stderr.strip() or 'unknown error'}"
        )
    breached = result.stdout.strip()
    if breached:
        return False, "unsafe scoring identity: " + "; ".join(breached.splitlines())
    return True, ""


def _reap_agent_processes(container_id: str) -> None:
    """Kill every agent-owned process before scoring, except PID 1.

    The seal denies the agent USER any write to the grader. It does not, on its
    own, stop a process the agent LEFT RUNNING. A check that runs agent code as
    the unprivileged scorer (pytest loading a planted conftest.py) can read the
    answer key — legitimately, the checks need it — and hand it to a lingering
    agent-owned daemon (`setsid … &` survives the `docker exec -u agent` that
    started the session). That daemon still owns the agent's answer files, which
    the workspace close leaves owner-writable, so it can rewrite the answer a
    later checkpoint grades. Killing it removes the only identity that can write
    a checkpoint-read path during the scoring window (the scorer cannot: the
    answer tree is agent-owned and the workspace is closed).

    PID 1 is the container's `sleep infinity`, itself running as agent; killing
    it would stop the container mid-score, so it is spared. Enumeration is via
    /proc so no `ps`/`pgrep` need exist in the image.

    Raises:
        RuntimeError: if agent processes cannot be enumerated. Scoring beside a
            live agent process is not a trustworthy measurement, so this fails
            loud rather than leaving the injection window open.

    Residual (bead EnterpriseBench-8krz5, fix-candidate-3): a single reap pass
    cannot outrace a respawner rooted at the un-killable PID 1, and it does not
    touch the answer key an earlier chain session may already have copied to a
    persistent drop point outside /workspace. The durable boundary is a separate
    scoring container that never shares a filesystem, or GT, with the agent.
    """
    # stat -c %u on /proc/<pid> yields the process's real uid. Two wrinkles:
    #  - Killing a parent orphans its children onto PID 1, which as `sleep
    #    infinity` never wait()s, so one pass is not enough AND every process we
    #    kill becomes a permanent ZOMBIE whose /proc entry still shows agent's
    #    uid. A zombie cannot write, so it is not a live writer — the scan skips
    #    state Z (field after the last ')' in /proc/<pid>/stat) or the run would
    #    fail closed forever on the corpses of the processes it just reaped.
    #  - So the loop re-kills the live (non-zombie) agent set until a scan finds
    #    none. A benign orphan clears within a couple of passes; only a genuine
    #    respawn loop rooted at the un-killable PID 1 survives all of them, and
    #    that residual (fix-candidate-3) is what the caller fails closed on.
    script = (
        "set -u; "
        "auid=$(id -u agent) || exit 3; "
        # is_live_agent <pid> -> 0 if a killable agent-owned process
        "is_live_agent() { "
        '  [ "$1" = 1 ] && return 1; '
        '  [ "$(stat -c %u /proc/$1 2>/dev/null)" = "$auid" ] || return 1; '
        "  st=$(cat /proc/$1/stat 2>/dev/null) || return 1; "
        '  state=${st##*) }; state=${state%% *}; '
        '  [ "$state" = Z ] && return 1; '
        "  return 0; }; "
        "count_live() { c=0; for d in /proc/[0-9]*; do "
        "  is_live_agent ${d#/proc/} && c=$((c+1)); done; echo $c; }; "
        "for attempt in 1 2 3 4 5 6 7 8; do "
        "  for d in /proc/[0-9]*; do "
        "    is_live_agent ${d#/proc/} && kill -9 ${d#/proc/} 2>/dev/null || true; "
        "  done; "
        '  [ "$(count_live)" = 0 ] && break; '
        "  sleep 0.2; "
        "done; "
        'echo "agent_procs_remaining=$(count_live)"'
    )
    result = _docker_exec(container_id, ["bash", "-c", script], user="root", workdir="/")
    if result.returncode != 0:
        raise RuntimeError(
            "could not reap agent processes before scoring: "
            f"{result.stderr.strip() or 'unknown error'}"
        )
    remaining = result.stdout.strip()
    if remaining != "agent_procs_remaining=0":
        # A survivor is a live agent-identity writer during scoring — the exact
        # gap this reap exists to close. Fail closed; do not score beside it.
        raise RuntimeError(
            "agent processes survived the pre-scoring reap "
            f"({remaining or 'unknown'}) — an agent-owned writer is live during "
            "the scoring window; refusing to score (bead EnterpriseBench-8krz5)"
        )
    logger.info("Reaped agent processes before scoring (none survived)")


def _close_workspace_for_scoring(container_id: str) -> None:
    """Leave the scorer nothing under /workspace to write.

    The seal stops planted code from rewriting the grader. On its own it does not
    stop it from writing an *answer*: the checks must read ground_truth.json, so
    code executing as the scorer can read it too, and any scorer-writable path a
    later checkpoint reads back is then a forge vector. Agent-owned is NOT the
    same as scorer-can't-write — the agent picks the modes on its own files and
    can leave a 0777 directory behind for exactly this purpose.

    So, for the scoring window: close /workspace itself, and drop the group/other
    write bit anywhere under it the agent opened one. Owner bits are untouched,
    so the agent keeps full access to its own tree in a later chain session. The
    cost is one metadata traversal; only the (rare) offending entries are
    written, so this is not the `chown -R /workspace` that is too slow to do.

    Raises:
        RuntimeError: if the lock cannot be applied. Scoring into a workspace the
            grader can write is not a measurement.
    """
    script = (
        f"set -e; "
        f"chmod {WORKSPACE_MODE_SCORING} {shlex.quote(WORKSPACE_DIR)}; "
        f"find {shlex.quote(WORKSPACE_DIR)} -mindepth 1 -xdev "
        f"\\( -type d -o -type f \\) -perm /go+w -exec chmod go-w {{}} +"
    )
    result = _docker_exec(container_id, ["bash", "-c", script], user="root", timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to close {WORKSPACE_DIR} for scoring: "
            f"{result.stderr.strip() or 'unknown error'}"
        )


def _reopen_workspace_for_agent(container_id: str) -> None:
    """Give the agent back its write access to /workspace after scoring.

    Chain tasks score between sessions and hand the same container back to the
    agent afterwards (chain_runner), so the scoring lock must not outlive the
    scoring window. Best-effort by design: the score is already computed, and
    failing the run here would throw away a sound measurement over a container
    the caller may be about to discard. Logged loudly, never swallowed.
    """
    result = _docker_exec(
        container_id,
        ["chmod", WORKSPACE_MODE_SESSION, WORKSPACE_DIR],
        user="root",
    )
    if result.returncode != 0:
        logger.error(
            "failed to reopen %s for the agent after scoring (a chain's next "
            "session may be unable to write): %s",
            WORKSPACE_DIR,
            result.stderr.strip() or "unknown error",
        )


def _can_read(container_id: str, path: str, user: str) -> bool:
    """Whether `user` can read `path` inside the container.

    Three trust gates hinge on this one probe, in opposing directions: the agent
    MUST be able to read its instruction file, MUST NOT be able to read the
    answer key, and the scorer MUST be able to read the grader it runs.
    """
    check = _docker_exec(container_id, ["test", "-r", path], timeout=30, user=user)
    return check.returncode == 0


def _assert_agent_readable(container_id: str, paths: list[str]) -> tuple[bool, str]:
    """Verify the AGENT user can read each path inside the container.

    Returns (ok, error_message). This is the pre-agent gate that converts a
    silent container EACCES into a loud, recorded failure instead of letting the
    agent fail to start and the run record a fake 0.0 score
    (bead EnterpriseBench-s58f).
    """
    for path in paths:
        if not _can_read(container_id, path, user="agent"):
            return False, (
                f"agent user cannot read {path} "
                "(EACCES or missing) — run is INVALID, not a real 0.0 score"
            )
    return True, ""


def _scan_mcp_config_error(output_dir: Path) -> bool:
    """Scan the agent stderr log for an MCP-config parse / EACCES / perms error.

    These are the audited no-op markers (validity audit uu8z): the agent could
    not load its instruction file or MCP config, so the run is INVALID, not a
    real 0.0 score.
    """
    stderr_log = output_dir / "agent_stderr.log"
    if not stderr_log.exists():
        return False
    content = stderr_log.read_text(errors="replace")
    if "Invalid MCP configuration" in content:
        return True
    if "instruction.md: Permission denied" in content:
        return True
    if "EACCES" in content and ".mcp.json" in content:
        return True
    return False


def _write_content_to_container(
    container_id: str, content: str, dest_path: str, suffix: str = ""
) -> None:
    """Write `content` to a local tempfile and docker-cp it into the container.

    Shared by every "write a small file into the container" call site
    (instruction.md, per-checkpoint .meta) so they can't drift out of sync on
    the write/copy/cleanup sequence.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
        f.write(content)
        tmp_path = f.name
    try:
        _docker_cp(tmp_path, f"{container_id}:{dest_path}")
    finally:
        os.unlink(tmp_path)


def _checkpoint_verifier_name(verifier_path: str | Path) -> str:
    """Basename of a checkpoint verifier path with the ``check_`` prefix stripped.

    e.g. ``checks/check_api_migration.sh`` -> ``api_migration``. Shared by
    _verifier_meta_by_name and _setup_container's check-script copy loop so
    both always derive the same .verifiers/<name> key from a verifier path.
    """
    name = Path(verifier_path).stem
    if name.startswith("check_"):
        name = name[len("check_") :]
    return name


def _verifier_meta_by_name(checkpoints: list[dict]) -> dict[str, tuple[float, int]]:
    """Map .verifiers/<name> -> (weight, timeout) from task toml checkpoints.

    The key is derived the same way _setup_container names the copied verifier
    (see _checkpoint_verifier_name). Weights come straight from the toml
    (schema bounds each to [0, 1]; a separate offline audit enforces they sum
    to 1.0); timeout falls back to test_runner.sh's 120s default when
    unspecified.
    """
    meta: dict[str, tuple[float, int]] = {}
    for cp in checkpoints:
        verifier = cp.get("verifier")
        if not verifier:
            continue
        name = _checkpoint_verifier_name(verifier)
        weight = float(cp.get("weight", 1.0))
        timeout = int(cp.get("timeout_seconds", 120))
        meta[name] = (weight, timeout)
    return meta


def _setup_container(
    container_id: str,
    task_dir: Path,
    task_data: dict,
    mode: str = "baseline",
) -> None:
    """Copy task files into the running container.

    - instruction.md -> /workspace/instruction.md
    - checks/*.sh -> /workspace/.verifiers/
    - test_runner.sh -> /workspace/test.sh
    - eb_verify library -> /workspace/.eb_verify/ (if needed by check scripts)
    """
    # Copy instruction.md with output format appendix and optional MCP preamble
    combined = _build_instruction_text(
        task_dir,
        mode,
        repos=task_data.get("repos", []),
        require_grounded_citations=(task_data.get("ground_truth") or {}).get(
            "require_grounded_citations", False
        ),
    )
    if combined is not None:
        _write_content_to_container(
            container_id, combined, "/workspace/instruction.md", suffix=".md"
        )
        logger.info(
            "Copied instruction.md (mode=%s) with output appendix into container",
            mode,
        )
    else:
        logger.warning("No instruction.md found in %s", task_dir)

    # Create .verifiers directory and copy check scripts
    _docker_exec(container_id, ["mkdir", "-p", "/workspace/.verifiers"])

    # Map checkpoint verifier name -> (weight, timeout) from the task toml so
    # test_runner.sh can read real weights from .verifiers/<name>.meta. Keyed by
    # the same name the .sh file is copied under (verifier basename, "check_"
    # prefix stripped). Without this, every checkpoint defaults to weight 1.0
    # and task_score becomes a 0-N sum instead of the toml-weighted 0-1.
    checkpoint_meta = _verifier_meta_by_name(task_data.get("checkpoints", []))

    checks_dir = task_dir / "checks"
    if checks_dir.is_dir():
        check_scripts = sorted(checks_dir.glob("*.sh"))
        for check_script in check_scripts:
            # Rename to just <name>.sh for test_runner.sh compatibility
            name = _checkpoint_verifier_name(check_script)
            dest = f"{container_id}:/workspace/.verifiers/{name}.sh"
            _docker_cp(str(check_script), dest)
            _docker_exec(
                container_id, ["chmod", "+x", f"/workspace/.verifiers/{name}.sh"]
            )
            meta = checkpoint_meta.get(name)
            if meta is not None:
                weight, timeout = meta
                _write_content_to_container(
                    container_id,
                    f"weight={weight}\ntimeout={timeout}\n",
                    f"/workspace/.verifiers/{name}.meta",
                    suffix=".meta",
                )
        logger.info(
            "Copied %d check scripts into .verifiers/ (%d with weight metadata)",
            len(check_scripts),
            len(checkpoint_meta),
        )
    else:
        logger.warning("No checks/ directory found in %s", task_dir)

    # Copy test_runner.sh as /workspace/test.sh
    if TEST_RUNNER_SH.exists():
        _docker_cp(str(TEST_RUNNER_SH), f"{container_id}:/workspace/test.sh")
        _docker_exec(container_id, ["chmod", "+x", "/workspace/test.sh"])
        logger.info("Copied test_runner.sh as /workspace/test.sh")

    # Copy eb_verify library for check scripts that import it.
    # The destination directory must exist BEFORE docker cp: copying a
    # directory to a non-existent path copies the source's *contents* into
    # the new directory, which drops the `eb_verify` package dir and breaks
    # `python3 -m eb_verify.plugins...` under PYTHONPATH=/workspace/.eb_verify.
    if EB_VERIFY_LIB.is_dir():
        _docker_exec(container_id, ["mkdir", "-p", "/workspace/.eb_verify"])
        _docker_cp(str(EB_VERIFY_LIB), f"{container_id}:/workspace/.eb_verify/")
        logger.info("Copied eb_verify library into container")

    # Copy ground_truth.json into a task metadata directory for verifiers
    gt_file = task_dir / "ground_truth.json"
    _docker_exec(container_id, ["mkdir", "-p", "/workspace/.task"])
    if gt_file.exists():
        _docker_cp(str(gt_file), f"{container_id}:/workspace/.task/ground_truth.json")
        logger.info("Copied ground_truth.json into container")

    # Fix ownership of copied files only — docker cp preserves host UID which
    # may not match the agent user inside the container.
    # Never chown -R /workspace (too slow for large repos like K8s, Terraform).
    # Fail-loud: a silently masked chown failure here is what produced
    # unreadable instruction.md and fake-0 no-op runs (bead EnterpriseBench-s58f).
    # The agent .mcp.json files are written and chowned later by
    # _configure_mcp; agent_output is created by the agent step — both are
    # intentionally omitted here (s58f design intent: don't chown stale
    # leftovers from a reused container).
    _chown_to_agent(container_id, [AGENT_INSTRUCTION])
    _seal_grading_assets(container_id)


def _install_claude_cli(container_id: str) -> bool:
    """Install Claude Code CLI and create non-root agent user. Returns True on success.

    If the Docker image was built with the updated dockerfile_generator (which
    pre-bakes Node.js, npm, Claude CLI, and the agent user), this function
    detects that and skips redundant work.
    """
    # Check if Claude CLI is already baked into the image
    ver = _docker_exec(container_id, ["claude", "--version"])
    if ver.returncode == 0:
        logger.info("Claude Code CLI already installed: %s", ver.stdout.strip())
    else:
        logger.info("Claude Code CLI not in image, installing...")
        # Ensure Node.js is available
        check_node = _docker_exec(container_id, ["which", "node"])
        if check_node.returncode != 0:
            logger.info("Node.js not found, installing via apt...")
            _docker_exec(
                container_id,
                [
                    "bash",
                    "-c",
                    "apt-get update -qq && apt-get install -y -qq nodejs npm >/dev/null 2>&1",
                ],
                timeout=300,
            )
        result = _docker_exec(
            container_id,
            [
                "bash",
                "-c",
                "npm install -g @anthropic-ai/claude-code@latest 2>&1 | tail -3",
            ],
            timeout=300,
        )
        if result.returncode != 0:
            logger.error("Failed to install Claude Code CLI: %s", result.stderr)
            return False

    # Ensure the non-root agent user exists and owns its own output dirs.
    # Images built with the updated dockerfile_generator already have the agent
    # user owning the cloned repos (USER agent before git clone). For older images
    # we still create the user and fix up output dirs only — never chown -R on
    # /workspace, which is both too slow for large repos and would hand the agent
    # the grading assets sealed inside it.
    _docker_exec(
        container_id,
        [
            "bash",
            "-c",
            "id agent >/dev/null 2>&1 || useradd -m -s /bin/bash agent; "
            f"mkdir -p {shlex.quote(AGENT_OUTPUT_DIR)}",
        ],
    )
    # Routed through the guarded helper rather than an inline `chown -R` so a
    # grading asset can never be re-granted to the agent from here again.
    _chown_to_agent(container_id, ["/home/agent", AGENT_OUTPUT_DIR])

    # Final verification
    ver = _docker_exec(container_id, ["claude", "--version"])
    if ver.returncode == 0:
        logger.info("Claude Code CLI ready: %s", ver.stdout.strip())
        return True
    logger.error("Claude Code CLI not found after install")
    return False


def _run_health_check(container_id: str, repos: list[dict]) -> bool:
    """Run health_check.sh inside the container. Returns True if healthy."""
    repo_paths = [r["path"] for r in repos if r.get("path")]

    # Check that repo directories exist and have .git
    all_healthy = True
    for repo_path in repo_paths:
        result = _docker_exec(
            container_id,
            ["test", "-d", f"/workspace/{repo_path}/.git"],
        )
        if result.returncode != 0:
            logger.error("Health check failed: /workspace/%s/.git not found", repo_path)
            all_healthy = False
        else:
            logger.info("Health check OK: /workspace/%s/", repo_path)

    return all_healthy


def _run_agent(
    container_id: str,
    agent_command: str,
    timeout: int,
    output_dir: Path,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, float]:
    """Execute the agent command inside the container.

    Returns (exit_code, duration_seconds).
    """
    _SAFE_AGENT_CMD_RE = re.compile(r"^[\w./@: -]+$")
    if not _SAFE_AGENT_CMD_RE.match(agent_command):
        raise ValueError(f"agent_command contains unsafe characters: {agent_command!r}")

    logger.info("Running agent: %s (timeout=%ds)", agent_command, timeout)

    # Write env vars to a temp file so they are not visible in `ps aux`
    env_items = dict(env_extra or {})
    env_items["HOME"] = "/home/agent"

    tmp_env_file = None
    start = time.monotonic()
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as fh:
            for key, value in env_items.items():
                fh.write(f"{key}={value}\n")
            tmp_env_file = fh.name

        full_cmd = [
            "docker",
            "exec",
            "--env-file",
            tmp_env_file,
            "-u",
            "agent",
            "-w",
            "/workspace",
            container_id,
        ] + [
            "bash",
            "-c",
            "mkdir -p /workspace/agent_output && "
            f"{agent_command} < /workspace/instruction.md",
        ]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.monotonic() - start
        exit_code = result.returncode

        # Save agent logs to both flat (backward compat) and agent/ subdir
        agent_dir = output_dir / "agent"
        agent_dir.mkdir(exist_ok=True)
        (output_dir / "agent_stdout.log").write_text(result.stdout)
        (output_dir / "agent_stderr.log").write_text(result.stderr)
        (agent_dir / "stdout.log").write_text(result.stdout)
        (agent_dir / "stderr.log").write_text(result.stderr)
        logger.info("Agent finished in %.1fs (exit %d)", duration, exit_code)

    except subprocess.TimeoutExpired as te:
        duration = time.monotonic() - start
        exit_code = 124
        # Capture any partial output the agent produced before timeout.
        # TimeoutExpired captures raw bytes even with text=True.
        raw_out = te.stdout if hasattr(te, "stdout") and te.stdout else b""
        raw_err = te.stderr if hasattr(te, "stderr") and te.stderr else b""
        partial_stdout = (
            raw_out.decode("utf-8", errors="replace")
            if isinstance(raw_out, bytes)
            else raw_out
        )
        partial_stderr = (
            raw_err.decode("utf-8", errors="replace")
            if isinstance(raw_err, bytes)
            else raw_err
        )
        stderr_content = f"{partial_stderr}\nTIMEOUT after {timeout}s\n"
        agent_dir = output_dir / "agent"
        agent_dir.mkdir(exist_ok=True)
        (output_dir / "agent_stdout.log").write_text(partial_stdout)
        (output_dir / "agent_stderr.log").write_text(stderr_content)
        (agent_dir / "stdout.log").write_text(partial_stdout)
        (agent_dir / "stderr.log").write_text(stderr_content)
        logger.error("Agent timed out after %ds", timeout)

    finally:
        if tmp_env_file is not None:
            try:
                os.unlink(tmp_env_file)
            except OSError:
                pass

    return exit_code, duration


def _route_verifier_infra_error(result: "TaskRunResult", scores: dict) -> None:
    """Route a ``verifier_infra_error`` on ``scores`` to the re-run channel.

    Single place that reacts to the scorer trust boundary: if any scoring stage
    tagged an infra error, mark the run so the phase-complete guard never
    records it as a legitimate score. Idempotent and safe to call after each
    stage.
    """
    infra_err = scores.get("verifier_infra_error")
    if not infra_err:
        return
    result.failure_class = "verifier_infra_error"
    result.phase = "verifier_infra_error"
    logger.warning(
        "Verifier infra error (%s): %s",
        infra_err.get("reason", "?"),
        infra_err.get("detail", ""),
    )


def _route_integrity_violation(result: "TaskRunResult", scores: dict) -> None:
    """Route a broken grading-asset seal to the INVALID (never-scored) channel.

    Deliberately NOT the ``verifier_infra_error`` re-run channel: that channel
    grants a fresh attempt, which would make sabotaging the harness a free
    mulligan — exactly the incentive inversion in bead EnterpriseBench-g5k5s.
    A tampered run is invalid and stays invalid.
    """
    violation = scores.get("integrity_violation")
    if not violation:
        return
    result.failure_class = "integrity_violation"
    result.phase = "integrity_violation"
    result.status = RUN_STATUS_INVALID
    result.success = False
    logger.error(
        "GRADING ASSET INTEGRITY VIOLATION (%s): %s",
        violation.get("reason", "?"),
        violation.get("detail", ""),
    )


def _route_zero_mcp_run(result: "TaskRunResult", mode: str) -> None:
    """Record MCP usage for an MCP-mode run, and gate ``mcp_only`` on zero calls.

    ``mcp_only`` is enforced only by a prompt preamble — local Read/Grep/Bash
    stay available in the container. An ``mcp_only`` run that made 0 MCP calls
    therefore delivered *baseline* tool access under an MCP label; scoring it
    into the mcp_only mean contaminates every MCP-vs-baseline comparison. Route
    it to the infra-error re-run channel instead.

    ``hybrid`` grants both toolsets by design, so 0 MCP calls there is a
    legitimate agent choice, not an infra failure: it is flagged and still
    scored. Gating it would drop exactly the runs where the agent preferred
    local tools and bias the hybrid arm upward.

    The gate is zero-vs-nonzero only. It catches a run that used no MCP at all;
    it does not make the arm clean, since an ``mcp_only`` run that made one MCP
    call and 300 local ones still passes.
    """
    if mode not in ("mcp_only", "hybrid"):
        return

    mcp_calls = result.tool_usage.get("mcp_tool_calls", 0)
    result.tool_usage["mcp_used"] = mcp_calls > 0

    if mcp_calls > 0:
        logger.info("Agent made %d MCP tool calls", mcp_calls)
        return

    if mode == "hybrid":
        logger.warning(
            "mode=hybrid but agent made 0 MCP tool calls — flagged as a "
            "local-tools run, still scored"
        )
        return

    result.status = RUN_STATUS_INVALID
    result.phase = "agent_infra_error"
    result.success = False

    if result.failure_class is not None:
        # An OOM kill, a timeout, a crashed agent or a broken MCP config all
        # produce 0 MCP calls as a *symptom*. Relabelling the run
        # infra_mcp_unused would bury the actual cause and send triage chasing
        # a phantom MCP problem, so keep the more specific classification.
        logger.info(
            "mode=mcp_only with 0 MCP tool calls, but the run already failed "
            "as %s — keeping that classification",
            result.failure_class,
        )
        return

    reason = (
        "mode=mcp_only but the agent made 0 MCP tool calls: the run had "
        "baseline tool access under an MCP label, so it is not a valid MCP "
        "measurement. Recorded as infra error for re-run."
    )
    logger.error(reason)
    result.failure_class = "infra_mcp_unused"
    result.error = reason


def _record_agent_trace(
    result: "TaskRunResult", container_id: str, output_dir: Path
) -> None:
    """Copy the conversation trace and record whether it landed.

    The capture result is what any trace-based audit (retrieval recall, tool
    telemetry) rests on, so a missing trace has to be visible on the artifacts
    rather than passing silently.
    """
    result.tool_usage["trace_captured"] = _copy_agent_trace(container_id, output_dir)
    if not result.tool_usage["trace_captured"]:
        logger.warning(
            "No agent trace captured — trace-derived metrics for this run are "
            "unavailable, not zero"
        )


def _run_scoring(container_id: str, verifier_timeout: int = 600) -> dict:
    """Run /workspace/test.sh and capture the JSON results.

    Refuses to run at all if the grading-asset seal is broken: a verifier the
    agent could have rewritten produces a number, not a measurement.
    """
    sealed, seal_err = _assert_grading_assets_sealed(container_id)
    if not sealed:
        return {
            "task_score": 0.0,
            "all_passed": False,
            "integrity_violation": {
                "reason": "grading_assets_tampered",
                "detail": seal_err,
            },
        }

    logger.info("Running checkpoint verifiers (timeout=%ds)...", verifier_timeout)

    # Scoring runs as SCORING_USER from a cwd neither it nor the agent can write.
    # Not as `agent` (the image ends with `USER agent`, so an exec without -u
    # would run the grader as the very user it is grading) and not as root: the
    # checks execute agent-controlled code by design, and a root grader turns
    # that into a rewrite of the seal. PYTHONSAFEPATH keeps the cwd off
    # sys.path[0] on py>=3.11; the cwd being unwritable is what holds on older
    # images. PYTHONDONTWRITEBYTECODE and the pytest cache opt-out keep the
    # checks from *needing* the write access they no longer have in the
    # now-closed workspace.
    _reap_agent_processes(container_id)
    _close_workspace_for_scoring(container_id)
    try:
        result = _docker_exec(
            container_id,
            [
                "bash",
                "-c",
                f"export WORKSPACE={shlex.quote(WORKSPACE_DIR)} "
                f"TASK_DIR={shlex.quote(TASK_DIR)} "
                f"PYTHONPATH={shlex.quote(EB_VERIFY_DIR)}:${{PYTHONPATH:-}} "
                f"HOME={shlex.quote(SCORING_WORKDIR)} "
                f"EB_RESULTS_FILE={shlex.quote(SCORING_RESULTS_FILE)} "
                f"PYTHONSAFEPATH=1 PYTHONDONTWRITEBYTECODE=1 "
                f"PYTEST_ADDOPTS={shlex.quote('-p no:cacheprovider')} "
                f"{GIT_SCORING_ENV}; "
                f"bash {shlex.quote(TEST_SH)}",
            ],
            timeout=verifier_timeout,
            workdir=SCORING_WORKDIR,
            user=SCORING_USER,
        )
    finally:
        _reopen_workspace_for_agent(container_id)

    # test.sh outputs JSON to stdout, diagnostics to stderr
    if result.stderr:
        logger.info("Verifier diagnostics:\n%s", result.stderr.rstrip())

    # Route the raw verifier output through the scorer trust boundary. Empty /
    # malformed output, a top-level error key, or a per-checkpoint infra
    # signature (docker-cp harness-import failure, explicit sentinel) become a
    # verifier_infra_error instead of a false 0.0 the caller would record as a
    # legitimate all-checkpoint-fail (beads s58f, hktt/pt0n, apfp #2).
    guarded = guard_verifier_output(result.stdout, result.returncode)
    if isinstance(guarded, InfraError):
        return {
            "task_score": 0.0,
            "all_passed": False,
            "verifier_infra_error": guarded.as_verifier_error(),
        }
    return guarded


# Canonical answer-artifact location appended to every task instruction by
# _build_instruction_text(); always a candidate for the LLM judge.
ANSWER_ARTIFACT_PATH = "/workspace/agent_output/answer.json"

# Matches a /workspace/... artifact path a task's instruction.md tells the agent
# to write to (e.g. /workspace/BLAST_RADIUS.md, /workspace/analysis/IMPACT_REPORT.md).
_WORKSPACE_ARTIFACT_RE = re.compile(r"/workspace/[A-Za-z0-9_./-]+\.(?:json|md|txt|ya?ml)")


def _derive_artifact_candidates(task_dir: Path) -> list[str]:
    """Candidate in-container paths where the agent's answer artifact may live.

    Derived from task metadata, not baked-in repo names. Always includes the
    canonical answer.json location (appended to every instruction by
    _build_instruction_text), then any /workspace/... artifact path the task's
    instruction.md instructs the agent to write to. Order preserved, deduped.
    """
    candidates: list[str] = [ANSWER_ARTIFACT_PATH]
    instruction = task_dir / "instruction.md"
    if instruction.exists():
        for match in _WORKSPACE_ARTIFACT_RE.findall(instruction.read_text()):
            if match not in candidates:
                candidates.append(match)
    return candidates


def _apply_llm_judge(
    scores: dict,
    task_dir: Path,
    container_id: str,
    task_data: dict,
) -> dict:
    """Apply Tier 2 LLM judge to cap grep-based scores.

    For each checkpoint with a curated expected_solution, runs the LLM judge
    and takes min(grep_score, judge_score). Returns updated scores dict.

    If the task declares an expected_solution but no agent artifact is found in
    the container, the Tier-2 ceiling cannot be applied — rather than silently
    returning the un-capped grep scores (which records inflated grep as the real
    measurement), the scores dict is tagged with a ``verifier_infra_error`` so
    the caller routes the run to the re-run channel.
    """
    expected_path = task_dir / "expected_solution.json"
    if not expected_path.exists():
        logger.info("No expected_solution.json, skipping LLM judge")
        return scores

    try:
        expected = json.loads(expected_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        # The task declares llm_curator + expected_solution but its ground
        # truth won't load: the Tier-2 cap cannot be applied. Do NOT pass the
        # un-capped grep scores through as a real measurement (apfp #3) — flag
        # the run for the re-run channel.
        logger.warning("Failed to load expected_solution.json: %s", exc)
        scores["verifier_infra_error"] = InfraError(
            reason="malformed_expected_solution",
            stage="llm_judge",
            detail=f"could not load expected_solution.json: {exc}",
        ).as_verifier_error()
        return scores

    checkpoints_gt = expected.get("checkpoints", {})
    if not checkpoints_gt:
        return scores

    # Extract agent output from container — candidate paths derived from task
    # metadata (canonical answer.json + instruction.md output paths), not from
    # baked-in repo names.
    candidates = _derive_artifact_candidates(task_dir)
    agent_output = ""
    for path in candidates:
        result = _docker_exec(container_id, ["cat", path], timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            agent_output = result.stdout.strip()
            break

    if not agent_output:
        # llm_curator + expected_solution present but no agent artifact found:
        # the Tier-2 cap cannot be applied. Do NOT pass through un-capped grep
        # scores as a real measurement — route to the re-run channel instead.
        logger.warning(
            "LLM judge: no agent output found for llm_curator task with "
            "expected_solution (candidates: %s) — routing to verifier_infra_error",
            ", ".join(candidates),
        )
        scores["verifier_infra_error"] = InfraError(
            reason="no_agent_output",
            stage="llm_judge",
            detail=(
                "llm_curator task declares expected_solution but no agent "
                "artifact was found in the container; Tier-2 cap could not be "
                "applied, so the deterministic grep scores are un-capped and "
                "must not be recorded as the final measurement"
            ),
            context={"candidates": candidates},
        ).as_verifier_error()
        return scores

    try:
        sys.path.insert(0, str(REPO_ROOT / "lib"))
        from eb_verify.judge import CheckpointJudgeInput, LLMJudge

        judge = LLMJudge(model="cc:haiku")
    except Exception as exc:
        # Judge could not be constructed (import/config/credential failure):
        # the Tier-2 cap cannot be applied. Flag infra error rather than
        # recording the un-capped grep scores as real (apfp #3).
        logger.warning("Failed to init LLM judge: %s", exc)
        scores["verifier_infra_error"] = InfraError(
            reason="judge_init_failed",
            stage="llm_judge",
            detail=f"could not initialize LLM judge: {exc}",
        ).as_verifier_error()
        return scores

    task_desc = task_data.get("task", {}).get("description", "")

    checkpoints = scores.get("checkpoints", [])
    for cp in checkpoints:
        cp_name = cp.get("name", "")
        cp_gt = checkpoints_gt.get(cp_name)
        if cp_gt is None:
            continue

        grep_score = cp.get("score", 0.0)

        try:
            judge_result = judge.evaluate_checkpoint(
                CheckpointJudgeInput(
                    task_id=expected.get("task_id", ""),
                    checkpoint_name=cp_name,
                    agent_output=agent_output,
                    expected_solution=cp_gt["expected_solution"],
                    evaluation_criteria=cp_gt.get("evaluation_criteria", []),
                ),
                task_description=task_desc,
                checkpoint_description=cp_gt.get("expected_solution", "")[:200],
            )
            judge_score = judge_result.score
        except Exception as exc:
            # A judge call raised: this checkpoint's grep score cannot be
            # capped. A bare `continue` would silently keep the un-capped grep
            # score (apfp #3, over-credit on judge outage). Flag the whole run
            # for re-run — a partially-capped score is not a real measurement.
            logger.warning("LLM judge failed for %s: %s", cp_name, exc)
            scores["verifier_infra_error"] = InfraError(
                reason="judge_checkpoint_failed",
                stage="llm_judge",
                detail=f"LLM judge raised on checkpoint {cp_name!r}: {exc}",
                context={"checkpoint": cp_name},
            ).as_verifier_error()
            return scores

        final_score = min(grep_score, judge_score)
        logger.info(
            "LLM judge: %s grep=%.2f judge=%.2f final=%.2f (%s)",
            cp_name,
            grep_score,
            judge_score,
            final_score,
            judge_result.reasoning[:80],
        )
        cp["score"] = final_score
        cp["passed"] = final_score > 0.0
        cp["judge_score"] = judge_score
        cp["grep_score"] = grep_score

    # Recompute task_score
    total_weight = sum(c.get("weight", 1.0) for c in checkpoints)
    if total_weight > 0:
        scores["task_score"] = sum(
            c.get("score", 0.0) * c.get("weight", 1.0) for c in checkpoints
        )

    return scores


def _save_results(
    result: TaskRunResult,
    task_data: dict,
    output_dir: Path,
    config: TaskRunConfig,
) -> Path:
    """Save run results, metadata, and scores to the output directory.

    Produces an enriched directory layout:
        results.json       — top-level results (backward compatible)
        config.json        — snapshot of run configuration
        task_metrics.json   — timing, tool_usage, status for skip-completed
        agent/              — agent stdout/stderr logs (created for later use)
        verifier/output.json — verifier scoring output
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Derived once so the two artifacts below cannot disagree.
    status = _effective_status(result)

    # --- results.json (backward compatible) ---
    results_path = output_dir / "results.json"
    payload = {
        "task_id": result.task_id,
        "success": result.success,
        "phase": result.phase,
        "status": status,
        "error": result.error,
        "failure_class": result.failure_class,
        "image_tag": result.image_tag,
        "scores": result.scores,
        "timing": result.timing,
        "tool_usage": result.tool_usage,
        "config": {
            "source": config.source,
            "agent_command": config.agent_command,
            "timeout": config.timeout,
            "dry_run": config.dry_run,
            "mode": config.mode,
        },
        "task_metadata": {
            "suite": task_data.get("task", {}).get("suite", ""),
            "task_type": task_data.get("task", {}).get("task_type", ""),
            "difficulty": task_data.get("task", {}).get("difficulty", ""),
            "languages": task_data.get("metadata", {}).get("languages", []),
        },
    }
    results_path.write_text(json.dumps(payload, indent=2) + "\n")

    # --- config.json — run configuration snapshot ---
    config_payload = {
        "source": config.source,
        "agent_command": config.agent_command,
        "timeout": config.timeout,
        "build_timeout": config.build_timeout,
        "verifier_timeout": config.verifier_timeout,
        "memory_mb": config.memory_mb,
        "dry_run": config.dry_run,
        "no_build": config.no_build,
        "keep_container": config.keep_container,
        "mode": config.mode,
    }
    (output_dir / "config.json").write_text(json.dumps(config_payload, indent=2) + "\n")

    # --- task_metrics.json — timing, tool_usage, status ---
    metrics_payload = {
        "task_id": result.task_id,
        "success": result.success,
        "phase": result.phase,
        "status": status,
        "error": result.error,
        "failure_class": result.failure_class,
        "timing": result.timing,
        "tool_usage": result.tool_usage,
    }
    (output_dir / "task_metrics.json").write_text(
        json.dumps(metrics_payload, indent=2) + "\n"
    )

    # --- agent/ subdirectory (logs written here by _run_agent) ---
    (output_dir / "agent").mkdir(exist_ok=True)

    # --- verifier/ subdirectory with scoring output ---
    verifier_dir = output_dir / "verifier"
    verifier_dir.mkdir(exist_ok=True)
    if result.scores:
        (verifier_dir / "output.json").write_text(
            json.dumps(result.scores, indent=2) + "\n"
        )

    logger.info("Results saved to: %s", results_path)
    return results_path


def _mcp_exec(
    container_id: str, cmd: list[str], timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run a command as agent with MCP-required env vars."""
    return subprocess.run(
        [
            "docker",
            "exec",
            "-u",
            "agent",
            "-e",
            "HOME=/home/agent",
            "-e",
            "NODE_TLS_REJECT_UNAUTHORIZED=0",
            "-w",
            "/workspace",
            container_id,
        ]
        + cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _verify_mcp_endpoint(container_id: str, sg_token: str) -> bool:
    """Verify the MCP endpoint is reachable and auth works via direct HTTP.

    Uses curl inside the container to hit the endpoint directly, bypassing
    Claude Code's MCP client entirely. This confirms that:
    - The endpoint is reachable from the container
    - The auth token is accepted
    - TLS settings are correct

    Returns True if the endpoint responds with HTTP 200.
    """
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        result = _docker_exec(
            container_id,
            [
                "curl",
                "-sf",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-H",
                f"Authorization: token {sg_token}",
                "-H",
                "Content-Type: application/json",
                "--max-time",
                "10",
                "-k",  # skip TLS verification (matches NODE_TLS_REJECT_UNAUTHORIZED=0)
                SOURCEGRAPH_MCP_ENDPOINT,
            ],
            timeout=15,
        )
        http_code = result.stdout.strip()
        # 200 = OK, 405 = Method Not Allowed (GET on POST-only MCP endpoint —
        # means reachable and auth accepted, just wrong HTTP method)
        if http_code in ("200", "405") or result.returncode == 0:
            logger.info(
                "MCP endpoint HTTP check OK (attempt %d, code=%s)",
                attempt,
                http_code,
            )
            return True
        backoff = min(2**attempt, 10)
        logger.warning(
            "MCP endpoint HTTP check attempt %d/%d failed "
            "(code=%s, rc=%d, err=%s) — retrying in %ds",
            attempt,
            max_retries,
            http_code,
            result.returncode,
            result.stderr.strip()[:120],
            backoff,
        )
        time.sleep(backoff)
    logger.error("MCP endpoint HTTP check FAILED after %d attempts", max_retries)
    return False


def _configure_mcp(container_id: str, mode: str) -> bool:
    """Configure Sourcegraph MCP endpoint with pre-flight verification.

    Strategy for 100% reliability:
    1. Verify the endpoint is reachable via direct HTTP (curl)
    2. Write .mcp.json to /workspace (project-level, Claude Code auto-discovers)
    3. Write equivalent config to ~/.claude/settings.json (user-level fallback)
    4. Verify via `claude mcp list` with retries

    Uses ONLY config files (no `claude mcp add` which has race conditions).
    Both project-level and user-level configs are written so Claude Code finds
    auth headers regardless of which config path it resolves first.

    Returns True when the handshake succeeded (or the mode has no MCP);
    False when the pre-flight failed — the caller must treat that as a hard
    gate and route the run to the infra-error re-run channel, never score it.
    """
    if mode not in ("mcp_only", "hybrid"):
        return True

    sg_token = os.environ.get("SOURCEGRAPH_ACCESS_TOKEN", "")
    if not sg_token:
        logger.warning("SOURCEGRAPH_ACCESS_TOKEN not set; MCP will not authenticate")

    logger.info("Configuring Sourcegraph MCP endpoint (mode=%s)", mode)

    # Step 1: Verify endpoint is reachable and auth works via HTTP.
    # A failure here (unreachable host, or a rejected/expired token returning
    # 401) means the MCP arm cannot run validly. Hard-fail the pre-flight and
    # return False instead of writing config and running a degraded no-MCP
    # agent whose result would masquerade as a real MCP measurement
    # (bead EnterpriseBench-c7wb). The caller routes a False return to the
    # infra-error re-run channel.
    if sg_token:
        if not _verify_mcp_endpoint(container_id, sg_token):
            logger.error(
                "MCP endpoint unreachable or auth rejected (mode=%s) — "
                "failing MCP pre-flight; run will be routed to the infra-error "
                "re-run channel, never scored as a degraded run",
                mode,
            )
            return False

    # Step 2: Write MCP config files (using docker cp to avoid shell escaping)
    mcp_config_json = json.dumps(
        {
            "mcpServers": {
                "sourcegraph": {
                    "type": "http",
                    "url": SOURCEGRAPH_MCP_ENDPOINT,
                    "headers": {"Authorization": f"token {sg_token}"},
                }
            }
        }
    )

    # Write config via docker cp to both project-level and user-level paths.
    # Same format works for both; writing to two locations ensures Claude Code
    # finds auth headers regardless of which config path it resolves first.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".mcp.json", delete=False) as fh:
        fh.write(mcp_config_json)
        tmp_project = fh.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".mcp.json", delete=False) as fh:
        fh.write(mcp_config_json)
        tmp_user = fh.name

    try:
        # Project-level config: /workspace/.mcp.json
        _docker_cp(tmp_project, f"{container_id}:/workspace/.mcp.json")
        _docker_exec(
            container_id,
            ["chown", "agent:agent", "/workspace/.mcp.json"],
        )

        # User-level config: /home/agent/.claude/.mcp.json
        _docker_exec(
            container_id,
            [
                "bash",
                "-c",
                "mkdir -p /home/agent/.claude && chown agent:agent /home/agent/.claude",
            ],
        )
        _docker_cp(tmp_user, f"{container_id}:/home/agent/.claude/.mcp.json")
        _docker_exec(
            container_id,
            ["chown", "agent:agent", "/home/agent/.claude/.mcp.json"],
        )

        # Also write to /home/agent/.mcp.json for --mcp-config flag
        _docker_cp(tmp_project, f"{container_id}:/home/agent/.mcp.json")
        _docker_exec(
            container_id,
            ["chown", "agent:agent", "/home/agent/.mcp.json"],
        )
        logger.info(
            "MCP config written to /workspace/.mcp.json, "
            "/home/agent/.claude/.mcp.json, and /home/agent/.mcp.json"
        )
    finally:
        os.unlink(tmp_project)
        os.unlink(tmp_user)

    # Step 3: Verify Claude Code sees the MCP server with retries
    handshake_ok = False
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        check = _mcp_exec(container_id, ["claude", "mcp", "list"])
        stdout = check.stdout.strip()
        if "sourcegraph" in stdout.lower():
            if "Connected" in stdout:
                logger.info(
                    "MCP pre-flight OK (attempt %d): sourcegraph connected",
                    attempt,
                )
                handshake_ok = True
                break
            if "needs-auth" in stdout:
                # Server is registered but auth failed — likely a timing issue
                # with the HTTP transport. Wait and retry.
                logger.warning(
                    "MCP pre-flight attempt %d/%d: server registered but "
                    "needs-auth (will retry)",
                    attempt,
                    max_retries,
                )
            else:
                logger.warning(
                    "MCP pre-flight attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    stdout.replace("\n", " ")[:200],
                )
        else:
            logger.warning(
                "MCP pre-flight attempt %d/%d: sourcegraph not found in: %s",
                attempt,
                max_retries,
                stdout.replace("\n", " ")[:200],
            )
        if attempt < max_retries:
            backoff = min(2**attempt, 8)
            time.sleep(backoff)
    else:
        logger.error(
            "MCP pre-flight FAILED after %d attempts — handshake never "
            "succeeded; caller will route this run to the infra-error re-run "
            "channel (not a scored degraded run)",
            max_retries,
        )

    if handshake_ok:
        logger.info("MCP endpoint configured: %s", SOURCEGRAPH_MCP_ENDPOINT)
    return handshake_ok


def _sum_model_usage(model_usage: dict) -> tuple[int, int, float]:
    """Sum token counts and cost across all models in a modelUsage dict.

    modelUsage can be either:
      - flat: {"inputTokens": N, "outputTokens": N, "costUSD": N}
      - per-model: {"claude-sonnet-4-6": {"inputTokens": N, ...}, ...}

    Returns (total_input, total_output, total_cost).
    """
    # Flat format: has inputTokens at top level
    if "inputTokens" in model_usage:
        return (
            model_usage.get("inputTokens", 0),
            model_usage.get("outputTokens", 0),
            model_usage.get("costUSD", 0.0),
        )
    # Per-model format: sum across all model entries
    total_input = 0
    total_output = 0
    total_cost = 0.0
    for val in model_usage.values():
        if isinstance(val, dict):
            total_input += val.get("inputTokens", 0)
            total_output += val.get("outputTokens", 0)
            total_cost += val.get("costUSD", 0.0)
    return total_input, total_output, total_cost


def _extract_tool_usage(output_dir: Path) -> dict:
    """Parse the agent's stdout log for tool-usage metadata.

    Claude Code JSON output includes modelUsage data with token counts,
    cost, and turn information. modelUsage may be flat (single model)
    or keyed by model name (multi-model). Returns a dict for results.json.
    """
    usage: dict = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "cost_usd": 0.0,
        "num_turns": 0,
        "mcp_tool_calls": 0,
    }

    stdout_log = output_dir / "agent_stdout.log"
    if not stdout_log.exists():
        return usage

    content = stdout_log.read_text()
    if not content.strip():
        return usage

    # Counted on the raw log, before the format branch below: the count must
    # not depend on --output-format, since the zero-MCP gate reads it.
    usage["mcp_tool_calls"] = content.count("mcp__sourcegraph__")

    # Claude Code --output-format json produces a JSON object on stdout.
    # Try to parse the entire output as JSON first.
    try:
        data = json.loads(content)
        inp, out, cost = _sum_model_usage(data.get("modelUsage", {}))
        usage["total_input_tokens"] = inp
        usage["total_output_tokens"] = out
        usage["cost_usd"] = cost or data.get("total_cost_usd", 0.0)
        usage["num_turns"] = data.get("numTurns", 0)
        return usage
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: scan stream-json lines for the result message and modelUsage
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if "modelUsage" in obj:
                inp, out, cost = _sum_model_usage(obj["modelUsage"])
                usage["total_input_tokens"] = inp
                usage["total_output_tokens"] = out
                usage["cost_usd"] = cost or obj.get("total_cost_usd", 0.0)
            if "numTurns" in obj:
                usage["num_turns"] = max(usage["num_turns"], obj["numTurns"])
            if "num_turns" in obj:
                usage["num_turns"] = max(usage["num_turns"], obj["num_turns"])
        except (json.JSONDecodeError, ValueError):
            continue

    return usage


def _effective_status(result: "TaskRunResult") -> str:
    """The status to persist: INVALID for any run that failed short of complete.

    An explicitly-set ``status`` wins; otherwise it is derived, because most
    infra branches (OOM, timeout, build-failed, setup-failed) set only
    ``phase``/``success`` and reading the raw field would let those runs look
    scoreable on disk. Deriving means a new failure branch cannot forget to
    flag its run: there is no allow-list to drift out of sync.

    A run is INVALID unless it succeeded or reached ``phase == "complete"``.
    The disjunction is what keeps ``dry_run_complete`` non-INVALID — it sets
    ``success=True`` and is not a failure, though it never scores.
    """
    if result.status:
        return result.status
    if result.success or result.phase == "complete":
        return ""
    return RUN_STATUS_INVALID


def _newest_trace_path(find_stdout: str) -> str | None:
    """Pick the newest trace path from NUL-delimited ``<mtime> <path>`` records.

    Returns None when no well-formed record survives validation. A record is
    dropped unless its mtime parses as a float and its path sits under
    ``TRACE_ROOT`` — the container is agent-controlled, so a path that claims to
    live elsewhere is treated as hostile rather than copied out.
    """
    candidates: list[tuple[float, str]] = []

    for record in find_stdout.split("\0"):
        if not record or " " not in record:
            continue
        raw_mtime, path = record.split(" ", 1)
        try:
            mtime = float(raw_mtime)
        except ValueError:
            logger.warning("Skipping trace record with unparseable mtime")
            continue
        if not path.startswith(f"{TRACE_ROOT}/"):
            logger.warning("Skipping trace path outside %s", TRACE_ROOT)
            continue
        candidates.append((mtime, path))

    if not candidates:
        return None

    return max(candidates, key=lambda candidate: candidate[0])[1]


def _copy_agent_trace(container_id: str, output_dir: Path) -> bool:
    """Copy the Claude Code conversation trace from the container.

    Claude Code stores session JSONL files under
    /home/agent/.claude/projects/<hash>/.  This function finds the most
    recent conversation JSONL and copies it to output_dir/agent_trace.jsonl.

    Returns True if a trace was successfully copied, False otherwise.
    Never raises — failures are logged and the run continues.
    """
    try:
        # Records are NUL-delimited and ranked in Python, not by `sort -rn`. A
        # newline is a legal filename character and the agent owns this
        # filesystem, so newline-framed `-printf '%T@ %p\n'` output lets a
        # crafted filename emit what looks like a second "<mtime> <path>"
        # record; a NUL cannot appear in a filename, so it cannot.
        find_result = _docker_exec(
            container_id,
            [
                "bash",
                "-c",
                f"find {TRACE_ROOT} -name '*.jsonl' -type f "
                "-printf '%T@ %p\\0' 2>/dev/null",
            ],
            timeout=30,
        )

        if find_result.returncode != 0 or not find_result.stdout.strip():
            logger.info("No agent conversation trace found in container")
            return False

        trace_path = _newest_trace_path(find_result.stdout)
        if trace_path is None:
            logger.info("No agent conversation trace found in container")
            return False

        dest = str(output_dir / "agent_trace.jsonl")
        _docker_cp(f"{container_id}:{trace_path}", dest)

        logger.info("Copied agent conversation trace to %s", dest)
        return True

    except subprocess.TimeoutExpired:
        logger.warning("Timed out while copying agent trace from container")
        return False
    except Exception as exc:
        logger.warning("Error copying agent trace: %s", exc)
        return False


def _check_disk_space(min_gb: float = 5.0) -> bool:
    """Check available disk space on the Docker storage path.

    Returns True if available space exceeds min_gb, False otherwise.
    Logs a warning when space is insufficient.
    """
    check_path = "/var/lib/docker" if os.path.exists("/var/lib/docker") else "/"
    try:
        usage = shutil.disk_usage(check_path)
        available_gb = usage.free / (1024**3)
        if available_gb < min_gb:
            logger.warning(
                "Low disk space on %s: %.1f GB available (minimum %.1f GB required)",
                check_path,
                available_gb,
                min_gb,
            )
            return False
        logger.debug("Disk space OK on %s: %.1f GB available", check_path, available_gb)
        return True
    except OSError as exc:
        logger.warning("Could not check disk space on %s: %s", check_path, exc)
        return True  # Fail open — don't block if we can't check


def run_task(config: TaskRunConfig) -> TaskRunResult:
    """Execute the full single-session task lifecycle.

    Phases:
        1. Parse task.toml
        2. Generate and build Dockerfile
        3. Create container and set up workspace
        4. Run agent (unless --dry-run)
        5. Score with checkpoint verifiers
        6. Save results and clean up
    """
    timings: dict[str, float] = {}
    result = TaskRunResult(task_id="unknown")

    try:
        # --- Phase 1: Parse ---
        t0 = time.monotonic()
        task_data = _parse_task(config.task_toml)
        task_info = task_data["task"]
        task_id = task_info["id"]
        task_dir = config.task_toml.parent.resolve()
        repos = task_data.get("repos", [])

        result.task_id = task_id
        # Include mode + ablation variant in image tag to prevent cache collisions
        mode_suffix = f"-{config.mode}" if config.mode != "baseline" else ""
        ablation_suffix = (
            f"-ablate-{config.ablation_variant}" if config.ablation_variant else ""
        )
        image_tag = f"eb-{task_id}{mode_suffix}{ablation_suffix}"
        container_name = (
            f"eb-run-{task_id}{mode_suffix}{ablation_suffix}-{time.time_ns()}"
        )
        result.image_tag = image_tag
        timings["parse"] = time.monotonic() - t0

        # Resolve output directory
        # Layout: results/runs/<task_id>/<mode>/rep<N>/  (mode + rep partitioned)
        # This prevents concurrent and repeated runs from overwriting each other.
        if config.output_dir is not None:
            output_dir = config.output_dir
        else:
            base = REPO_ROOT / "results" / "runs" / task_id / config.mode
            if config.rep is not None:
                output_dir = base / f"rep{config.rep}"
            else:
                output_dir = base
        output_dir.mkdir(parents=True, exist_ok=True)
        result.output_dir = str(output_dir)

        logger.info(
            "Task: %s (suite=%s, type=%s)",
            task_id,
            task_info.get("suite"),
            task_info.get("task_type"),
        )

        # --- Disk pre-flight ---
        if not _check_disk_space(min_gb=config.min_disk_gb):
            result.phase = "preflight_failed"
            result.error = "Insufficient disk space"
            result.failure_class = "infra_disk"
            _save_results(result, task_data, output_dir, config)
            return result

        # --- Phase 2: Build ---
        if not config.no_build:
            t0 = time.monotonic()
            try:
                dockerfile_path = _generate_dockerfile(config.task_toml, config.source)
                _docker_build(dockerfile_path, image_tag, timeout=config.build_timeout)
            except Exception as build_exc:
                result.phase = "build_failed"
                result.error = str(build_exc)
                result.failure_class = "infra_build"
                result.timing = timings
                _save_results(result, task_data, output_dir, config)
                raise
            timings["build"] = time.monotonic() - t0
        else:
            logger.info("Skipping Docker build (--no-build)")

        # --- Phase 3: Setup ---
        t0 = time.monotonic()
        try:
            container_id = _docker_create_container(
                image_tag, container_name, config.memory_mb
            )
            result.container_id = container_id
            _docker_start(container_id)
            _setup_container(container_id, task_dir, task_data, mode=config.mode)
        except Exception as setup_exc:
            result.phase = "setup_failed"
            result.error = str(setup_exc)
            result.failure_class = "infra_clone"
            result.timing = timings
            _save_results(result, task_data, output_dir, config)
            raise

        healthy = _run_health_check(container_id, repos)
        timings["setup"] = time.monotonic() - t0

        if not healthy:
            logger.warning("Health check reported issues (continuing anyway)")

        # --- Configure MCP if needed ---
        if config.mode in ("mcp_only", "hybrid"):
            mcp_handshake_ok = _configure_mcp(container_id, config.mode)

            # MCP pre-flight is a HARD gate for the MCP arms. If the endpoint
            # never handshaked (unreachable / expired or rejected token), the
            # agent would run with no working MCP and the result would be
            # silently recorded as an MCP measurement — corrupting the MCP arm
            # of any affected comparison. Route it to the infra-error re-run
            # channel instead of proceeding degraded (bead EnterpriseBench-c7wb).
            # This branch only runs for mcp_only/hybrid; the baseline arm has no
            # MCP and is unaffected.
            if not mcp_handshake_ok:
                logger.error(
                    "MCP pre-flight FAILED for mode=%s — routing to infra-error "
                    "re-run channel (not a scored degraded run)",
                    config.mode,
                )
                result.phase = "mcp_infra_error"
                result.status = RUN_STATUS_INVALID
                result.success = False
                result.failure_class = "infra_mcp_preflight"
                result.error = (
                    "MCP pre-flight failed: endpoint unreachable or token "
                    f"rejected/expired (mode={config.mode}). The MCP arm cannot "
                    "run validly; recorded as infra error for re-run."
                )
                result.timing = timings
                _save_results(result, task_data, output_dir, config)
                return result

        # --- Dry run stops here ---
        if config.dry_run:
            result.phase = "dry_run_complete"
            result.success = True
            result.timing = timings
            logger.info(
                "Dry run complete. Container: %s, Image: %s, Mode: %s",
                container_name,
                image_tag,
                config.mode,
            )
            return result

        # --- Phase 4: Agent ---
        # Resolve OAuth token if --account was specified
        env_extra: dict[str, str] = {}
        agent_command = config.agent_command

        # Set Sourcegraph access token and TLS bypass for MCP modes
        if config.mode in ("mcp_only", "hybrid"):
            env_extra["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
            sg_token = os.environ.get("SOURCEGRAPH_ACCESS_TOKEN", "")
            if sg_token:
                env_extra["SOURCEGRAPH_ACCESS_TOKEN"] = sg_token
            else:
                logger.warning(
                    "SOURCEGRAPH_ACCESS_TOKEN not set in environment; "
                    "MCP endpoint may not authenticate (mode=%s)",
                    config.mode,
                )

        if config.account is not None:
            try:
                oauth_token = _load_oauth_token(config.account)
            except (FileNotFoundError, ValueError) as auth_exc:
                result.phase = "setup_failed"
                result.error = str(auth_exc)
                result.failure_class = "infra_auth"
                result.timing = timings
                _save_results(result, task_data, output_dir, config)
                return result
            env_extra["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
            # Use default OAuth agent command if none was explicitly provided
            if not agent_command:
                agent_command = DEFAULT_OAUTH_AGENT_COMMAND
            # For MCP modes, add --mcp-config flag for explicit config loading
            # (auto-discovery from project dir is less reliable)
            if (
                config.mode in ("mcp_only", "hybrid")
                and "--mcp-config" not in agent_command
            ):
                agent_command = agent_command + " --mcp-config /home/agent/.mcp.json"
            # Install Claude Code CLI inside the container
            if not _install_claude_cli(container_id):
                result.phase = "setup_failed"
                result.error = "Failed to install Claude Code CLI"
                result.failure_class = "infra_clone"
                result.timing = timings
                _save_results(result, task_data, output_dir, config)
                return result

        if agent_command:
            # --- Pre-agent readability gate (fail loud, never fake-0) ---
            # Re-assert ownership then verify the AGENT user can actually read
            # the files it needs. A silent EACCES here previously let the agent
            # fail to start while the run still recorded success=True,
            # num_turns=0, task_score=0.0 — a fake 0 that corrupted the
            # MCP-vs-baseline comparison (bead EnterpriseBench-s58f).
            readability_targets = ["/workspace/instruction.md"]
            if config.mode in ("mcp_only", "hybrid"):
                readability_targets += [
                    "/workspace/.mcp.json",
                    "/home/agent/.mcp.json",
                ]
            _chown_to_agent(container_id, readability_targets)
            readable, read_err = _assert_agent_readable(
                container_id, readability_targets
            )
            if not readable:
                logger.error("Pre-agent readability gate FAILED: %s", read_err)
                result.phase = "agent_preflight_failed"
                result.status = RUN_STATUS_INVALID
                result.success = False
                result.failure_class = "infra_perms"
                result.error = read_err
                result.timing = timings
                _save_results(result, task_data, output_dir, config)
                return result

            t0 = time.monotonic()
            agent_exit, agent_duration = _run_agent(
                container_id,
                agent_command,
                config.timeout,
                output_dir,
                env_extra=env_extra,
            )
            timings["agent"] = agent_duration

            if agent_exit == 137:
                result.failure_class = "infra_oom"
                result.phase = "agent_infra_error"
                logger.warning("Agent killed by OOM/SIGKILL (exit 137)")
            elif agent_exit == 124:
                result.failure_class = "infra_timeout"
                result.phase = "agent_infra_error"
                logger.warning("Agent timed out (exit 124)")
            elif agent_exit != 0:
                result.failure_class = "agent_error"
                logger.warning("Agent exited with non-zero code: %d", agent_exit)

            # Extract tool-usage metadata from agent output
            result.tool_usage = _extract_tool_usage(output_dir)

            # An MCP-config parse / EACCES error means the agent never really
            # started: route to the infra-error re-run channel instead of
            # recording a fake 0.0 score (bead EnterpriseBench-s58f).
            if _scan_mcp_config_error(output_dir):
                result.status = RUN_STATUS_INVALID
                result.failure_class = "infra_mcp_config"
                result.phase = "agent_infra_error"
                logger.error(
                    "MCP-config / EACCES error in agent stderr — run is "
                    "INVALID, recording as infra error for re-run"
                )

            _route_zero_mcp_run(result, config.mode)
            _record_agent_trace(result, container_id, output_dir)
        else:
            logger.info("No agent command specified, skipping agent phase")

        # --- Phase 5: Score (Tier 1 — deterministic) ---
        t0 = time.monotonic()
        scores = _run_scoring(container_id, config.verifier_timeout)
        timings["scoring"] = time.monotonic() - t0
        _route_verifier_infra_error(result, scores)
        _route_integrity_violation(result, scores)

        # --- Phase 5b: Score (Tier 2 — LLM curator) ---
        verification_modes = task_data.get("verification_modes", ["deterministic"])
        if (
            "llm_curator" in verification_modes
            and result.phase not in UNTRUSTED_SCORE_PHASES
        ):
            scores = _apply_llm_judge(scores, task_dir, container_id, task_data)
            _route_verifier_infra_error(result, scores)

        result.scores = scores

        # --- Save ---
        # Phases flagged *inline* during the agent/scoring stages reach here
        # without an early return; every other failure has already returned.
        # Those inline-flagged phases (agent/verifier infra errors, and a broken
        # grading-asset seal) must never be overwritten with complete/success.
        if result.phase not in NON_COMPLETE_PHASES:
            result.phase = "complete"
            result.success = True
        result.timing = timings
        _save_results(result, task_data, output_dir, config)

        return result

    except Exception as e:
        result.error = type(e).__name__
        result.phase = "error"
        result.timing = timings
        if isinstance(e, subprocess.TimeoutExpired):
            result.failure_class = "infra_timeout"
        elif result.failure_class is None:
            # failure_class may already be set by inner handlers that re-raised
            pass
        logger.error("Task run failed: %s", e, exc_info=True)
        # Always save results, even on error — so we have a record
        try:
            _save_results(result, task_data, output_dir, config)
        except Exception:
            logger.warning("Failed to save error results for %s", result.task_id)
        return result

    finally:
        # --- Phase 6: Cleanup ---
        if result.container_id and not config.keep_container:
            if not config.dry_run or not config.keep_container:
                logger.info("Cleaning up container: %s", result.container_id)
                _docker_stop_rm(result.container_id)
        elif result.container_id:
            logger.info("Keeping container for debugging: %s", result.container_id)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a single-session EnterpriseBench task in a Docker sandbox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Dry run: build container and validate setup
  python3 scripts/orchestration/run_task.py benchmarks/.../task.toml --dry-run

  # Run with an agent
  python3 scripts/orchestration/run_task.py benchmarks/.../task.toml --agent "claude -p"

  # Use upstream repos instead of mirrors
  python3 scripts/orchestration/run_task.py benchmarks/.../task.toml --source upstream --dry-run
""",
    )
    parser.add_argument(
        "task_toml",
        type=Path,
        help="Path to the task.toml file",
    )
    parser.add_argument(
        "--source",
        choices=["mirror", "upstream"],
        default="mirror",
        help="Clone source: 'mirror' (default) or 'upstream'",
    )
    parser.add_argument(
        "--agent",
        dest="agent_command",
        default="",
        help="Agent command to run (e.g. 'claude -p')",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Max seconds for agent execution (default: 1800)",
    )
    parser.add_argument(
        "--build-timeout",
        type=int,
        default=1800,
        help="Max seconds for Docker image build (default: 1800)",
    )
    parser.add_argument(
        "--verifier-timeout",
        type=int,
        default=600,
        help="Max seconds for verifier/scoring (default: 600)",
    )
    parser.add_argument(
        "--memory",
        type=int,
        default=8192,
        dest="memory_mb",
        help="Container memory limit in MB (default: 8192)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to save results (default: results/runs/<task-id>/<mode>/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build container and validate setup, but do not run agent",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip Docker build (reuse existing image)",
    )
    parser.add_argument(
        "--keep-container",
        action="store_true",
        help="Keep container after run for debugging",
    )
    parser.add_argument(
        "--account",
        type=int,
        default=None,
        help=(
            "OAuth account number N (loads token from "
            "~/.claude-homes/accountN/.claude/.credentials.json). "
            "When set, CLAUDE_CODE_OAUTH_TOKEN is passed into the container."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=list(VALID_MODES),
        default="baseline",
        help=(
            "Tool-access mode: 'baseline' (no MCP), 'mcp_only' "
            "(Sourcegraph MCP only), or 'hybrid' (local + MCP). "
            "Default: baseline"
        ),
    )
    parser.add_argument(
        "--max-concurrent-large",
        type=int,
        default=3,
        help=(
            "Maximum number of large tasks to run concurrently (default: 3). "
            "Accepted for future use; not yet enforced by this script."
        ),
    )
    parser.add_argument(
        "--rep",
        type=int,
        default=None,
        help=(
            "Repetition index (1-based). When set, output directory includes "
            "rep<N>/ suffix to prevent repeated runs from overwriting results."
        ),
    )
    parser.add_argument(
        "--ablation-variant",
        default=None,
        help=(
            "Ablation variant name (e.g. excluded repo name). When set, "
            "Docker image tag includes '-ablate-<variant>' to prevent "
            "build cache returning non-ablated images."
        ),
    )
    parser.add_argument(
        "--min-disk-gb",
        type=float,
        default=10.0,
        help=(
            "Minimum available disk space in GB before starting (default: 10). "
            "Increase when running multiple tasks concurrently."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = TaskRunConfig(
        task_toml=args.task_toml.resolve(),
        source=args.source,
        agent_command=args.agent_command,
        timeout=args.timeout,
        build_timeout=args.build_timeout,
        verifier_timeout=args.verifier_timeout,
        memory_mb=args.memory_mb,
        output_dir=args.output_dir.resolve() if args.output_dir else None,
        dry_run=args.dry_run,
        no_build=args.no_build,
        keep_container=args.keep_container,
        verbose=args.verbose,
        account=args.account,
        mode=args.mode,
        rep=args.rep,
        ablation_variant=args.ablation_variant,
        min_disk_gb=args.min_disk_gb,
    )

    result = run_task(config)

    # Print summary
    print()
    print("=" * 60)
    print(f"Task:      {result.task_id}")
    print(f"Mode:      {config.mode}")
    print(f"Phase:     {result.phase}")
    print(f"Success:   {result.success}")
    if result.error:
        print(f"Error:     {result.error}")
    if result.image_tag:
        print(f"Image:     {result.image_tag}")
    if result.output_dir:
        print(f"Output:    {result.output_dir}")
    if result.timing:
        print(f"Timing:    { {k: f'{v:.1f}s' for k, v in result.timing.items()} }")
    if result.scores:
        score = result.scores.get("task_score", "N/A")
        passed = result.scores.get("checkpoints_passed", "?")
        total = result.scores.get("checkpoints_total", "?")
        print(f"Score:     {score} ({passed}/{total} checkpoints)")
    print("=" * 60)

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
