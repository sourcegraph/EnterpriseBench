"""Scoring integrity for chain-err-flask-import-001 (EnterpriseBench-e4w15).

The task used to score 1.00/1.00 for zero comprehension: a verbatim copy of the
session prompts into INVESTIGATION.md/FIX_SUMMARY.md plus ONE comment appended
to tests/conftest.py. Three separate defects made that possible.

1. The premise was FABRICATED. The task asserted a cycle
   flask -> flask.json -> flask.globals -> flask.app -> flask.json in Flask
   3.1.0. Two of those four edges do not exist: flask.globals imports flask.app
   only under `if t.TYPE_CHECKING:`, and flask.app never imports flask.json at
   all. `from flask.json import dumps` succeeds; the task's own reported user
   scenario serves HTTP 200. There was no bug, so no check could grade a fix —
   only its footprint.
2. check_fix.sh graded footprint: FIX_SUMMARY.md > 50 bytes AND any *.py file in
   `git diff --name-only HEAD~1`. A comment in a TEST file paid the full 0.40.
3. check_cycle.sh / check_investigation.sh grepped concept vocabulary the
   session prompt already supplied (the prompt stated the cycle verbatim), so an
   echo scored 1.0 — the gen-1 leak class (see test_report_prompt_echo.py).

The re-scope grades the comprehension the fabrication accidentally created: the
claim is stated as unconfirmed triage notes, and the agent must work out which
claimed edges are real. Discrimination is the signal, so blanket answers and
coin flips earn nothing (negative marking), and no diff is graded at all — which
is what removes the comment vector rather than merely tightening it.

This module is the bead's regression test: the uc12m repro (comment-only edit ->
expect 0) is one row of the matrix below.
"""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import tomllib
from itertools import product
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH = REPO_ROOT / "benchmarks"
TASK_DIR = BENCH / "customer_escalation" / "chain-err-flask-import-001"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))
sys.path.insert(0, str(REPO_ROOT / "lib"))
import curated_gate_analyzer as gate  # noqa: E402
from eb_verify.scorer_guard import InfraError, run_verifier_subprocess  # noqa: E402


def _load_toml(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _toml() -> dict:
    return _load_toml(TASK_DIR / "task.toml")


def _ground_truth() -> dict:
    return json.loads((TASK_DIR / "ground_truth.json").read_text())


# --- scoring harness -------------------------------------------------------
#
# Scores the task the way the REAL runners do. The three runners disagree on how
# the workspace reaches a check, and this task's scripts run under all three:
#   runner.py     (checkpoints): no $1, env WORKSPACE+TASK_DIR, cwd=workspace
#   milestone.py  (milestones) : $1 passed, NO env at all, cwd=workspace
#   test_runner.sh (sandbox)   : $1 AND env, cwd deliberately OUTSIDE workspace
# The CONVENTIONS matrix rides only on the tests that assert a NON-ZERO score.
# That is where it carries signal: a check that resolves no convention finds no
# deliverable and scores 0.0, so every "gaming variant -> 0.0" test would pass
# vacuously under exactly the breakage the matrix exists to catch. The two
# correct-answer tests prove resolution works under all three; downstream of
# $WORKSPACE the scoring logic is convention-independent, so re-running the zero
# tests per convention re-tests nothing and just triples the subprocess count.


def _score_one(verifier: Path, workspace: Path, convention: str, cwd: Path) -> float:
    argv = ["bash", str(verifier)]
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)}
    if convention in ("milestone", "sandbox"):
        argv.append(str(workspace))
    if convention in ("runner", "sandbox"):
        env["WORKSPACE"] = str(workspace)
        env["TASK_DIR"] = str(TASK_DIR)
    out = subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, env=env, timeout=120
    )
    if not out.stdout.strip():
        raise AssertionError(f"{verifier.name} printed no verdict: {out.stderr}")
    return float(json.loads(out.stdout.strip())["score"])


def score(workspace: Path, convention: str, tmp_path: Path) -> float:
    """Weighted task score in [0,1] under one runner convention."""
    # cwd=workspace for the two host runners; outside it for the sandbox.
    cwd = workspace if convention in ("runner", "milestone") else tmp_path
    total = 0.0
    for cp in _toml()["checkpoints"]:
        s = _score_one(TASK_DIR / cp["verifier"], workspace, convention, cwd)
        total += s * cp["weight"]
    return round(total, 4)


CONVENTIONS = ["runner", "milestone", "sandbox"]


# --- payloads --------------------------------------------------------------


def _session_prompts() -> str:
    return "\n".join(s["prompt"] for s in _toml()["sessions"])


def _make_ws(tmp_path: Path, name: str) -> Path:
    ws = tmp_path / name
    (ws / "flask").mkdir(parents=True)
    (ws / "agent_output").mkdir(parents=True)
    return ws


def _write(
    ws: Path, *, investigation: str, cycle_verdict: str, resolution: str
) -> Path:
    """Write this task's three deliverables into `ws/agent_output/`.

    Deliverables were migrated out of `ws/flask/` (mode_gate locks repo dirs in
    gated arms; agent_output is the one location every arm leaves writable) --
    scripts/authoring/migrate_deliverable_to_agent_output.py. `ws/flask/` stays
    (created by _make_ws) as the repo tree the checks may still scan for source
    evidence.

    Named for the deliverables rather than taking arbitrary filenames: the task
    grades exactly these three, and every caller writes all three, so a missing
    one is a typo the signature should catch rather than a workspace that quietly
    scores 0.0 for the wrong reason.
    """
    for name, content in (
        ("INVESTIGATION.md", investigation),
        ("CYCLE_VERDICT.json", cycle_verdict),
        ("RESOLUTION.json", resolution),
    ):
        (ws / "agent_output" / name).write_text(content)
    return ws


CORRECT_INVESTIGATION = """# Investigation: the reported import cycle

I checked each edge the triage notes claim against src/flask/ at 3.1.0.

- flask -> flask.json: REAL. src/flask/__init__.py imports `from . import json as json`
  at module level.
- flask.json -> flask.globals: REAL. src/flask/json/__init__.py does
  `from ..globals import current_app` at module level.
- flask.globals -> flask.app: NOT a runtime import. src/flask/globals.py only does
  `from .app import Flask` inside `if t.TYPE_CHECKING:`, which the interpreter never
  executes. Its actual runtime imports are __future__, typing, contextvars
  (ContextVar) and werkzeug.local (LocalProxy). The globals are LocalProxy objects
  over ContextVar, so no Flask class is needed at import time.
- flask.app -> flask.json: ABSENT. src/flask/app.py does not import flask.json at all,
  not even under a type-checking guard.

The chain therefore never closes: two of the four claimed edges do not exist at
runtime. Exercising it confirms this -- `from flask.json import dumps` imports
cleanly and the reported blueprint scenario serves a normal response.
"""

CORRECT_VERDICT = json.dumps(
    {
        "claimed_edges": [
            {"from": "flask", "to": "flask.json", "imported_at_runtime": True},
            {"from": "flask.json", "to": "flask.globals", "imported_at_runtime": True},
            {"from": "flask.globals", "to": "flask.app", "imported_at_runtime": False},
            {"from": "flask.app", "to": "flask.json", "imported_at_runtime": False},
        ]
    },
    indent=2,
)

CORRECT_RESOLUTION = json.dumps(
    {
        "code_change_required": False,
        "reason": (
            "The reported cycle does not exist, so there is nothing to break. "
            "flask.globals imports flask.app only under a TYPE_CHECKING guard and "
            "flask.app never imports flask.json, so the chain never closes. Patching "
            "working code to defer an import that already is not there would add risk "
            "and fix nothing; the customer's ImportError has another cause and needs "
            "their actual traceback."
        ),
    },
    indent=2,
)


def correct_ws(tmp_path: Path) -> Path:
    ws = _make_ws(tmp_path, "correct")
    return _write(
        ws,
        investigation=CORRECT_INVESTIGATION,
        cycle_verdict=CORRECT_VERDICT,
        resolution=CORRECT_RESOLUTION,
    )


# --- the matrix ------------------------------------------------------------


@pytest.mark.parametrize("convention", CONVENTIONS)
def test_correct_answer_scores_high(tmp_path: Path, convention: str) -> None:
    """The refutation -- the reported cycle is not real -- is the correct answer.

    A task where the correct answer cannot score is not fixed, it is un-passable
    (see the note in test_report_prompt_echo.py). This is the half that keeps the
    zero-scoring tests below honest.
    """
    got = score(correct_ws(tmp_path), convention, tmp_path)
    assert got >= 0.9, f"correct answer scored {got} under {convention}"


def test_prompt_echo_scores_zero(tmp_path: Path) -> None:
    """The defect that opened the bead: echoing the REAL prompt (task.toml
    [[sessions]].prompt, not the instruction.md stub) used to score 0.84."""
    echo = _session_prompts()
    ws = _write(
        _make_ws(tmp_path, "echo"),
        investigation=echo,
        cycle_verdict=echo,
        resolution=echo,
    )
    got = score(ws, "runner", tmp_path)
    assert got == 0.0, f"prompt echo scored {got}"


def test_comment_only_patch_scores_zero(tmp_path: Path) -> None:
    """The uc12m repro. Echo + one comment in tests/conftest.py scored 1.00/1.00:
    check_fix.sh took bare changed-file membership from `git diff HEAD~1`, so a
    comment in a TEST file paid the full 0.40 fix weight. No diff is graded now."""
    echo = _session_prompts()
    ws = _write(
        _make_ws(tmp_path, "comment"),
        investigation=echo,
        cycle_verdict=echo,
        resolution=echo,
    )
    repo = ws / "flask"
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text("import pytest\n")
    for cmd in (
        ["git", "init", "-q", "."],
        ["git", "config", "user.email", "t@t.t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / "tests" / "conftest.py").write_text("import pytest\n# a comment\n")
    for cmd in (["git", "add", "-A"], ["git", "commit", "-qm", "comment only"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)

    got = score(ws, "runner", tmp_path)
    assert got == 0.0, f"comment-only patch scored {got}"


ALL_TRUE_VERDICT = json.dumps(
    {
        "claimed_edges": [
            {"from": f, "to": t, "imported_at_runtime": True}
            for f, t in (
                ("flask", "flask.json"),
                ("flask.json", "flask.globals"),
                ("flask.globals", "flask.app"),
                ("flask.app", "flask.json"),
            )
        ]
    }
)

# The two ways to assert the cycle without having looked: restate the task's own
# old answer key, or rebuild it fluently from words the prompt handed over. The
# gen-1 checks scored both 1.0. They are one test because they are one behaviour --
# measured, not assumed: both produce the identical score vector
# {investigation 0.0, cycle 0.0, resolution 0.0}, for the identical reasons.
FABRICATIONS = {
    "old_answer_key": (
        "# Circular Import Investigation\n\n## Import Cycle\n"
        "flask/__init__.py -> flask/json/__init__.py -> flask/globals.py -> "
        "flask/app.py -> flask/__init__.py\n\n## Affected Modules\n"
        "1. flask/__init__.py\n2. flask/json/__init__.py\n3. flask/globals.py\n"
        "4. flask/app.py\n\n## Severity\nMedium - triggered when importing "
        "flask.json at module level.\n\n## Workaround\nImport inside functions.\n",
        "Modified flask/json/__init__.py to use a lazy import for flask.globals, "
        "breaking the circular dependency.",
    ),
    "prompt_vocabulary": (
        "# Circular Import Investigation\n\nThe import chain flask -> "
        "flask.json -> flask.globals -> flask.app -> flask.json forms a "
        "circular import. The modules involved are flask/__init__.py, "
        "flask/json/__init__.py, flask/globals.py and flask/app.py. The "
        "symbols that trigger the cycle are dumps and current_app. Severity "
        "is medium; the workaround is to import flask.json inside functions "
        "rather than at module level.\n",
        "Break the cycle with a lazy import in flask/json/__init__.py.",
    ),
}


@pytest.mark.parametrize("variant", sorted(FABRICATIONS), ids=sorted(FABRICATIONS))
def test_fabricated_cycle_scores_zero(tmp_path: Path, variant: str) -> None:
    """Asserting the reported cycle is real must earn nothing, however fluently.

    old_answer_key is the task's OWN previous reference answer, which encoded the
    fabrication -- it is now the thing the task exists to catch. prompt_vocabulary
    is the same claim rebuilt from the prompt's words with the right shape and no
    reading.
    """
    investigation, reason = FABRICATIONS[variant]
    ws = _write(
        _make_ws(tmp_path, f"fabricated_{variant}"),
        investigation=investigation,
        cycle_verdict=ALL_TRUE_VERDICT,
        resolution=json.dumps(
            {"code_change_required": True, "reason": reason}
        ),
    )
    got = score(ws, "runner", tmp_path)
    assert got == 0.0, f"{variant} fabrication scored {got}"




# The head of src/flask/globals.py at flask 3.1.0, verbatim. This IS the dump vector:
# every investigation_evidence token AND the sole resolution_correct token appear in
# these few lines of the one file the session-1 prompt names as an edge endpoint, so a
# `cat` of it into the two free-text deliverables scored 1.0 on both -- 0.60 of the
# task for a file copy. `head -8` pays exactly the same, which is why a length
# threshold is not the fix, and a multi-file cat pays any token drawn from source,
# which is why respreading the tokens is not the fix either (EnterpriseBench-e4w15).
GLOBALS_HEAD = '''from __future__ import annotations

import typing as t
from contextvars import ContextVar

from werkzeug.local import LocalProxy

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import Flask
'''


def _dump_ws(tmp_path: Path, name: str, cycle_verdict: str) -> Path:
    """The dump payload in both free-text deliverables, with a caller-chosen verdict."""
    return _write(
        _make_ws(tmp_path, name),
        investigation=GLOBALS_HEAD,
        cycle_verdict=cycle_verdict,
        resolution=json.dumps(
            {"code_change_required": False, "reason": GLOBALS_HEAD}
        ),
    )


def test_the_graded_tokens_really_are_co_located_in_one_file() -> None:
    """Pins the premise of the two tests below. If a future edit respreads the tokens
    so this fails, the dump tests stop testing the dump vector and start passing for a
    reason nobody chose -- re-read them before touching this."""
    ev = _ground_truth()["scoring_evidence"]
    tokens = ev["investigation_evidence"] + ev["resolution_correct"]
    assert tokens and all(t.lower() in GLOBALS_HEAD.lower() for t in tokens), (
        f"every graded token must live in the globals.py head for the dump vector to "
        f"exist: {tokens}"
    )


def test_source_dump_scores_zero(tmp_path: Path) -> None:
    """The second blocking defect: non-prompt was necessary and NOT sufficient.

    `cat src/flask/globals.py` into INVESTIGATION.md and into RESOLUTION.json's
    free-text `reason` paid 3/3 and 1/1 tokens = 0.60 of the task, guaranteed, for zero
    comprehension -- the same diagnosis the bead makes of the deleted check_fix.sh
    ("cannot distinguish a fix from a comment because it never looks at the code's
    meaning") applied verbatim to check_investigation.sh: it could not distinguish an
    investigation from a cat. Both checkpoints now gate on the verdict, which no dump
    produces.
    """
    ws = _dump_ws(tmp_path, "dump_blanket", ALL_TRUE_VERDICT)
    got = score(ws, "runner", tmp_path)
    assert got == 0.0, f"source dump scored {got}"


def test_the_dump_is_zeroed_by_the_gate_and_not_by_a_missing_file(
    tmp_path: Path,
) -> None:
    """Positive control for the test above, and the recorded residual, in one.

    A check that never finds its deliverable also returns 0.0, so the assertion above
    would pass for the wrong reason in exactly the failure mode it exists to detect.
    The SAME dump bytes, behind a CORRECT verdict, must still pay their tokens: that
    proves the payload is found and matched, so the 0.0 above is the gate biting and
    nothing else.

    It is also the residual stated in ground_truth._residual_risk, made executable
    rather than merely asserted: past the gate, investigation_evidence credits citation
    and not exposition, so an agent that genuinely discriminated all four edges may
    still paste the source instead of writing prose. Accepted deliberately -- judging
    prose is not a deterministic check -- and bounded, because it pays only agents that
    already demonstrated the comprehension the task measures.
    """
    ws = _dump_ws(tmp_path, "dump_correct", CORRECT_VERDICT)
    got = score(ws, "runner", tmp_path)
    assert got == 1.0, (
        f"the dump scored {got} behind a correct verdict — it should still pay its "
        f"tokens, so test_source_dump_scores_zero is passing for the wrong reason"
    )


@pytest.mark.parametrize("blanket", [True, False], ids=["all_true", "all_false"])
def test_blanket_edge_verdicts_score_zero(tmp_path: Path, blanket: bool) -> None:
    """Negative marking. 2 of 4 claimed edges are real, so any blanket answer is
    2 right and 2 wrong. Fraction-correct would pay 0.5 of the weightiest
    checkpoint for zero work -- and 'the premise is flagged unconfirmed, so deny
    everything' is exactly the strategy the framing invites. It must pay nothing."""
    ws = _write(
        _make_ws(tmp_path, f"blanket_{blanket}"),
        investigation="# Report\n\nNothing was checked.\n" * 5,
        cycle_verdict=json.dumps(
            {
                "claimed_edges": [
                    {"from": f, "to": t, "imported_at_runtime": blanket}
                    for f, t in (
                        ("flask", "flask.json"),
                        ("flask.json", "flask.globals"),
                        ("flask.globals", "flask.app"),
                        ("flask.app", "flask.json"),
                    )
                ]
            }
        ),
        resolution=json.dumps({"code_change_required": not blanket, "reason": "."}),
    )
    got = score(ws, "runner", tmp_path)
    assert got == 0.0, f"blanket-{blanket} scored {got}"




def test_documented_guessing_expectation_is_what_the_checks_actually_pay(
    tmp_path: Path,
) -> None:
    """Enumerate the best no-comprehension strategy against the REAL checks.

    ground_truth._residual_risk states two numbers, and this computes both rather than
    trusting them. The previous iteration shipped a flattering claim in that exact
    field -- "with negative marking a coin flip is worth 0 in expectation", when
    exhaustive enumeration gives 0.1875 -- and a number in _residual_risk that no test
    computes is an assertion, not a measurement. Overclaiming in the residual-risk field
    of a bead about unearned scores is its own small version of the bug.

    Strategy enumerated: dump globals.py into both free-text deliverables (pays every
    token), answer "no change" deliberately (the correct decision, taken for free), and
    guess all four edges. That is the ceiling for an agent that reads nothing.

    The gate lowers the MEAN without raising the TAIL: P(1.0) is 1/16 either way,
    because a perfect score always required a perfect verdict, which already maxed
    cycle_discrimination. Trading a guaranteed 0.60 for nothing is the whole point.
    """
    edges = [(e["from"], e["to"]) for e in _ground_truth()["claimed_edges"]]
    totals = [
        score(
            _dump_ws(
                tmp_path,
                f"guess_{i}",
                json.dumps(
                    {
                        "claimed_edges": [
                            {"from": f, "to": t, "imported_at_runtime": g}
                            for (f, t), g in zip(edges, guess)
                        ]
                    }
                ),
            ),
            "runner",
            tmp_path,
        )
        for i, guess in enumerate(product([True, False], repeat=len(edges)))
    ]

    # len(totals) == 2 ** len(edges) holds by construction; 16 is the claim -- the
    # documented numbers below are for FOUR claimed edges.
    assert len(totals) == 16, f"{len(edges)} claimed edges, not 4: {edges}"
    mean = round(sum(totals) / len(totals), 4)
    assert mean == 0.1125, (
        f"E[score] for the best no-comprehension strategy is {mean}, but "
        f"ground_truth._residual_risk documents 0.1125 (it was 0.675 ungated). Fix the "
        f"scoring or fix the recorded number — do not leave the flattering one."
    )
    assert sum(1 for t in totals if t == 1.0) == 1, (
        f"P(score == 1.0) under guessing must stay 1/16 — the documented tail: {totals}"
    )


def test_empty_workspace_scores_zero(tmp_path: Path) -> None:
    got = score(_make_ws(tmp_path, "empty"), "runner", tmp_path)
    assert got == 0.0, f"empty workspace scored {got}"


def test_empty_deliverables_score_zero(tmp_path: Path) -> None:
    ws = _write(
        _make_ws(tmp_path, "blank"),
        investigation="",
        cycle_verdict="",
        resolution="",
    )
    got = score(ws, "runner", tmp_path)
    assert got == 0.0, f"empty deliverables scored {got}"


@pytest.mark.parametrize("convention", CONVENTIONS)
def test_correct_answer_tolerates_path_spellings_and_extra_edges(
    tmp_path: Path, convention: str
) -> None:
    """A correct answer must not be failed by cosmetics. Module identity arrives
    as flask.globals, flask/globals.py or src/flask/globals.py, and an agent may
    also report the real (benign) cycles it found. Only the four CLAIMED edges
    are graded; extras are ignored."""
    ws = _write(
        _make_ws(tmp_path, "spellings"),
        investigation=CORRECT_INVESTIGATION,
        cycle_verdict=json.dumps(
            {
                "claimed_edges": [
                    {
                        "from": "src/flask/__init__.py",
                        "to": "flask/json/__init__.py",
                        "imported_at_runtime": True,
                    },
                    {
                        "from": "flask/json/__init__.py",
                        "to": "src/flask/globals.py",
                        "imported_at_runtime": True,
                    },
                    {
                        # a leading "./" is a natural repo-relative spelling, and
                        # naive path-joining emits "././"; it normalized to
                        # "..flask.globals" and silently missed the key
                        "from": "./flask/globals.py",
                        "to": "flask/app.py",
                        "imported_at_runtime": False,
                    },
                    {
                        "from": "flask.app",
                        "to": "flask.json",
                        "imported_at_runtime": False,
                    },
                    # a real cycle the agent noticed; not one of the claims
                    {"from": "flask", "to": "flask.app", "imported_at_runtime": True},
                ]
            }
        ),
        resolution=CORRECT_RESOLUTION,
    )
    got = score(ws, convention, tmp_path)
    assert got >= 0.9, f"correct answer with alternate spellings scored {got}"


def test_correct_answer_tolerates_the_workspace_layout_spellings(
    tmp_path: Path,
) -> None:
    """A CORRECT verdict scored 0.0 on the heaviest checkpoint, for its spelling.

    The other blocking defect, and the opposite failure to the dump: norm() stripped
    only a LEADING "src/", but the repo mounts at /workspace/flask/ with the package at
    src/flask/. So the workspace-relative path is flask/src/flask/globals.py -- the
    repo directory shares the package's name -- and the absolute path the session-1
    prompt ITSELF supplies ("checked out at /workspace/flask/") is
    /workspace/flask/src/flask/globals.py. Both normalized to a key miss
    (flask.src.flask.globals, workspace.flask.src.flask.globals), and negative marking
    then floored a fully correct answer to 0.0 -- indistinguishable from blanket-wrong.

    The task's own layout produces these two spellings, and the existing spelling test
    covered only spellings that already passed. Same class as the "./" bug, one step
    out (EnterpriseBench-e4w15).

    Not parametrized over CONVENTIONS, per the harness note above: what regressed is
    norm(), which lives downstream of $WORKSPACE and is convention-independent, and the
    two correct-answer tests already prove resolution under all three runners.
    """
    ws = _write(
        _make_ws(tmp_path, "layout_spellings"),
        investigation=CORRECT_INVESTIGATION,
        cycle_verdict=json.dumps(
            {
                "claimed_edges": [
                    {
                        # absolute, exactly as the prompt hands the checkout over
                        "from": "/workspace/flask/src/flask/__init__.py",
                        "to": "/workspace/flask/src/flask/json/__init__.py",
                        "imported_at_runtime": True,
                    },
                    {
                        # workspace-relative: repo dir + package dir, both "flask"
                        "from": "flask/src/flask/json/__init__.py",
                        "to": "flask/src/flask/globals.py",
                        "imported_at_runtime": True,
                    },
                    {
                        "from": "/workspace/flask/src/flask/globals.py",
                        "to": "/workspace/flask/src/flask/app.py",
                        "imported_at_runtime": False,
                    },
                    {
                        "from": "flask/src/flask/app.py",
                        "to": "flask/src/flask/json/__init__.py",
                        "imported_at_runtime": False,
                    },
                ]
            }
        ),
        resolution=CORRECT_RESOLUTION,
    )
    got = score(ws, "runner", tmp_path)
    assert got >= 0.9, (
        f"correct answer spelled the way this workspace's own layout produces "
        f"scored {got}"
    )


def _func_ast(check: str, name: str) -> ast.FunctionDef:
    """A function defined inside a check's `python3 -I -c` heredoc, unevaluated."""
    body = (TASK_DIR / "checks" / check).read_text().split("python3 -I -c '")[1]
    tree = ast.parse(body.rsplit("'", 1)[0])
    return next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name
    )


def _func_from(check: str, name: str, imports: str = "import os"):
    """A helper defined in a check's heredoc, compiled so this module can call it.

    `imports` supplies the module-level names the extracted function closes over (the
    heredoc has `import json, os` at the top; a lone function pulled out of it does not).
    """
    ns: dict = {}
    exec(imports, ns)
    exec(
        compile(ast.Module(body=[_func_ast(check, name)], type_ignores=[]), check, "exec"),
        ns,
    )
    return ns[name]


def _norm_from(check: str):
    """That check's norm(), compiled so this module can call it."""
    return _func_from(check, "norm")


# Every function the staging contract forces us to copy, and where it lives.
# check_cycle.sh scores the verdict inline, so it has no verdict_is_fully_correct().
DUPLICATED = {
    "norm": ("check_cycle.sh", "check_investigation.sh", "check_resolution.sh"),
    "open_agent_file": ("check_cycle.sh", "check_investigation.sh", "check_resolution.sh"),
    "verdict_is_fully_correct": ("check_investigation.sh", "check_resolution.sh"),
}


@pytest.mark.parametrize("func", sorted(DUPLICATED), ids=sorted(DUPLICATED))
def test_the_duplicated_check_helpers_are_identical(func: str) -> None:
    """The gate forced norm() and verdict_is_fully_correct() into more than one check,
    and the duplication is the staging contract's price, not an oversight: run_task.py
    copies only checks/*.sh, each individually, into a flat .verifiers/, so a shared
    library is either never copied (outside checks/) or is itself run as a check at
    weight 1.0 (inside checks/, tripping test_every_check_is_claimed_by_a_checkpoint).

    "Keep the copies identical" is a comment, and comments do not fail CI. A future
    editor who fixes a normalization bug in one copy would leave the gates disagreeing
    with the checkpoint about which module an edge names — the gate would deny evidence
    for a verdict check_cycle.sh itself scored correct, which is the one disagreement
    this design cannot tolerate. Compare the code, ignoring docstrings: the prose
    differs on purpose (check_cycle.sh carries the reasoning, the copies point at it).
    """
    checks = DUPLICATED[func]
    bodies = {}
    for check in checks:
        fn = _func_ast(check, func)
        # Drop the docstring, and only the docstring: stripping every top-level
        # ast.Expr would also silently ignore a bare call added to one copy.
        code = fn.body[1:] if ast.get_docstring(fn) else fn.body
        bodies[check] = ast.dump(ast.Module(body=code, type_ignores=[]))
    reference = checks[0]
    diverged = [c for c, b in bodies.items() if b != bodies[reference]]
    assert not diverged, (
        f"{func}() in {diverged} has diverged from {reference}; the gates and the "
        f"checkpoint would disagree about which module an edge names"
    )


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("flask.globals", "flask.globals"),
        ("flask/globals.py", "flask.globals"),
        ("./flask/globals.py", "flask.globals"),
        ("src/flask/globals.py", "flask.globals"),
        # the two the workspace layout actually produces
        ("flask/src/flask/globals.py", "flask.globals"),
        ("/workspace/flask/src/flask/globals.py", "flask.globals"),
        ("/workspace/flask/src/flask/json/__init__.py", "flask.json"),
        ("src.flask.globals", "flask.globals"),
        ("flask\\globals.py", "flask.globals"),
        # the case that makes the "/__init__.py" branch load-bearing rather than dead:
        # without it the cut lands on the trailing "src" and the __init__ pop empties
        # the list, so the edge normalizes to "" and silently vanishes.
        ("flask/src/__init__.py", "flask.src"),
        # a TRAILING "src" names a module, not the package root. Cutting there
        # collapsed these to "", and two empty endpoints make an edge that is not in
        # the key, which negative marking charges as unanswered.
        ("flask/src.py", "flask.src"),
        ("src", "src"),
    ],
)
def test_norm_collapses_spellings_without_collapsing_modules(
    spelling: str, expected: str
) -> None:
    assert _norm_from("check_cycle.sh")(spelling) == expected


# --- the gate that should have caught this ---------------------------------


def test_chain_echo_payload_is_the_session_prompts_not_the_stub() -> None:
    """Locks the blind spot closed. curated_gate_analyzer copied instruction.md
    as the echo payload, but this task is session_type='chain': its real prompts
    live in task.toml [[sessions]].prompt and its instruction.md was a 4-line
    stub pointing at them. So echo_leak() returned {} ('clean') while the real
    session-prompt echo scored 1.00, and known_prompt_echo_leaks.json stayed [].
    Chain tasks were structurally invisible to the echo gate."""
    payload = gate.agent_prompt_text(TASK_DIR)
    for session in _toml()["sessions"]:
        assert session["prompt"].strip() in payload, (
            "chain echo payload must be the session prompts the agent is really "
            "shown, not the instruction.md stub"
        )


def test_non_chain_payload_is_still_instruction_md() -> None:
    """The chain fix must not change any other task's payload. [task].prompt is
    stale CSB metadata that is never shown to an agent, so resolving it here
    would manufacture phantom leaks across ~155 tasks.

    Swept over EVERY non-chain task rather than one hand-picked fixture. A single
    named task would need a skip-if-absent guard, and this same commit deletes one
    of those from test_grading_asset_seal.py for eroding coverage silently — a
    guard that skips itself when its fixture moves is not a guard.
    """
    checked = 0
    for toml_path in sorted(BENCH.rglob("task.toml")):
        d = toml_path.parent
        if {"_archived", "mined"} & set(d.relative_to(BENCH).parts):
            continue
        instr = d / "instruction.md"
        if not instr.exists() or _load_toml(toml_path).get("sessions"):
            continue
        assert gate.agent_prompt_text(d) == instr.read_text(errors="replace"), (
            f"{d.relative_to(BENCH)} is not a chain task, so its payload must "
            f"still be instruction.md verbatim"
        )
        checked += 1
    assert checked >= 100, (
        f"only {checked} non-chain tasks checked; the sweep is passing vacuously"
    )


def test_echo_gate_now_sees_this_task_as_clean() -> None:
    """End-to-end: the gate's own verdict on the re-scoped task."""
    assert gate.echo_leak(TASK_DIR) == {}


def test_run_checks_actually_reaches_a_workspace_resolving_check(
    tmp_path: Path,
) -> None:
    """Positive control for the gate's OTHER vacuity, which no clean verdict can
    distinguish from a real pass.

    run_checks exported $WORKSPACE but passed no argv[1] and no cwd. A check that
    resolves `WORKSPACE="${1:-.}"` shadows the exported variable with the shell
    assignment, so it read cwd — the repo root — and scored 0.0 for ANY payload.
    Every such task was certified clean because nothing could score, not because an
    echo earned nothing. That is indistinguishable from a real pass at the call
    site, so this drives a probe check that scores 1.0 ONLY if it can actually see
    the materialized deliverable, under the same `${1:-.}` idiom the shipped
    gen-1 checks use (EnterpriseBench-e4w15).
    """
    task = tmp_path / "probe_task"
    (task / "checks").mkdir(parents=True)
    (task / "instruction.md").write_text("probe payload")
    (task / "checks" / "check_probe.sh").write_text(
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        'WORKSPACE="${1:-.}"\n'
        'if [ -f "$WORKSPACE/report/FINDINGS.md" ]; then\n'
        '  echo \'{"score": 1.0, "message": "saw the workspace"}\'\n'
        'else\n'
        '  echo \'{"score": 0.0, "message": "resolved cwd, not the workspace"}\'\n'
        'fi\n'
    )
    assert gate.deliverable_paths(task) == ["report/FINDINGS.md"]

    ws = tmp_path / "ws"
    gate.materialize(ws, ["report/FINDINGS.md"], "anything")
    scores = gate.run_checks(task, ws)
    assert scores == {"check_probe.sh": 1.0}, (
        "run_checks did not deliver the workspace to a ${1:-.} check — the gate is "
        f"scoring something else and certifying tasks clean vacuously: {scores}"
    )


def test_task_is_inside_the_echo_gate_universe() -> None:
    """deliverable_paths() drives discover_report_tasks(), which parametrizes the
    suite-wide echo test. A check that stops spelling the deliverable as a literal
    $WORKSPACE/... path silently drops the task from that sweep -- passing CI by
    becoming invisible rather than by being clean."""
    assert TASK_DIR in gate.discover_report_tasks()
    assert set(gate.deliverable_paths(TASK_DIR)) == {
        "agent_output/CYCLE_VERDICT.json",
        "agent_output/INVESTIGATION.md",
        "agent_output/RESOLUTION.json",
    }


# --- the scoring trust boundary: a broken grader must never score ----------


CHECK_FOR = {
    "INVESTIGATION.md": "check_investigation.sh",
    "CYCLE_VERDICT.json": "check_cycle.sh",
    "RESOLUTION.json": "check_resolution.sh",
}


def _raw(
    check: str, ws: Path, base: Path = TASK_DIR
) -> subprocess.CompletedProcess:
    """Drive one check the way runner.py does, without parsing its verdict.

    `base` supplies both the script location and the TASK_DIR env, so a test can
    point at a temp copy of the task with a corrupted ground_truth.json.
    """
    return subprocess.run(
        ["bash", str(base / "checks" / check)],
        cwd=str(ws),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(ws),
            "WORKSPACE": str(ws),
            "TASK_DIR": str(base),
        },
        timeout=120,
    )


GATED_CHECKS = ["check_investigation.sh", "check_resolution.sh"]


@pytest.mark.parametrize("check", GATED_CHECKS)
def test_a_corrupt_answer_key_is_an_infra_error_even_with_no_verdict_to_gate_on(
    tmp_path: Path, check: str
) -> None:
    """A broken grader must never be recorded as a real agent failure.

    lib/eb_verify/scorer_guard.py's boundary: a score is valid only if the pristine
    verifier ran on real agent output. An infra failure must surface as
    verifier_infra_error and route to the re-run channel; it must never bank a
    legitimate 0.0.

    THIS IS A REGRESSION THE GATE INTRODUCED, and it is why the gate's guards live in
    python rather than beside the others in the bash preamble. The first version
    pre-checked CYCLE_VERDICT.json in bash, which runs BEFORE python parses the answer
    key — so a corrupt ground_truth.json plus a missing verdict returned
    {"score": 0.0, "detail": "No gradeable CYCLE_VERDICT.json"} where the pre-gate check
    had returned VERIFIER_INFRA_ERROR. The grader was broken and the agent was charged
    for it.

    The scenario is exact and the test is worthless without both halves: the gate's input
    MISSING (so the bash pre-check would fire) and the key CORRUPT (so python would have
    blamed the key). The check's OWN deliverable is present, so nothing else can
    legitimately score 0 first — a first draft of this test left CYCLE_VERDICT.json in
    place and passed against the buggy code, because the pre-check never fired.

    Only the two GATED checks: check_cycle.sh owns CYCLE_VERDICT.json, so for it a
    missing file is a real 0.0 that legitimately precedes the key. That ordering is
    pre-existing and not this bead's (see the note in _residual_risk).
    """
    task_gt = tmp_path / "task"
    (task_gt / "checks").mkdir(parents=True)
    (task_gt / "ground_truth.json").write_text("{ this is not valid json !!!")
    for c in CHECK_FOR.values():
        (task_gt / "checks" / c).write_text((TASK_DIR / "checks" / c).read_text())

    ws = _make_ws(tmp_path, f"corrupt_key_{check}")
    (ws / "agent_output" / "INVESTIGATION.md").write_text(CORRECT_INVESTIGATION)
    (ws / "agent_output" / "RESOLUTION.json").write_text(CORRECT_RESOLUTION)
    # deliberately NO CYCLE_VERDICT.json — the gate has nothing to read
    assert not (ws / "agent_output" / "CYCLE_VERDICT.json").exists()

    out = _raw(check, ws, base=task_gt)
    assert out.returncode == 0, f"{check} crashed instead of a verdict: {out.stderr}"
    verdict = json.loads(out.stdout.strip())
    assert "VERIFIER_INFRA_ERROR" in verdict["detail"], (
        f"{check} recorded a corrupt answer key as a legitimate score "
        f"{verdict['score']} instead of routing it to the re-run channel: {verdict}"
    )


@pytest.mark.parametrize(
    ("deliverable", "check"), sorted(CHECK_FOR.items()), ids=sorted(CHECK_FOR)
)
def test_an_unreadable_deliverable_still_returns_a_verdict(
    tmp_path: Path, deliverable: str, check: str
) -> None:
    """The agent owns its deliverables and can chmod 000 them.

    The bash `wc -c <"$FILE"` size guard fails OPEN on a permission error — the redirect
    fails, the command substitution is empty, and `[ "" -gt N ]` is merely false — so
    execution fell through to an unguarded open() and the check died with a bare
    traceback, no verdict, exit 1. run_verifier_subprocess reads a missing verdict as an
    InfraError and routes it to the re-run channel, so an agent could mint itself a
    re-run on demand by making its own file unreadable.

    Every path must print exactly one verdict and exit 0. An unreadable deliverable is
    the agent's failure, so it scores 0.0 — it is not a broken grader (EnterpriseBench-e4w15).
    """
    ws = _make_ws(tmp_path, f"unreadable_{check}")
    _write(
        ws,
        investigation=CORRECT_INVESTIGATION,
        cycle_verdict=CORRECT_VERDICT,
        resolution=CORRECT_RESOLUTION,
    )
    target = ws / "agent_output" / deliverable
    target.chmod(0o000)
    try:
        out = _raw(check, ws)
    finally:
        target.chmod(0o644)

    assert out.returncode == 0, (
        f"{check} exited {out.returncode} on an unreadable {deliverable} instead of "
        f"returning a verdict — that is an agent-triggerable re-run: {out.stderr[-300:]}"
    )
    assert out.stdout.strip(), f"{check} printed no verdict at all: {out.stderr[-300:]}"
    verdict = json.loads(out.stdout.strip())
    assert verdict["score"] == 0.0, verdict
    assert "VERIFIER_INFRA_ERROR" not in verdict["detail"], (
        f"an agent making its OWN file unreadable is not a grader failure and must not "
        f"reach the re-run channel: {verdict}"
    )


@pytest.mark.parametrize(
    ("deliverable", "check"), sorted(CHECK_FOR.items()), ids=sorted(CHECK_FOR)
)
def test_a_nested_json_deliverable_cannot_opt_a_run_out_of_scoring(
    tmp_path: Path, deliverable: str, check: str
) -> None:
    """An agent must not be able to remove its own run from the scored corpus.

    json.load on a deeply nested array raises RecursionError, which subclasses
    RuntimeError — NOT ValueError, and not OSError — so it escaped every except tuple.
    The check then died with a bare traceback and printed no verdict, and no-verdict is
    read as VERIFIER_INFRA_ERROR: the run leaves scored space and routes to the re-run
    channel. So an agent that knows it answered badly could drop a 400KB file of
    brackets and take the whole task out of the corpus, deterministically and for free.
    That is selection bias, not a denial of service, and it is the "exit 1 vs exit 2"
    distinction CLAUDE.md calls load-bearing: exit 1 is the agent scoring zero, exit 2 is
    no score existing. The agent must not get to choose which one it gets.

    This bead's diff is what made it worth the WHOLE task: before the gate, only
    check_cycle.sh read CYCLE_VERDICT.json (0.40). EnterpriseBench-ifo4h tracks the
    general shape and deferred it as "availability, not a scoring break" — true when it
    cost 0.40 and could not be aimed. Both halves of that changed here.

    The harness ran correctly; the agent's own input killed it, so it scores 0.0.
    """
    ws = _make_ws(tmp_path, f"nested_{check}")
    _write(
        ws,
        investigation=CORRECT_INVESTIGATION,
        cycle_verdict=CORRECT_VERDICT,
        resolution=CORRECT_RESOLUTION,
    )
    depth = 200_000
    (ws / "agent_output" / deliverable).write_text("[" * depth + "]" * depth)

    out = _raw(check, ws)
    assert out.returncode == 0, (
        f"{check} died on a nested-JSON {deliverable} (exit {out.returncode}) instead of "
        f"scoring it — no verdict is read as VERIFIER_INFRA_ERROR, which lets the agent "
        f"opt out of the corpus: {out.stderr[-200:]}"
    )
    assert out.stdout.strip(), f"{check} printed no verdict: {out.stderr[-200:]}"
    verdict = json.loads(out.stdout.strip())
    assert verdict["score"] == 0.0, verdict
    assert "VERIFIER_INFRA_ERROR" not in verdict["detail"], (
        f"the agent's own malformed deliverable was blamed on the harness and routed to "
        f"the re-run channel: {verdict}"
    )


# --- the grader must not run the agent's code ------------------------------


@pytest.mark.parametrize(
    "check", ["check_cycle.sh", "check_investigation.sh", "check_resolution.sh"]
)
def test_open_agent_file_refuses_a_fifo_without_blocking(
    tmp_path: Path, check: str
) -> None:
    """open_agent_file must not hang on a FIFO the agent swaps in.

    The bash [ ! -f ] guard rejects a PRE-PLACED FIFO, but the agent owns the directory
    and can swap the deliverable to a FIFO in the window between that guard and the read
    — the same TOCTOU window O_NOFOLLOW closes for symlinks. A plain O_RDONLY open of a
    FIFO blocks until a writer appears; under the milestone/checkpoint runners that
    becomes a verifier_timeout, i.e. a no-verdict route the agent can trigger for a free
    re-run. O_NONBLOCK makes the open return, and the fstat S_ISREG check then rejects it
    (EnterpriseBench-e4w15). Driven on the extracted helper, since the bash guard hides
    the window from an end-to-end run.
    """
    fn = _func_from(check, "open_agent_file")
    fifo = tmp_path / "swapped.fifo"
    os.mkfifo(fifo)

    # A regression that drops O_NONBLOCK makes the open block forever. The alarm must
    # RAISE (not the default SIGALRM, which kills the process and takes pytest with it),
    # and it must raise something pytest.raises(OSError) will NOT catch — TimeoutError is
    # an OSError subclass, so raising it would let a blocked read masquerade as the fast
    # rejection this asserts (a vacuous pass, timed at ~5s instead of instant). RuntimeError
    # is outside OSError, so a reintroduced block escapes pytest.raises and fails loudly.
    def _boom(signum, frame):
        raise RuntimeError("open_agent_file blocked on a FIFO — O_NONBLOCK is missing")

    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(5)
    try:
        with pytest.raises(OSError):
            fn(str(fifo)).read()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def test_symlinked_answer_key_cannot_forge_the_whole_task(tmp_path: Path) -> None:
    """The agent cannot READ the sealed key. It can still POINT at it.

    CYCLE_VERDICT.json mirrors ground_truth.json's `claimed_edges` by design -- same
    from/to/imported_at_runtime shape -- so the answer key IS a perfect verdict for this
    task. Creating a symlink never requires reading its target, and the checks run as
    the scorer identity, which can read /workspace/.task. So an agent that runs
    `ln -s ../.task/ground_truth.json CYCLE_VERDICT.json` makes the grader feed itself
    the key. Under the gate that unlocks the two free-text checkpoints as well, so the
    dump payload beside it collects everything: measured at 1.00/1.00 with the `[ -L ]`
    guards removed, versus 0.00 with them.

    The guard predates this bead on check_cycle.sh; the gate is what extends the vector
    to the other two checks, and it is guarded there identically. Driving the real
    symlink rather than asserting the guard's presence, because a green security test
    that cannot fail is decoration (EnterpriseBench-e4w15).
    """
    ws = _make_ws(tmp_path, "symlinked_key")
    (ws / "agent_output" / "INVESTIGATION.md").write_text(GLOBALS_HEAD)
    (ws / "agent_output" / "RESOLUTION.json").write_text(
        json.dumps({"code_change_required": False, "reason": GLOBALS_HEAD})
    )
    (ws / "agent_output" / "CYCLE_VERDICT.json").symlink_to(TASK_DIR / "ground_truth.json")

    assert score(ws, "runner", tmp_path) == 0.0, (
        "an agent symlinked the sealed answer key as its own verdict and the grader "
        "followed it — the key has the deliverable's exact shape, so this forges the "
        "whole task"
    )


def test_planted_stdlib_module_cannot_forge_a_verdict(tmp_path: Path) -> None:
    """An agent that drops `json.py` beside its deliverables must not mint its own
    score.

    These checks are bash, but they score via `python3 -c`, and `-c` puts
    sys.path[0]='' — the cwd — ahead of the stdlib. Both Python runners set cwd to
    the agent's workspace, so a planted json.py shadows the real module and the
    check's own `json.dumps` returns whatever the agent wrote. Reproduced before
    the fix: a blanket-true verdict scoring 0.0 came back as
    {"score": 1.0, "detail": "FORGED BY THE AGENT"}.

    This is bead 5cfxa's shadowing class through a different door. runner.py set
    PYTHONSAFEPATH=1 in checkpoint_env, but milestone.py passes no env and
    inherited — uncovered, and unreachable only until these checks became the first
    milestone verifiers to invoke python. scorer_guard.run_verifier_subprocess now
    sets it for every verifier (EnterpriseBench-e4w15).
    """
    ws = _write(
        _make_ws(tmp_path, "planted"),
        investigation=CORRECT_INVESTIGATION,
        # blanket-true: 2 of 4 right, 2 wrong -> negative marking scores this 0.0
        cycle_verdict=ALL_TRUE_VERDICT,
        resolution=CORRECT_RESOLUTION,
    )
    (ws / "json.py").write_text(
        'def dumps(*a, **k):\n'
        '    return \'{"score": 1.0, "passed": true, "detail": "FORGED"}\'\n'
        'def load(fh, *a, **k):\n'
        '    return {"claimed_edges": []}\n'
    )

    # Drive the real boundary both Python runners share, with milestone.py's exact
    # contract: cwd=workspace, env inherited (no PYTHONSAFEPATH of its own).
    verdict = run_verifier_subprocess(
        "checks/check_cycle.sh",
        base_dir=TASK_DIR,
        argv_suffix=(str(ws),),
        cwd=ws,
        timeout=120,
        checkpoint="cycle_discrimination",
    )
    assert not isinstance(verdict, InfraError), f"probe did not reach a verdict: {verdict}"
    assert verdict["score"] == 0.0, (
        f"a planted json.py forged a verdict: {verdict} — the grader is importing "
        f"the agent's code from its own cwd"
    )
    # Not enough to assert 0.0: a check that never found the deliverable also scores
    # 0.0, so the assertion above would pass vacuously in exactly the failure mode
    # this test exists to detect. Require the detail that only a check which really
    # read and graded the file can print.
    assert "2/4 claimed edges" in verdict["detail"], (
        f"probe scored 0.0 without actually grading the payload — the assertion "
        f"above would pass for the wrong reason: {verdict}"
    )


def test_scorer_guard_isolates_every_verifier_from_the_agents_cwd(
    tmp_path: Path,
) -> None:
    """The BOUNDARY half of the shadowing fix, guarded on its own.

    The task-level test above now passes because these three checks use `python3
    -I`, so it can no longer tell whether scorer_guard still isolates the
    interpreter. That matters: -I protects THESE checks, but PYTHONSAFEPATH at the
    boundary is what protects every other verifier and every future milestone check
    that forgets -I. Without this test the boundary fix could be reverted and the
    suite would stay green (EnterpriseBench-e4w15).

    Drives a probe verifier that reports the env it actually received.
    """
    probe_task = tmp_path / "probe_task"
    (probe_task / "checks").mkdir(parents=True)
    (probe_task / "checks" / "check_env.sh").write_text(
        '#!/usr/bin/env bash\n'
        'printf \'{"score": %s, "passed": true, "detail": "safepath=%s"}\\n\' \\\n'
        '  "$([ "${PYTHONSAFEPATH:-unset}" = "1" ] && echo 1.0 || echo 0.0)" \\\n'
        '  "${PYTHONSAFEPATH:-unset}"\n'
    )
    verdict = run_verifier_subprocess(
        "checks/check_env.sh",
        base_dir=probe_task,
        argv_prefix=("bash",),
        cwd=tmp_path,
        timeout=60,
        checkpoint="probe",
    )
    assert not isinstance(verdict, InfraError), verdict
    assert verdict["score"] == 1.0, (
        f"run_verifier_subprocess did not set PYTHONSAFEPATH=1: {verdict}. Every "
        f"verifier runs with cwd=the agent's workspace, so without it a bash check "
        f"that shells out to python3 imports the agent's code from sys.path[0]."
    )


# --- task-definition invariants --------------------------------------------


def test_no_check_grades_a_git_diff() -> None:
    """The fix vector was `git diff --name-only HEAD~1 | grep '\\.py$'` -- bare
    changed-file membership, which cannot tell a fix from a comment because it
    never reads the code's meaning. Grading the decision instead of the footprint
    is what removes it; this asserts it does not come back."""
    for sh in sorted((TASK_DIR / "checks").glob("*.sh")):
        body = sh.read_text()
        assert "git diff" not in body, f"{sh.name} grades a diff again"


def test_ground_truth_does_not_assert_the_fabricated_cycle() -> None:
    gt = _ground_truth()
    assert gt.get("cycle_exists") is False
    assert gt.get("code_change_required") is False
    runtime = {
        (e["from"], e["to"]) for e in gt["claimed_edges"] if e["imported_at_runtime"]
    }
    assert runtime == {("flask", "flask.json"), ("flask.json", "flask.globals")}


def test_task_does_not_require_a_code_patch_artifact() -> None:
    """`artifacts.required = ["code_patch"]` asserted a patch that must not exist:
    the correct answer changes no code."""
    assert "code_patch" not in _toml().get("artifacts", {}).get("required", [])


def test_simulation_encodes_the_correct_answer() -> None:
    """--simulate must exercise the correct answer, not the fabrication it used to
    hardcode (a lazy-import FIX_SUMMARY for a cycle that does not exist).

    Asserted on the parsed deliverables, not on prose: the correct resolution
    REFUSES a lazy-import patch and says so, so banning the phrase would fail the
    right answer for describing what it declined to do.
    """
    toml = _toml()
    actions = {
        a["file"]: a["content"]
        for key in ("session_1", "session_2")
        for a in toml["simulation"][key]["actions"]
    }
    key = {(e["from"], e["to"]): e["imported_at_runtime"] for e in _ground_truth()["claimed_edges"]}

    verdict = json.loads(actions["CYCLE_VERDICT.json"])
    assert {
        (e["from"], e["to"]): e["imported_at_runtime"] for e in verdict["claimed_edges"]
    } == key, "simulated session 1 must answer every claimed edge as ground_truth does"

    resolution = json.loads(actions["RESOLUTION.json"])
    assert resolution["code_change_required"] is False
    assert "type_checking" in resolution["reason"].lower()
    assert "type_checking" in actions["INVESTIGATION.md"].lower()


def test_every_check_is_claimed_by_a_checkpoint() -> None:
    """run_task.py stages EVERY checks/*.sh into .verifiers/ but writes a .meta
    weight only for scripts a [[checkpoints]].verifier names, and test_runner.sh
    defaults a missing weight to 1.0. So a check left behind by a rename -- or one
    reachable only from a milestone -- is scored at weight 1.0 and pushes
    task_score above 1.0 with nothing raising. Holds for all 141 tasks today."""

    def vname(p: str) -> str:
        stem = Path(p).stem
        return stem[len("check_") :] if stem.startswith("check_") else stem

    offenders: list[str] = []
    for toml_path in sorted(BENCH.rglob("task.toml")):
        d = toml_path.parent
        if {"_archived", "mined"} & set(d.relative_to(BENCH).parts):
            continue
        checks = d / "checks"
        if not checks.is_dir():
            continue
        scripts = {vname(p.name) for p in checks.glob("*.sh")}
        if not scripts:
            continue
        data = tomllib.load(open(toml_path, "rb"))
        claimed = {
            vname(c["verifier"]) for c in data.get("checkpoints", []) if c.get("verifier")
        }
        if scripts - claimed or claimed - scripts:
            offenders.append(
                f"{d.relative_to(BENCH)}: unclaimed={sorted(scripts - claimed)} "
                f"missing={sorted(claimed - scripts)}"
            )
    assert not offenders, "checks/ and [[checkpoints]] must correspond: " + "; ".join(
        offenders
    )
