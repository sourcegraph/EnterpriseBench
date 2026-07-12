"""Tests for filesystem-level enforcement of the mcp_only tool-access arm.

The invariant under test: mcp_only must vary exactly ONE thing versus baseline —
whether the agent can read repository source from local disk. Toolset, turn
budget, and verifier behaviour must stay identical across arms, so that the
measured baseline-vs-mcp_only delta is attributable to the retrieval channel and
not to a capability the gated arm quietly lost.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)

import run_task
from mode_gate import (
    AGENT_USER,
    VERIFIER_ASSETS,
    VERIFIER_USER,
    IneligibleTask,
    check_eligibility,
    lockdown_commands,
    repo_dirs,
    should_gate,
    verifier_lockdown_commands,
)


def _task(repos=(("ansible", "ansible"),), required=("answer",), optional=()):
    return {
        "repos": [
            {"url": f"https://x/{n}", "rev": "main", "path": p} for n, p in repos
        ],
        "artifacts": {"required": list(required), "optional": list(optional)},
    }


# --- which arms get gated -------------------------------------------------


def test_only_mcp_only_is_gated():
    assert should_gate("mcp_only") is True
    # hybrid is MCP *plus* local by design; baseline is local-only.
    assert should_gate("hybrid") is False
    assert should_gate("baseline") is False


# --- repo directory derivation --------------------------------------------


def test_repo_dirs_derives_workspace_paths():
    task = _task(repos=(("ansible", "ansible"), ("galaxy", "galaxy-ng")))
    assert repo_dirs(task) == ["/workspace/ansible", "/workspace/galaxy-ng"]


def test_repo_dirs_empty_when_no_repos():
    assert repo_dirs({"repos": []}) == []


def test_repo_dirs_rejects_path_escape():
    """A task.toml path must not be able to point the chmod outside /workspace.

    The rule is `validation.validate_repo_entry` — the same one `_parse_task`
    already applied — not a second, looser copy living in mode_gate.
    """
    escaping = {"url": "https://x/etc", "rev": "main", "path": "../../etc"}
    with pytest.raises(ValueError, match="Invalid repo path"):
        repo_dirs({"repos": [escaping]})


# --- the lockdown itself ---------------------------------------------------


def test_lockdown_denies_agent_and_keeps_root():
    """Repo trees become root-owned and mode 700: unreadable to the agent UID.

    Both halves matter. The Dockerfile clones AFTER `USER agent`, so the agent
    owns the repo files to begin with — and an owner can always chmod its own
    files back. Ownership must move to root for the mode bits to stick.
    """
    assert lockdown_commands(["/workspace/ansible"]) == [
        ["chown", "-R", "root:root", "/workspace/ansible"],
        ["chmod", "-R", "go-rwx", "/workspace/ansible"],
    ]


def test_lockdown_batches_every_repo_into_one_command_each():
    """chown/chmod take many operands: repo count must not drive the exec count."""
    cmds = lockdown_commands(["/workspace/ansible", "/workspace/galaxy-ng"])
    assert cmds == [
        ["chown", "-R", "root:root", "/workspace/ansible", "/workspace/galaxy-ng"],
        ["chmod", "-R", "go-rwx", "/workspace/ansible", "/workspace/galaxy-ng"],
    ]


def test_lockdown_commands_need_no_shell():
    """argv, not shell strings — a repo path can never be reparsed as syntax."""
    for cmd in lockdown_commands(["/workspace/ansible"]) + verifier_lockdown_commands():
        assert isinstance(cmd, list)
        assert "bash" not in cmd and "-c" not in cmd


def test_lockdown_never_touches_workspace_root_or_agent_output():
    """The agent still needs /workspace traversable and agent_output writable.

    Locking down /workspace itself would break artifact delivery and the
    instruction file — i.e. it would zero the arm rather than gate it.
    """
    for cmd in lockdown_commands(["/workspace/ansible"]):
        for arg in cmd:
            assert arg != "/workspace"
            assert "agent_output" not in arg
            assert "instruction.md" not in arg


def test_lockdown_is_noop_without_repos():
    assert lockdown_commands([]) == []


# --- users: the whole design rests on agent != verifier --------------------


def test_agent_and_verifier_run_as_different_users():
    """If these collapse, the gate also blinds the verifier (the option-A bug).

    dockerfile_generator emits `USER agent`, so the verifier must be explicitly
    re-exec'd as root or it inherits the very UID we just denied.
    """
    assert AGENT_USER != VERIFIER_USER
    assert VERIFIER_USER == "root"


# --- arm eligibility -------------------------------------------------------


def test_code_patch_task_is_ineligible_for_mcp_only():
    """You cannot patch source you cannot read — that is a structural
    impossibility, and must fail loudly rather than silently score ~0."""
    task = _task(required=("code_patch",))
    with pytest.raises(IneligibleTask, match="code_patch"):
        check_eligibility(task, "mcp_only")


def test_optional_code_patch_is_still_eligible():
    """required=[answer], optional=[code_patch] is the common shape: the agent
    can deliver the answer without ever touching the repo tree."""
    task = _task(required=("answer",), optional=("code_patch",))
    check_eligibility(task, "mcp_only")  # must not raise


def test_code_patch_is_eligible_in_ungated_arms():
    task = _task(required=("code_patch",))
    check_eligibility(task, "baseline")  # must not raise
    check_eligibility(task, "hybrid")  # must not raise


def test_ineligible_task_records_an_invalid_run(tmp_path):
    """The ineligible exit must produce a saved INVALID record, not a crash.

    It returns before any Docker call, so it also runs before `output_dir` used
    to exist — referencing it there raised UnboundLocalError and the run was
    filed as a generic 'error' with the reason discarded.
    """
    toml = tmp_path / "task.toml"
    toml.write_text(
        '[task]\nid = "t-1"\n\n[artifacts]\nrequired = ["code_patch"]\n',
        encoding="utf-8",
    )
    out = tmp_path / "out"

    result = run_task.run_task(
        run_task.TaskRunConfig(task_toml=toml, mode="mcp_only", output_dir=out)
    )

    assert result.status == run_task.RUN_STATUS_INVALID
    assert result.phase == "ineligible_for_mode"
    assert result.failure_class == "task_ineligible"
    assert "code_patch" in result.error
    assert (out / "results.json").exists(), "INVALID run was never recorded"


# --- wiring into run_task: the gate must be enforced, not merely attempted ---


class _FakeExec:
    """Records docker exec calls and replays scripted return codes."""

    def __init__(self, agent_can_read: bool):
        self.agent_can_read = agent_can_read
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, container_id, cmd, timeout=120, workdir="/workspace", user=None):
        self.calls.append((cmd, user))
        rc = 0
        out = ""
        if cmd[0] == "find":
            out = "/workspace/ansible/setup.py"
        elif cmd[:2] == ["test", "-r"]:
            # This is the leak probe, run as the agent user.
            rc = 0 if self.agent_can_read else 1
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")


def test_gate_aborts_the_run_when_the_agent_can_still_read(monkeypatch):
    """The whole point: if the ablation did not hold, refuse to score the run.

    A gate that fails open would reproduce the original bug — an 'mcp_only' run
    that read local files the entire time and was scored as if it hadn't.
    """
    fake = _FakeExec(agent_can_read=True)
    monkeypatch.setattr(run_task, "_docker_exec", fake)

    ok, err = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "LEAKED" in err


def test_gate_passes_when_agent_is_denied(monkeypatch):
    fake = _FakeExec(agent_can_read=False)
    monkeypatch.setattr(run_task, "_docker_exec", fake)

    ok, err = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is True and err == ""
    chowned = [c for c, _ in fake.calls if c[:3] == ["chown", "-R", "root:root"]]
    assert chowned, "repo tree was never taken away from the agent UID"
    assert "/workspace/ansible" in chowned[0]


def test_gate_that_cannot_be_proven_is_treated_as_failed(monkeypatch):
    """An empty repo dir yields no file to probe, so the denial is untested.

    Treat unprovable as failed: the alternative is concluding the ablation held
    because we never actually looked, which is how the original bug survived.
    """

    def no_files(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_task, "_docker_exec", no_files)

    ok, err = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "unprovable" in err


def test_gate_apply_failure_invalidates_the_run(monkeypatch):
    """A chown that fails must not leave the agent reading source unnoticed."""

    def failing_chown(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        return SimpleNamespace(
            returncode=1, stdout="", stderr="chown: permission denied"
        )

    monkeypatch.setattr(run_task, "_docker_exec", failing_chown)

    ok, err = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "failed to apply" in err


def test_gate_timeout_invalidates_the_run(monkeypatch):
    """chown -R over a Kubernetes-sized tree can outlast the exec timeout.

    That must surface as a mode-gate failure, not as a generic error from the
    outer handler — the largest repos are the likeliest to hit it.
    """

    def slow(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        raise subprocess.TimeoutExpired(cmd="chown", timeout=timeout)

    monkeypatch.setattr(run_task, "_docker_exec", slow)

    ok, err = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "timed out" in err


def test_unsafe_repo_path_invalidates_the_run(monkeypatch):
    """A traversing task.toml path must abort the run, not chmod outside /workspace."""
    fake = _FakeExec(agent_can_read=False)
    monkeypatch.setattr(run_task, "_docker_exec", fake)
    escaping = {"url": "https://x/etc", "rev": "main", "path": "../../etc"}

    ok, err = run_task._apply_mode_gate("cid", {"repos": [escaping]}, "mcp_only")

    assert ok is False
    assert "Invalid repo path" in err
    assert fake.calls == [], "aborted before touching anything, not mid-chmod"


def test_gate_is_a_noop_for_ungated_arms(monkeypatch):
    """baseline/hybrid must not pay any part of the gate — not even a chmod."""
    fake = _FakeExec(agent_can_read=True)
    monkeypatch.setattr(run_task, "_docker_exec", fake)

    for mode in ("baseline", "hybrid"):
        ok, err = run_task._apply_mode_gate("cid", _task(), mode)
        assert ok is True and err == ""
    assert fake.calls == []


# --- the verifier runs as root, so its assets must not be agent-writable -----


def test_verifier_assets_are_taken_from_the_agent():
    """test_runner.sh executes every .verifiers/*.sh it globs, as root.

    An agent-writable check script is therefore root code execution, and .task
    holds the oracle — an agent that can merely READ it need not solve the task
    at all, which is why the mode is `go-rwx` (strips read, not just write).
    Both must be root-owned in every arm, not just the gated one.
    """
    cmds = verifier_lockdown_commands()
    assert ["chown", "-R", "root:root", *VERIFIER_ASSETS] in cmds
    assert ["chmod", "-R", "go-rwx", *VERIFIER_ASSETS] in cmds
    for asset in ("/workspace/.verifiers", "/workspace/.task", "/workspace/test.sh"):
        assert asset in VERIFIER_ASSETS


def test_workspace_gets_the_sticky_bit():
    """/workspace stays agent-writable (tasks write artifacts there), so without
    the sticky bit the agent could unlink and replace root-owned test.sh purely
    by virtue of directory write permission — the file's own mode is no defence.
    """
    assert ["chmod", "+t", "/workspace"] in verifier_lockdown_commands()


def test_setup_does_not_hand_verifier_assets_back_to_the_agent(tmp_path):
    """Regression: two setup paths used to chown .verifiers/.task to agent.

    They did so because the verifier once ran as agent. Re-granting either would
    silently reopen the escalation, so run the real _setup_container and assert
    that no exec it issues hands a verifier asset to the agent UID.
    """
    (tmp_path / "instruction.md").write_text("do the thing\n")
    calls: list[list[str]] = []

    def recording_exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(run_task, "_docker_exec", recording_exec)
    monkeypatch.setattr(run_task, "_docker_cp", lambda *a, **k: None)
    try:
        run_task._setup_container("cid", tmp_path, {})
    finally:
        monkeypatch.undo()

    granting = [c for c in calls if "agent:agent" in " ".join(c)]
    assert granting, "setup no longer chowns anything to the agent — check the fixture"
    for cmd in granting:
        joined = " ".join(cmd)
        for asset in (".verifiers", ".task", ".eb_verify", "test.sh"):
            assert asset not in joined, f"setup still chowns {asset} to agent"


def test_verifier_runs_as_root_in_every_arm(monkeypatch):
    """If the verifier inherits `USER agent` it cannot read the tree it scores.

    Root is applied uniformly so verification is identical across arms — an
    arm-conditional verifier would itself be a confound.
    """
    captured: dict = {}

    def fake_exec(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        captured["user"] = user
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(run_task, "_docker_exec", fake_exec)
    run_task._run_scoring("cid")

    assert captured["user"] == VERIFIER_USER == "root"
