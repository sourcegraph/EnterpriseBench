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

from eb_verify.scorer_guard import (  # noqa: E402
    _SCORE_EPSILON,
    InfraError,
    guard_verifier_output,
    is_valid_score,
)

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


# --- a verdict needs a SCORE, not just a pair of braces ------------------------
#
# Every never-ran vector above printed no JSON at all. A verifier that prints a
# well-formed JSON object carrying a score the schema would reject has reached no
# verdict either, and the shell must not launder it into a plausible 0.0 before
# the guard ever sees it — the guard can only judge the number it is handed.


def verifier_printing(payload: str) -> str:
    """A check script whose entire job is to print ``payload`` and exit 0."""
    return f"#!/usr/bin/env bash\ncat <<'JSON'\n{payload}\nJSON\n"


NO_READABLE_SCORE = {
    # json.loads accepts bare NaN, so a verifier dividing by zero really can
    # print one; the rest are ordinary verifier bugs.
    "string": '{"score": "abc", "passed": false, "detail": "d"}',
    "nan": '{"score": NaN, "passed": false, "detail": "d"}',
    "null": '{"score": null, "passed": false, "detail": "d"}',
    "missing": '{"passed": false, "detail": "d"}',
    "above_one": '{"score": 999, "passed": true, "detail": "d"}',
    "negative": '{"score": -0.5, "passed": false, "detail": "d"}',
    # Not valid JSON: a leading zero may not be followed by another digit. A regex
    # that matches the leading "0" and stops yields a well-formed, in-range 0.0, so
    # only a number spanning the WHOLE value token rejects it.
    "leading_zero": '{"score": 00.5, "passed": false, "detail": "d"}',
    # Two score keys, and a regex cannot see nesting: a first-match read credits
    # the nested 1.0 on a failed checkpoint. The mirror pins the rule rather than
    # the reading order — it is the vector a last-match "fix" would still fail.
    "nested_before_real": '{"detail": {"score": 1.0}, "score": 0.0, "passed": false}',
    "nested_after_real": '{"score": 0.0, "detail": {"score": 1.0}, "passed": false}',
    # The same hole with the top-level key REMOVED. Counting score keys does not
    # close it: there is only one, it is simply not the payload's verdict. Nothing
    # short of tracking nesting depth can tell these from a real 1.0.
    "nested_only": '{"passed": false, "detail": {"score": 1.0}}',
    "nested_in_array": '{"evidence": [{"type": "x", "score": 1.0}], "passed": true}',
    # A verifier killed mid-print (OOM, SIGKILL, a full disk) never finished
    # saying what it meant, however complete the score token happens to look.
    "unclosed_root": '{"score": 1.0, "passed": tr',
    # A diagnostic object printed beside the verdict is not the verdict. The
    # score key here is at depth 1 of its own root, so only "exactly one root"
    # rejects it.
    "second_root": '{"passed": false, "detail": "x"}\n{"score": 1.0}',
    # The same rule pointed the other way. A byte BEFORE the root is the same
    # class as a byte after it: the payload is not one verdict, and the score
    # token sitting in it was never attested as one. Pinned in both directions
    # because a walker that only guards the tail reads this as full marks.
    "noise_before_root": 'INFO running checks\n{"score": 1.0, "passed": true}',
    # The verdict FIRST, a diagnostic object after it. This is the payload that
    # pins the walker's "ok" flag, and second_root does not: there the score sits
    # in the trailing object, so counting the keys already rejects it. Here the
    # score is real and at depth 1 of a properly closed root, and every other
    # gate is satisfied — only "a byte followed the root" stands between it and
    # full marks. Drop that flag and this credits 1.0.
    "verdict_then_diagnostic": '{"score": 1.0}\n{"detail": "x"}',
    # The forgery a high-effort review found: an agent runs text straight into a
    # scoreless verifier's unescaped detail field to abut a fake "score" key with
    # no separator. Two strings cannot sit adjacent in JSON, so json.loads refuses
    # this outright — but a walker that reads "score" wherever it finds a key
    # credits the injected 1.0 the verifier never wrote. The key-position rule (a
    # key follows only "{" or ",") is what refuses it. Distinct from glued_token,
    # which abuts a number and a key; here two strings abut.
    "injected_key_no_separator": '{"detail": "x""score": 1.0}',
    # Same shape, the injection carrying trailing fields to look more like a real
    # verdict. Still no comma before the forged key, so still refused.
    "injected_key_with_tail": '{"passed": false, "detail": "x""score": 1.0, "y": "z"}',
    # A bracket root, which parse_score scored 1.0 standalone until the root was
    # required to be an object. Honest about what this vector does and does not
    # prove: it passes either way, because the ^{ shape gate refuses a non-object
    # root before parse_score's verdict is ever read, so it never pinned the
    # walker. It pins the END-TO-END invariant — this payload is never a verdict,
    # by whichever gate gets there first — and that is worth a row, as long as
    # nobody reads it as covering the walker.
    "array_root": '["score": 1.0]',
    # Anchored grammar: awk coerces "1.0abc" to 1.0, so an unanchored match would
    # score this. strtod takes 1e400 to +inf, which the range check then rejects.
    "trailing_junk": '{"score": 1.0abc, "passed": true}',
    "infinity": '{"score": 1e400, "passed": true}',
    # -inf. Not a separate code path from +inf — there is one comparison, and
    # -0.5 already exercises it — so this is redundant coverage, kept only
    # because it costs 20ms and an infinite token is what a verifier that
    # divided by zero actually prints.
    "negative_infinity": '{"score": -1e400, "passed": false}',
    # A verifier that dropped a comma. The value scan runs THROUGH the quote and
    # hands the grammar 1.0"passed":true, which it refuses. Pinned because the
    # obvious "hardening" — adding the quote to the scan's stop set — truncates
    # this to a clean 1.0 and awards the malformed payload full marks. Tightening
    # the scan loosens the verdict; a reviewer proposed exactly that, and this
    # vector is what caught it.
    "glued_token": '{"score": 1.0"passed": true}',
    # Two score keys at the SAME depth, neither more the verdict than the other.
    # Redundant with nested_after_real, which already forces the key count to be
    # global (drop that gate and it credits the 0.0 it must refuse). Kept as the
    # plainest statement of the rule: a payload that says the score twice has not
    # said it once.
    "duplicate_at_root": '{"score": 0.0, "score": 1.0, "passed": false}',
}


@pytest.mark.parametrize("shape", sorted(NO_READABLE_SCORE))
def test_a_score_the_shell_cannot_read_is_infra_not_a_verdict(
    shape: str, tmp_path: Path, real_path: str
) -> None:
    """Well-formed braces are not a verdict.

    A verifier that cannot state an unambiguous score in [0, 1] has judged
    nothing, and must not be recorded as a legitimate agent failure.
    """
    payload = NO_READABLE_SCORE[shape]
    result = run_test_runner(tmp_path, {"cp": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "cp")
    assert cp["verifier_ran"] is False, f"{payload} carries no readable score"
    assert cp["score"] == 0.0, f"{payload} must not carry a fabricated score"

    guarded = guard_verifier_output(json.dumps(result), returncode=0)
    assert isinstance(guarded, InfraError), (
        f"{payload} was scored as a real result; a verifier that cannot state a "
        f"score has not judged the agent"
    )
    assert guarded.reason == "verifier_did_not_run"


def test_a_tiny_score_is_not_fabricated_into_full_marks(
    tmp_path: Path, real_path: str
) -> None:
    """The over-credit direction: a near-zero score is not full marks.

    ``json.dumps`` emits the exponent form for any small float, so no exotic
    verifier is needed to reach this.
    """
    payload = '{"score": 1e-05, "passed": false, "detail": "1 of 100000"}'
    result = run_test_runner(tmp_path, {"tiny": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "tiny")
    assert cp["verifier_ran"] is True, "a JSON number IS a verdict, however small"
    assert cp["score"] == pytest.approx(1e-05), "the score must be the one the verifier gave"

    guarded = guard_verifier_output(json.dumps(result), returncode=0)
    assert isinstance(guarded, dict), "an exponent is valid JSON; this is a real score"
    # Exactly 0.0, not merely "less than full marks": the shell aggregates with
    # awk's %.4f, so 1e-05 rounds to 0.0000. A looser bound would let a wrong
    # implementation that landed on, say, 0.3 pass.
    assert guarded["task_score"] == 0.0, "1e-05 was credited as full marks"


# The awk cannot import a Python constant, so the [0, 1] tolerance is written out
# twice — once here as _SCORE_EPSILON, once as the literal 1e-6 in test_runner.sh's
# range check — and the shell's comment promises the two admit exactly the same
# values. Nothing structural holds them together, so this derives its payloads FROM
# the Python constant: widen the shell's literal and the just-outside vector starts
# being credited, which fails here. Pinning literals instead would leave the
# duplication free to drift, which is the whole risk.
EPSILON_BOUNDARY = {
    # json.dumps emits the exponent form for a float this small, and the shell
    # prints the token back verbatim, so these round-trip as themselves.
    "just_inside_high": repr(1.0 + _SCORE_EPSILON / 2),
    "just_inside_low": repr(-_SCORE_EPSILON / 2),
    # Two orders of magnitude outside — comfortably clear of float noise, and
    # still far tighter than any plausible widening of the shell's literal.
    "outside_high": repr(1.0 + _SCORE_EPSILON * 100),
    "outside_low": repr(-_SCORE_EPSILON * 100),
}


@pytest.mark.parametrize("shape", sorted(EPSILON_BOUNDARY))
def test_the_shell_admits_exactly_what_the_guard_admits(
    shape: str, tmp_path: Path, real_path: str
) -> None:
    """The shell's tolerance is the guard's tolerance, not merely near it.

    A score a hair outside [0, 1] is float slop from a verifier's own arithmetic
    and is absorbed; one meaningfully outside is a verifier that has miscomputed,
    and absence of a trustworthy verdict is not a verdict. The two must be told
    apart at the same threshold on both sides of the boundary, or the shell hands
    the guard a score the guard would have refused.

    The expectation is not a hand-kept column: it is ``is_valid_score``, the same
    predicate ``guard_verifier_output`` itself consults. So this asserts the two
    implementations agree, rather than that each matches a third table that could
    rot against both.
    """
    token = EPSILON_BOUNDARY[shape]
    admitted = is_valid_score(float(token))
    payload = f'{{"score": {token}, "passed": true, "detail": "boundary"}}'
    result = run_test_runner(tmp_path, {"edge": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "edge")
    assert cp["verifier_ran"] is admitted, (
        f"{payload}: the shell {'refused' if admitted else 'admitted'} a score the "
        f"guard would have {'taken' if admitted else 'refused'} — the 1e-6 literal in "
        f"test_runner.sh has drifted from scorer_guard._SCORE_EPSILON ({_SCORE_EPSILON})"
    )

    guarded = guard_verifier_output(json.dumps(result), returncode=0)
    if admitted:
        assert isinstance(guarded, dict)
        # Not a clamp: guard_verifier_output validates and returns the score
        # untouched (the clamp lives in guard_checkpoint_verdict). The overshoot
        # is gone because the shell aggregates with awk's %.4f, which rounds
        # 1.0000005 to 1.0000 — so an admitted hair over the line cannot reach a
        # caller as a task_score above 1.
        assert 0.0 <= guarded["task_score"] <= 1.0
    else:
        assert isinstance(guarded, InfraError)
        assert guarded.reason == "verifier_did_not_run"


def test_partial_credit_survives_the_parse(tmp_path: Path, real_path: str) -> None:
    """The control the new range check could plausibly break: an ordinary
    fractional score must still be scored, and scored as itself."""
    payload = '{"score": 0.75, "passed": false, "detail": "3 of 4 assertions held"}'
    result = run_test_runner(tmp_path, {"partial": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "partial")
    assert cp["verifier_ran"] is True
    assert cp["score"] == 0.75

    guarded = guard_verifier_output(json.dumps(result), returncode=0)
    assert isinstance(guarded, dict)
    assert guarded["task_score"] == 0.75


# --- the detail string is attacker-controlled, and it is inside the payload ----
#
# Depth-tracking is only as good as its idea of where a string ends: every byte a
# verifier echoes from the agent's answer lands in "detail", so a walker that
# reads INTO strings can be fed a brace that moves the nesting depth or a quoted
# "score" that forges a key. Each vector below carries a REAL score alongside the
# hostile text, so the pin is two-sided — the walker must return the real score,
# neither the forgery (over-credit) nor the empty string (over-reject).

FORGERY_IN_A_STRING = {
    # A quoted score key inside the detail text. Byte-for-byte identical to a real
    # key once the surrounding \" are ignored, which is exactly what a regex does.
    "forged_key": ('{"detail": "\\"score\\": 1.0", "score": 0.0}', 0.0),
    # Braces inside the detail text. Read as structure, they move the depth and
    # push the real key off the top level, where it stops being credited.
    "braces": ('{"detail": "}{ unbalanced", "score": 0.5}', 0.5),
    # A trailing backslash: \\ is an escaped backslash, so the quote AFTER it does
    # close the string. Mishandled, the walker runs on and swallows the real key.
    "trailing_backslash": ('{"detail": "C:\\\\", "score": 0.5}', 0.5),
    # \\u007b is six bytes of text, not a brace. A walker that decodes escapes
    # would open a nesting level here that the payload never closes.
    "unicode_escape": (r'{"detail": "\u007b", "score": 0.5}', 0.5),
    # An escape can only make a key LONGER than the five bytes of score, so the
    # span check rejects the forgery without ever decoding it.
    "escaped_key_bytes": (r'{"\score": 1.0, "score": 0.5}', 0.5),
}


@pytest.mark.parametrize("shape", sorted(FORGERY_IN_A_STRING))
def test_a_string_cannot_forge_or_hide_the_score(
    shape: str, tmp_path: Path, real_path: str
) -> None:
    """Hostile text in a string literal must not change which score is read."""
    payload, expected = FORGERY_IN_A_STRING[shape]
    result = run_test_runner(tmp_path, {"cp": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "cp")
    assert cp["verifier_ran"] is True, f"{payload} carries a real score at the top level"
    assert cp["score"] == expected, (
        f"{payload} was read as {cp['score']}: the string literal changed which "
        f"score the runner credited"
    )


def test_a_pretty_printed_verdict_is_still_a_verdict(
    tmp_path: Path, real_path: str
) -> None:
    """The over-reject direction, and the one legitimate shape most at risk.

    ``json.dumps(..., indent=2)`` puts the closing brace of a trailing score on
    its own line. A line-oriented reader sees no value terminator and scores
    nothing, which would route an ordinary passing checkpoint to the re-run
    channel — a false infra error is as wrong as a false 0.0.
    """
    payload = json.dumps({"passed": True, "score": 1.0, "detail": "ok"}, indent=2)
    result = run_test_runner(tmp_path, {"pretty": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "pretty")
    assert cp["verifier_ran"] is True, "an indented verdict is a verdict"
    assert cp["score"] == 1.0


# --- passed and detail are read at the root too ---------------------------------
#
# score is not the only field the result document is built from. "passed" feeds
# checkpoints_passed and all_passed, and through them the pass-rate reporting in
# analyze_scores.py and scorer_passed in the trace-quality adapter; "detail" is
# embedded into the emitted JSON verbatim. Both sat on an unanchored, nesting-blind
# regex after the score token was hardened against exactly that, so a nested
# diagnostics.passed — which json.dumps emits with no adversary anywhere — was the
# only "passed" the reader could find on the 22 corpus verifiers that state a score
# and no top-level passed (scorer_guard.py:335).
#
# These are pinned separately from the score table because they fail in a different
# DIRECTION. A score the shell cannot read routes to the infra chain: a re-run, a
# safe escape. A passed the shell cannot read is a recorded false — a wrong data
# point that is indistinguishable from a real agent failure. So both directions get
# a vector here, and the over-reject one is what forbids reusing score's
# duplicate-anywhere rule for these keys.


def test_a_nested_passed_does_not_flip_a_zero_checkpoint(
    tmp_path: Path, real_path: str
) -> None:
    """The fabrication direction, on ordinary json.dumps output.

    A verifier that states its score and puts a stage flag in a diagnostics object
    is not doing anything exotic, and it does not need a top-level "passed" — most
    of the corpus does not have one. An unanchored read finds the nested flag and
    credits the checkpoint it was never about.
    """
    payload = (
        '{"score": 0.0, "detail": "assertion failed", '
        '"diagnostics": {"stage": "compile", "passed": true}}'
    )
    result = run_test_runner(tmp_path, {"cp": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "cp")
    assert cp["verifier_ran"] is True, "0.0 is a readable score; this IS a verdict"
    assert cp["score"] == 0.0
    assert cp["passed"] is False, "diagnostics.passed is not the verdict's passed"
    assert result["checkpoints_passed"] == 0
    assert result["all_passed"] is False, (
        "a 0.0 checkpoint was counted as passing: the pass rate is fabricated"
    )


def test_two_passed_keys_do_not_corrupt_the_result_document(
    tmp_path: Path, real_path: str
) -> None:
    """The same blindness, one field over, taking the whole document with it.

    With a real top-level passed AND a nested one, an unanchored read returns both
    matches and the unquoted interpolation emits `"passed": false<newline>true`.
    That fails closed rather than fabricating — the guard routes a malformed
    document to a re-run — but it costs every other checkpoint in the run too.
    ``run_test_runner`` parses stdout, so reaching the asserts at all is the pin.
    """
    payload = '{"score": 0.0, "passed": false, "diagnostics": {"passed": true}}'
    result = run_test_runner(tmp_path, {"cp": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "cp")
    assert cp["passed"] is False, "the top-level passed is the verdict"
    assert cp["score"] == 0.0

    guarded = guard_verifier_output(json.dumps(result), returncode=0)
    assert isinstance(guarded, dict), "a readable 0.0 verdict is not an infra error"


def test_a_passed_stated_twice_at_the_root_is_not_stated(
    tmp_path: Path, real_path: str
) -> None:
    """A payload that says passed twice, at the root, has not said it once.

    Valid JSON, and json.loads quietly keeps the last one — but nothing makes the
    last more the verdict than the first, and no dict can print this, so it comes
    from a verifier building JSON by hand. Falls back to the recorded false.

    This is the vector that pins "exactly one at depth 1" for the keys read with
    strict=0. score's duplicate_at_root looks like it covers the same rule and does
    not: score is read with strict=1, so its duplicate-anywhere gate rejects that
    payload first and the depth-1 count never decides anything.
    """
    payload = '{"score": 1.0, "passed": true, "passed": false}'
    result = run_test_runner(tmp_path, {"cp": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "cp")
    assert cp["score"] == 1.0, "the score is stated once and is not in dispute"
    assert cp["passed"] is False, "an ambiguous passed must not be read as a pass"


def test_a_nested_passed_does_not_reject_a_real_pass(
    tmp_path: Path, real_path: str
) -> None:
    """The over-reject direction — and the vector that forbids score's rule here.

    score treats a duplicate key ANYWHERE as poison, which it can afford because
    its fail-closed direction is a re-run. Apply that rule to passed and this
    entirely legitimate payload records passed=false at score=1.0: a fabricated
    FAIL, the same pass-rate integrity bug as the vector above pointed backwards.
    Scoping to depth 1 fixes both directions at once; counting globally would only
    trade one for the other.
    """
    payload = (
        '{"score": 1.0, "passed": true, "detail": "ok", '
        '"diagnostics": {"stage": "compile", "passed": true}}'
    )
    result = run_test_runner(tmp_path, {"cp": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "cp")
    assert cp["passed"] is True, "a nested passed must not poison the real one"
    assert cp["score"] == 1.0
    assert result["checkpoints_passed"] == 1
    assert result["all_passed"] is True


DETAIL_READ = {
    # The nested-only shape, mirroring NO_READABLE_SCORE["nested_only"]: the key is
    # there, but this payload never made it the verdict's detail. Empty is the right
    # answer — the field is diagnostic, so failing closed costs a debugging aid
    # rather than a grade, which is why it may fail closed at all.
    "nested_only": ('{"score": 0.0, "diagnostics": {"detail": "inner"}}', ""),
    # A nested detail either side of the real one. The mirror pins the rule rather
    # than the reading order — the vector a "take the first match" reader gets
    # wrong is the one a last-match reader gets right, and only depth gets both.
    #
    # Neither is a first-match read in practice, which is worth spelling out: the
    # old extraction passed grep -m1 and looked bounded, but -m1 stops after the
    # first matching LINE, while -o prints EVERY match on it. json.dumps emits one
    # line, so both of these returned two tokens and corrupted the document. The
    # flag read as a fix for exactly the bug it did not fix.
    "nested_before_real": (
        '{"score": 0.0, "diagnostics": {"detail": "inner"}, "detail": "outer"}',
        "outer",
    ),
    "nested_after_real": (
        '{"score": 0.0, "detail": "outer", "diagnostics": {"detail": "inner"}}',
        "outer",
    ),
    # The flat value scan that reads the score token stops at a comma, a space or a
    # brace, so it could not read any of these three at all. They pin the string
    # branch: a detail is taken as a quoted span, by the same escape rule the walker
    # already uses to skip strings, not scanned to the next terminator.
    "comma_and_space": (
        '{"score": 0.0, "detail": "expected 3, got 4"}',
        "expected 3, got 4",
    ),
    # Stated twice at the root: neither one is the detail, for the same reason
    # neither of two top-level "passed" keys is the verdict.
    "duplicate_at_root": ('{"score": 0.0, "detail": "first", "detail": "second"}', ""),
    "escaped_quote": (r'{"score": 0.0, "detail": "got \"x\""}', 'got "x"'),
    "brace": ('{"score": 0.0, "detail": "want {a: 1}"}', "want {a: 1}"),
    "escaped_newline": (r'{"score": 0.0, "detail": "line 1\nline 2"}', "line 1\nline 2"),
    # A RAW tab, which JSON does not allow inside a string. The verifier that
    # printed this already emitted invalid JSON; what the runner must not do is
    # copy it into the document the scorer reads and take the run down with it.
    # Dropped to empty, and the score beside it still lands.
    "raw_control_char": ('{"score": 0.0, "detail": "a\tb"}', ""),
}


@pytest.mark.parametrize("shape", sorted(DETAIL_READ))
def test_detail_is_read_at_the_root_and_survives_verbatim(
    shape: str, tmp_path: Path, real_path: str
) -> None:
    """detail is embedded into the result JSON verbatim, so it has to come back as
    the exact token the verifier wrote, and from the root — not from whatever
    nested object happens to carry the same key name."""
    payload, expected = DETAIL_READ[shape]
    result = run_test_runner(tmp_path, {"cp": verifier_printing(payload)}, real_path)

    cp = checkpoint(result, "cp")
    assert cp["detail"] == expected, f"{payload} read detail as {cp['detail']!r}"
    assert cp["score"] == 0.0, "reading detail must not disturb the score beside it"


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


def test_a_leaked_skeleton_does_not_buy_a_re_run(tmp_path: Path, real_path: str) -> None:
    """The escape hatch the score check opens if it is not held to the same
    policy as its sibling.

    Requiring a READABLE score means a check that chokes on the agent's answer
    and leaks a half-built ``{"score": null}`` on its way down now reaches "no
    verdict" — the branch above, which attributes that to the agent, only looks
    at payloads with no JSON at all. Left there, the two structurally identical
    deaths (garbage answer, no verdict) would score oppositely on nothing but
    whether a skeleton happened to print, and an agent could buy itself a re-run
    by provoking one. Same cause, same attribution: a scored 0.0.
    """
    choker = """\
#!/usr/bin/env bash
set -euo pipefail
printf '{"score": null, "passed": false}\\n'
python3 -c 'import json; json.load(open("'"$WORKSPACE"'/agent_output/answer.json"))'
printf '{"score": 1.0, "passed": true}\\n'
"""
    result = run_test_runner(
        tmp_path, {"choker": choker}, real_path, answer="not json {{{"
    )

    cp = checkpoint(result, "choker")
    assert cp["exit_code"] != 0, "precondition: the answer really does kill the check"
    assert cp["verifier_ran"] is True, "the verifier ran; the agent's answer killed it"
    assert cp["score"] == 0.0

    guarded = guard_verifier_output(json.dumps(result), returncode=1)
    assert not isinstance(guarded, InfraError), (
        "a leaked JSON skeleton is no more a verdict than no output at all — "
        "routing it to the re-run channel lets a garbage answer escape its 0.0"
    )
    assert guarded["task_score"] == 0.0
    assert "not a JSON object" in cp["detail"], "the 0.0 must say where it came from"


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
