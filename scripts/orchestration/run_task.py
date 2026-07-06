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
from pathlib import Path

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


# Status marker for runs that must not be scored (e.g. MCP pre-flight
# failure): the run is routed to the infra-error re-run channel.
RUN_STATUS_INVALID = "invalid"


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
    user_flag = ["-u", user] if user else []
    full_cmd = ["docker", "exec", "-w", workdir, *user_flag, container_id] + cmd
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
) -> str | None:
    """Build the full instruction text with optional MCP preamble and output appendix.

    For mcp_only/hybrid modes, prepends the Sourcegraph MCP preamble (from
    agents.harnesses.claude.mcp.sourcegraph) and any task-specific
    instruction_mcp.md. For baseline mode, uses instruction.md as-is.

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
        '  "related_issues": ["/workspace/<repo>/path/to/related/file.go", "description of related component"]\n'
        "}\n```\n"
        "Include only the fields relevant to this task. "
        "Your answer is evaluated against a closed-world oracle — completeness matters.\n"
    )

    if preamble_parts:
        preamble = "\n\n".join(preamble_parts)
        return preamble + "\n\n---\n\n" + instruction_text + output_appendix
    return instruction_text + output_appendix


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


def _grading_file_manifest(
    task_dir: Path, task_data: dict
) -> tuple[dict[str, tuple[float, int]], set[str]]:
    """Canonical checkpoint metadata and the full set of legitimate checkpoint names.

    checkpoint_meta maps .verifiers/<name> -> (weight, timeout) from the task
    toml, as before. canonical_names is the union of those toml-declared
    checkpoints and the check scripts actually present under task_dir/checks
    — either source can drift from the other, and both are host-authored
    (never agent-writable), so the union is the trust boundary
    _run_scoring() cross-validates returned checkpoint names against
    (EnterpriseBench-7jpm AC3).
    """
    checkpoint_meta = _verifier_meta_by_name(task_data.get("checkpoints", []))
    checks_dir = task_dir / "checks"
    canonical_names = set(checkpoint_meta)
    if checks_dir.is_dir():
        canonical_names.update(
            _checkpoint_verifier_name(p) for p in checks_dir.glob("*.sh")
        )
    return checkpoint_meta, canonical_names


def _copy_grading_files(
    container_id: str,
    task_dir: Path,
    checkpoint_meta: dict[str, tuple[float, int]],
) -> list[str]:
    """(Re-)copy every file scoring depends on fresh from the host.

    Copies test.sh, each checkpoint verifier + its .meta, the eb_verify
    library, and ground_truth.json. Deletes the destination paths first —
    docker cp only overwrites the files it names, so without the delete an
    agent-injected extra file (e.g. .verifiers/evil.sh + evil.meta) would
    survive a "refresh" untouched and keep running via test_runner.sh's
    unfiltered ``for verifier in .verifiers/*.sh`` loop. Called once at
    initial setup and again immediately before scoring so nothing an agent
    wrote during its session survives into the grading pass
    (EnterpriseBench-7jpm). ground_truth.json is included alongside the
    other grading files: verifiers read it directly, so rewriting it lets a
    canonically-named, otherwise-legitimate verifier report a false pass —
    the same bypass class as rewriting the verifier itself.

    Returns the in-container paths written, for checksum verification. The
    eb_verify library's own files are re-copied (so tampering there is
    discarded like everything else) but not included in the returned list —
    it's a whole vendored package tree, not a handful of small files, so it
    isn't individually checksummed; the unconditional recopy is its only
    integrity guarantee.
    """
    _docker_exec(
        container_id,
        [
            "bash",
            "-c",
            "rm -rf /workspace/.verifiers /workspace/test.sh /workspace/.eb_verify "
            "/workspace/.task/ground_truth.json",
        ],
        user="root",
    )
    _docker_exec(container_id, ["mkdir", "-p", "/workspace/.verifiers"])

    written: list[str] = []
    checks_dir = task_dir / "checks"
    if checks_dir.is_dir():
        check_scripts = sorted(checks_dir.glob("*.sh"))
        for check_script in check_scripts:
            # Rename to just <name>.sh for test_runner.sh compatibility
            name = _checkpoint_verifier_name(check_script)
            sh_path = f"/workspace/.verifiers/{name}.sh"
            _docker_cp(str(check_script), f"{container_id}:{sh_path}")
            written.append(sh_path)
            meta = checkpoint_meta.get(name)
            if meta is not None:
                weight, timeout = meta
                meta_path = f"/workspace/.verifiers/{name}.meta"
                _write_content_to_container(
                    container_id,
                    f"weight={weight}\ntimeout={timeout}\n",
                    meta_path,
                    suffix=".meta",
                )
                written.append(meta_path)
        if check_scripts:
            _docker_exec(
                container_id, ["bash", "-c", "chmod +x /workspace/.verifiers/*.sh"]
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
        written.append("/workspace/test.sh")
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
        gt_path = "/workspace/.task/ground_truth.json"
        _docker_cp(str(gt_file), f"{container_id}:{gt_path}")
        written.append(gt_path)
        logger.info("Copied ground_truth.json into container")

    # Grading files are root-owned and non-writable by agent — never
    # agent:agent. The agent has no legitimate reason to read or write
    # test.sh/.verifiers/.eb_verify/ground_truth.json during its own
    # session; granting it write access here was the direct cause of the
    # original bug (EnterpriseBench-7jpm). The container's default user
    # (docker exec with no -u) is `agent`, so root-owned + no group/other
    # write is enough: read+execute bits from the host source files are
    # preserved (test.sh/checks still run fine as agent at scoring time),
    # only write access is removed.
    _docker_exec(
        container_id,
        [
            "bash",
            "-c",
            "chown -R root:root "
            "/workspace/.verifiers "
            "/workspace/.task "
            "/workspace/.eb_verify "
            "/workspace/test.sh "
            "2>/dev/null; "
            "chmod -R go-w "
            "/workspace/.verifiers "
            "/workspace/.task "
            "/workspace/.eb_verify "
            "/workspace/test.sh "
            "2>/dev/null; true",
        ],
        user="root",
        timeout=60,
    )
    return written


def _container_file_checksum(container_id: str, path: str) -> str:
    """sha256 of a single in-container file; "" if missing.

    Runs as root so an agent that changed ownership or permissions on a
    grading file can't hide it from the check by making it unreadable to
    the default exec user (EnterpriseBench-7jpm AC2). Raises if the docker
    exec itself fails (container gone, daemon error) instead of treating
    that the same as "file missing" — cut's exit status, not sha256sum's,
    determines this pipeline's own exit code, so a bad `docker exec`
    invocation is the only case where the OUTER returncode is nonzero; an
    infra fault must never be recorded as tamper evidence.
    """
    result = _docker_exec(
        container_id,
        ["bash", "-c", f"sha256sum {shlex.quote(path)} 2>/dev/null | cut -d' ' -f1"],
        user="root",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker exec failed while checksumming {path}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _kill_agent_processes(container_id: str) -> None:
    """Terminate anything still running as the agent user.

    A session can background a process (``cmd & disown``) that outlives its
    own docker-exec invocation — orphans reparent inside the container and
    keep running. Without this, such a process could keep rewriting
    canonically-named grading files throughout the entire scoring window,
    defeating the checksum-then-recopy defense by racing it instead of
    editing a file once (EnterpriseBench-7jpm). Run before every refresh,
    not just once, since a new one could be spawned between checks.
    pkill exits 1 when nothing matches — the common case, not an error.
    """
    _docker_exec(container_id, ["pkill", "-9", "-u", "agent"], user="root")


def _container_file_checksums(container_id: str, paths: list[str]) -> dict[str, str]:
    """sha256 of each in-container path.

    One docker exec per path — the grading-file set is small (a handful of
    checkpoints per task), so per-path simplicity outweighs batching into a
    single positionally-parsed script.
    """
    return {path: _container_file_checksum(container_id, path) for path in paths}


def _diff_checksums(
    container_id: str, expected_checksums: dict[str, str], when: str
) -> list[dict[str, str]]:
    """Compare current in-container checksums against a prior baseline.

    Shared by the pre-scoring refresh and the post-scoring check — both need
    the exact same "what changed since I last looked" comparison, just at
    different points in the container's lifecycle (EnterpriseBench-7jpm).
    `when` is folded into the tamper-detected log line (e.g. "since setup",
    "during the scoring window") so both call sites share one log-and-record
    step instead of each logging it separately.

    Returns a list of {"path", "expected", "found"} entries for every path
    whose current checksum no longer matches the baseline; empty if clean.
    """
    paths = list(expected_checksums)
    found_checksums = _container_file_checksums(container_id, paths)
    # found_checksums covers exactly `paths` (== expected_checksums.keys()),
    # so a plain index is safe — no default needed.
    tampered = [
        {"path": path, "expected": expected, "found": found_checksums[path]}
        for path, expected in expected_checksums.items()
        if found_checksums[path] != expected
    ]
    if tampered:
        logger.error(
            "GRADING FILE TAMPER DETECTED (%s): %d file(s): %s",
            when,
            len(tampered),
            [t["path"] for t in tampered],
        )
    return tampered


def _check_post_scoring_tamper(
    container_id: str, grading_checksums: dict[str, str]
) -> list[dict[str, str]]:
    """_diff_checksums for the post-scoring window, tolerating infra faults.

    _container_file_checksum (called transitively) deliberately raises
    RuntimeError on a genuine docker-exec failure so infra faults never get
    misrecorded as tamper evidence. That's the right contract for the
    pre-scoring refresh, which runs before any scores exist. But by the time
    this post-scoring check runs, _run_scoring() has already produced a
    valid scores dict — letting the exception propagate up would discard an
    otherwise-successful, fully-scored run over a transient failure in this
    detect-only, defense-in-depth check (EnterpriseBench-7jpm). Fail open on
    the check itself instead: log it and report no tamper found.
    """
    try:
        return _diff_checksums(
            container_id, grading_checksums, when="during the scoring window"
        )
    except RuntimeError as exc:
        logger.error(
            "Post-scoring integrity check itself failed (infra error, not "
            "treated as tamper): %s",
            exc,
        )
        return []


def _refresh_and_verify_grading_files(
    container_id: str,
    task_dir: Path,
    checkpoint_meta: dict[str, tuple[float, int]],
    expected_checksums: dict[str, str],
) -> list[dict[str, str]]:
    """Kill lingering agent processes, detect tampering, then discard it.

    Kills anything still running as the agent user FIRST — otherwise a
    backgrounded process could keep rewriting a grading file after this
    function's own checksum read, through the recopy below, and into
    test_runner.sh's execution window. Then checksums grading files exactly
    as the agent's session left them, diffs against the checksums
    _setup_container recorded before the agent ever ran, then
    unconditionally re-copies pristine, non-agent-writable files from the
    host — scoring must never trust in-container grading state, tampered or
    not (EnterpriseBench-7jpm AC1+AC2).

    checkpoint_meta is the same manifest _setup_container computed and used
    for the initial copy — passed in rather than recomputed here so the two
    copies of the same task can't drift apart.

    Returns a list of {"path", "expected", "found"} entries for every
    grading file that was missing or modified since setup; empty if clean.
    """
    _kill_agent_processes(container_id)
    tampered = _diff_checksums(container_id, expected_checksums, when="since setup")

    # Unconditional: discard whatever the agent left, tamper or not.
    _copy_grading_files(container_id, task_dir, checkpoint_meta)
    return tampered


def _lock_workspace_root(container_id: str) -> None:
    """Chown /workspace itself to root:agent + set the sticky bit.

    /workspace stays agent:agent-owned for the container's whole life
    (dockerfile_generator.py, image-build time), so unlink/rename of a
    directory ENTRY — gated by permissions on the PARENT, not the entry's
    own owner/mode — let the agent swap out a whole locked grading dir
    (``mv .verifiers .verifiers.bak && mkdir .verifiers``) or test.sh
    directly, routing around the per-file root:root+go-w lockdown entirely.
    Reproduced locally and independently confirmed by two reviewers in
    EnterpriseBench-7jpm's second review round.

    root:agent + sticky bit closes that vector while preserving the
    legitimate case: agent (group member) can still create new entries
    under /workspace, but only an entry's owner (or root) may unlink/rename
    it — so the agent keeps write access to its own entries (agent_output/,
    cloned repos, output artifacts) but can no longer swap out the
    root-owned .verifiers/.task/.eb_verify/test.sh. Non-recursive, so this
    is cheap regardless of repo size. Applied as the last step of
    _setup_container, after every setup write under /workspace has landed.

    Raises if the chown/chmod itself fails: this is the sole *preventive*
    control in EnterpriseBench-7jpm's fix (everything else is detect-only),
    so a silently-ignored failure here would leave /workspace unlocked with
    no signal at all — fail closed instead, matching the raise-on-exec-
    failure discipline _container_file_checksum already uses.
    """
    result = _docker_exec(
        container_id,
        ["bash", "-c", "chown root:agent /workspace && chmod 1775 /workspace"],
        user="root",
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to lock /workspace: {result.stderr.strip()}")


def _setup_container(
    container_id: str,
    task_dir: Path,
    task_data: dict,
    mode: str = "baseline",
) -> tuple[dict[str, str], set[str], dict[str, tuple[float, int]]]:
    """Copy task files into the running container.

    - instruction.md -> /workspace/instruction.md
    - checks/*.sh -> /workspace/.verifiers/
    - test_runner.sh -> /workspace/test.sh
    - eb_verify library -> /workspace/.eb_verify/ (if needed by check scripts)
    - ground_truth.json -> /workspace/.task/ground_truth.json

    Returns (grading_checksums, canonical_checkpoint_names, checkpoint_meta):
    a sha256 of every grading file computed immediately after copying, while
    the container is still pre-agent-session and therefore trusted — the
    anchor _refresh_and_verify_grading_files() later checks the
    post-agent-session state against (EnterpriseBench-7jpm). checkpoint_meta
    is returned so the refresh pass can reuse it instead of recomputing it.
    """
    # Copy instruction.md with output format appendix and optional MCP preamble
    combined = _build_instruction_text(task_dir, mode, repos=task_data.get("repos", []))
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

    checkpoint_meta, canonical_names = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files(container_id, task_dir, checkpoint_meta)
    grading_checksums = _container_file_checksums(container_id, grading_paths)

    # Fix ownership of the remaining copied files — docker cp preserves host
    # UID which may not match the agent user inside the container. Never
    # chown -R /workspace (too slow for large repos like K8s, Terraform).
    # Grading files (.verifiers, .eb_verify, test.sh, .task) are chowned
    # inside _copy_grading_files, since that's also where they get refreshed
    # pre-scoring.
    _docker_exec(
        container_id,
        [
            "bash",
            "-c",
            "chown -R agent:agent "
            "/workspace/instruction.md "
            "/workspace/agent_output "
            "/workspace/.mcp.json "
            "2>/dev/null; true",
        ],
        user="root",
        timeout=60,
    )

    # Last step, after every setup write above has landed and before the
    # agent's session starts (EnterpriseBench-7jpm).
    _lock_workspace_root(container_id)

    return grading_checksums, canonical_names, checkpoint_meta


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

    # Ensure non-root agent user exists and owns key output dirs.
    # Images built with the updated dockerfile_generator already have the agent
    # user owning /workspace (USER agent before git clone), so cloned repos are
    # correctly owned. For older images we still create the user and fix up
    # output dirs only (never chown -R on /workspace — too slow for large repos).
    # Deliberately excludes .task/.verifiers: _setup_container (which always
    # runs before this) already created and root-owned+locked them via
    # _copy_grading_files. Re-chowning them to agent:agent here would reopen
    # the write access that lockdown exists to remove (EnterpriseBench-7jpm).
    _docker_exec(
        container_id,
        [
            "bash",
            "-c",
            "id agent >/dev/null 2>&1 || useradd -m -s /bin/bash agent; "
            "mkdir -p /workspace/agent_output; "
            "chown -R agent:agent /home/agent /workspace/agent_output",
        ],
    )

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


def _run_scoring(
    container_id: str,
    verifier_timeout: int = 600,
    canonical_checkpoint_names: set[str] | None = None,
) -> dict:
    """Run /workspace/test.sh and capture the JSON results.

    If canonical_checkpoint_names is given, any returned checkpoint whose
    name isn't in that set is excluded from the recomputed task_score /
    checkpoints_passed / checkpoints_total / all_passed and reported under
    scores["rejected_checkpoints"] — a forged, renamed, or extra checkpoint
    an agent injected via .verifiers/*.sh + *.meta (EnterpriseBench-7jpm
    AC3). Rejected entries are removed from scores["checkpoints"] itself,
    not just flagged, so downstream consumers — including
    _apply_llm_judge()'s own task_score recompute for llm_curator tasks —
    can't re-inflate the score from them.
    """
    logger.info("Running checkpoint verifiers (timeout=%ds)...", verifier_timeout)

    result = _docker_exec(
        container_id,
        [
            "bash",
            "-c",
            "export WORKSPACE=/workspace TASK_DIR=/workspace/.task "
            "PYTHONPATH=/workspace/.eb_verify:${PYTHONPATH:-}; "
            "bash /workspace/test.sh",
        ],
        timeout=verifier_timeout,
    )

    # test.sh outputs JSON to stdout, diagnostics to stderr
    if result.stderr:
        logger.info("Verifier diagnostics:\n%s", result.stderr.rstrip())

    # Parse the JSON output
    stdout = result.stdout.strip()
    if not stdout:
        return {
            "task_score": 0.0,
            "all_passed": False,
            "error": f"test.sh produced no output (exit {result.returncode})",
        }

    try:
        scores = json.loads(stdout)
    except json.JSONDecodeError as e:
        return {
            "task_score": 0.0,
            "all_passed": False,
            "error": f"test.sh output was not valid JSON: {e}",
            "raw_output": stdout[:2000],
        }

    if canonical_checkpoint_names is not None:
        checkpoints = scores.get("checkpoints", [])
        accepted, rejected = [], []
        for c in checkpoints:
            (accepted if c.get("name") in canonical_checkpoint_names else rejected).append(c)
        if rejected:
            logger.error(
                "REJECTED %d non-canonical checkpoint(s) from scoring: %s",
                len(rejected),
                [c.get("name") for c in rejected],
            )
            scores["rejected_checkpoints"] = rejected
            scores["checkpoints"] = accepted
            scores["task_score"] = sum(
                c.get("score", 0.0) * c.get("weight", 1.0) for c in accepted
            )
            scores["checkpoints_passed"] = sum(1 for c in accepted if c.get("passed"))
            scores["checkpoints_total"] = len(accepted)
            scores["all_passed"] = (
                scores["checkpoints_total"] > 0
                and scores["checkpoints_passed"] == scores["checkpoints_total"]
            )

    return scores


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
        logger.warning("Failed to load expected_solution.json: %s", exc)
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
        # setdefault, not assignment: a grading-integrity violation detected
        # earlier in the pipeline (EnterpriseBench-7jpm) already recorded its
        # own reason here, and that's the more actionable one to keep — an
        # agent tampering with grading files explains a missing artifact
        # better than "no_agent_output" alone would.
        scores.setdefault("verifier_infra_error", {
            "reason": "no_agent_output",
            "stage": "llm_judge",
            "detail": (
                "llm_curator task declares expected_solution but no agent "
                "artifact was found in the container; Tier-2 cap could not be "
                "applied, so the deterministic grep scores are un-capped and "
                "must not be recorded as the final measurement"
            ),
            "candidates": candidates,
        })
        return scores

    try:
        sys.path.insert(0, str(REPO_ROOT / "lib"))
        from eb_verify.judge import CheckpointJudgeInput, LLMJudge

        judge = LLMJudge(model="cc:haiku")
    except Exception as exc:
        logger.warning("Failed to init LLM judge: %s", exc)
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
            logger.warning("LLM judge failed for %s: %s", cp_name, exc)
            continue

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

    # --- results.json (backward compatible) ---
    results_path = output_dir / "results.json"
    payload = {
        "task_id": result.task_id,
        "success": result.success,
        "phase": result.phase,
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

    # Write config to both project-level and user-level paths. Same format
    # works for both; writing to two locations ensures Claude Code finds auth
    # headers regardless of which config path it resolves first.
    _write_content_to_container(
        container_id, mcp_config_json, "/workspace/.mcp.json", suffix=".mcp.json"
    )
    _docker_exec(container_id, ["chown", "agent:agent", "/workspace/.mcp.json"])

    _docker_exec(
        container_id,
        [
            "bash",
            "-c",
            "mkdir -p /home/agent/.claude && chown agent:agent /home/agent/.claude",
        ],
    )
    _write_content_to_container(
        container_id,
        mcp_config_json,
        "/home/agent/.claude/.mcp.json",
        suffix=".mcp.json",
    )
    _docker_exec(
        container_id, ["chown", "agent:agent", "/home/agent/.claude/.mcp.json"]
    )

    # Also write to /home/agent/.mcp.json for --mcp-config flag
    _write_content_to_container(
        container_id, mcp_config_json, "/home/agent/.mcp.json", suffix=".mcp.json"
    )
    _docker_exec(container_id, ["chown", "agent:agent", "/home/agent/.mcp.json"])
    logger.info(
        "MCP config written to /workspace/.mcp.json, "
        "/home/agent/.claude/.mcp.json, and /home/agent/.mcp.json"
    )

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

    # Count MCP tool calls by scanning for mcp__sourcegraph in the raw log
    usage["mcp_tool_calls"] = content.count("mcp__sourcegraph__")

    return usage


def _copy_agent_trace(container_id: str, output_dir: Path) -> bool:
    """Copy the Claude Code conversation trace from the container.

    Claude Code stores session JSONL files under
    /home/agent/.claude/projects/<hash>/.  This function finds the most
    recent conversation JSONL and copies it to output_dir/agent_trace.jsonl.

    Returns True if a trace was successfully copied, False otherwise.
    Never raises — failures are logged and the run continues.
    """
    try:
        # Find JSONL conversation files, sorted newest-first
        find_result = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "bash",
                "-c",
                "find /home/agent/.claude/projects -name '*.jsonl' -type f "
                "2>/dev/null | head -20",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if find_result.returncode != 0 or not find_result.stdout.strip():
            logger.info("No agent conversation trace found in container")
            return False

        # Take the first (or newest) file
        trace_files = [
            f.strip() for f in find_result.stdout.strip().splitlines() if f.strip()
        ]
        if not trace_files:
            logger.info("No agent conversation trace found in container")
            return False

        trace_path = trace_files[0]
        dest = str(output_dir / "agent_trace.jsonl")

        cp_result = subprocess.run(
            ["docker", "cp", f"{container_id}:{trace_path}", dest],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if cp_result.returncode != 0:
            logger.warning("Failed to copy agent trace: %s", cp_result.stderr.strip())
            return False

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
            grading_checksums, canonical_checkpoint_names, checkpoint_meta = (
                _setup_container(container_id, task_dir, task_data, mode=config.mode)
            )
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

            # Flag hybrid runs where MCP wasn't used — these don't count
            # as valid MCP comparison data
            mcp_calls = result.tool_usage.get("mcp_tool_calls", 0)
            if config.mode in ("mcp_only", "hybrid") and mcp_calls == 0:
                logger.warning(
                    "MCP mode=%s but agent made 0 MCP tool calls — "
                    "run is not a valid MCP comparison",
                    config.mode,
                )
                result.tool_usage["mcp_used"] = False
            elif config.mode in ("mcp_only", "hybrid"):
                result.tool_usage["mcp_used"] = True
                logger.info("Agent made %d MCP tool calls", mcp_calls)

            # Copy full conversation trace from container
            _copy_agent_trace(container_id, output_dir)
        else:
            logger.info("No agent command specified, skipping agent phase")

        # --- Phase 5: Score (Tier 1 — deterministic) ---
        t0 = time.monotonic()
        # Discard anything the agent wrote to test.sh/.verifiers/.eb_verify/
        # ground_truth.json during its session and re-check integrity before
        # trusting any of it (EnterpriseBench-7jpm AC1+AC2). This must run
        # immediately before _run_scoring(), after the agent phase ends.
        tampered_files = _refresh_and_verify_grading_files(
            container_id, task_dir, checkpoint_meta, grading_checksums
        )
        scores = _run_scoring(
            container_id, config.verifier_timeout, canonical_checkpoint_names
        )
        timings["scoring"] = time.monotonic() - t0

        # _lock_workspace_root (the rename/swap vector) and the per-file
        # root:root+go-w lockdown (direct content writes) together close every
        # grading-file tamper path this diff identified. This check exists as
        # a backstop against an unproven escalation path this diff did not
        # verify — e.g. a setuid binary in the base image letting the agent
        # regain root mid-scoring — not a named, confirmed residual gap
        # (EnterpriseBench-7jpm).
        post_scoring_tamper = _check_post_scoring_tamper(container_id, grading_checksums)

        if tampered_files:
            scores["grading_tamper"] = tampered_files
        if post_scoring_tamper:
            scores["post_scoring_tamper"] = post_scoring_tamper

        tamper_keys = ("grading_tamper", "post_scoring_tamper", "rejected_checkpoints")
        if any(scores.get(key) for key in tamper_keys):
            # Distinct from verifier_infra_error: this is agent malfeasance,
            # not a system fault, so it gets its own failure_class for
            # run_task.py's own routing below...
            result.failure_class = "grading_integrity_violation"
            result.phase = "grading_integrity_violation"
            # ...but existing result-aggregation consumers (aggregate_baseline_aq8e.py,
            # rescore_baseline_aq8e.py) only key off scores["verifier_infra_error"],
            # so also set that marker to keep the run excluded from their
            # scored-results totals without touching five more scripts.
            scores.setdefault(
                "verifier_infra_error",
                {
                    "reason": "grading_integrity",
                    "stage": "scoring",
                    "detail": (
                        "grading files were tampered with and/or the scoring "
                        "JSON contained non-canonical checkpoint names during "
                        "this run; task_score reflects only canonical, "
                        "post-refresh checkpoints and this run must not be "
                        "counted as a normal scored result"
                    ),
                },
            )
            logger.warning(
                "Grading integrity violation: tampered=%s "
                "post_scoring_tampered=%s rejected_checkpoints=%s",
                [t["path"] for t in scores.get("grading_tamper", [])],
                [t["path"] for t in scores.get("post_scoring_tamper", [])],
                [c.get("name") for c in scores.get("rejected_checkpoints", [])],
            )

        # --- Phase 5b: Score (Tier 2 — LLM curator) ---
        verification_modes = task_data.get("verification_modes", ["deterministic"])
        if "llm_curator" in verification_modes:
            scores = _apply_llm_judge(scores, task_dir, container_id, task_data)
            infra_err = scores.get("verifier_infra_error")
            if infra_err and result.phase != "grading_integrity_violation":
                result.failure_class = "verifier_infra_error"
                result.phase = "verifier_infra_error"
                logger.warning(
                    "Verifier infra error (%s): %s",
                    infra_err["reason"],
                    infra_err["detail"],
                )

        result.scores = scores

        # --- Save ---
        if result.phase not in ("agent_infra_error", "verifier_infra_error", "grading_integrity_violation"):
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
