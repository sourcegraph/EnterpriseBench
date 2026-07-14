"""Grading-asset trust boundary — the agent under test must not own the grader.

Before this seal, setup_container and _install_claude_cli both chowned
/workspace/.verifiers, /workspace/.task (ground_truth.json) and
/workspace/.eb_verify to the agent user, and the verifiers then ran AS that
same user with cwd=/workspace. So the party being graded could overwrite a
check with a forged verdict, read the answer key, or sabotage the harness
import for a free re-run (beads EnterpriseBench-8krz5, -g5k5s).

Sealing those assets is necessary and not sufficient. Some checks run
agent-controlled code BY DESIGN — check_test_fails.sh runs the agent's own test
suite, pytest auto-loads a planted /workspace/conftest.py, git runs a
.gitattributes textconv driver — so code of the agent's choosing WILL execute
inside the scoring window. A root grader turns each of those into a rewrite of
the seal it is grading against, which is how the first version of this fix was
rejected: it sealed the assets and then handed them straight back.

So the boundary is drawn between three parties, and no two may collapse into one:

    root          owns the grading assets; the only identity that can modify them
    SCORING_USER  runs the checks; reads and executes the assets, writes none
    agent         no access at all

Everything below pins some edge of that triangle: the assets are root-owned and
agent-unreadable, scoring executes as an unprivileged third user from a cwd
nobody but root can write, /workspace is closed for the scoring window so the
grader has nowhere to plant an artifact for a later checkpoint, and a broken seal
is reported as an ``integrity_violation`` — score 0.0, never routed to the
``verifier_infra_error`` re-run channel, so tampering is never a mulligan.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestration"))

import run_task as rt  # noqa: E402
from run_task import (  # noqa: E402
    AGENT_INSTRUCTION,
    AGENT_OUTPUT_DIR,
    GRADING_PATHS,
    GROUND_TRUTH,
    SCORING_GROUP,
    SCORING_RESULTS_FILE,
    SCORING_USER,
    SCORING_WORKDIR,
    WORKSPACE_DIR,
    WORKSPACE_MODE_SESSION,
    _assert_grading_assets_sealed,
    _assert_scoring_identity,
    _chown_to_agent,
    _run_scoring,
    _seal_grading_assets,
)


def _is_parent_check(cmd: list[str]) -> bool:
    """The /workspace ownership+sticky probe (vs the grading-asset find)."""
    return len(cmd) > 2 and "maxdepth" in cmd[2]


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def _fail(stderr: str = "boom", returncode: int = 1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr=stderr
    )


def _reap_is(cmd: list) -> bool:
    """True if this exec is the pre-scoring agent-process reap."""
    return bool(cmd) and cmd[0] == "bash" and "kill -9" in cmd[-1] and "id -u agent" in cmd[-1]


def _run_scoring_capturing(stdout: str = '{"task_score": 0.5}'):
    """Drive _run_scoring past the seal gate and return the _docker_exec mock.

    Scoring now brackets the verifier exec with a reap + workspace close/reopen,
    so the exec of interest is neither first nor last — use _scoring_call() to
    find it. The reap fails closed unless it reports zero survivors, so the mock
    returns that sentinel for the reap call and the caller's stdout otherwise.
    """
    def fake_exec(container_id, cmd, **kwargs):
        if _reap_is(cmd):
            return _ok(stdout="agent_procs_remaining=0")
        return _ok(stdout=stdout)

    with patch.object(rt, "_assert_grading_assets_sealed", return_value=(True, "")):
        with patch.object(rt, "_docker_exec", side_effect=fake_exec) as exec_:
            _run_scoring("cid")
    return exec_


def _scoring_call(exec_):
    """The call that actually runs test.sh, among the workspace lock calls."""
    for call in exec_.call_args_list:
        cmd = call.args[1]
        if cmd[0] == "bash" and rt.TEST_SH in cmd[-1]:
            return call
    raise AssertionError(f"no test.sh exec found in {exec_.call_args_list}")


def _seal_script() -> str:
    """The shell the seal actually runs inside the container."""
    with patch.object(rt, "_docker_exec", return_value=_ok()) as exec_:
        _seal_grading_assets("cid")
    return exec_.call_args.args[1][2]


class TestGradingPathsConstant:
    def test_covers_every_grading_asset(self) -> None:
        assert set(GRADING_PATHS) == {
            "/workspace/.verifiers",
            "/workspace/.task",
            "/workspace/.eb_verify",
            "/workspace/test.sh",
        }

    def test_instruction_md_is_not_a_grading_asset(self) -> None:
        """instruction.md must stay agent-readable — the s58f readability gate
        depends on it, and sealing it would reintroduce fake-0 no-op runs."""
        assert "/workspace/instruction.md" not in GRADING_PATHS


class TestSetupChownSite:
    """SITE 1 — setup_container must not hand the grader to the agent."""

    def test_setup_chowns_instruction_but_no_grading_asset(self) -> None:
        captured: list[list[str]] = []

        with patch.object(rt, "_chown_to_agent") as chown:
            with patch.object(rt, "_docker_exec", return_value=_ok()), patch.object(
                rt, "_docker_cp"
            ):
                rt._setup_container("cid", Path("/nonexistent-task"), {})
            for call in chown.call_args_list:
                captured.append(call.args[1])

        assert captured, "_setup_container never called _chown_to_agent"
        chowned = [p for paths in captured for p in paths]
        assert "/workspace/instruction.md" in chowned
        for grading_path in GRADING_PATHS:
            assert grading_path not in chowned, (
                f"setup_container chowns {grading_path} to the agent — the agent "
                "under test would own its own grader"
            )


class TestInstallClaudeCliChownSite:
    """SITE 2 — _install_claude_cli re-granted .task/.verifiers unconditionally.

    A fix that patches only SITE 1 is silently defeated here.
    """

    def test_shell_command_touches_no_grading_asset(self) -> None:
        shell_cmds: list[str] = []

        def fake_exec(container_id, cmd, **kwargs):
            if cmd[:2] == ["bash", "-c"]:
                shell_cmds.append(cmd[2])
            return _ok(stdout="1.0.0")

        with patch.object(rt, "_docker_exec", side_effect=fake_exec):
            assert rt._install_claude_cli("cid") is True

        joined = "\n".join(shell_cmds)
        assert "/workspace/.task" not in joined
        assert "/workspace/.verifiers" not in joined
        assert "/workspace/agent_output" in joined, "agent output dir must stay agent-owned"
        assert "/home/agent" in joined


class TestChownGuard:
    """The invariant, enforced once, instead of per-site vigilance.

    Both known holes were a caller handing _chown_to_agent (or an inline
    `chown -R`) a grading path. The guard makes that unrepresentable, so a THIRD
    site cannot quietly reopen it.
    """

    @pytest.mark.parametrize("path", GRADING_PATHS)
    def test_refuses_a_grading_asset(self, path: str) -> None:
        with patch.object(rt, "_docker_exec") as exec_:
            with pytest.raises(ValueError, match="never own the grader"):
                _chown_to_agent("cid", [path])
        exec_.assert_not_called()

    def test_refuses_the_workspace_root(self) -> None:
        """chowning the parent is enough: POSIX unlink/rename keys off its write
        bit, so the agent could move the sealed assets aside without touching
        them."""
        with pytest.raises(ValueError, match="never own the grader"):
            _chown_to_agent("cid", [WORKSPACE_DIR])

    def test_refuses_a_path_inside_a_grading_asset(self) -> None:
        with pytest.raises(ValueError, match="never own the grader"):
            _chown_to_agent("cid", [GROUND_TRUTH])

    def test_refuses_a_grading_asset_hidden_among_legitimate_paths(self) -> None:
        with pytest.raises(ValueError, match="never own the grader"):
            _chown_to_agent("cid", [AGENT_INSTRUCTION, "/workspace/.task"])

    def test_allows_the_paths_the_agent_legitimately_owns(self) -> None:
        with patch.object(rt, "_docker_exec", return_value=_ok()) as exec_:
            _chown_to_agent("cid", [AGENT_INSTRUCTION, AGENT_OUTPUT_DIR, "/home/agent"])
        exec_.assert_called_once()


class TestSealGradingAssets:
    def test_seal_runs_as_root_and_strips_agent_access(self) -> None:
        with patch.object(rt, "_docker_exec", return_value=_ok()) as exec_:
            _seal_grading_assets("cid")

        kwargs = exec_.call_args.kwargs
        assert kwargs.get("user") == "root"
        script = exec_.call_args.args[1][2]
        # root owns; the scorer's group reads; the agent gets nothing (o=).
        assert f"chown -R root:{SCORING_GROUP}" in script
        assert "chmod -R u=rwX,g=rX,o=" in script
        for grading_path in GRADING_PATHS:
            assert grading_path in script
        assert SCORING_WORKDIR in script, "scoring cwd must be created"
        # The parent must be taken back from the agent and made sticky, or the
        # sealed entries can simply be renamed/unlinked out of it.
        assert f"chown root:root {WORKSPACE_DIR}" in script
        assert f"chmod {WORKSPACE_MODE_SESSION} {WORKSPACE_DIR}" in script

    def test_seal_creates_the_scoring_identity(self) -> None:
        """The seal is only worth the identity that runs against it: root would
        ignore it, the agent would own it. Both need a third user to exist."""
        with patch.object(rt, "_docker_exec", return_value=_ok()) as exec_:
            _seal_grading_assets("cid")

        script = exec_.call_args.args[1][2]
        assert f"useradd" in script and SCORING_USER in script

    def test_scoring_cwd_is_not_writable_by_the_scorer(self) -> None:
        """The checks shell out to `python3 -c`, whose sys.path[0] is the cwd.
        A scorer-writable cwd would let code planted by one check hijack the
        import of the next — the seal defeated from inside the scoring window."""
        with patch.object(rt, "_docker_exec", return_value=_ok()) as exec_:
            _seal_grading_assets("cid")

        script = exec_.call_args.args[1][2]
        assert f"chown root:root {SCORING_WORKDIR}" in script
        assert f"chmod 755 {SCORING_WORKDIR}" in script

    def test_seal_failure_raises_loud(self) -> None:
        """An unsealed run is unscoreable — never silently continue (s58f: a
        masked chown failure is what produced fake-0 no-op runs)."""
        with patch.object(rt, "_docker_exec", return_value=_fail("chown: denied")):
            with pytest.raises(RuntimeError, match="seal"):
                _seal_grading_assets("cid")


class TestAssertGradingAssetsSealed:
    def test_clean_seal_passes(self) -> None:
        def fake_exec(container_id, cmd, **kwargs):
            # find reports nothing; agent cannot read ground truth
            if kwargs.get("user") == "agent":
                return _fail(returncode=1)
            return _ok(stdout="")

        with patch.object(rt, "_docker_exec", side_effect=fake_exec):
            ok, err = _assert_grading_assets_sealed("cid")

        assert ok is True
        assert err == ""

    def test_agent_owned_grading_file_is_a_breach(self) -> None:
        def fake_exec(container_id, cmd, **kwargs):
            if kwargs.get("user") == "agent":
                return _fail(returncode=1)
            if _is_parent_check(cmd):
                return _ok(stdout="")
            return _ok(stdout="/workspace/.verifiers/check_1.sh\n")

        with patch.object(rt, "_docker_exec", side_effect=fake_exec):
            ok, err = _assert_grading_assets_sealed("cid")

        assert ok is False
        assert "check_1.sh" in err

    def test_agent_owned_workspace_is_a_breach(self) -> None:
        """The parent governs unlink/rename. An agent-owned (or non-sticky)
        /workspace lets the agent move the sealed assets aside wholesale, no
        write to a sealed file required."""
        def fake_exec(container_id, cmd, **kwargs):
            if kwargs.get("user") == "agent":
                return _fail(returncode=1)
            if _is_parent_check(cmd):
                return _ok(stdout="/workspace\n")
            return _ok(stdout="")

        with patch.object(rt, "_docker_exec", side_effect=fake_exec):
            ok, err = _assert_grading_assets_sealed("cid")

        assert ok is False
        assert "unlink/rename" in err

    def test_deleted_grading_asset_is_a_breach_not_a_pass(self) -> None:
        """Fail CLOSED on absence. A deleted test.sh produces empty verifier
        output, which the scorer guard reads as verifier_infra_error — the
        re-run mulligan (g5k5s). An absent grader must never read as 'sealed'."""
        def fake_exec(container_id, cmd, **kwargs):
            if kwargs.get("user") == "agent":
                return _fail(returncode=1)
            if _is_parent_check(cmd):
                return _ok(stdout="")
            return _ok(stdout="MISSING:/workspace/test.sh\n")

        with patch.object(rt, "_docker_exec", side_effect=fake_exec):
            ok, err = _assert_grading_assets_sealed("cid")

        assert ok is False
        assert "test.sh" in err

    def test_absent_path_emits_missing_marker(self) -> None:
        """The probe must report absence, not silently skip it (`[ ! -e ]`)."""
        with patch.object(rt, "_docker_exec", return_value=_ok()) as exec_:
            _assert_grading_assets_sealed("cid")

        asset_probe = [
            c.args[1][2] for c in exec_.call_args_list
            if len(c.args) > 1 and isinstance(c.args[1], list)
            and len(c.args[1]) > 2 and "MISSING" in c.args[1][2]
        ]
        assert asset_probe, "seal probe never checks for a missing grading asset"
        assert '[ ! -e "$f" ]' in asset_probe[0]

    def test_agent_readable_ground_truth_is_a_breach(self) -> None:
        """Reading the answer key is as fatal as rewriting the checker."""
        def fake_exec(container_id, cmd, **kwargs):
            if kwargs.get("user") == "agent":
                return _ok(returncode=0)  # test -r succeeded
            return _ok(stdout="")  # parent + asset probes both clean

        with patch.object(rt, "_docker_exec", side_effect=fake_exec):
            ok, err = _assert_grading_assets_sealed("cid")

        assert ok is False
        assert "ground_truth" in err


class TestRunScoringUnderTheSeal:
    def test_scoring_execs_as_the_unprivileged_scorer_never_as_root(self) -> None:
        """The whole seal turns on this line. The checks run agent-controlled
        code BY DESIGN — pytest loads a planted /workspace/conftest.py, git runs
        a .gitattributes textconv driver, check_test_fails.sh runs the agent's
        own test suite. As root, any one of those rewrites the root-owned
        verifiers for a not-yet-run checkpoint and forges the score. As an
        unprivileged third user it gains nothing: it can read the grader, and it
        cannot touch it."""
        call = _scoring_call(_run_scoring_capturing())

        assert call.kwargs.get("user") == SCORING_USER
        assert call.kwargs.get("user") != "root"
        assert call.kwargs.get("user") != "agent"
        assert call.kwargs.get("workdir") == SCORING_WORKDIR
        assert "PYTHONSAFEPATH=1" in call.args[1][2]

    def test_agent_processes_are_reaped_before_the_workspace_is_closed(self) -> None:
        """A lingering agent-owned process is a live writer during scoring: it
        can take the answer key a check leaks and rewrite the answer a later
        checkpoint grades. It must be killed BEFORE the checks run, and before
        the workspace close (order matters only in that both precede the exec)."""
        exec_ = _run_scoring_capturing()
        scripts = [
            c.args[1][-1] for c in exec_.call_args_list
            if c.args[1] and c.args[1][0] == "bash"
        ]
        reap_idx = next(
            (i for i, s in enumerate(scripts) if "kill -9" in s and "id -u agent" in s),
            None,
        )
        assert reap_idx is not None, "agent processes are never reaped before scoring"

        close_idx = next(
            (i for i, s in enumerate(scripts) if f"chmod {rt.WORKSPACE_MODE_SCORING}" in s),
            None,
        )
        test_idx = next((i for i, s in enumerate(scripts) if rt.TEST_SH in s), None)
        assert reap_idx < test_idx, "reap must precede the checkpoint verifiers"
        assert close_idx < test_idx, "workspace close must precede the verifiers"

    def test_a_surviving_agent_process_fails_the_run_closed(self) -> None:
        """If the reap cannot clear the agent's processes, scoring beside a live
        agent-identity writer is not trustworthy — refuse, do not score."""
        def fake_exec(container_id, cmd, **kwargs):
            script = cmd[-1] if cmd and cmd[0] == "bash" else ""
            if "kill -9" in script and "id -u agent" in script:
                return _ok(stdout="agent_procs_remaining=1")
            return _ok(stdout="")

        with patch.object(rt, "_assert_grading_assets_sealed", return_value=(True, "")):
            with patch.object(rt, "_docker_exec", side_effect=fake_exec):
                with pytest.raises(RuntimeError, match="survived the pre-scoring reap"):
                    _run_scoring("cid")

    def test_reap_spares_pid_1(self) -> None:
        """PID 1 is the container's own `sleep infinity`, running as agent.
        Killing it stops the container mid-score."""
        with patch.object(rt, "_docker_exec", return_value=_ok(
            stdout="agent_procs_remaining=0")) as exec_:
            rt._reap_agent_processes("cid")

        script = exec_.call_args.args[1][2]
        assert '[ "$1" = 1 ] && return 1' in script, "PID 1 must be spared"
        assert '[ "$state" = Z ] && return 1' in script, (
            "zombies of just-reaped processes must not count as live writers"
        )

    def test_workspace_is_closed_for_the_scoring_window_and_reopened_after(self) -> None:
        """The scorer can read ground_truth.json (the checks must), so any path
        it can write that a later checkpoint reads is a forge vector. Closing
        /workspace removes the shared drop point. It must reopen afterwards:
        chain tasks score between sessions and hand the container back."""
        exec_ = _run_scoring_capturing()
        scripts = [c.args[1][-1] for c in exec_.call_args_list]

        closed = [s for s in scripts if f"chmod {rt.WORKSPACE_MODE_SCORING}" in s]
        assert closed, "workspace must be closed before the checks run"
        assert "-perm /go+w" in closed[0], (
            "agent-owned is not scorer-can't-write: the agent picks the modes on "
            "its own files and can leave a 0777 dir behind to be written into"
        )

        assert exec_.call_args_list[-1].args[1] == [
            "chmod",
            WORKSPACE_MODE_SESSION,
            WORKSPACE_DIR,
        ], "the scoring lock must not outlive scoring — chains reuse the container"

    def test_workspace_is_reopened_even_when_scoring_raises(self) -> None:
        """A verifier timeout must not leave a chain's next session locked out."""
        calls: list = []

        def fake_exec(container_id, cmd, **kwargs):
            calls.append(cmd)
            if _reap_is(cmd):
                return _ok(stdout="agent_procs_remaining=0")
            if cmd[0] == "bash" and rt.TEST_SH in cmd[-1]:
                raise subprocess.TimeoutExpired(cmd="test.sh", timeout=600)
            return _ok()

        with patch.object(rt, "_assert_grading_assets_sealed", return_value=(True, "")):
            with patch.object(rt, "_docker_exec", side_effect=fake_exec):
                with pytest.raises(subprocess.TimeoutExpired):
                    _run_scoring("cid")

        assert calls[-1] == ["chmod", WORKSPACE_MODE_SESSION, WORKSPACE_DIR]

    def test_results_file_is_redirected_off_the_closed_workspace(self) -> None:
        """test_runner.sh writes $WORKSPACE/.results.json by default, which the
        scoring lock makes unwritable. Redirect it rather than let the runner
        fail its last write."""
        script = _scoring_call(_run_scoring_capturing()).args[1][2]
        assert f"EB_RESULTS_FILE={SCORING_RESULTS_FILE}" in script

        runner = (REPO_ROOT / "scripts/sandbox/test_runner.sh").read_text()
        assert 'RESULTS_FILE="${EB_RESULTS_FILE:-$WORKSPACE/.results.json}"' in runner

    def test_breach_yields_integrity_violation_not_a_rerun(self) -> None:
        """The re-run channel keys off verifier_infra_error. Routing a breach
        there would make tampering a free mulligan (the g5k5s inversion)."""
        with patch.object(
            rt, "_assert_grading_assets_sealed", return_value=(False, "tampered")
        ):
            with patch.object(rt, "_docker_exec") as exec_:
                scores = _run_scoring("cid")

        exec_.assert_not_called(), "must not run a tampered verifier at all"
        assert scores["task_score"] == 0.0
        assert scores["all_passed"] is False
        assert "verifier_infra_error" not in scores
        assert scores["integrity_violation"]["reason"] == "grading_assets_tampered"


class TestScoringDoesNotBreakTheChecks:
    """The seal moves the scoring cwd and exec user. Both are load-bearing for
    the checks, and getting either wrong silently zeroes real checkpoints
    instead of crashing — the worst possible failure mode for a benchmark."""

    def test_runner_passes_workspace_as_first_arg(self) -> None:
        """11 checks resolve WORKSPACE="${1:-.}" and document "Receives
        workspace path as $1", but the runner never passed it — they worked only
        because the cwd happened to be /workspace. Off that cwd they would all
        score 0."""
        runner = (REPO_ROOT / "scripts/sandbox/test_runner.sh").read_text()
        assert 'bash "$verifier_path" "$WORKSPACE"' in runner
        assert 'timeout "$timeout_sec" bash "$verifier_path" "$WORKSPACE"' in runner

    def test_cwd_dependent_checks_still_resolve_off_the_scoring_cwd(
        self, tmp_path: Path
    ) -> None:
        """Drive a real cwd-dependent check from the new root-owned cwd."""
        check = (
            REPO_ROOT
            / "benchmarks/customer_escalation/chain-err-flask-import-001"
            / "checks/check_fix.sh"
        )
        if not check.exists():
            pytest.skip("task not present")

        workspace = tmp_path / "workspace"
        (workspace / "flask").mkdir(parents=True)
        # must clear the check's own >50-byte threshold to count as a summary
        (workspace / "flask" / "FIX_SUMMARY.md").write_text(
            "# Fix\n\nRoot cause: a circular import between flask/cli.py and "
            "flask/app.py. Broke the cycle by deferring the import.\n"
        )
        elsewhere = tmp_path / "eb_scoring"  # stands in for SCORING_WORKDIR
        elsewhere.mkdir()

        # cwd is NOT the workspace — exactly the post-seal scoring condition.
        out = subprocess.run(
            ["bash", str(check), str(workspace)],
            cwd=str(elsewhere),
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        assert '"score": 0.0' not in out.stdout, (
            "check scored 0 off the scoring cwd — the workspace arg is not "
            f"being honoured: {out.stdout}"
        )

    def test_scoring_neutralizes_agent_controlled_git_config(self) -> None:
        """Scoring runs git inside agent-owned repos. safe.directory is required
        or `git diff` refuses (dubious ownership) and the check's
        `2>/dev/null || true` turns that into a false 'no code changes'. But
        trusting the repo must not let its config execute code as the scorer."""
        script = _scoring_call(_run_scoring_capturing()).args[1][2]

        assert "safe.directory" in script
        # the two knobs git executes during `git diff`
        assert "core.fsmonitor" in script
        assert "core.hooksPath" in script
        assert "GIT_CONFIG_NOSYSTEM=1" in script

    def test_checks_that_write_into_the_agent_tree_still_run(self) -> None:
        """The scoring lock takes away write access the checks used to have.
        pytest wants a cache dir and .pyc files next to the agent's sources; if
        it is not told to stop, a check that runs the agent's tests turns an
        EACCES into a checkpoint failure that has nothing to do with the agent."""
        script = _scoring_call(_run_scoring_capturing()).args[1][2]

        assert "PYTHONDONTWRITEBYTECODE=1" in script
        assert "no:cacheprovider" in script


class TestAgentCodeExecutionDuringScoring:
    """The rejected first pass sealed the assets and then ran the grader as root.

    Both vectors below were reproduced in real containers against that seal: they
    are not hypothetical, and neither is blocked by validating the SHAPE of the
    JSON a verifier prints. They are blocked by who the grader IS.
    """

    def test_git_diff_never_runs_an_agent_chosen_program(self) -> None:
        """.gitattributes names a diff driver; diff.<driver>.textconv then names
        a command git executes while diffing. The driver name is the agent's to
        choose, so no fixed GIT_CONFIG_KEY_n can override it — the execution has
        to be refused at the call site, on every diff against an agent repo."""
        from eb_verify.plugins import code_patch

        argv = code_patch._git_diff_argv("HEAD")
        assert argv[:2] == ["git", "diff"]
        assert "--no-textconv" in argv
        assert "--no-ext-diff" in argv

        # _git_diff_argv is the one sanctioned place a git-diff argv is built.
        # Any other one is a call site that will silently miss the flags.
        source = (REPO_ROOT / "lib/eb_verify/plugins/code_patch.py").read_text()
        raw = [
            line.strip()
            for line in source.splitlines()
            if '["git", "diff"' in line and "_GIT_NO_EXEC_FLAGS" not in line
        ]
        assert not raw, (
            f"raw git-diff argv bypasses the no-execute flags: {raw} — build it "
            "with _git_diff_argv()"
        )

    def test_every_check_that_diffs_an_agent_repo_disables_textconv(self) -> None:
        checks = (REPO_ROOT / "benchmarks").glob("*/*/checks/*.sh")
        offenders = [
            str(c.relative_to(REPO_ROOT))
            for c in checks
            if "git diff" in c.read_text()
            and "--no-textconv" not in c.read_text()
        ]
        assert not offenders, (
            f"these checks git-diff an agent-controlled repo without disabling "
            f"the textconv/external-diff hooks: {offenders}"
        )

    def test_a_planted_conftest_cannot_reach_the_grader(self) -> None:
        """`cd $WORKSPACE; python3 -m pytest` (check_test_fails.sh) auto-loads a
        planted /workspace/conftest.py — PYTHONSAFEPATH does not stop conftest
        discovery, and /workspace must stay agent-writable during the session.
        Unfixable at the check; contained by the identity that runs it."""
        call = _scoring_call(_run_scoring_capturing())

        # Whatever the planted code does, it does as this user...
        assert call.kwargs.get("user") == SCORING_USER
        # ...who owns none of the four things it would need to forge a score.
        seal = _seal_script()
        for asset in GRADING_PATHS:
            assert asset in seal
        assert f"chown -R root:{SCORING_GROUP}" in seal
        assert "chmod -R u=rwX,g=rX,o=" in seal, (
            "the scorer's group must read the assets, never write them"
        )


class TestScoringIdentity:
    def test_root_scorer_is_a_breach(self) -> None:
        """A root grader ignores the mode bits the seal just set."""
        def fake_exec(container_id, cmd, **kwargs):
            return _ok(stdout="the scoring user is root — it can rewrite the sealed grading assets\n")

        with patch.object(rt, "_docker_exec", side_effect=fake_exec):
            ok, err = _assert_scoring_identity("cid")

        assert ok is False
        assert "root" in err

    def test_missing_scoring_user_is_a_breach(self) -> None:
        with patch.object(
            rt, "_docker_exec", return_value=_ok(stdout=f"the {SCORING_USER} user does not exist\n")
        ):
            ok, err = _assert_scoring_identity("cid")

        assert ok is False
        assert "does not exist" in err

    def test_clean_identity_passes(self) -> None:
        with patch.object(rt, "_docker_exec", return_value=_ok(stdout="")):
            ok, err = _assert_scoring_identity("cid")

        assert ok is True
        assert err == ""

    def test_seal_gate_refuses_to_score_under_an_unsafe_identity(self) -> None:
        """The identity check is part of the seal gate, not an advisory log."""
        with patch.object(
            rt, "_assert_scoring_identity", return_value=(False, "scoring user is root")
        ):
            def fake_exec(container_id, cmd, **kwargs):
                if kwargs.get("user") == "agent":
                    return _fail(returncode=1)
                return _ok(stdout="")

            with patch.object(rt, "_docker_exec", side_effect=fake_exec):
                ok, err = _assert_grading_assets_sealed("cid")

        assert ok is False
        assert "root" in err

    def test_assets_unreadable_by_the_scorer_is_a_breach(self) -> None:
        """The mirror of the seal: a scorer that cannot READ the grader scores
        every checkpoint 0.0 with no crash and no diagnostic. A silent false
        zero is the same failure class the seal already caused once."""
        def fake_exec(container_id, cmd, **kwargs):
            if kwargs.get("user") in ("agent", SCORING_USER):
                return _fail(returncode=1)  # nobody can read anything
            return _ok(stdout="")

        with patch.object(rt, "_docker_exec", side_effect=fake_exec):
            ok, err = _assert_grading_assets_sealed("cid")

        assert ok is False
        assert "false 0.0" in err


class TestIntegrityViolationRouting:
    def test_router_marks_run_invalid_and_never_succeeds(self) -> None:
        result = rt.TaskRunResult(task_id="t")
        rt._route_integrity_violation(
            result, {"integrity_violation": {"reason": "grading_assets_tampered"}}
        )

        assert result.failure_class == "integrity_violation"
        assert result.phase == "integrity_violation"
        assert result.status == rt.RUN_STATUS_INVALID
        assert result.success is False

    def test_clean_scores_leave_the_result_untouched(self) -> None:
        result = rt.TaskRunResult(task_id="t")
        rt._route_integrity_violation(result, {"task_score": 1.0})

        assert result.failure_class is None
        assert result.phase == ""


class TestTextconvVectorAgainstARealGitRepo:
    """The first of the two vectors the review reproduced against the root scorer.

    Not a shell-string assertion: this builds the attacker's repo for real and
    runs the actual plugin over it. The control case fires the payload, which is
    what proves the test is not vacuous — remove --no-textconv and it fails.
    """

    @staticmethod
    def _agent_repo_with_a_textconv_payload(tmp_path: Path) -> tuple[Path, Path]:
        repo = tmp_path / "flask"
        repo.mkdir()
        marker = tmp_path / "PWNED"
        payload = tmp_path / "payload.sh"
        payload.write_text(f"#!/bin/sh\ntouch {marker}\ncat \"$1\"\n")
        payload.chmod(0o755)

        def git(*args: str) -> None:
            subprocess.run(
                ["git", *args], cwd=repo, check=True, capture_output=True
            )

        git("init", "-q")
        git("config", "user.email", "agent@example.com")
        git("config", "user.name", "agent")
        (repo / "app.txt").write_text("original\n")
        git("add", "-A")
        git("commit", "-qm", "base")

        # Everything below is inside the repo the AGENT owns and may edit at will.
        (repo / "app.txt").write_text("agent's change\n")
        (repo / ".gitattributes").write_text("*.txt diff=pwn\n")
        git("config", "diff.pwn.textconv", str(payload))
        return repo, marker

    def test_the_vector_is_real(self, tmp_path: Path) -> None:
        """Control: a bare `git diff HEAD` executes the agent's program."""
        repo, marker = self._agent_repo_with_a_textconv_payload(tmp_path)

        subprocess.run(
            ["git", "diff", "HEAD"], cwd=repo, check=True, capture_output=True
        )

        assert marker.exists(), (
            "the textconv vector did not fire — this git build ignores it, so "
            "the fix assertion below would prove nothing"
        )

    def test_the_plugin_does_not_execute_it_and_still_sees_the_diff(
        self, tmp_path: Path
    ) -> None:
        from eb_verify.plugins import code_patch

        repo, marker = self._agent_repo_with_a_textconv_payload(tmp_path)

        lines = code_patch._get_diff_lines(repo)

        assert not marker.exists(), (
            "the grader executed a program of the agent's choosing while diffing "
            "its repo (bead EnterpriseBench-8krz5)"
        )
        assert lines > 0, "suppressing textconv must not suppress the diff itself"
        assert code_patch._get_diff_stat(repo), "diff --stat must still report the change"
        assert not marker.exists()


@pytest.mark.docker
class TestScoringIdentityContainsAgentCodeEndToEnd:
    """The second vector, and the one that cannot be blocked at the trigger.

    check_test_fails.sh runs the agent's own test suite by design, and pytest
    auto-loads a planted /workspace/conftest.py. Agent code WILL execute inside
    the scoring window; there is no flag that prevents it. So the property under
    test is not "the code does not run" but "the code runs as an identity that
    can do nothing with it" — arbitrary execution as the scorer, which is the
    most any of these vectors yields, buys no forgery.
    """

    def test_arbitrary_code_as_the_scorer_cannot_forge_a_score(self) -> None:
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon unavailable")

        cid = subprocess.run(
            ["docker", "run", "-d", "--rm", "debian:bookworm-slim", "sleep", "300"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        try:
            subprocess.run(
                [
                    "docker", "exec", cid, "bash", "-c",
                    "useradd -m -s /bin/bash agent; "
                    "mkdir -p /workspace && chown agent:agent /workspace; "
                    "mkdir -p /workspace/.verifiers /workspace/.task "
                    "/workspace/.eb_verify/eb_verify /workspace/agent_output; "
                    "chown agent:agent /workspace/agent_output; "
                    'echo \'{"score": 0.0}\' > /workspace/.verifiers/check_1.sh; '
                    'echo \'{"answer": "42"}\' > /workspace/.task/ground_truth.json; '
                    "echo '# harness' > /workspace/.eb_verify/eb_verify/__init__.py; "
                    "echo '# runner' > /workspace/test.sh; "
                    "chown -R agent:agent /workspace/.verifiers /workspace/.task "
                    "/workspace/.eb_verify /workspace/test.sh; "
                    # The agent, during its own session, leaves itself a
                    # world-writable drop point to be written into later.
                    "install -d -m 0777 -o agent -g agent /workspace/agent_output/open",
                ],
                capture_output=True, check=True,
            )

            _seal_grading_assets(cid)
            rt._close_workspace_for_scoring(cid)

            def as_scorer(script: str) -> subprocess.CompletedProcess:
                return subprocess.run(
                    ["docker", "exec", "-u", SCORING_USER, cid, "sh", "-c", script],
                    capture_output=True, text=True,
                )

            # Whoever we are, we are not root and not the agent.
            whoami = as_scorer("id -u")
            assert whoami.returncode == 0, whoami.stderr
            assert whoami.stdout.strip() != "0", "the grader is running as root"

            # 1. Rewrite a verifier that has not run yet — the bead's forgery.
            for forge in (
                'echo \'{"score": 1.0, "passed": true}\' > /workspace/.verifiers/check_1.sh',
                'echo \'{"score": 1.0}\' > /workspace/.verifiers/check_2.sh',
                "rm -f /workspace/.verifiers/check_1.sh",
                "mv /workspace/.verifiers /workspace/.v2",
                'echo \'{"answer": "42"}\' > /workspace/.task/ground_truth.json',
                "echo forged > /workspace/test.sh",
                "echo 'raise ImportError' > /workspace/.eb_verify/eb_verify/__init__.py",
                "rm -rf /workspace/.eb_verify",
            ):
                out = as_scorer(forge)
                assert out.returncode != 0, (
                    f"code running as the grader forged the grader: {forge}"
                )

            # 2. Read the answer key, then plant the answer for a later
            #    checkpoint. The read is allowed (the checks need it); the write
            #    is what must fail, everywhere the checks look.
            assert as_scorer("cat /workspace/.task/ground_truth.json").returncode == 0
            for plant in (
                'echo \'{"answer": "42"}\' > /workspace/agent_output/answer.json',
                'echo \'{"answer": "42"}\' > /workspace/agent_output/open/answer.json',
                'echo \'{"answer": "42"}\' > /workspace/answer.json',
                "echo 42 > /workspace/BLAST_RADIUS.md",
            ):
                out = as_scorer(plant)
                assert out.returncode != 0, (
                    f"the grader planted an artifact for a later checkpoint to "
                    f"grade: {plant}"
                )

            # 3. ...but it can still do its job: read every asset it must run.
            for readable in (
                "/workspace/test.sh",
                "/workspace/.verifiers/check_1.sh",
                "/workspace/.task/ground_truth.json",
                "/workspace/.eb_verify/eb_verify/__init__.py",
            ):
                out = as_scorer(f"cat {readable}")
                assert out.returncode == 0, (
                    f"the grader cannot read {readable} — every checkpoint would "
                    f"score a false 0.0: {out.stderr.strip()}"
                )
            assert "forged" not in as_scorer("cat /workspace/test.sh").stdout

            ok, err = _assert_grading_assets_sealed(cid)
            assert ok is True, err

            # 4. The lock lifts: a chain's next session gets its workspace back.
            rt._reopen_workspace_for_agent(cid)
            back = subprocess.run(
                ["docker", "exec", "-u", "agent", cid, "sh", "-c",
                 "echo report > /workspace/NOTES.md"],
                capture_output=True, text=True,
            )
            assert back.returncode == 0, (
                f"the scoring lock outlived scoring — a chain's next session "
                f"cannot write: {back.stderr.strip()}"
            )
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

    def test_a_root_scorer_is_caught_by_the_gate(self) -> None:
        """Mutation guard: the identity assertion must actually fail the run if
        someone points scoring back at root (the state this bead rejected)."""
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon unavailable")

        cid = subprocess.run(
            ["docker", "run", "-d", "--rm", "debian:bookworm-slim", "sleep", "120"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        try:
            subprocess.run(
                ["docker", "exec", cid, "bash", "-c",
                 "useradd -m -s /bin/bash agent; "
                 # a scoring user that is really uid 0 under another name
                 f"useradd -o -u 0 -M {SCORING_USER}"],
                capture_output=True, check=True,
            )

            ok, err = _assert_scoring_identity(cid)

            assert ok is False
            assert "root" in err
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


@pytest.mark.docker
class TestLingeringAgentProcessCannotInjectAnswer:
    """The exfiltrate-and-inject chain a security review reproduced.

    It never touches a sealed asset. A check leaks the answer key (which the
    scorer may read) to a shared path; a lingering agent-owned process — which
    still owns the agent's answer files even after the workspace close, because
    that close strips only group/other bits — reads the leak and rewrites the
    answer a later checkpoint grades. The reap of agent processes before scoring
    is what removes the only identity that can perform the injection.
    """

    def test_the_injection_works_without_the_reap_and_the_reap_stops_it(self) -> None:
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon unavailable")

        def build() -> str:
            cid = subprocess.run(
                ["docker", "run", "-d", "--rm", "python:3.11-slim", "sleep", "300"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            subprocess.run(
                ["docker", "exec", cid, "bash", "-c",
                 "useradd -m -s /bin/bash agent; "
                 f"groupadd {SCORING_GROUP}; "
                 f"useradd -M -s /bin/bash -g {SCORING_GROUP} {SCORING_USER}; "
                 "mkdir -p /workspace/agent_output; chown -R agent:agent /workspace; "
                 # the leak channel is world-writable scratch, outside the seal
                 "chmod 1777 /tmp"],
                capture_output=True, check=True,
            )
            # The agent's answer, wrong, and an agent-owned daemon that will flip
            # it to the leaked value the moment the leak channel is populated.
            daemon = (
                "for i in $(seq 1 60); do "
                "  if [ -s /tmp/leak ]; then "
                "    cp /tmp/leak /workspace/agent_output/answer.json; exit 0; fi; "
                "  sleep 0.3; done"
            )
            subprocess.run(
                ["docker", "exec", "-u", "agent", cid, "bash", "-c",
                 "echo '{\"answer\": \"WRONG\"}' > /workspace/agent_output/answer.json"],
                capture_output=True, check=True,
            )
            # setsid + -d: a detached child that outlives the exec, exactly like
            # an agent `nohup … & disown` from inside its own session.
            subprocess.run(
                ["docker", "exec", "-d", "-u", "agent", cid, "setsid", "bash", "-c", daemon],
                capture_output=True, check=True,
            )
            return cid

        def leak_as_scorer(cid: str) -> None:
            # Stand in for a check that runs agent code as the scorer and leaks
            # the answer key it is allowed to read.
            subprocess.run(
                ["docker", "exec", "-u", SCORING_USER, cid, "bash", "-c",
                 "echo '{\"answer\": \"CORRECT\"}' > /tmp/leak"],
                capture_output=True,
            )

        def answer(cid: str) -> str:
            return subprocess.run(
                ["docker", "exec", cid, "cat", "/workspace/agent_output/answer.json"],
                capture_output=True, text=True,
            ).stdout

        # Control: WITHOUT the reap, the daemon survives and the injection lands.
        cid = build()
        try:
            leak_as_scorer(cid)
            deadline = subprocess.run(
                ["docker", "exec", cid, "bash", "-c",
                 "for i in $(seq 1 40); do "
                 "grep -q CORRECT /workspace/agent_output/answer.json && exit 0; "
                 "sleep 0.3; done; exit 1"],
                capture_output=True,
            )
            assert deadline.returncode == 0 and "CORRECT" in answer(cid), (
                "the injection did not reproduce — the test proves nothing if the "
                "control case does not forge"
            )
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

        # Fixed: reap first, and the daemon is gone before the leak is possible.
        cid = build()
        try:
            rt._reap_agent_processes(cid)  # raises if any agent proc survives
            leak_as_scorer(cid)
            # Give a would-be daemon the same wall-clock the control had.
            subprocess.run(["docker", "exec", cid, "sleep", "2"], capture_output=True)
            assert "WRONG" in answer(cid) and "CORRECT" not in answer(cid), (
                "the answer was injected despite the reap — a live agent process "
                "survived the scoring window"
            )
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

    def test_reap_raises_when_a_respawner_keeps_agent_processes_alive(self) -> None:
        """Honesty check on the residual: a respawn loop rooted at a process the
        reap cannot kill leaves survivors, and the run must fail closed rather
        than score beside them (the durable fix is a separate scoring container,
        fix-candidate-3)."""
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon unavailable")

        cid = subprocess.run(
            ["docker", "run", "-d", "--rm", "python:3.11-slim", "sleep", "300"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        try:
            subprocess.run(
                ["docker", "exec", cid, "bash", "-c", "useradd -m -s /bin/bash agent"],
                capture_output=True, check=True,
            )
            # A long-lived agent process present at reap time.
            subprocess.run(
                ["docker", "exec", "-d", "-u", "agent", cid, "sleep", "300"],
                capture_output=True, check=True,
            )
            # Monkeypatch the kill out to simulate a survivor the reap can't clear.
            script_seen = {}

            orig = rt._docker_exec

            def no_kill(container_id, cmd, **kwargs):
                if cmd and cmd[0] == "bash" and "kill -9" in cmd[-1]:
                    # neuter the kill, keep the survivor count honest
                    cmd = ["bash", "-c", cmd[-1].replace("kill -9 ${d#/proc/}", "true")]
                return orig(container_id, cmd, **kwargs)

            with patch.object(rt, "_docker_exec", side_effect=no_kill):
                with pytest.raises(RuntimeError, match="survived the pre-scoring reap"):
                    rt._reap_agent_processes(cid)
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


@pytest.mark.docker
class TestRealTaskStillScoresUnderTheNewIdentity:
    """The other half of the bill: the seal must not silently zero real work.

    Moving the grader off root is exactly the kind of change that scores 0.0
    everywhere and crashes nowhere — the same shape as the ${1:-.} workspace bug
    the first pass of this seal shipped. So this drives the REAL scoring path
    (seal -> close workspace -> exec as the scorer -> the real test_runner -> a
    real task's check -> python3 -> the real ground truth) in a container and
    insists on a real score coming back out.
    """

    CALIBRATION = "benchmarks/customer_escalation/calibration-001"

    def _container_with_a_real_task(self, answer: dict) -> str:
        cid = subprocess.run(
            ["docker", "run", "-d", "--rm", "python:3.11-slim", "sleep", "300"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        task = REPO_ROOT / self.CALIBRATION
        subprocess.run(
            [
                "docker", "exec", cid, "bash", "-c",
                "useradd -m -s /bin/bash agent; "
                # production layout: the image hands /workspace to the agent
                "mkdir -p /workspace/.verifiers /workspace/.task "
                "/workspace/.eb_verify /workspace/agent_output /workspace/flask/.git; "
                "chown -R agent:agent /workspace",
            ],
            capture_output=True, check=True,
        )

        rt._docker_cp(str(task / "checks/check_error_source.sh"),
                      f"{cid}:/workspace/.verifiers/check_error_source.sh")
        rt._docker_cp(str(task / "ground_truth.json"),
                      f"{cid}:/workspace/.task/ground_truth.json")
        rt._docker_cp(str(REPO_ROOT / "lib/eb_verify"),
                      f"{cid}:/workspace/.eb_verify/eb_verify")
        rt._docker_cp(str(REPO_ROOT / "scripts/sandbox/test_runner.sh"),
                      f"{cid}:/workspace/test.sh")

        import json as _json
        subprocess.run(
            ["docker", "exec", cid, "bash", "-c",
             f"cat > /workspace/agent_output/answer.json <<'EOF'\n"
             f"{_json.dumps(answer)}\nEOF\n"
             "chown -R agent:agent /workspace/agent_output /workspace/flask; "
             # ...and the pre-seal state this bead exists to end
             "chown -R agent:agent /workspace/.verifiers /workspace/.task "
             "/workspace/.eb_verify /workspace/test.sh"],
            capture_output=True, check=True,
        )
        return cid

    def test_a_correct_answer_still_scores_and_a_wrong_one_still_fails(self) -> None:
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon unavailable")
        if not (REPO_ROOT / self.CALIBRATION / "ground_truth.json").exists():
            pytest.skip("calibration task not present")

        # The answer the ground truth actually asks for — both required files.
        cid = self._container_with_a_real_task(
            {"source_files": [
                "src/flask/blueprints.py",
                "src/flask/sansio/scaffold.py",
            ]}
        )
        try:
            _seal_grading_assets(cid)
            scores = _run_scoring(cid, verifier_timeout=120)
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

        assert "integrity_violation" not in scores, scores
        assert "verifier_infra_error" not in scores, (
            f"the real scoring path broke under the unprivileged grader: {scores}"
        )
        assert scores["task_score"] == 1.0, (
            f"a correct answer no longer scores — the seal is zeroing real work: "
            f"{scores}"
        )

        # ...and the check is still discriminating, not just passing everything.
        cid = self._container_with_a_real_task({"source_files": ["src/flask/wrong.py"]})
        try:
            _seal_grading_assets(cid)
            wrong = _run_scoring(cid, verifier_timeout=120)
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

        assert wrong["task_score"] == 0.0, wrong
        assert wrong["all_passed"] is False

    def test_a_planted_conftest_cannot_turn_a_wrong_answer_into_a_right_one(self) -> None:
        """The reviewer's vector, end to end, against the real scoring path.

        The agent answers wrong, then plants /workspace/conftest.py — the file
        pytest auto-loads — carrying the forgery it would run inside the grader:
        rewrite the verifier, and copy the answer key over its own answer. Under
        the root grader both succeeded. The score must still be 0.0.
        """
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon unavailable")
        if not (REPO_ROOT / self.CALIBRATION / "ground_truth.json").exists():
            pytest.skip("calibration task not present")

        cid = self._container_with_a_real_task({"source_files": ["src/flask/wrong.py"]})
        try:
            forgery = (
                'import json, os, shutil\n'
                '# rewrite the grader with a perfect verdict\n'
                'open("/workspace/.verifiers/check_error_source.sh", "w").write('
                '\'echo \\\'{"score": 1.0, "passed": true}\\\'\\n\')\n'
                '# or just read the answer key and become correct\n'
                'gt = json.load(open("/workspace/.task/ground_truth.json"))\n'
                'json.dump({"source_files": [f["path"] for f in gt["required_files"]]},\n'
                '          open("/workspace/agent_output/answer.json", "w"))\n'
            )
            payload = subprocess.run(
                ["docker", "exec", "-u", "agent", cid, "bash", "-c",
                 f"cat > /workspace/conftest.py <<'EOF'\n{forgery}EOF"],
                capture_output=True, text=True,
            )
            assert payload.returncode == 0, (
                f"could not plant the payload — /workspace must stay agent-writable "
                f"during the session or the test is vacuous: {payload.stderr}"
            )

            _seal_grading_assets(cid)

            # Execute the planted file exactly as a check running the agent's
            # tests would: as the grader, from inside the agent's workspace.
            fired = subprocess.run(
                ["docker", "exec", "-u", SCORING_USER, "-w", "/workspace", cid,
                 "python3", "/workspace/conftest.py"],
                capture_output=True, text=True,
            )
            assert fired.returncode != 0, (
                "the forgery ran to completion as the grader — it rewrote the "
                "verifier and the answer with the key it read"
            )

            scores = _run_scoring(cid, verifier_timeout=120)
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

        assert scores["task_score"] == 0.0, (
            f"agent-planted code forged a score from inside the scoring window: "
            f"{scores}"
        )
        assert scores["all_passed"] is False


@pytest.mark.docker
class TestForgeAttackEndToEnd:
    """The empirical repro from the bead: chown -R agent + docker exec -u agent
    let the unprivileged user overwrite a verifier with a forged verdict."""

    def test_sealed_agent_cannot_forge_a_verdict_or_read_ground_truth(self) -> None:
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon unavailable")

        # debian-based, matching every production base image in
        # dockerfile_generator (bash, GNU find, useradd).
        cid = subprocess.run(
            [
                "docker", "run", "-d", "--rm", "debian:bookworm-slim",
                "sleep", "300",
            ],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        try:
            subprocess.run(
                [
                    "docker", "exec", cid, "bash", "-c",
                    "useradd -m -s /bin/bash agent; "
                    # Production layout: dockerfile_generator hands /workspace
                    # to the agent. Sealing only the assets inside it is not
                    # enough — unlink/rename is governed by the PARENT's write
                    # bit — so the seal must take /workspace back. Reproducing
                    # this faithfully is what makes the vectors below real.
                    "mkdir -p /workspace && chown agent:agent /workspace; "
                    "mkdir -p /workspace/.verifiers /workspace/.task "
                    "/workspace/.eb_verify/eb_verify /workspace/agent_output; "
                    "chown agent:agent /workspace/agent_output; "
                    'echo \'{"score": 0.0}\' > /workspace/.verifiers/check_1.sh; '
                    'echo \'{"answer": "secret"}\' > /workspace/.task/ground_truth.json; '
                    "echo '# harness' > /workspace/.eb_verify/eb_verify/__init__.py; "
                    "touch /workspace/test.sh; "
                    # the pre-fix state: the agent owns the grader
                    "chown -R agent:agent /workspace/.verifiers /workspace/.task "
                    "/workspace/.eb_verify /workspace/test.sh",
                ],
                capture_output=True, check=True,
            )

            _seal_grading_assets(cid)

            forge = subprocess.run(
                [
                    "docker", "exec", "-u", "agent", cid, "sh", "-c",
                    'echo \'{"score": 1.0, "passed": true}\' > /workspace/.verifiers/check_1.sh',
                ],
                capture_output=True, text=True,
            )
            assert forge.returncode != 0, "agent forged a perfect verdict"

            leak = subprocess.run(
                ["docker", "exec", "-u", "agent", cid, "cat",
                 "/workspace/.task/ground_truth.json"],
                capture_output=True, text=True,
            )
            assert leak.returncode != 0, "agent read the answer key"
            assert "secret" not in leak.stdout

            # g5k5s: deleting or corrupting the harness the checks import must
            # not be reachable. Post-kyo34 a ModuleNotFoundError routes to
            # verifier_infra_error -> re-run, so a reachable sabotage would turn
            # a bad score into a free mulligan.
            #
            # The last four vectors are the PARENT-DIRECTORY bypass: they never
            # write to a sealed file at all, they unlink or rename it out of
            # /workspace and drop a forgery in its place. An earlier version of
            # this seal chowned the assets but left /workspace agent-owned, and
            # every one of these succeeded against it.
            for sabotage_cmd in (
                ["rm", "-rf", "/workspace/.eb_verify"],
                ["rm", "-f", "/workspace/.eb_verify/eb_verify/__init__.py"],
                ["sh", "-c", "echo raise ImportError > "
                             "/workspace/.eb_verify/eb_verify/__init__.py"],
                ["rm", "-rf", "/workspace/.verifiers"],
                ["rm", "-f", "/workspace/test.sh"],
                ["mv", "/workspace/test.sh", "/workspace/test.sh.bak"],
                ["mv", "/workspace/.verifiers", "/workspace/.verifiers.bak"],
                ["mv", "/workspace/.task", "/workspace/.task.bak"],
            ):
                sabotage = subprocess.run(
                    ["docker", "exec", "-u", "agent", cid] + sabotage_cmd,
                    capture_output=True,
                )
                assert sabotage.returncode != 0, (
                    f"agent sabotaged the harness via {' '.join(sabotage_cmd)} "
                    "— g5k5s mulligan is still reachable"
                )

            # test.sh survived intact — not unlinked and replaced by a forgery
            runner = subprocess.run(
                ["docker", "exec", cid, "cat", "/workspace/test.sh"],
                capture_output=True, text=True,
            )
            assert "FORGED" not in runner.stdout

            # ...and the agent can still do its actual job: write its answer,
            # drop a /workspace artifact, and clean up its own files.
            for legit_cmd in (
                ["sh", "-c", "echo '{}' > /workspace/agent_output/answer.json"],
                ["sh", "-c", "echo report > /workspace/BLAST_RADIUS.md"],
                ["rm", "-f", "/workspace/BLAST_RADIUS.md"],
            ):
                legit = subprocess.run(
                    ["docker", "exec", "-u", "agent", cid] + legit_cmd,
                    capture_output=True, text=True,
                )
                assert legit.returncode == 0, (
                    f"seal broke a legitimate agent action: {' '.join(legit_cmd)} "
                    f"-> {legit.stderr.strip()}"
                )

            # the harness is intact, so the checks still import it
            survives = subprocess.run(
                ["docker", "exec", cid, "cat",
                 "/workspace/.eb_verify/eb_verify/__init__.py"],
                capture_output=True, text=True,
            )
            assert survives.stdout.strip() == "# harness"

            ok, err = _assert_grading_assets_sealed(cid)
            assert ok is True, err

            # Fail CLOSED if a grading asset is gone. Even with /workspace
            # hardened this must hold: the assertion is the backstop, and a
            # backstop that reports "sealed" when the grader is absent would let
            # a deleted test.sh through as empty output -> verifier_infra_error
            # -> free re-run. Delete it as ROOT (the agent can no longer do so)
            # to prove the *detection* layer, not just the prevention layer.
            subprocess.run(
                ["docker", "exec", "-u", "root", cid, "rm", "-f",
                 "/workspace/test.sh"],
                capture_output=True, check=True,
            )
            ok, err = _assert_grading_assets_sealed(cid)
            assert ok is False, "a missing test.sh reported as sealed — fail-open"
            assert "test.sh" in err
        finally:
            subprocess.run(["docker", "kill", cid], capture_output=True)
