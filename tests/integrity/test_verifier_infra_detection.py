"""A verifier that never ran must never be scored — end-to-end (bead glka.2).

These vectors exec the REAL ``scripts/sandbox/test_runner.sh`` with the python3
interpreter genuinely absent from PATH, then route its output through the real
scorer trust boundary. Nothing here is synthetic: the same shell that runs in
the container produces the JSON that the same guard parses.

Two never-ran variants are proven, both of which used to land as a scored 0.0
that was indistinguishable from a genuine wrong answer:

* LOUD   — a bare ``python3`` under ``set -euo pipefail`` exits 127.
* SILENT — ``if python3 ... 2>/dev/null`` swallows the failure, so the verifier
  exits 0 and prints a well-formed ``{"score": 0.0}``. No exit code, no score,
  and no detail string distinguishes it from a real failure.

Two mechanisms, because neither covers the other's ground:

* command_not_found_handle catches a missing command per checkpoint, in any
  context — but ONLY when bash itself performs the PATH lookup. It is blind to
  ``env python3`` and to absolute paths, where the lookup happens in another
  process or not at all.
* The preflight refuses to score the task at all when an interpreter the check
  scripts reference is absent. That closes every invocation form, because no
  verifier runs — but it only knows about interpreters named up front.

The genuine-score controls are the point of the exercise: a wrong answer must
still be a 0.0 and a correct one still a 1.0, or the fix has merely traded a
false-zero problem for a false-infra one. The last section of this file is that
trade caught in the act — an answer the check scripts cannot parse aborts them
mid-flight, and "no verdict" alone cannot tell that apart from a verifier that
never ran. Scored one way, a garbage answer buys the agent a re-run instead of
the 0.0 it earned.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.scorer_guard import InfraError, guard_verifier_output  # noqa: E402

TEST_RUNNER = REPO_ROOT / "scripts" / "sandbox" / "test_runner.sh"

# Everything test_runner.sh legitimately needs — deliberately WITHOUT python3.
_REQUIRED_TOOLS = (
    "bash", "sed", "tr", "awk", "grep", "cat", "mktemp",
    "date", "basename", "rm", "timeout", "dirname", "env",
)

# --- the two never-ran variants, verbatim in shape from the real check scripts --

LOUD_MISSING_INTERPRETER = """\
#!/usr/bin/env bash
set -euo pipefail
python3 -c 'import json; print(json.dumps({"score": 1.0, "passed": True}))'
"""

SILENT_SWALLOWED_INTERPRETER = """\
#!/usr/bin/env bash
set -euo pipefail
if python3 -c "import sys; sys.exit(0)" 2>/dev/null; then
  printf '{"score": 1.0, "passed": true, "reason": "config consistent"}\\n'
else
  printf '{"score": 0.0, "passed": false, "reason": "config inconsistent"}\\n'
fi
exit 0
"""

GENUINE_WRONG_ANSWER = """\
#!/usr/bin/env bash
set -euo pipefail
printf '{"score": 0.0, "passed": false, "detail": "answer did not match ground truth"}\\n'
"""

GENUINE_PASS = """\
#!/usr/bin/env bash
set -euo pipefail
printf '{"score": 1.0, "passed": true, "detail": "all assertions held"}\\n'
"""


@pytest.fixture
def sanitized_path(tmp_path_factory) -> str:
    """A PATH with real coreutils but no python3 — a minimal image with no
    interpreter, which is exactly the condition the bead is about."""
    bin_dir = tmp_path_factory.mktemp("bin")
    for tool in _REQUIRED_TOOLS:
        resolved = shutil.which(tool)
        if resolved:
            (bin_dir / tool).symlink_to(resolved)
    assert shutil.which("python3", path=str(bin_dir)) is None, "python3 must be absent"
    return str(bin_dir)


def run_test_runner(
    workspace: Path,
    verifiers: dict[str, str],
    path: str,
    answer: str | None = None,
    task_dir: Path | None = None,
) -> dict:
    """Exec the real test_runner.sh over ``verifiers`` and parse its JSON.

    ``answer`` writes the agent's artifact at the path the check scripts read;
    ``task_dir`` points them at a task's ground truth, as run_task.py does.
    """
    vdir = workspace / ".verifiers"
    vdir.mkdir(parents=True, exist_ok=True)
    (workspace / "repo-a" / ".git").mkdir(parents=True, exist_ok=True)

    for name, body in verifiers.items():
        script = vdir / f"{name}.sh"
        script.write_text(body)
        script.chmod(0o755)

    if answer is not None:
        (workspace / "agent_output").mkdir(exist_ok=True)
        (workspace / "agent_output" / "answer.json").write_text(answer)

    env = {"WORKSPACE": str(workspace), "PATH": path}
    if task_dir is not None:
        env["TASK_DIR"] = str(task_dir)

    proc = subprocess.run(
        ["bash", str(TEST_RUNNER)], capture_output=True, text=True, env=env
    )
    return json.loads(proc.stdout)


def checkpoint(result: dict, name: str) -> dict:
    return next(cp for cp in result["checkpoints"] if cp["name"] == name)


# A missing command that is NOT a preflighted interpreter, so it reaches the
# verifier and exercises the per-checkpoint attestation rather than the preflight.
LOUD_MISSING_TOOL = """\
#!/usr/bin/env bash
set -euo pipefail
eb_missing_tool --check
printf '{"score": 1.0, "passed": true, "detail": "ok"}\\n'
"""

SILENT_MISSING_TOOL = """\
#!/usr/bin/env bash
set -euo pipefail
if eb_missing_tool --check 2>/dev/null; then
  printf '{"score": 1.0, "passed": true, "detail": "ok"}\\n'
else
  printf '{"score": 0.0, "passed": false, "detail": "check failed"}\\n'
fi
exit 0
"""


class TestNeverRanVerifierIsNotScored:
    """Acceptance for the two proven variants (bead glka.2)."""

    @pytest.mark.parametrize(
        "name,body",
        [("loud", LOUD_MISSING_INTERPRETER), ("silent", SILENT_SWALLOWED_INTERPRETER)],
    )
    def test_never_ran_routes_to_infra_error_not_zero(
        self, tmp_path: Path, sanitized_path: str, name: str, body: str
    ) -> None:
        """A check script that shells a missing binary yields verifier_infra_error
        (the re-run channel), never a task_score of 0.0."""
        result = run_test_runner(tmp_path, {name: body}, sanitized_path)
        guarded = guard_verifier_output(json.dumps(result), returncode=1)
        assert isinstance(guarded, InfraError), f"{name} variant must not be scored"

    def test_loud_missing_tool_is_not_attested(
        self, tmp_path: Path, sanitized_path: str
    ) -> None:
        """Per-checkpoint attestation, for a missing command the preflight does
        not know about: exit 127, the verifier died before reaching a verdict."""
        result = run_test_runner(tmp_path, {"loud": LOUD_MISSING_TOOL}, sanitized_path)
        cp = checkpoint(result, "loud")
        assert cp["verifier_ran"] is False
        assert "eb_missing_tool" in cp["detail"]

    def test_silent_missing_tool_is_not_attested(
        self, tmp_path: Path, sanitized_path: str
    ) -> None:
        """The crux: the verifier swallows the failure, exits 0, and prints a
        well-formed 0.0. Exit code, score and detail are all indistinguishable
        from a genuine wrong answer — only the side channel sees the miss."""
        result = run_test_runner(tmp_path, {"silent": SILENT_MISSING_TOOL}, sanitized_path)
        cp = checkpoint(result, "silent")
        assert cp["exit_code"] == 0, "precondition: the failure IS swallowed"
        assert cp["verifier_ran"] is False, "a swallowed missing command must still surface"
        assert "eb_missing_tool" in cp["detail"]
        assert isinstance(guard_verifier_output(json.dumps(result), returncode=0), InfraError)

    def test_no_verdict_is_never_fabricated_into_a_pass(
        self, tmp_path: Path, sanitized_path: str
    ) -> None:
        """Over-credit direction: a verifier that exits 0 printing NOTHING used
        to be fabricated into a free 1.0. Absence of a verdict is not a pass."""
        result = run_test_runner(
            tmp_path, {"silent_ok": "#!/usr/bin/env bash\nexit 0\n"}, sanitized_path
        )
        cp = checkpoint(result, "silent_ok")
        assert cp["verifier_ran"] is False
        assert cp["score"] == 0.0, "an empty verifier must never be credited a pass"
        assert isinstance(guard_verifier_output(json.dumps(result), returncode=0), InfraError)


class TestGenuineScoresSurvive:
    """The fix must not trade false zeros for false infra errors."""

    def test_genuine_wrong_answer_stays_a_scored_zero(
        self, tmp_path: Path, sanitized_path: str
    ) -> None:
        result = run_test_runner(
            tmp_path, {"wrong": GENUINE_WRONG_ANSWER}, sanitized_path
        )
        cp = checkpoint(result, "wrong")
        assert cp["verifier_ran"] is True
        assert cp["score"] == 0.0

        guarded = guard_verifier_output(json.dumps(result), returncode=1)
        assert isinstance(guarded, dict), "a real wrong answer is a score, not an infra error"
        assert guarded["task_score"] == 0.0

    def test_genuine_pass_stays_a_scored_one(
        self, tmp_path: Path, sanitized_path: str
    ) -> None:
        result = run_test_runner(tmp_path, {"pass": GENUINE_PASS}, sanitized_path)
        cp = checkpoint(result, "pass")
        assert cp["verifier_ran"] is True
        assert cp["score"] == 1.0

        guarded = guard_verifier_output(json.dumps(result), returncode=0)
        assert isinstance(guarded, dict)
        assert guarded["task_score"] == 1.0

    def test_one_broken_verifier_does_not_let_the_others_be_scored(
        self, tmp_path: Path, sanitized_path: str
    ) -> None:
        """A mixed run is an infra error for the WHOLE task: a task_score built
        from a partially-run verifier set is not a measurement of the agent."""
        result = run_test_runner(
            tmp_path,
            {
                "pass": GENUINE_PASS,
                "wrong": GENUINE_WRONG_ANSWER,
                "silent": SILENT_MISSING_TOOL,
            },
            sanitized_path,
        )
        assert checkpoint(result, "pass")["verifier_ran"] is True
        assert checkpoint(result, "wrong")["verifier_ran"] is True
        assert checkpoint(result, "silent")["verifier_ran"] is False

        guarded = guard_verifier_output(json.dumps(result), returncode=1)
        assert isinstance(guarded, InfraError)


# Every way a check script can reach an interpreter. Only the first has its PATH
# lookup performed by bash itself, so command_not_found_handle sees only that one
# — the rest are closed by the preflight, not by detection.
SWALLOWED_INVOCATIONS = {
    "bare": "python3",
    "env": "env python3",
    "usr_bin_env": "/usr/bin/env python3",
    "absolute": "/usr/bin/python3",
}


@pytest.mark.parametrize("label,invocation", sorted(SWALLOWED_INVOCATIONS.items()))
def test_missing_interpreter_is_caught_however_it_is_invoked(
    tmp_path: Path, sanitized_path: str, label: str, invocation: str
) -> None:
    """The SILENT variant, reached through every idiom a check script might use.

    `env python3` and absolute paths do their PATH lookup outside bash, so
    command_not_found_handle never fires for them — and the verifier swallows the
    127 and prints a clean 0.0. Each of these must still refuse to be scored.
    """
    body = f"""\
#!/usr/bin/env bash
set -euo pipefail
if {invocation} -c "pass" 2>/dev/null; then
  printf '{{"score": 1.0, "passed": true, "reason": "ok"}}\\n'
else
  printf '{{"score": 0.0, "passed": false, "reason": "drift"}}\\n'
fi
exit 0
"""
    result = run_test_runner(tmp_path, {f"cp_{label}": body}, sanitized_path)
    guarded = guard_verifier_output(json.dumps(result), returncode=1)
    assert isinstance(guarded, InfraError), (
        f"a missing interpreter invoked as `{invocation}` was scored as a real 0.0"
    )


def test_preflight_does_not_fire_when_checks_do_not_need_the_interpreter(
    tmp_path: Path, sanitized_path: str
) -> None:
    """The preflight is gated on the interpreter actually being referenced: a
    pure-bash check must still score normally in an image without python3."""
    result = run_test_runner(tmp_path, {"pure_bash": GENUINE_PASS}, sanitized_path)
    guarded = guard_verifier_output(json.dumps(result), returncode=0)
    assert isinstance(guarded, dict), "a pure-bash check must not need python3"
    assert guarded["task_score"] == 1.0


def test_empty_verifiers_dir_is_infra_not_zero(tmp_path: Path, sanitized_path: str) -> None:
    """An existing-but-empty .verifiers/ ran no verifiers, so its 0.0 measures
    nothing. An empty checkpoint list must not pass through as a real score."""
    result = run_test_runner(tmp_path, {}, sanitized_path)
    assert result["checkpoints"] == []
    guarded = guard_verifier_output(json.dumps(result), returncode=1)
    assert isinstance(guarded, InfraError)
    assert guarded.reason == "no_checkpoints_run"


def test_attestation_cannot_be_forged_by_a_check_script(
    tmp_path: Path, sanitized_path: str
) -> None:
    """.verifiers/ is agent-writable, so a check script may print whatever it
    likes — including a perfect score and a forged attestation. The attestation
    stamped into the checkpoint must be the RUNNER's own observation, never a
    value echoed back from the script's stdout.
    """
    forger = """\
#!/usr/bin/env bash
if eb_missing_tool --check 2>/dev/null; then :; fi
printf '{"score": 1.0, "passed": true, "verifier_ran": true, "detail": "legit"}\\n'
exit 0
"""
    result = run_test_runner(tmp_path, {"forger": forger}, sanitized_path)
    cp = checkpoint(result, "forger")
    assert cp["verifier_ran"] is False, "a script must not be able to attest for itself"
    assert cp["score"] == 0.0, "the score printed alongside a forged attestation is discarded"
    assert "eb_missing_tool" in cp["detail"]
    assert isinstance(guard_verifier_output(json.dumps(result), returncode=0), InfraError)


def test_handler_does_not_fire_for_command_v_probes(
    tmp_path: Path, sanitized_path: str
) -> None:
    """False-positive guard for the side channel.

    The real config-drift scripts probe optional tooling with `command -v helm`
    and fall back when it is absent. `command -v` is a bash builtin, so it must
    NOT trip command_not_found_handle — otherwise every such script would become
    a spurious infra error. This is the only tool-probe idiom in the corpus.
    """
    probe = """\
#!/usr/bin/env bash
set -euo pipefail
if command -v helm &>/dev/null; then
  printf '{"score": 1.0, "passed": true, "detail": "helm present"}\\n'
else
  printf '{"score": 1.0, "passed": true, "detail": "helm absent, fell back"}\\n'
fi
"""
    result = run_test_runner(tmp_path, {"probe": probe}, sanitized_path)
    cp = checkpoint(result, "probe")
    assert cp["verifier_ran"] is True, "command -v is a builtin and must not trip the handler"
    assert cp["score"] == 1.0
    assert isinstance(guard_verifier_output(json.dumps(result), returncode=0), dict)


# --- the mirror image: a bad ANSWER must not be laundered into an infra error --
#
# "No verdict" has two causes, and only one of them is the harness's fault. The
# check scripts run under `set -euo pipefail` and read the agent's answer through
# a command substitution, so an answer they cannot parse aborts them BEFORE their
# final print — no verdict, exactly as if they had never run. Attribute that to
# the harness and a garbage answer buys the agent a re-run instead of the 0.0 it
# earned: the same false-scoring disease as the bead, pointed the other way.
#
# Measured over the corpus: of the 132 check scripts that read answer.json, 104
# abort on an answer that is not a JSON object. This is the common case.

REAL_TASKS = {
    # One per affected suite. Every check listed here was verified to abort on a
    # malformed answer, so these vectors exercise the attribution, not a script's
    # own early-exit verdict.
    "customer_escalation/err-provenance-01": 3,
    "dependency_management/api-contract-dual-fastapi-001": 3,
    "technical_debt/calibration-001": 2,
}

# Neither is a JSON object, and neither is anything a check script can read.
INVALID_ANSWERS = {
    "unparseable": "not json {{{",
    "json-but-a-list": "[]",
    "json-but-a-string": '"the answer is in the files"',
}


@pytest.fixture
def real_path() -> str:
    """The unmodified PATH: python3 present, as in the task images. The point
    here is a verifier that CAN run, unlike the never-ran vectors above."""
    return os.environ["PATH"]


@pytest.mark.parametrize("task", sorted(REAL_TASKS))
@pytest.mark.parametrize("shape,answer", sorted(INVALID_ANSWERS.items()))
def test_invalid_agent_answer_is_scored_not_re_run(
    task: str, shape: str, answer: str, tmp_path: Path, real_path: str
) -> None:
    """The REAL check scripts of a REAL task, fed an answer they cannot read."""
    task_dir = REPO_ROOT / "benchmarks" / task
    checks = {p.stem: p.read_text() for p in sorted((task_dir / "checks").glob("*.sh"))}
    assert len(checks) == REAL_TASKS[task], f"{task} checks changed; revisit this vector"

    result = run_test_runner(tmp_path, checks, real_path, answer=answer, task_dir=task_dir)
    guarded = guard_verifier_output(json.dumps(result), returncode=1)

    assert not isinstance(guarded, InfraError), (
        f"a {shape} answer is the AGENT's failure — scoring it 0.0 is the whole "
        f"point of the benchmark; routing it to the re-run channel lets an agent "
        f"escape a 0.0 by emitting garbage"
    )
    assert guarded["task_score"] == 0.0
    for cp in result["checkpoints"]:
        assert cp["verifier_ran"] is True, "the verifier ran; the answer killed it"
        assert cp["score"] == 0.0


def test_the_checkpoint_says_why_an_unreadable_answer_scored_zero(
    tmp_path: Path, real_path: str
) -> None:
    """Non-vacuity, and the diagnosis a 0.0 owes its reader.

    Some checks survive a hostile answer and reach their own verdict; the three
    here do not — each dies in the command substitution that reads it. Their 0.0
    therefore comes from the runner, and must say so rather than pass for a
    considered judgement of the agent's reasoning.
    """
    task_dir = REPO_ROOT / "benchmarks" / "customer_escalation" / "err-provenance-01"
    checks = {p.stem: p.read_text() for p in sorted((task_dir / "checks").glob("*.sh"))}

    result = run_test_runner(
        tmp_path, checks, real_path, answer="not json {{{", task_dir=task_dir
    )
    for cp in result["checkpoints"]:
        assert "not a JSON object" in cp["detail"]


def test_a_valid_answer_object_still_reaches_the_checks(
    tmp_path: Path, real_path: str
) -> None:
    """The other side of the line. Only the two unreadable SHAPES are attributed
    to the agent; an answer that is a JSON object — however wrong — is scored by
    the check scripts themselves, never short-circuited here."""
    task_dir = REPO_ROOT / "benchmarks" / "customer_escalation" / "err-provenance-01"
    checks = {p.stem: p.read_text() for p in sorted((task_dir / "checks").glob("*.sh"))}

    result = run_test_runner(
        tmp_path,
        checks,
        real_path,
        answer=json.dumps({"source_files": ["wrong/file.py"]}),
        task_dir=task_dir,
    )
    guarded = guard_verifier_output(json.dumps(result), returncode=1)
    assert isinstance(guarded, dict)
    for cp in result["checkpoints"]:
        assert "not a JSON object" not in cp["detail"], "the checks must do the judging"


class TestInfraSignalsOutrankTheAgentAttribution:
    """A garbage answer must not become a laundering channel for real harness
    failures: every never-ran signal still wins, even when the answer is invalid.
    """

    def test_missing_command_still_wins(self, tmp_path: Path, real_path: str) -> None:
        result = run_test_runner(
            tmp_path, {"broken": LOUD_MISSING_TOOL}, real_path, answer="not json {{{"
        )
        cp = checkpoint(result, "broken")
        assert cp["verifier_ran"] is False
        guarded = guard_verifier_output(json.dumps(result), returncode=1)
        assert isinstance(guarded, InfraError), "a missing command is the harness's fault"
        assert guarded.reason == "verifier_did_not_run"

    def test_verifier_that_exits_zero_without_a_verdict_still_wins(
        self, tmp_path: Path, real_path: str
    ) -> None:
        """The attribution demands a nonzero exit. A check that exits 0 printing
        nothing did not die on the answer — it is simply broken, and its silence
        must not be read as a considered 0.0."""
        result = run_test_runner(
            tmp_path, {"silent": "#!/usr/bin/env bash\nexit 0\n"}, real_path,
            answer="not json {{{",
        )
        assert checkpoint(result, "silent")["verifier_ran"] is False
        assert isinstance(
            guard_verifier_output(json.dumps(result), returncode=0), InfraError
        )

    def test_harness_side_crash_under_a_valid_answer_still_wins(
        self, tmp_path: Path, real_path: str
    ) -> None:
        """The bead's own case, unbroken: a verifier that dies for a reason the
        agent did not cause is still an infra error, not a 0.0."""
        crasher = """\
#!/usr/bin/env bash
set -euo pipefail
cat /nonexistent/ground_truth.json
printf '{"score": 1.0, "passed": true}\\n'
"""
        result = run_test_runner(
            tmp_path, {"crasher": crasher}, real_path, answer=json.dumps({"files": []})
        )
        assert checkpoint(result, "crasher")["verifier_ran"] is False
        guarded = guard_verifier_output(json.dumps(result), returncode=1)
        assert isinstance(guarded, InfraError)
        assert guarded.reason == "verifier_did_not_run"
