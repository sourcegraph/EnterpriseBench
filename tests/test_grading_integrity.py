"""Regression tests for the grading-integrity fix (EnterpriseBench-7jpm).

Prior to this fix, an agent-under-test could forge its own score during its
session: /workspace/test.sh, .verifiers/*.sh, .verifiers/*.meta, and
.task/ground_truth.json were all chowned to the agent before its session
started, and _run_scoring() trusted whatever test.sh printed with no
re-copy, no checksum, and no cross-validation against the task's canonical
checkpoint set.

These tests exercise the three defenses without spinning up real Docker: a
_FakeContainer stands in for the container's filesystem, and module-level
_docker_cp/_docker_exec are monkeypatched to operate on it.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import run_task
from run_task import (
    TEST_RUNNER_SH,
    _apply_llm_judge,
    _check_post_scoring_tamper,
    _container_file_checksum,
    _container_file_checksums,
    _copy_grading_files,
    _diff_checksums,
    _grading_file_manifest,
    _install_claude_cli,
    _kill_agent_processes,
    _lock_workspace_root,
    _refresh_and_verify_grading_files,
    _run_scoring,
    _setup_container,
)


# ---------------------------------------------------------------------------
# Fake container
# ---------------------------------------------------------------------------

class _FakeContainer:
    """In-memory stand-in for one container's filesystem.

    Understands exactly the docker operations run_task.py issues against
    grading files: docker cp (write), mkdir/chmod (no-ops), rm -rf (delete),
    sha256sum (checksum), `bash /workspace/test.sh` (canned scoring output),
    and `cat <path>` (read, used by _apply_llm_judge's agent-output probe).
    """

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.test_sh_stdout: str = '{"task_score": 0.0, "all_passed": false, "checkpoints": []}'
        self.exec_calls: list[tuple[list[str], str | None]] = []
        self.force_exec_failure: bool = False

    def write(self, src: str, dest_path: str) -> None:
        p = Path(src)
        if p.is_dir():
            return  # directory copies (eb_verify lib) aren't content-tracked
        self.files[dest_path] = p.read_text()

    def exec_(self, cmd: list[str], user: str | None = None) -> subprocess.CompletedProcess:
        self.exec_calls.append((cmd, user))
        if self.force_exec_failure:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="docker daemon error")
        if cmd[:1] in (["mkdir"], ["chmod"]):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:1] == ["pkill"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")  # no match, the common case
        if cmd and cmd[0] == "cat" and len(cmd) > 1:
            content = self.files.get(cmd[1])
            if content is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
            return subprocess.CompletedProcess(cmd, 0, stdout=content, stderr="")
        if cmd[:2] == ["bash", "-c"]:
            script = cmd[2]
            if script.startswith("rm -rf "):
                for path in script[len("rm -rf ") :].split():
                    self.files.pop(path, None)
                    prefix = f"{path}/"
                    for key in [k for k in self.files if k.startswith(prefix)]:
                        del self.files[key]
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            m = re.search(r"sha256sum\s+(\S+)", script)
            if m:
                content = self.files.get(m.group(1))
                stdout = (
                    hashlib.sha256(content.encode()).hexdigest()
                    if content is not None
                    else ""
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            if "bash /workspace/test.sh" in script:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=self.test_sh_stdout, stderr=""
                )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def fake_container(monkeypatch: pytest.MonkeyPatch) -> _FakeContainer:
    fc = _FakeContainer()

    def fake_docker_cp(src: str, dest: str) -> None:
        _, path = dest.split(":", 1)
        fc.write(src, path)

    def fake_docker_exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        return fc.exec_(cmd, user)

    monkeypatch.setattr(run_task, "_docker_cp", fake_docker_cp)
    monkeypatch.setattr(run_task, "_docker_exec", fake_docker_exec)
    return fc


# ---------------------------------------------------------------------------
# Task fixtures
# ---------------------------------------------------------------------------

CHECK_SCRIPT = "#!/usr/bin/env bash\necho '{\"score\": 1.0, \"passed\": true}'\n"


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "check_api_migration.sh").write_text(CHECK_SCRIPT)
    (checks / "check_evidence.sh").write_text(CHECK_SCRIPT)
    return tmp_path


@pytest.fixture
def task_data() -> dict:
    return {
        "checkpoints": [
            {"verifier": "checks/check_api_migration.sh", "weight": 0.6, "timeout_seconds": 60},
            {"verifier": "checks/check_evidence.sh", "weight": 0.4},
        ]
    }


# ---------------------------------------------------------------------------
# _grading_file_manifest
# ---------------------------------------------------------------------------

def test_grading_file_manifest_unions_toml_and_checks_dir(task_dir, task_data):
    """Canonical set includes toml-declared checkpoints even if a checks/*.sh
    is missing a toml entry, and vice versa — either source alone can drift."""
    (task_dir / "checks" / "check_orphan_script.sh").write_text(CHECK_SCRIPT)

    checkpoint_meta, canonical_names = _grading_file_manifest(task_dir, task_data)

    assert set(checkpoint_meta) == {"api_migration", "evidence"}
    assert canonical_names == {"api_migration", "evidence", "orphan_script"}


# ---------------------------------------------------------------------------
# AC1 — re-copy discards agent tampering (also closes the "rm before copy"
# gap: docker cp alone doesn't delete files it doesn't name)
# ---------------------------------------------------------------------------

def test_copy_grading_files_overwrites_tampered_test_sh(fake_container, task_dir, task_data):
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    _copy_grading_files("cid", task_dir, checkpoint_meta)

    fake_container.files["/workspace/test.sh"] = "#!/usr/bin/env bash\necho PWNED\n"

    _copy_grading_files("cid", task_dir, checkpoint_meta)

    assert fake_container.files["/workspace/test.sh"] == TEST_RUNNER_SH.read_text()


def test_copy_grading_files_deletes_injected_extra_verifier(fake_container, task_dir, task_data):
    """An agent-injected .verifiers/evil.sh + evil.meta must be GONE after a
    refresh, not merely left in place because docker cp never names it."""
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    _copy_grading_files("cid", task_dir, checkpoint_meta)

    fake_container.files["/workspace/.verifiers/evil.sh"] = "echo '{\"score\":1.0,\"passed\":true}'"
    fake_container.files["/workspace/.verifiers/evil.meta"] = "weight=999\ntimeout=120\n"

    _copy_grading_files("cid", task_dir, checkpoint_meta)

    assert "/workspace/.verifiers/evil.sh" not in fake_container.files
    assert "/workspace/.verifiers/evil.meta" not in fake_container.files
    # legitimate checkpoints survive the refresh
    assert "/workspace/.verifiers/api_migration.sh" in fake_container.files


def test_copy_grading_files_discards_rewritten_ground_truth(fake_container, task_dir, task_data):
    (task_dir / "ground_truth.json").write_text('{"real": "answer"}')
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    _copy_grading_files("cid", task_dir, checkpoint_meta)

    fake_container.files["/workspace/.task/ground_truth.json"] = '{"real": "whatever agent wants"}'

    _copy_grading_files("cid", task_dir, checkpoint_meta)

    assert fake_container.files["/workspace/.task/ground_truth.json"] == '{"real": "answer"}'


# ---------------------------------------------------------------------------
# AC1 + AC2 — checksum mismatch is flagged AND the file is restored
# ---------------------------------------------------------------------------

def test_refresh_flags_tampered_verifier_and_restores_it(fake_container, task_dir, task_data):
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files("cid", task_dir, checkpoint_meta)
    expected_checksums = _container_file_checksums("cid", grading_paths)

    verifier_path = "/workspace/.verifiers/api_migration.sh"
    original_content = fake_container.files[verifier_path]
    fake_container.files[verifier_path] = "#!/usr/bin/env bash\necho '{\"score\":1.0,\"passed\":true}'  # hacked\n"

    tampered = _refresh_and_verify_grading_files("cid", task_dir, checkpoint_meta, expected_checksums)

    tampered_paths = [t["path"] for t in tampered]
    assert verifier_path in tampered_paths
    # re-copy happened regardless of the tamper finding — file is pristine again
    assert fake_container.files[verifier_path] == original_content


def test_refresh_reports_no_tamper_when_clean(fake_container, task_dir, task_data):
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files("cid", task_dir, checkpoint_meta)
    expected_checksums = _container_file_checksums("cid", grading_paths)

    tampered = _refresh_and_verify_grading_files("cid", task_dir, checkpoint_meta, expected_checksums)

    assert tampered == []


# ---------------------------------------------------------------------------
# AC3 — checkpoint-name cross-validation rejects forged/extra entries and
# the task_score isn't inflated by a high-weight forged entry
# ---------------------------------------------------------------------------

def test_run_scoring_rejects_noncanonical_checkpoint(fake_container, task_dir, task_data):
    _, canonical_names = _grading_file_manifest(task_dir, task_data)
    fake_container.test_sh_stdout = (
        '{"task_score": 1000.0, "all_passed": true, "checkpoints": ['
        '{"name": "api_migration", "weight": 0.6, "score": 1.0, "passed": true},'
        '{"name": "evidence", "weight": 0.4, "score": 0.5, "passed": false},'
        '{"name": "evil_checkpoint", "weight": 999.0, "score": 1.0, "passed": true}'
        ']}'
    )

    scores = _run_scoring("cid", 60, canonical_names)

    rejected_names = [c["name"] for c in scores["rejected_checkpoints"]]
    assert rejected_names == ["evil_checkpoint"]
    accepted_names = [c["name"] for c in scores["checkpoints"]]
    assert accepted_names == ["api_migration", "evidence"]
    # score reflects only the two canonical checkpoints, not the 999-weight forgery
    assert scores["task_score"] == pytest.approx(0.6 * 1.0 + 0.4 * 0.5)
    assert scores["checkpoints_passed"] == 1
    assert scores["checkpoints_total"] == 2
    assert scores["all_passed"] is False


def test_run_scoring_accepts_all_canonical_checkpoints(fake_container, task_dir, task_data):
    _, canonical_names = _grading_file_manifest(task_dir, task_data)
    fake_container.test_sh_stdout = (
        '{"task_score": 1.0, "all_passed": true, "checkpoints": ['
        '{"name": "api_migration", "weight": 0.6, "score": 1.0, "passed": true},'
        '{"name": "evidence", "weight": 0.4, "score": 1.0, "passed": true}'
        ']}'
    )

    scores = _run_scoring("cid", 60, canonical_names)

    assert "rejected_checkpoints" not in scores
    assert [c["name"] for c in scores["checkpoints"]] == ["api_migration", "evidence"]


# ---------------------------------------------------------------------------
# C2 — a rejected checkpoint stays excluded through the llm_curator recompute
# ---------------------------------------------------------------------------

class _FakeJudgeResult:
    def __init__(self, score: float) -> None:
        self.score = score
        self.reasoning = "fake judge result for testing"


class _FakeLLMJudge:
    def __init__(self, model: str | None = None) -> None:
        pass

    def evaluate_checkpoint(self, input_, task_description=None, checkpoint_description=None):
        return _FakeJudgeResult(score=1.0)


def test_apply_llm_judge_does_not_reinflate_rejected_checkpoints(
    monkeypatch, fake_container, task_dir, task_data
):
    """scores["checkpoints"] arrives already filtered by _run_scoring's AC3
    step; _apply_llm_judge's own task_score recompute must not resurrect a
    rejected checkpoint since it only iterates scores["checkpoints"]."""
    import eb_verify.judge as judge_module

    monkeypatch.setattr(judge_module, "LLMJudge", _FakeLLMJudge)

    (task_dir / "expected_solution.json").write_text(
        '{"task_id": "t1", "checkpoints": {'
        '"api_migration": {"expected_solution": "x", "evaluation_criteria": []}}}'
    )
    fake_container.files["/workspace/agent_output/answer.json"] = '{"source_files": []}'

    # As _run_scoring would have already produced after rejecting evil_checkpoint:
    scores = {
        "task_score": 0.6,
        "checkpoints": [
            {"name": "api_migration", "weight": 0.6, "score": 1.0, "passed": True},
        ],
        "rejected_checkpoints": [
            {"name": "evil_checkpoint", "weight": 999.0, "score": 1.0, "passed": True},
        ],
    }

    result = _apply_llm_judge(scores, task_dir, "cid", task_data)

    assert len(result["checkpoints"]) == 1
    assert result["task_score"] == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Write-access lockdown — grading files must never be agent-writable, and any
# process the agent left running must be killed before the refresh, not just
# detected after the fact (closes the "backgrounded writer races the
# checksum" gap: chown-to-agent + no process reaping meant a `cmd & disown`
# from inside the agent's own session could keep rewriting canonically-named
# grading files for the entire scoring window).
# ---------------------------------------------------------------------------

def test_copy_grading_files_never_chowns_to_agent(fake_container, task_dir, task_data):
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    _copy_grading_files("cid", task_dir, checkpoint_meta)

    chown_scripts = [
        cmd[2]
        for cmd, _user in fake_container.exec_calls
        if cmd[:2] == ["bash", "-c"] and "chown" in cmd[2]
    ]
    assert chown_scripts, "expected a chown call"
    assert all("agent:agent" not in script for script in chown_scripts)
    assert all("root:root" in script for script in chown_scripts)


def test_install_claude_cli_does_not_reopen_grading_paths(fake_container, task_dir, task_data):
    """_install_claude_cli always runs after _setup_container in the real
    pipeline; its agent-user provisioning step must not chown .task/.verifiers
    back to agent:agent, or it would silently undo the lockdown
    _copy_grading_files just applied (EnterpriseBench-7jpm)."""
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    _copy_grading_files("cid", task_dir, checkpoint_meta)
    fake_container.exec_calls.clear()

    _install_claude_cli("cid")

    agent_chown_scripts = [
        cmd[2]
        for cmd, _user in fake_container.exec_calls
        if cmd[:2] == ["bash", "-c"] and "chown" in cmd[2] and "agent:agent" in cmd[2]
    ]
    assert agent_chown_scripts, "expected an agent:agent chown call"
    assert all("/workspace/.task" not in script for script in agent_chown_scripts)
    assert all("/workspace/.verifiers" not in script for script in agent_chown_scripts)


def test_refresh_kills_agent_processes_as_root(fake_container, task_dir, task_data):
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files("cid", task_dir, checkpoint_meta)
    expected_checksums = _container_file_checksums("cid", grading_paths)
    fake_container.exec_calls.clear()

    _refresh_and_verify_grading_files("cid", task_dir, checkpoint_meta, expected_checksums)

    pkill_calls = [
        (cmd, user) for cmd, user in fake_container.exec_calls if cmd[:1] == ["pkill"]
    ]
    assert pkill_calls, "expected _kill_agent_processes to run pkill before the refresh"
    cmd, user = pkill_calls[0]
    assert cmd == ["pkill", "-9", "-u", "agent"]
    assert user == "root"


def test_kill_agent_processes_tolerates_no_matching_process(fake_container):
    # pkill exits 1 when nothing matches (the common case) — must not raise.
    _kill_agent_processes("cid")


# ---------------------------------------------------------------------------
# Infra failure vs. tamper: a broken docker exec must not be recorded as if
# it were agent malfeasance.
# ---------------------------------------------------------------------------

def test_container_file_checksum_raises_on_docker_exec_failure(fake_container):
    fake_container.force_exec_failure = True

    with pytest.raises(RuntimeError):
        _container_file_checksum("cid", "/workspace/test.sh")


def test_container_file_checksum_returns_empty_for_missing_file(fake_container):
    assert _container_file_checksum("cid", "/workspace/does_not_exist.sh") == ""


# ---------------------------------------------------------------------------
# Workspace-root lockdown — see _lock_workspace_root's docstring for the
# directory-swap bypass this closes.
# ---------------------------------------------------------------------------

def test_lock_workspace_root_chowns_to_root_agent_and_sets_sticky_bit(fake_container):
    _lock_workspace_root("cid")

    lock_scripts = [
        (cmd[2], user)
        for cmd, user in fake_container.exec_calls
        if cmd[:2] == ["bash", "-c"] and "chown root:agent /workspace" in cmd[2]
    ]
    assert lock_scripts, "expected a chown root:agent /workspace call"
    script, user = lock_scripts[0]
    assert "chmod 1775 /workspace" in script
    assert user == "root"


def test_lock_workspace_root_raises_on_exec_failure(fake_container):
    """The lock is the sole preventive control in this fix — a silently
    swallowed failure would leave /workspace unlocked with no signal at all,
    so a failed chown/chmod must raise, not no-op."""
    fake_container.force_exec_failure = True

    with pytest.raises(RuntimeError):
        _lock_workspace_root("cid")


def test_setup_container_locks_workspace_root_last(fake_container, task_dir, task_data):
    """The lock must be the LAST thing _setup_container does — every write
    directly under /workspace (grading files, instruction.md, agent_output,
    .mcp.json) must land before /workspace's own ownership is changed, since
    the agent has no path to root and therefore can't undo the lock once
    it's applied (no sudo grant in dockerfile_generator.py)."""
    _setup_container("cid", task_dir, task_data)

    bash_scripts = [
        cmd[2] for cmd, _user in fake_container.exec_calls if cmd[:2] == ["bash", "-c"]
    ]
    lock_indices = [i for i, s in enumerate(bash_scripts) if "chown root:agent /workspace" in s]
    assert lock_indices, "expected the workspace lock to run"
    assert lock_indices[0] == len(bash_scripts) - 1, (
        "workspace lock must be the last bash -c exec in _setup_container"
    )


def test_directory_swap_around_locked_verifiers_is_detected_by_checksum(
    fake_container, task_dir, task_data
):
    """Models the directory-swap bypass (rename .verifiers out of the way,
    recreate it with a forged, agent-owned checkpoint) that _lock_workspace_root
    closes; the checksum diff must catch it regardless of mechanism."""
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files("cid", task_dir, checkpoint_meta)
    expected_checksums = _container_file_checksums("cid", grading_paths)

    swapped = {
        path.replace("/workspace/.verifiers", "/workspace/.verifiers.bak"): content
        for path, content in fake_container.files.items()
        if path.startswith("/workspace/.verifiers")
    }
    for path in list(fake_container.files):
        if path.startswith("/workspace/.verifiers"):
            del fake_container.files[path]
    fake_container.files.update(swapped)
    fake_container.files["/workspace/.verifiers/api_migration.sh"] = (
        "echo '{\"score\":1.0,\"passed\":true}'  # forged after swap\n"
    )

    tampered = _diff_checksums("cid", expected_checksums, when="since setup")

    tampered_paths = [t["path"] for t in tampered]
    assert "/workspace/.verifiers/api_migration.sh" in tampered_paths


# ---------------------------------------------------------------------------
# Post-scoring checksum diff — defense-in-depth for the test.sh execution
# window itself, see _diff_checksums'/_run_scoring's call site docstrings.
# ---------------------------------------------------------------------------

def test_diff_checksums_detects_tamper_during_scoring_window(
    fake_container, task_dir, task_data
):
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files("cid", task_dir, checkpoint_meta)
    grading_checksums = _container_file_checksums("cid", grading_paths)

    # This is the exact state _refresh_and_verify_grading_files leaves the
    # container in right before _run_scoring() execs test.sh. Simulate a
    # writer that fires *during* that window, after the refresh's own
    # checksum read completed.
    fake_container.files["/workspace/test.sh"] = "#!/usr/bin/env bash\necho PWNED\n"

    post_scoring_tamper = _diff_checksums(
        "cid", grading_checksums, when="during the scoring window"
    )

    assert [t["path"] for t in post_scoring_tamper] == ["/workspace/test.sh"]


def test_diff_checksums_clean_when_nothing_changed(fake_container, task_dir, task_data):
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files("cid", task_dir, checkpoint_meta)
    grading_checksums = _container_file_checksums("cid", grading_paths)

    assert _diff_checksums("cid", grading_checksums, when="since setup") == []


def test_check_post_scoring_tamper_tolerates_infra_failure(fake_container, task_dir, task_data):
    """A genuine docker-exec failure in this detect-only check must not
    propagate and destroy an already-computed, valid scores dict — by the
    time this check runs, _run_scoring() has already succeeded."""
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files("cid", task_dir, checkpoint_meta)
    grading_checksums = _container_file_checksums("cid", grading_paths)
    fake_container.force_exec_failure = True

    assert _check_post_scoring_tamper("cid", grading_checksums) == []


def test_check_post_scoring_tamper_detects_real_tamper(fake_container, task_dir, task_data):
    checkpoint_meta, _ = _grading_file_manifest(task_dir, task_data)
    grading_paths = _copy_grading_files("cid", task_dir, checkpoint_meta)
    grading_checksums = _container_file_checksums("cid", grading_paths)
    fake_container.files["/workspace/test.sh"] = "#!/usr/bin/env bash\necho PWNED\n"

    tampered = _check_post_scoring_tamper("cid", grading_checksums)

    assert [t["path"] for t in tampered] == ["/workspace/test.sh"]
