"""Tests for the file_extraction scorer.

Exercised through the real ``python -m`` CLI boundary rather than by importing
the module, because that boundary *is* the contract: the two check scripts
shell out to it, and the bug this module fixes (a missing module scoring a
silent 0.0) was invisible to import-level tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Import the real sentinel rather than re-spelling it: scorer_guard is what greps
# for it, so a test asserting against its own copy would pass straight through a
# drift that silently re-books infra failures as agent zeros.
from eb_verify.plugins.file_extraction import components
from eb_verify.scorer_guard import INFRA_SENTINEL
from eb_verify.runner import CheckpointRunner
from eb_verify.task_parser import parse_task

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "lib"

# The two live tasks whose 0.40-weight checkpoint execs this module.
AFFECTED_TASKS = [
    "benchmarks/customer_escalation/support-mapping-dual-httpx-httpcore-001",
    "benchmarks/customer_escalation/err-provenance-tri-httpx-proxy-001",
]


# The scorer's full key surface, deliberately NOT a mirror of any shipped check
# script's --keys: a restatement exercises this module's parsing rather than the
# artifact that ships, which is how a dropped key went unnoticed. What the scripts
# pass is guarded by running the scripts themselves, in tests/integrity/.
ALL_KEYS = "source_files,files,error_source.files,code_paths,citations"


def cli_env(answer_file, gt_file) -> dict:
    """The environment the scorer reads its two inputs from."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    env["ANSWER_FILE"] = str(answer_file)
    env["GT_FILE"] = str(gt_file)
    return env


def run_cli(answer_file, gt_file, argv=("--keys", ALL_KEYS)):
    """Invoke the scorer across its real ``python -m`` CLI boundary."""
    return subprocess.run(
        [sys.executable, "-m", "eb_verify.plugins.file_extraction", *argv],
        capture_output=True, text=True,
        env=cli_env(answer_file, gt_file), cwd=str(REPO_ROOT),
    )


def write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload))
    return path


def gt_with(tmp_path: Path, paths) -> Path:
    """A ground_truth.json carrying repo-prefixed required_files, as the real ones do."""
    return write_json(
        tmp_path / "ground_truth.json",
        {"required_files": [{"path": p, "repo": p.split("/")[0]} for p in paths]},
    )


def score_of(proc) -> float:
    return json.loads(proc.stdout)["score"]


def test_a_pathological_citation_string_does_not_hang_the_scorer():
    """The citation regex must stay linear in the length of an agent-supplied path.

    ``_CITATION_SUFFIX_RE`` has no start anchor, so ``re.sub`` retries it at every ':'
    in the string. Unbounded repetition made each of those O(n) attempts run to the end
    and unwind, i.e. quadratic — 32KB of ':1' took 5.7s, and ~200KB blows the 120s
    checkpoint timeout, which runner.py books as a silent agent 0.0. That is the false
    zero this module exists to kill, reachable from a graded answer.json, and it is not
    only adversarial: a mangled `rg --vimgrep` blob pasted as one string reaches it too.

    The trailing 'x' is the whole point of the vector — it breaks the ``$`` anchor, so
    the match FAILS and the engine backtracks. A *well-formed* citation of the same
    length matches immediately and runs fast, which is how an earlier ReDoS review that
    measured only matching inputs concluded the regex was linear.

    Asserted as a wall-clock ceiling rather than a growth curve so it cannot flake into
    a false red on a loaded box: the bound is ~3 orders of magnitude above the linear
    cost and ~2 below the quadratic one, so only a real complexity regression trips it.
    """
    payload = "file.py" + (":1" * 20000) + "x"
    start = time.perf_counter()
    parts = components(payload)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, (
        f"components() took {elapsed:.1f}s — the citation regex is superlinear again"
    )
    # Fail-safe: an unstrippable tail stays attached (a false miss), never over-strips.
    assert parts == [payload]


# --- the core bug: GT is repo-prefixed, agents answer repo-relative ----------

def test_repo_prefixed_gt_matches_repo_relative_answer(tmp_path):
    """GT 'httpx/httpx/_config.py' must match an agent's 'httpx/_config.py'.

    The sibling check scripts use `gt in af or af.endswith(gt)`, which does not
    match this case and would keep these tasks near-zero.
    """
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py", "httpcore/httpcore/_async/http11.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": ["httpx/_config.py", "httpcore/_async/http11.py"]})
    proc = run_cli(answer, gt)
    assert proc.returncode == 0, proc.stderr
    assert score_of(proc) == 1.0


def test_match_is_symmetric(tmp_path):
    """An agent answering the *longer* repo-prefixed path also matches."""
    gt = gt_with(tmp_path, ["httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["httpx/httpx/_config.py"]})
    assert score_of(run_cli(answer, gt)) == 1.0


def test_suffix_match_respects_component_boundaries(tmp_path):
    """'my_config.py' must NOT satisfy GT '_config.py' — raw endswith says it does."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["httpx/my_config.py"]})
    assert score_of(run_cli(answer, gt)) == 0.0


def test_partial_credit_is_fraction_of_gt_found(tmp_path):
    gt = gt_with(tmp_path, ["a/a/one.py", "b/b/two.py", "c/c/three.py", "d/d/four.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["a/one.py", "b/two.py"]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 0.5
    assert json.loads(proc.stdout)["passed"] is True  # 0.5 threshold is inclusive


# --- answer-shape handling ---------------------------------------------------

def test_empty_earlier_key_falls_through_to_later_key(tmp_path):
    """An empty 'source_files' must not shadow a populated later key."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": [], "files": ["httpx/_config.py"]})
    assert score_of(run_cli(answer, gt)) == 1.0


def test_wrong_guess_in_earlier_key_does_not_discard_correct_later_key(tmp_path):
    """A populated-but-wrong earlier key must not shadow a populated-and-right
    later key: keys are unioned, and recall-only scoring cannot over-credit from
    a union."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py", "httpcore/httpcore/_client.py"])
    answer = write_json(tmp_path / "answer.json", {
        "source_files": ["totally/unrelated.py"],
        "files": ["httpx/_config.py", "httpcore/_client.py"],
    })
    proc = run_cli(answer, gt)
    assert score_of(proc) == 1.0, json.loads(proc.stdout)["detail"]


def test_files_split_across_two_populated_keys_union_to_full_credit(tmp_path):
    """Different required files named under different keys must combine."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py", "httpcore/httpcore/_client.py"])
    answer = write_json(tmp_path / "answer.json", {
        "source_files": ["httpx/_config.py"],
        "files": ["httpcore/_client.py"],
    })
    assert score_of(run_cli(answer, gt)) == 1.0


def test_code_paths_key_is_scored(tmp_path):
    """run_task.py's output appendix mandates `code_paths` for every task; an
    agent that follows it must not be scored 0.0 for using the advertised key."""
    gt = gt_with(tmp_path, ["httpx/httpx/_transports/default.py"])
    answer = write_json(tmp_path / "answer.json", {
        "code_paths": [{"path": "/workspace/httpx/httpx/_transports/default.py"}],
    })
    proc = run_cli(answer, gt)
    assert score_of(proc) == 1.0, json.loads(proc.stdout)["detail"]


def test_citations_key_is_scored(tmp_path):
    """task.toml's require_grounded_citations=true mandates a top-level
    `citations` list of {repo,file,evidence_span} dicts (run_task.py's
    citations_block). The `file` entry must be scored like any other path."""
    gt = gt_with(tmp_path, ["httpx/httpx/_transports/default.py"])
    answer = write_json(tmp_path / "answer.json", {
        "citations": [{
            "repo": "httpx",
            "file": "httpx/_transports/default.py",
            "evidence_span": "class HTTPTransport(BaseTransport):",
        }],
    })
    proc = run_cli(answer, gt)
    assert score_of(proc) == 1.0, json.loads(proc.stdout)["detail"]


@pytest.mark.parametrize("suffix", [
    ":120",         # a bare line number
    ":120-140",     # a line range: 'reflector.go:417-418' appears verbatim in results/
    ":120:5",       # line:column, as grep -n and rg --vimgrep emit it
    "#L120",        # GitHub blob anchor
    "#L120-L140",   # GitHub range anchor
    "#L120-140",    # GitHub range anchor, unprefixed end
    "?L120-140",    # Sourcegraph range anchor
])
def test_citation_line_suffix_is_stripped(tmp_path, suffix):
    """A citation-style path must still match — agents cite an exact line alongside
    the evidence span, in whatever style their tool emits. Ranges are not
    hypothetical: the captured results/ corpus holds 'reflector.go:417-418'. Missing
    the Sourcegraph anchor specifically would be a mode-correlated scoring bias (see
    _CITATION_SUFFIX_RE)."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": [f"httpx/_config.py{suffix}"]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 1.0, f"suffix {suffix!r}: {json.loads(proc.stdout)['detail']}"


def test_a_version_like_filename_is_not_mistaken_for_a_citation(tmp_path):
    """The suffix pattern must not eat part of a legitimate filename. Every
    alternative is anchored behind a literal ':' / '#L' / '?L', so a name that
    merely ends in digits and dashes survives."""
    gt = gt_with(tmp_path, ["repo/report-2024-2025.md"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": ["repo/report-2024-2025.md"]})
    assert score_of(run_cli(answer, gt)) == 1.0


def test_dotted_key_is_traversed(tmp_path):
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"error_source": {"files": ["httpx/_config.py"]}})
    assert score_of(run_cli(answer, gt)) == 1.0


def test_dict_entries_and_str_entries_both_extract(tmp_path):
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py", "h11/h11/_connection.py"])
    answer = write_json(tmp_path / "answer.json", {
        "source_files": [{"path": "httpx/_config.py"}, {"file": "h11/_connection.py"}],
    })
    assert score_of(run_cli(answer, gt)) == 1.0


def test_bare_string_value_is_accepted(tmp_path):
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": "httpx/_config.py"})
    assert score_of(run_cli(answer, gt)) == 1.0


# --- fail-closed split: agent failure vs harness failure ---------------------

def test_missing_answer_is_a_real_zero_not_an_infra_error(tmp_path):
    """The agent simply didn't produce output. That is a legitimate 0.0, exit 0."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    proc = run_cli(tmp_path / "nonexistent.json", gt)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.0
    assert payload["passed"] is False
    assert INFRA_SENTINEL not in payload["detail"]


def test_unparseable_answer_is_a_real_zero(tmp_path):
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    bad = tmp_path / "answer.json"
    bad.write_text("{not json")
    proc = run_cli(bad, gt)
    assert proc.returncode == 0
    assert score_of(proc) == 0.0
    assert INFRA_SENTINEL not in json.loads(proc.stdout)["detail"]


def test_no_matches_is_a_real_zero(tmp_path):
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["totally/unrelated.py"]})
    proc = run_cli(answer, gt)
    assert proc.returncode == 0
    assert score_of(proc) == 0.0
    assert INFRA_SENTINEL not in json.loads(proc.stdout)["detail"]


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses file permission bits, so chmod 000 cannot simulate EACCES",
)
def test_a_present_but_unreadable_answer_is_infra_not_a_zero(tmp_path):
    """A chmod-000 answer.json EXISTS but cannot be read — a UID/permission mismatch on
    a docker-cp'd file (hktt/pt0n). That is our failure, not the agent's; booking it as
    a 0.0 is the fail-open bug ssikq reopened. Only a genuinely ABSENT file is an agent
    zero, so a present-but-unreadable one must carry the sentinel and exit nonzero."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(
        tmp_path / "answer.json", {"source_files": ["/workspace/httpx/httpx/_config.py"]}
    )
    answer.chmod(0o000)
    try:
        proc = run_cli(answer, gt)
    finally:
        answer.chmod(0o644)  # let pytest's tmp_path cleanup remove it
    assert proc.returncode != 0, "an unreadable present answer must fail closed"
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.0
    assert INFRA_SENTINEL in payload["detail"]


def test_an_unset_answer_file_is_infra_not_a_zero(tmp_path):
    """ANSWER_FILE unset is a harness misconfiguration — the runner never told the
    scorer where to look — not an agent that wrote nothing. It must fail closed, where
    an absent-but-named file is a real agent 0.0."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    env["GT_FILE"] = str(gt)
    env.pop("ANSWER_FILE", None)
    proc = subprocess.run(
        [sys.executable, "-m", "eb_verify.plugins.file_extraction", "--keys", ALL_KEYS],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0, "an unset ANSWER_FILE must fail closed"
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.0
    assert INFRA_SENTINEL in payload["detail"]


def test_a_whitespace_only_answer_file_is_infra_not_a_zero(tmp_path):
    """A blank ANSWER_FILE (' ') is the same misconfiguration as an unset one — the
    runner gave no real path. os.path.isfile(' ') is False, so without the strip it
    would masquerade as an absent file (an agent 0.0) instead of infra."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    env["GT_FILE"] = str(gt)
    env["ANSWER_FILE"] = "   "
    proc = subprocess.run(
        [sys.executable, "-m", "eb_verify.plugins.file_extraction", "--keys", ALL_KEYS],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0, "a whitespace-only ANSWER_FILE must fail closed"
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.0
    assert INFRA_SENTINEL in payload["detail"]


def test_a_path_named_under_two_keys_is_one_ambiguous_guess(tmp_path):
    """agent_files dedups, so the same path under two unioned keys is a single guess:
    it appears once in the ambiguous detail, not read as two distinct misses."""
    gt = gt_with(tmp_path, ["httpx/httpx/_client.py", "httpcore/httpcore/_client.py"])
    answer = write_json(tmp_path / "answer.json", {
        "source_files": ["_client.py"], "files": ["_client.py"],
    })
    proc = run_cli(answer, gt)
    assert score_of(proc) == 0.0
    ambiguous_part = json.loads(proc.stdout)["detail"].split("credited none:")[1]
    assert ambiguous_part.count("_client.py") == 1, ambiguous_part


@pytest.mark.parametrize("gt_payload", [
    None,                      # file absent
    "{not json",               # corrupt
    json.dumps({}),            # no required_files
    json.dumps({"required_files": []}),  # empty required_files
])
def test_broken_ground_truth_fails_closed_as_infra_error(tmp_path, gt_payload):
    """A broken GT is OUR bug, not the agent's. It must never look like a 0.0.

    Emits valid JSON on stdout (so runner.py's json branch wins deterministically)
    carrying the INFRA_SENTINEL in detail, and exits nonzero.
    """
    gt = tmp_path / "ground_truth.json"
    if gt_payload is not None:
        gt.write_text(gt_payload)
    answer = write_json(tmp_path / "answer.json", {"source_files": ["httpx/_config.py"]})

    proc = run_cli(answer, gt)
    assert proc.returncode != 0, "harness failure must exit nonzero"
    payload = json.loads(proc.stdout)  # must still be valid JSON
    assert payload["score"] == 0.0
    assert INFRA_SENTINEL in payload["detail"], "scorer_guard keys off this sentinel"


# --- the module must never exit without printing JSON ------------------------
#
# An exit with no JSON on stdout leaves runner.py fabricating a score from the
# exit code. Each case below crashed or printed usage text instead of a verdict.

def test_non_utf8_ground_truth_is_an_infra_error_not_a_crash(tmp_path):
    gt = tmp_path / "ground_truth.json"
    gt.write_bytes(b'{"required_files": [{"path": "\xff\xfe bad bytes"}]}')
    answer = write_json(tmp_path / "answer.json", {"source_files": ["httpx/_config.py"]})

    proc = run_cli(answer, gt)
    assert proc.stdout, "must print JSON even when it cannot read the GT"
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.0
    assert INFRA_SENTINEL in payload["detail"]
    assert proc.returncode != 0


def test_non_utf8_answer_is_a_real_zero_not_a_crash(tmp_path):
    """A corrupt answer is the *agent's* failure — a real 0.0, never an infra error."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = tmp_path / "answer.json"
    answer.write_bytes(b'{"source_files": ["\xff\xfe"]}')

    proc = run_cli(answer, gt)
    assert proc.stdout, "must print JSON even when it cannot read the answer"
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.0
    assert proc.returncode == 0
    assert INFRA_SENTINEL not in payload["detail"]


@pytest.mark.parametrize("argv", [
    [],                                        # --keys omitted
    ["--kesy", "source_files"],                # typo'd flag
    ["--keys", "source_files", "--policy", "suffix"],  # flag that no longer exists
    ["--help"],                                # would print usage on stdout, exit 0
])
def test_cli_misuse_emits_infra_json_and_never_a_fabricated_score(tmp_path, argv):
    """A check-script typo must not be scoreable.

    `--help` is the nastiest: stock argparse prints text to stdout and exits 0,
    which runner.py's exit-code fallback reads as a pass and fabricates a 1.0.
    """
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["httpx/_config.py"]})

    proc = run_cli(answer, gt, argv=argv)

    payload = json.loads(proc.stdout)  # must be JSON, not usage text
    assert payload["score"] == 0.0
    assert payload["passed"] is False
    assert INFRA_SENTINEL in payload["detail"]
    assert proc.returncode != 0


def test_broken_stdout_is_an_infra_error_not_a_false_zero(tmp_path):
    """If the verdict cannot reach stdout, the module must still print the sentinel to
    stderr and exit nonzero rather than dying with a bare traceback. The host runner
    books this as a 0.0 today (it does not scan stderr for the sentinel — bead wto43),
    but a legible sentinel on stderr is what a stderr-aware caller keys off, and it
    keeps a second BrokenPipeError from masking the message at shutdown."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["httpx/_config.py"]})

    proc = subprocess.Popen(
        [sys.executable, "-m", "eb_verify.plugins.file_extraction",
         "--keys", "source_files"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=cli_env(answer, gt), cwd=str(REPO_ROOT),
    )
    proc.stdout.close()  # the reader goes away, so the write breaks
    stderr = proc.stderr.read().decode()
    proc.wait()

    assert proc.returncode != 0
    assert INFRA_SENTINEL in stderr
    assert "Traceback" not in stderr, "must not die with a bare traceback"


# --- an answer earns credit only if it picks out exactly one required file ---
#
# Credit is per answer, not per ground-truth entry. Scoring each GT entry
# independently would let one under-specified guess claim several at once.

def test_one_ambiguous_guess_cannot_claim_two_repos(tmp_path):
    """The amplification case: '_client.py' names neither repo's _client.py."""
    gt = gt_with(tmp_path, ["httpx/httpx/_client.py", "httpcore/httpcore/_client.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["_client.py"]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 0.0
    assert "ambiguous" in json.loads(proc.stdout)["detail"]


def test_ambiguity_is_not_about_depth(tmp_path):
    """A 2-component guess is just as ambiguous when two repos share that tail."""
    gt = gt_with(tmp_path, ["repo1/src/file.py", "repo2/src/file.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["src/file.py"]})
    assert score_of(run_cli(answer, gt)) == 0.0


def test_disambiguating_the_repo_earns_both(tmp_path):
    """Naming each repo explicitly resolves the ambiguity and scores both."""
    gt = gt_with(tmp_path, ["repo1/src/file.py", "repo2/src/file.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": ["repo1/src/file.py", "repo2/src/file.py"]})
    assert score_of(run_cli(answer, gt)) == 1.0


def test_unambiguous_bare_name_still_scores(tmp_path):
    """Specificity is only required where it distinguishes something.

    A repo-root file's natural repo-relative answer IS a bare name. Rejecting it
    on depth alone would zero a correct answer — the false-zero class this whole
    module exists to remove.
    """
    gt = gt_with(tmp_path, ["httpx/setup.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["setup.py"]})
    assert score_of(run_cli(answer, gt)) == 1.0


def test_partial_ambiguity_still_credits_the_unambiguous_guesses(tmp_path):
    gt = gt_with(tmp_path, ["httpx/httpx/_client.py", "httpcore/httpcore/_client.py",
                            "h11/h11/_connection.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": ["_client.py", "h11/_connection.py"]})
    assert score_of(run_cli(answer, gt)) == round(1 / 3, 4)


def test_parent_directory_localizes(tmp_path):
    gt = gt_with(tmp_path, ["httpcore/httpcore/_async/http11.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["_async/http11.py"]})
    assert score_of(run_cli(answer, gt)) == 1.0


def test_dotdot_is_normalized_away(tmp_path):
    """'httpx/sub/../_config.py' is lexically 'httpx/_config.py' — credit it."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": ["httpx/sub/../_config.py"]})
    assert score_of(run_cli(answer, gt)) == 1.0


# --- a precise answer outranks the ambiguity rule ----------------------------
#
# The ambiguity rule must not fire on an answer that names a required file *more*
# specifically than the ground truth does, which is what happens whenever one
# required path is a component-suffix of another.

# The ground truth of technical_debt/refactor-orchestration-tri-babel-001, verbatim:
# repo-relative paths plus a separate `repo` field, so webpack's bare 'package.json'
# is a component-suffix of the other two. (The tokio task has the same shape with
# Cargo.toml, and bead rmz1x points 26 more check scripts at this scorer.)
NESTED_GT = [
    ("babel", "packages/babel-parser/package.json"),
    ("webpack", "package.json"),
    ("nextjs", "packages/next/package.json"),
]


def nested_gt_file(tmp_path: Path) -> Path:
    return write_json(tmp_path / "ground_truth.json", {"required_files": [
        {"path": path, "repo": repo} for repo, path in NESTED_GT
    ]})


def test_nested_gt_does_not_zero_a_fully_specified_answer(tmp_path):
    """Crediting per ground-truth entry, every guess in a perfect answer matched
    more than one required file — 'package.json' is a tail of the other two — so all
    three were booked ambiguous: 0/3, the worst possible score for a flawless answer."""
    gt = nested_gt_file(tmp_path)
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": [path for _, path in NESTED_GT]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 1.0, json.loads(proc.stdout)["detail"]


def test_nested_gt_scores_the_absolute_shape_the_harness_mandates(tmp_path):
    """The case that actually ships. run_task.py's appendix tells every agent 'All
    file paths MUST be absolute and anchored at /workspace/<repo>/...', so the answer
    is never component-equal to a ground-truth entry — it is strictly longer.
    Crediting only exact matches would leave this at 1/3, fixing nothing that ships.
    """
    gt = nested_gt_file(tmp_path)
    answer = write_json(tmp_path / "answer.json", {"code_paths": [
        {"path": f"/workspace/{repo}/{path}"} for repo, path in NESTED_GT
    ]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 1.0, json.loads(proc.stdout)["detail"]


def test_an_abbreviation_cannot_claim_the_deeper_of_two_unequal_matches(tmp_path):
    """The trap that rules out 'just credit the longest hit'. '_client.py' is a tail
    of both required files but refines neither, so a longest-hit rule would hand it
    the deeper one and score 0.5 — a passing grade for a one-word non-answer."""
    gt = gt_with(tmp_path, ["httpx/_client.py", "httpcore/httpcore/_async/_client.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["_client.py"]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 0.0, json.loads(proc.stdout)["detail"]
    assert json.loads(proc.stdout)["passed"] is False


def test_an_answer_matching_an_underqualified_gt_entry_exactly_is_credited(tmp_path):
    """Accepted consequence, stated rather than discovered later: against a ground
    truth that spells a required file 'package.json', the answer 'package.json' is
    credited even though a deeper required file shares that tail. The agent named the
    file exactly as the ground truth names it, and no more specific answer exists for
    that entry; the under-qualification is the ground truth's defect, not the
    scorer's (repo-prefixed paths are the documented contract).
    """
    gt = gt_with(tmp_path, ["package.json", "packages/next/package.json"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["package.json"]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 0.5, json.loads(proc.stdout)["detail"]


# The two nested required files above are 'a.py' and 'x/y/a.py'. Between them sits a
# band of answers — deeper than the first, shallower than the second — that refine one
# and abbreviate the other. Which file such an answer means is not recoverable, so the
# rule is that it means neither, and says so.
BETWEEN_GT = ["a.py", "x/y/a.py"]


def test_an_answer_between_two_nested_required_files_is_ambiguous(tmp_path):
    """Deliberate rule, stated rather than left to fall out of branch order.

    'y/a.py' refines the required 'a.py' and abbreviates the required 'x/y/a.py'; it
    distinguishes neither, so it is credited to neither and is reported. Preferring the
    refined reading (the earlier rule) silently credited it to 'a.py' — which the other
    answer already named exactly — and booked the file it actually points at as missed,
    with an empty `ambiguous` list to say so.

    The alternative of crediting the deepest hit is what
    test_an_abbreviation_cannot_claim_the_deeper_of_two_unequal_matches rules out, and a
    global answer-to-file assignment would buy a better number only on an answer shape
    the harness forbids (see the test below).
    """
    gt = gt_with(tmp_path, BETWEEN_GT)
    answer = write_json(tmp_path / "answer.json", {"source_files": ["a.py", "y/a.py"]})
    proc = run_cli(answer, gt)
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.5, payload["detail"]
    assert "ambiguous" in payload["detail"] and "y/a.py" in payload["detail"]


def test_a_between_band_answer_alone_is_credited_nothing(tmp_path):
    """Where the rule above is visible in the *score*, and in which direction.

    Paired with the test above, which pins the same rule where it is not: there, the
    answer also names "a.py" exactly, and because ``matched`` is a set of required
    files, crediting "y/a.py" to "a.py" a second time changed no number. The silent
    miscredit was real but invisible — it showed up only as an empty ``ambiguous``.

    Alone, "y/a.py" is the whole answer, and the old rule paid it 0.5 for a path that
    names *neither* required file unambiguously: half marks for resolving to "a.py",
    which the agent never wrote. So the score-visible half of this fix closes an
    OVER-credit. It does not raise the deeper file's score, and must not — "x/y/a.py"
    stays missed here, because nothing in the answer unambiguously names it.
    """
    gt = gt_with(tmp_path, BETWEEN_GT)
    answer = write_json(tmp_path / "answer.json", {"source_files": ["y/a.py"]})
    proc = run_cli(answer, gt)
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.0, payload["detail"]
    assert "y/a.py" in payload["detail"]


def test_the_mandated_absolute_shape_is_not_ambiguous_against_nested_gt(tmp_path):
    """Why the rule above costs the benchmark nothing on the answer shape that ships.

    run_task.py's appendix mandates '/workspace/<repo>/...' answers. Such an answer
    carries the mount component plus the repo, so it is strictly deeper than the ground
    truth entry it names, abbreviates it rather than being abbreviated by it, and the
    ambiguity clause cannot fire on it. The 0.5 above is reachable only by an
    under-qualified repo-relative answer, where it is earned.

    Deeper than *its own* entry, note — NOT deeper than every entry, which is a stronger
    claim and a false one. See the test below for the ground truth shape that breaks it.
    """
    gt = gt_with(tmp_path, BETWEEN_GT)
    answer = write_json(tmp_path / "answer.json", {"source_files": [
        "/workspace/a/a.py", "/workspace/x/x/y/a.py",
    ]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 1.0, json.loads(proc.stdout)["detail"]


def test_a_gt_path_holding_the_mount_component_zeroes_the_mandated_shape(tmp_path):
    """A KNOWN, LATENT false zero, pinned here rather than asserted away. Tracked as
    EnterpriseBench-d900w; NOT fixed in this bead.

    '/workspace/' is a hardcoded universal mount prefix (run_task.py), and components()
    keeps 'workspace' as an ordinary path component. So a required file whose own path
    happens to END in 'workspace/<a repo>/<that repo's file>' is a component-suffix of
    the mandated answer for a DIFFERENT required file, and gets booked as an abbreviated
    hit. The answer then refines one entry and abbreviates another, the ambiguity clause
    fires, and a correct, spec-compliant answer is credited nothing:

        gt     = ['config.py', 'src/workspace/app/config.py']
        answer = ['/workspace/app/config.py']   # the mandated shape, naming 'config.py'
        => 0/2, ambiguous — where the pre-refinement rule scored 1/2

    The root cause is that matches() is blind to the `repo` field both sides carry: were
    it honoured, the answer declares repo 'app' and the deeper entry declares repo
    'src', so they would never be candidates for one another and no ambiguity would
    arise. That is d900w's subject, and fixing it here would be a silent semantics
    change to two live 0.40-weight checkpoints inside a bead nobody reviewed for it.

    LATENT, not live: no ground_truth.json in the repo has a 'workspace' path component
    (verified across benchmarks/**). Repos named 'workspace' are not exotic though —
    Cargo/npm/pnpm workspaces and Bazel all use the name — so this test exists to make
    the next task that trips it fail loudly here, not silently in a published score.
    """
    gt = gt_with(tmp_path, ["config.py", "src/workspace/app/config.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": ["/workspace/app/config.py"]})
    proc = run_cli(answer, gt)
    payload = json.loads(proc.stdout)
    assert payload["score"] == 0.0, payload["detail"]
    assert "ambiguous" in payload["detail"]


# --- ground truth is OUR artifact: strict, and never silently shrunk ---------

def test_duplicate_gt_spellings_do_not_distort_the_denominator(tmp_path):
    """'a/a.py' and './a/a.py', from the same repo, are one required file, not two."""
    gt = write_json(tmp_path / "ground_truth.json", {"required_files": [
        {"path": "repo/a/a.py", "repo": "repo"},
        {"path": "./repo/a/a.py", "repo": "repo"},
        {"path": "repo/b/b.py", "repo": "repo"},
    ]})
    answer = write_json(tmp_path / "answer.json", {"source_files": ["a/a.py"]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 0.5, "denominator must be 2 distinct files, not 3"
    assert "1/2" in json.loads(proc.stdout)["detail"]


def test_a_required_file_without_a_repo_fails_closed(tmp_path):
    """`repo` is required by schemas/task.schema.json, but nothing validates
    ground_truth.json against it, so this module is the last line of defence.

    Without it the collision check below has nothing to adjudicate: two entries that
    both omit `repo` compare equal, collapse into one, and silently halve the
    denominator — the same over-credit, arrived at by a missing field instead of a
    conflicting one.
    """
    gt = write_json(tmp_path / "ground_truth.json", {"required_files": [
        {"path": "package.json"}, {"path": "package.json"},
    ]})
    answer = write_json(tmp_path / "answer.json", {"source_files": ["package.json"]})
    proc = run_cli(answer, gt)
    assert proc.returncode != 0, "a ground truth missing `repo` must not score"
    assert INFRA_SENTINEL in json.loads(proc.stdout)["detail"]


def test_the_same_path_required_from_two_repos_fails_closed(tmp_path):
    """Two repos requiring the same relative path are two files, not a duplicate
    spelling — but a component-suffix matcher cannot tell them apart, and the dedup
    above would silently collapse them, shrinking the denominator and inflating every
    agent's score. Our artifact is the wrong one, so it fails closed. (It also keeps
    ground-truth component lists distinct, which score_answer's crediting relies on.)
    """
    gt = write_json(tmp_path / "ground_truth.json", {"required_files": [
        {"path": "package.json", "repo": "webpack"},
        {"path": "package.json", "repo": "babel"},
    ]})
    answer = write_json(tmp_path / "answer.json", {"source_files": ["package.json"]})
    proc = run_cli(answer, gt)
    assert proc.returncode != 0
    assert INFRA_SENTINEL in json.loads(proc.stdout)["detail"]


@pytest.mark.parametrize("required", [
    [{"file": "a/a.py"}],                        # 'file' alias is not the GT contract
    [{"path": "repo/a.py"}, {}],                 # a malformed entry among good ones
    [{"path": "repo/a.py"}, {"path": ""}],       # empty path
    [{"path": "repo/a.py"}, "repo/b.py"],        # bare string, not an object
])
def test_malformed_gt_entry_fails_closed(tmp_path, required):
    """Silently dropping a bad GT entry would shrink the denominator and inflate
    every agent's score. It is a harness bug, so it must fail closed."""
    gt = write_json(tmp_path / "ground_truth.json", {"required_files": required})
    answer = write_json(tmp_path / "answer.json", {"source_files": ["a/a.py"]})
    proc = run_cli(answer, gt)
    assert proc.returncode != 0
    assert INFRA_SENTINEL in json.loads(proc.stdout)["detail"]


@pytest.mark.parametrize("dotpath", [".", "./", "..", "/", "  .  "])
def test_a_gt_path_that_normalizes_to_no_components_fails_closed(tmp_path, dotpath):
    """'.', '/', '..' are non-empty strings that strip to zero path components, so
    _matches_parts can never match them: they would sit in the recall denominator
    forever and halve every agent's score on a 0.40-weight checkpoint. The 'if not
    path' guard only catches the empty string, so this is a distinct hole — a GT
    authoring slip that must fail closed rather than score."""
    gt = write_json(tmp_path / "ground_truth.json", {"required_files": [
        {"path": "httpx/httpx/_config.py", "repo": "httpx"},
        {"path": dotpath, "repo": "httpcore"},
    ]})
    answer = write_json(
        tmp_path / "answer.json", {"source_files": ["/workspace/httpx/httpx/_config.py"]}
    )
    proc = run_cli(answer, gt)
    assert proc.returncode != 0, f"a GT path ({dotpath!r}) with no components must not score"
    assert INFRA_SENTINEL in json.loads(proc.stdout)["detail"]


def test_deeply_nested_gt_is_an_infra_error_not_a_recursion_crash(tmp_path):
    """json.load raises RecursionError, which is neither ValueError nor OSError."""
    gt = tmp_path / "ground_truth.json"
    gt.write_text("[" * 10000 + "0" + "]" * 10000)
    answer = write_json(tmp_path / "answer.json", {"source_files": ["a/a.py"]})
    proc = run_cli(answer, gt)
    assert proc.stdout, "must print JSON rather than dying with a traceback"
    assert INFRA_SENTINEL in json.loads(proc.stdout)["detail"]
    assert proc.returncode != 0


# --- end-to-end through the real runner --------------------------------------
#
# Driven through CheckpointRunner rather than by shelling out to the check script:
# the runner is the real host-side caller and the thing that puts the harness on
# PYTHONPATH, so a standalone invocation would pass even with that export gone.

def run_error_source_checkpoint(task_dir: str, answer, tmp_path, monkeypatch):
    """Score the real error_source checkpoint of a real task against `answer`.

    PYTHONPATH is scrubbed first: the suite runs with PYTHONPATH=lib and
    run_checkpoint inherits os.environ, so an ambient value would carry the child
    process and these tests would pass with the runner's export removed. (Safe
    in-process — PYTHONPATH is only read at interpreter startup.)
    """
    monkeypatch.delenv("PYTHONPATH", raising=False)

    task_path = REPO_ROOT / task_dir
    workspace = tmp_path / "ws"
    (workspace / "agent_output").mkdir(parents=True)
    write_json(workspace / "agent_output" / "answer.json", answer)

    task = parse_task(task_path / "task.toml")
    checkpoint = next(c for c in task.checkpoints if c.name == "error_source")
    runner = CheckpointRunner(task, task_dir=task_path, workspace=workspace)
    return runner.run_checkpoint(checkpoint)


def correct_answer_for(task_dir: str):
    """The repo-relative form an agent working inside /workspace/<repo> emits."""
    gt = json.loads((REPO_ROOT / task_dir / "ground_truth.json").read_text())
    return {"source_files": ["/".join(f["path"].split("/")[1:]) for f in gt["required_files"]]}


@pytest.mark.parametrize("task_dir", AFFECTED_TASKS)
def test_real_checkpoint_scores_a_correct_answer(tmp_path, monkeypatch, task_dir):
    """The regression that would have caught the missing module: the checkpoint
    exited 1 with empty stdout and a ModuleNotFoundError, which the runner books
    as a silent 0.0. Also guards the runner's PYTHONPATH export — remove it and
    this drops to 0.0 with 'No module named eb_verify'."""
    result = run_error_source_checkpoint(
        task_dir, correct_answer_for(task_dir), tmp_path, monkeypatch
    )

    assert result.score == 1.0, result.detail
    assert result.passed is True
    assert INFRA_SENTINEL not in result.detail


@pytest.mark.parametrize("task_dir", AFFECTED_TASKS)
def test_real_checkpoint_discriminates_a_wrong_answer(tmp_path, monkeypatch, task_dir):
    """The checkpoint must be agent-dependent — a wrong answer scores below a right one."""
    result = run_error_source_checkpoint(
        task_dir, {"source_files": ["some/irrelevant/file.py"]}, tmp_path, monkeypatch
    )

    assert result.score == 0.0
    assert INFRA_SENTINEL not in result.detail, "a wrong answer is the agent's miss, not infra"


# --- the real checkpoint must score the answer shapes the harness mandates ---
#
# run_task.py's output appendix puts `code_paths` in every task's instructions,
# and adds a required `citations` list whenever task.toml sets
# require_grounded_citations. An answer that follows those instructions to the
# letter has to score on a checkpoint carrying 0.40 of the task's weight.

def code_paths_answer_for(task_dir: str):
    """The absolute /workspace/<repo>/... shape run_task.py's appendix mandates."""
    gt = json.loads((REPO_ROOT / task_dir / "ground_truth.json").read_text())
    return {"code_paths": [{"path": f"/workspace/{f['path']}"} for f in gt["required_files"]]}


@pytest.mark.parametrize("task_dir", AFFECTED_TASKS)
def test_real_checkpoint_scores_a_code_paths_answer(tmp_path, monkeypatch, task_dir):
    """code_paths is mandated for every task, not just the
    require_grounded_citations ones, so it must score on both."""
    result = run_error_source_checkpoint(
        task_dir, code_paths_answer_for(task_dir), tmp_path, monkeypatch
    )
    assert result.score == 1.0, result.detail
    assert result.passed is True


def test_real_checkpoint_scores_a_citations_answer_on_the_grounded_citations_task(
    tmp_path, monkeypatch
):
    """err-provenance-tri-httpx-proxy-001 sets require_grounded_citations = true
    (task.toml), so run_task.py's appendix mandates a top-level `citations` list
    of {repo, file, evidence_span} dicts. An agent that names every required
    file only under `citations` -- exactly as instructed -- must score 1.0."""
    task_dir = "benchmarks/customer_escalation/err-provenance-tri-httpx-proxy-001"
    gt = json.loads((REPO_ROOT / task_dir / "ground_truth.json").read_text())
    answer = {
        "citations": [
            {
                "repo": f["path"].split("/")[0],
                "file": "/".join(f["path"].split("/")[1:]),
                "evidence_span": "x" * 20,
            }
            for f in gt["required_files"]
        ]
    }
    result = run_error_source_checkpoint(task_dir, answer, tmp_path, monkeypatch)
    assert result.score == 1.0, result.detail
    assert result.passed is True
