"""Tests for filesystem-level enforcement of the mcp_only tool-access arm.

The invariant under test: mcp_only must vary exactly ONE thing versus baseline --
whether the agent can read repository source from local disk. Toolset, turn
budget, and scorer behaviour must stay identical across arms, so that the
measured baseline-vs-mcp_only delta is attributable to the retrieval channel and
not to a capability the gated arm quietly lost.

Three identities share the container and the gate must land on exactly one of
them: ``agent`` loses local source, ``ebscorer`` keeps it (it still has to score
the tree), and ``root`` is never denied. Both halves are asserted here, because a
gate that blinds the scorer fails just as badly as one that fails to blind the
agent -- it simply fails less visibly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from dataclasses import replace

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)

import run_task
from mode_gate import (
    GATED_MODES,
    IneligibleTask,
    check_eligibility,
    lockdown_commands,
    repo_dirs,
    should_gate,
)

AGENT_USER = run_task.AGENT_USER
SCORER = run_task.SCORING_USER
WORKSPACE = run_task.WORKSPACE_DIR


def _task(repos=(("ansible", "ansible"),), required=("answer",), optional=()):
    return {
        "repos": [
            {"url": f"https://x/{n}", "rev": "main", "path": p} for n, p in repos
        ],
        "artifacts": {"required": list(required), "optional": list(optional)},
    }


# --- which arms get gated -------------------------------------------------


# Every mode run_task accepts needs a deliberate answer here. Iterating
# VALID_MODES rather than listing arms by hand is load-bearing: a mode added to
# run_task without a decision recorded in this table would otherwise default to
# ungated and pass silently, which is exactly how mode became prompt-only in the
# first place (EnterpriseBench-7rc1). A new mode fails this test until someone
# says which side of the gate it belongs on.
EXPECTED_GATING = {
    "baseline": False,  # local-only; the control arm.
    "mcp_only": True,  # the ablation: local source denied at the filesystem.
    "mcp_default": True,  # the server as shipped; still MCP-only for code.
    "mcp_treatment": True,  # mcp_default with the unshipped server change applied.
    "mcp_code_finder": True,  # Code Finder is the sole remote retrieval path.
    "mcp_assisted": True,  # Finder bootstrap plus targeted remote follow-up.
    "mcp_toast": True,  # Toast-1, offered as a tool, is the sole path to code.
    "hybrid": False,  # MCP *plus* local by design.
    "cli": False,  # baseline + sgx; the manipulation is the tool interface,
    # not readability, so local source stays present.
}


def test_every_valid_mode_has_a_recorded_gate_decision():
    assert set(run_task.VALID_MODES) == set(EXPECTED_GATING), (
        "run_task.VALID_MODES and EXPECTED_GATING disagree. A new mode must "
        "declare whether it is gated; it must not inherit ungated by default."
    )


def test_no_mode_is_gated_without_being_runnable():
    """A gated mode run_task does not accept would gate nothing, silently.

    The set-equality above only catches modes VALID_MODES knows about, so a
    name added to GATED_MODES alone (typo, rename, or a mode retired from
    VALID_MODES) would slip through it.
    """
    assert set(GATED_MODES) <= set(run_task.VALID_MODES), (
        "GATED_MODES names a mode run_task cannot run: "
        f"{sorted(set(GATED_MODES) - set(run_task.VALID_MODES))}"
    )


@pytest.mark.parametrize("mode", sorted(EXPECTED_GATING))
def test_mode_is_gated_as_recorded(mode):
    assert should_gate(mode) is EXPECTED_GATING[mode]


# --- repo directory derivation --------------------------------------------


def test_repo_dirs_derives_workspace_paths():
    task = _task(repos=(("ansible", "ansible"), ("galaxy", "galaxy-ng")))
    assert repo_dirs(task, WORKSPACE) == ["/workspace/ansible", "/workspace/galaxy-ng"]


def test_repo_dirs_empty_when_no_repos():
    assert repo_dirs({"repos": []}, WORKSPACE) == []


def test_repo_dirs_rejects_path_escape():
    escaping = {"url": "https://x/etc", "rev": "main", "path": "../../etc"}
    try:
        repo_dirs({"repos": [escaping]}, WORKSPACE)
    except ValueError as exc:
        assert "Invalid repo path" in str(exc)
    else:
        raise AssertionError("a traversing path must not become a chmod target")


# --- what the lockdown actually does --------------------------------------


def test_lockdown_denies_agent_but_keeps_the_scorer():
    """The load-bearing asymmetry: agent loses read, scorer keeps it.

    ``chown root:ebscorer`` moves ownership away from the agent (which otherwise
    OWNS the tree, since the Dockerfile clones after ``USER agent``, and an owner
    can always chmod its own files back) while handing the scoring group the read
    access its checkpoints need. ``o-rwx`` then strips the world bits the agent
    would otherwise still read through.
    """
    cmds = lockdown_commands(["/workspace/ansible"], SCORER)

    assert cmds == [
        ["chown", "-R", f"root:{SCORER}", "/workspace/ansible"],
        ["chmod", "-R", "o-rwx,g-w", "/workspace/ansible"],
    ]


def test_lockdown_keeps_group_read_so_the_scorer_is_not_blinded():
    """``go-rwx`` would blind the scorer along with the agent.

    The scorer reads through the GROUP bits. Clearing them would relocate the
    blindness rather than remove it, and every checkpoint would score a tree it
    cannot see.
    """
    mode = [c[2] for c in lockdown_commands(["/workspace/x"], SCORER) if c[0] == "chmod"][0]

    assert "g-r" not in mode, "stripping group read blinds the scorer"
    assert "go-rwx" not in mode


def test_lockdown_strips_group_write_to_keep_scorer_access_identical_across_arms():
    """The gate must change the AGENT's access and nothing else.

    In an ungated arm the tree is agent-owned and the scorer reads it as "other":
    read-only. Under a container umask of 002 the clone is 0664/0775, so chown +
    o-rwx alone would leave the scoring GROUP at rw- — handing the scorer write
    access to the tree it grades, which it never had in baseline. That is a
    second thing varying between arms, which is the exact failure this gate
    exists to avoid.
    """
    mode = [c[2] for c in lockdown_commands(["/workspace/x"], SCORER) if c[0] == "chmod"][0]

    assert "g-w" in mode, "scorer would gain repo write it does not have in baseline"


def test_lockdown_batches_every_repo_into_one_command_each():
    cmds = lockdown_commands(["/workspace/a", "/workspace/b", "/workspace/c"], SCORER)

    assert len(cmds) == 2, "one chown and one chmod, however many repos"
    for cmd in cmds:
        assert cmd[-3:] == ["/workspace/a", "/workspace/b", "/workspace/c"]


def test_lockdown_commands_need_no_shell():
    """argv lists, never shell strings: a repo path can never become syntax."""
    for cmd in lockdown_commands(["/workspace/a b; rm -rf /"], SCORER):
        assert isinstance(cmd, list)
        assert all(isinstance(arg, str) for arg in cmd)
        assert cmd[0] in ("chown", "chmod")


def test_lockdown_adds_no_target_beyond_the_dirs_it_is_handed():
    """Locking /workspace itself would zero the arm instead of gating it.

    The agent still has to traverse /workspace to reach instruction.md and to
    write agent_output/.

    Scope: this proves only that lockdown_commands invents no target of its own.
    That the workspace root can never REACH ``dirs`` in the first place is
    repo_dirs'/validate_repo_entry's guarantee, asserted by
    test_repo_dirs_rejects_path_escape, not here.
    """
    for cmd in lockdown_commands(["/workspace/ansible"], SCORER):
        assert "/workspace" not in cmd
        assert "/workspace/agent_output" not in cmd


def test_lockdown_is_noop_without_repos():
    assert lockdown_commands([], SCORER) == []


# --- eligibility ----------------------------------------------------------


def test_code_patch_task_is_ineligible_for_mcp_only():
    """You cannot patch what you cannot read; refuse rather than score a fake 0."""
    try:
        check_eligibility(_task(required=("code_patch",)), "mcp_only")
    except IneligibleTask as exc:
        assert "code_patch" in str(exc)
    else:
        raise AssertionError("a code_patch task cannot run in an unreadable tree")


def test_optional_code_patch_is_still_eligible():
    """required=[answer], optional=[code_patch] is deliverable without the tree."""
    check_eligibility(_task(required=("answer",), optional=("code_patch",)), "mcp_only")


def test_code_patch_is_eligible_in_ungated_arms():
    task = _task(required=("code_patch",))
    check_eligibility(task, "baseline")  # must not raise
    check_eligibility(task, "hybrid")  # must not raise



def test_deliverable_inside_a_repo_tree_is_ineligible_for_gated_arms():
    """The lockdown denies write as surely as read: the agent cannot create a
    file in a directory it cannot enter. Scoring such a run 0.0 would record a
    harness failure as a measured agent failure."""
    task = _task()
    try:
        check_eligibility(
            task, "mcp_only", graded_artifact_path="/workspace/ansible/REPORT.md"
        )
    except IneligibleTask as exc:
        assert "/workspace/ansible" in str(exc)
    else:
        raise AssertionError("an in-repo deliverable is unwritable under the gate")


def test_deliverable_outside_the_repo_trees_is_eligible():
    check_eligibility(
        _task(), "mcp_only", graded_artifact_path="/workspace/REPORT.md"
    )
    # A repo path is a prefix of the deliverable's directory name but not its
    # parent -- string containment is not the test, path containment is.
    check_eligibility(
        _task(), "mcp_only", graded_artifact_path="/workspace/ansible-out/REPORT.md"
    )


def test_in_repo_deliverable_is_fine_in_ungated_arms():
    check_eligibility(
        _task(), "baseline", graded_artifact_path="/workspace/ansible/REPORT.md"
    )


def test_ineligible_task_records_an_invalid_run(tmp_path):
    """The ineligible exit must produce a saved INVALID record, not a crash."""
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
    """Records docker exec calls and replays scripted readability per user."""

    def __init__(self, agent_can_read: bool, scorer_can_read: bool = True):
        self.agent_can_read = agent_can_read
        self.scorer_can_read = scorer_can_read
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(
        self, container_id, cmd, timeout=120, workdir="/workspace", user=None
    ):
        self.calls.append((cmd, user))
        rc, out = 0, ""
        if cmd[0] == "find":
            out = "/workspace/ansible/setup.py"
        elif cmd[:2] == ["test", "-r"]:
            allowed = self.agent_can_read if user == AGENT_USER else self.scorer_can_read
            rc = 0 if allowed else 1
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")


def test_gate_aborts_the_run_when_the_agent_can_still_read(monkeypatch):
    """The whole point: if the ablation did not hold, refuse to score the run.

    A gate that fails open reproduces the original bug — an 'mcp_only' run that
    read local files the entire time and was scored as if it hadn't.
    """
    monkeypatch.setattr(run_task, "_docker_exec", _FakeExec(agent_can_read=True))

    ok, err, _proof = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "agent can still read" in err
    assert "INVALID" in err


def test_gate_aborts_when_it_blinds_the_scorer(monkeypatch):
    """The other half. A gate that takes the tree from the SCORER is also broken.

    This is the failure that sank the 'just omit the repos from the image' design:
    checkpoints score against /workspace, so a scorer that cannot read the tree
    returns 0 on some checks and full credit on others.
    """
    monkeypatch.setattr(
        run_task,
        "_docker_exec",
        _FakeExec(agent_can_read=False, scorer_can_read=False),
    )

    ok, err, _proof = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "blinded the scorer" in err


def test_gate_passes_when_agent_is_denied_and_scorer_is_not(monkeypatch):
    fake = _FakeExec(agent_can_read=False, scorer_can_read=True)
    monkeypatch.setattr(run_task, "_docker_exec", fake)

    ok, err, _proof = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is True and err == ""
    chowned = [c for c, _ in fake.calls if c[0] == "chown"]
    assert chowned, "repo tree was never taken away from the agent UID"
    assert f"root:{SCORER}" in chowned[0]
    assert "/workspace/ansible" in chowned[0]


def test_gate_probes_both_identities(monkeypatch):
    """The proof is only a proof if it actually asks both users."""
    fake = _FakeExec(agent_can_read=False, scorer_can_read=True)
    monkeypatch.setattr(run_task, "_docker_exec", fake)

    run_task._apply_mode_gate("cid", _task(), "mcp_only")

    probed = {u for c, u in fake.calls if c[:2] == ["test", "-r"]}
    assert probed == {AGENT_USER, SCORER}


def test_gate_that_cannot_be_proven_is_treated_as_failed(monkeypatch):
    """An empty repo dir yields no file to probe, so the denial is untested.

    Treat unprovable as failed: the alternative is concluding the ablation held
    because we never actually looked, which is how the original bug survived.
    """

    def no_files(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_task, "_docker_exec", no_files)

    ok, err, _proof = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "no file" in err


def test_gate_apply_failure_invalidates_the_run(monkeypatch):
    """A chown that fails must not leave the agent reading source unnoticed."""

    def failing_chown(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        return SimpleNamespace(
            returncode=1, stdout="", stderr="chown: permission denied"
        )

    monkeypatch.setattr(run_task, "_docker_exec", failing_chown)

    ok, err, _proof = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "mode gate failed" in err


def test_gate_timeout_invalidates_the_run(monkeypatch):
    """chown -R over a Kubernetes-sized tree can outlast the exec timeout.

    That must surface as a mode-gate failure, not a generic error from the outer
    handler — the largest repos are the likeliest to hit it.
    """

    def slow(container_id, cmd, timeout=120, workdir="/workspace", user=None):
        raise subprocess.TimeoutExpired(cmd="chown", timeout=timeout)

    monkeypatch.setattr(run_task, "_docker_exec", slow)

    ok, err, _proof = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert "timed out" in err


def test_unsafe_repo_path_invalidates_the_run(monkeypatch):
    """A traversing task.toml path must abort the run, not chmod outside /workspace."""
    fake = _FakeExec(agent_can_read=False)
    monkeypatch.setattr(run_task, "_docker_exec", fake)
    escaping = {"url": "https://x/etc", "rev": "main", "path": "../../etc"}

    ok, err, _proof = run_task._apply_mode_gate(
        "cid", {"repos": [escaping]}, "mcp_only"
    )

    assert ok is False
    assert "Invalid repo path" in err
    assert fake.calls == [], "aborted before touching anything, not mid-chmod"


def test_gate_is_a_noop_for_ungated_arms(monkeypatch):
    """baseline/hybrid must not pay any part of the gate — not even a chmod.

    This is what keeps the toolset and the filesystem identical everywhere except
    the one axis under test.
    """
    fake = _FakeExec(agent_can_read=True)
    monkeypatch.setattr(run_task, "_docker_exec", fake)

    for mode in ("baseline", "hybrid"):
        ok, err, _proof = run_task._apply_mode_gate("cid", _task(), mode)
        assert ok is True and err == ""

    assert fake.calls == [], "an ungated arm must not be touched by the gate"


# --- regression: the gate must not be bypassable, and the import must be clean ---


def test_run_task_imports_standalone_on_a_clean_path():
    """run_task must import via its package path without another test's help.

    ``mode_gate`` is a sibling module; run_task's own ``sys.path.insert`` is what
    makes ``from mode_gate import ...`` resolve. A full-suite run masks a missing
    insert (tests/integrity/* inject scripts/orchestration onto sys.path first),
    so this reproduces a single-file / CI-shard import in a fresh interpreter
    where only the repo root (cwd) and lib are on the path.
    """
    repo_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-c", "import scripts.orchestration.run_task"],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root / "lib")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "No module named 'mode_gate'" not in proc.stderr


def test_gated_mode_without_agent_command_is_invalid_not_complete(
    tmp_path, monkeypatch
):
    """A gated arm reaching the no-agent path never applied the gate.

    Such an arm must refuse (INVALID), not fall through to scoring and save
    ``phase="complete"``, ``success=True`` on a container whose repos were never
    chowned -- the exact confound this bead removes, reintroduced through a
    nesting hole (the gate lived inside ``if agent_command:``).

    An empty ``--agent`` no longer reaches this path: the claude default command
    now comes from the harness registry, so the model flag is applied in one
    place. The property still needs a test, so the empty command is forced at
    the plan instead of through the flag.
    """
    toml = tmp_path / "task.toml"
    toml.write_text(
        '[task]\nid = "t-1"\n\n[artifacts]\nrequired = ["answer"]\n',
        encoding="utf-8",
    )
    # Drive the pipeline to the no-agent branch: every infra step succeeds so the
    # only thing missing is an agent command.
    for name, ret in (
        ("_check_disk_space", True),
        ("_docker_create_container", "cid"),
        ("_docker_start", None),
        ("_setup_container", None),
        ("_run_health_check", True),
        ("_configure_mcp", True),
    ):
        monkeypatch.setattr(
            run_task, name, (lambda r: (lambda *a, **k: r))(ret)
        )
    real_plan = run_task.build_harness_plan
    monkeypatch.setattr(
        run_task,
        "build_harness_plan",
        lambda *a, **k: replace(real_plan(*a, **k), command=()),
    )

    result = run_task.run_task(
        run_task.TaskRunConfig(
            task_toml=toml,
            mode="mcp_only",
            output_dir=tmp_path / "out",
            no_build=True,
            agent_command="",
        )
    )

    assert result.phase == "mode_gate_skipped"
    assert result.phase != "complete"
    assert result.status == run_task.RUN_STATUS_INVALID
    assert result.success is False


def test_gate_proof_records_both_directions_for_a_gated_run(monkeypatch):
    """A passing gate writes the probes it proved, so results.json can be audited."""
    monkeypatch.setattr(run_task, "_docker_exec", _FakeExec(agent_can_read=False))

    ok, err, proof = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert (ok, err) == (True, "")
    assert proof["gated"] is True
    assert proof["proven"] is True
    assert proof["agent_denied_probes"] == proof["scorer_readable_probes"]
    assert proof["agent_denied_probes"], "a proven gate must name the probes it used"


def test_gate_proof_marks_an_ungated_arm_as_not_gated():
    ok, err, proof = run_task._apply_mode_gate("cid", _task(), "baseline")

    assert (ok, err) == (True, "")
    assert proof["gated"] is False
    assert proof["proven"] is True
    assert proof["agent_denied_probes"] == []


def test_gate_proof_survives_a_failed_gate(monkeypatch):
    """A gate that did not hold still records what it checked, and why it failed."""
    monkeypatch.setattr(run_task, "_docker_exec", _FakeExec(agent_can_read=True))

    ok, err, proof = run_task._apply_mode_gate("cid", _task(), "mcp_only")

    assert ok is False
    assert proof["proven"] is False
    assert proof["repo_dirs"], "a failed gate still records the trees it targeted"
    assert proof["reason"] == err
    assert "INVALID" in proof["reason"]
