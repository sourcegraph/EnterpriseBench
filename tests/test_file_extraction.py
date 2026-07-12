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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "lib"

# The two live tasks whose 0.40-weight checkpoint execs this module.
AFFECTED_TASKS = [
    "benchmarks/customer_escalation/support-mapping-dual-httpx-httpcore-001",
    "benchmarks/customer_escalation/err-provenance-tri-httpx-proxy-001",
]

INFRA_SENTINEL = "VERIFIER_INFRA_ERROR"


def run_cli(answer_file, gt_file, keys="source_files,files,error_source.files", policy="suffix"):
    """Invoke the scorer exactly as the check scripts do."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    env["ANSWER_FILE"] = str(answer_file)
    env["GT_FILE"] = str(gt_file)
    proc = subprocess.run(
        [sys.executable, "-m", "eb_verify.plugins.file_extraction",
         "--keys", keys, "--policy", policy],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    return proc


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


# --- the core bug: GT is repo-prefixed, agents answer repo-relative ----------

def test_repo_prefixed_gt_matches_repo_relative_answer(tmp_path):
    """GT 'httpx/httpx/_config.py' must match an agent's 'httpx/_config.py'.

    This is the whole point of --policy suffix. The ~18 sibling check blobs use
    `gt in af or af.endswith(gt)`, which does NOT match this case and would keep
    these tasks near-zero.
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

def test_first_non_empty_key_wins(tmp_path):
    """An empty 'source_files' must not shadow a populated later key."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json",
                        {"source_files": [], "files": ["httpx/_config.py"]})
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
# Every case below used to crash or print usage text instead of a verdict. An
# exit with no JSON on stdout is the original bug wearing a different hat:
# runner.py falls back to fabricating a score from the exit code.

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
    ["--policy", "suffix"],                    # --keys omitted
    ["--keys", "source_files", "--polciy", "suffix"],  # typo'd flag
    ["--keys", "source_files", "--policy", "bogus"],   # bad choice
    ["--help"],                                # would print usage on stdout, exit 0
])
def test_cli_misuse_emits_infra_json_and_never_a_fabricated_score(tmp_path, argv):
    """A check-script typo must not be scoreable.

    `--help` is the nastiest: stock argparse prints text to stdout and exits 0,
    which runner.py's exit-code fallback reads as a pass and fabricates a 1.0.
    """
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["httpx/_config.py"]})

    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    env["ANSWER_FILE"] = str(answer)
    env["GT_FILE"] = str(gt)
    proc = subprocess.run(
        [sys.executable, "-m", "eb_verify.plugins.file_extraction", *argv],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    payload = json.loads(proc.stdout)  # must be JSON, not usage text
    assert payload["score"] == 0.0
    assert payload["passed"] is False
    assert INFRA_SENTINEL in payload["detail"]
    assert proc.returncode != 0


def test_broken_stdout_is_an_infra_error_not_a_false_zero(tmp_path):
    """If the verdict cannot reach stdout, say so on stderr — runner.py reads
    stderr as `detail` when stdout is empty, and scorer_guard scans it for the
    sentinel. Otherwise a dead pipe books a 0.0 the agent never earned."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    answer = write_json(tmp_path / "answer.json", {"source_files": ["httpx/_config.py"]})

    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    env["ANSWER_FILE"] = str(answer)
    env["GT_FILE"] = str(gt)
    proc = subprocess.Popen(
        [sys.executable, "-m", "eb_verify.plugins.file_extraction",
         "--keys", "source_files", "--policy", "suffix"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(REPO_ROOT),
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


# --- ground truth is OUR artifact: strict, and never silently shrunk ---------

def test_duplicate_gt_spellings_do_not_distort_the_denominator(tmp_path):
    """'a/a.py' and './a/a.py' are one required file, not two."""
    gt = write_json(tmp_path / "ground_truth.json", {"required_files": [
        {"path": "repo/a/a.py"}, {"path": "./repo/a/a.py"}, {"path": "repo/b/b.py"},
    ]})
    answer = write_json(tmp_path / "answer.json", {"source_files": ["a/a.py"]})
    proc = run_cli(answer, gt)
    assert score_of(proc) == 0.5, "denominator must be 2 distinct files, not 3"
    assert "1/2" in json.loads(proc.stdout)["detail"]


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


def test_deeply_nested_gt_is_an_infra_error_not_a_recursion_crash(tmp_path):
    """json.load raises RecursionError, which is neither ValueError nor OSError."""
    gt = tmp_path / "ground_truth.json"
    gt.write_text("[" * 10000 + "0" + "]" * 10000)
    answer = write_json(tmp_path / "answer.json", {"source_files": ["a/a.py"]})
    proc = run_cli(answer, gt)
    assert proc.stdout, "must print JSON rather than dying with a traceback"
    assert INFRA_SENTINEL in json.loads(proc.stdout)["detail"]
    assert proc.returncode != 0


# --- end-to-end through the actual check scripts -----------------------------

@pytest.mark.parametrize("task_dir", AFFECTED_TASKS)
def test_real_check_script_scores_a_correct_answer(tmp_path, task_dir):
    """`bash check_error_source.sh` on the real task, with a correct answer.

    This is the regression that would have caught the missing module: before the
    fix it exits 1 with empty stdout and a ModuleNotFoundError.
    """
    task = REPO_ROOT / task_dir
    gt = json.loads((task / "ground_truth.json").read_text())
    # Answer with the repo-relative form an agent working inside /workspace/<repo> emits.
    agent_files = ["/".join(f["path"].split("/")[1:]) for f in gt["required_files"]]

    workspace = tmp_path / "ws"
    (workspace / "agent_output").mkdir(parents=True)
    write_json(workspace / "agent_output" / "answer.json", {"source_files": agent_files})

    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(task)
    proc = subprocess.run(
        ["bash", str(task / "checks" / "check_error_source.sh")],
        capture_output=True, text=True, env=env, cwd=str(workspace),
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["score"] == 1.0, payload
    assert payload["passed"] is True


@pytest.mark.parametrize("task_dir", AFFECTED_TASKS)
def test_real_check_script_discriminates_a_wrong_answer(tmp_path, task_dir):
    """The checkpoint must be agent-dependent — a wrong answer scores below a right one."""
    task = REPO_ROOT / task_dir
    workspace = tmp_path / "ws"
    (workspace / "agent_output").mkdir(parents=True)
    write_json(workspace / "agent_output" / "answer.json",
               {"source_files": ["some/irrelevant/file.py"]})

    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(task)
    proc = subprocess.run(
        ["bash", str(task / "checks" / "check_error_source.sh")],
        capture_output=True, text=True, env=env, cwd=str(workspace),
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert json.loads(proc.stdout)["score"] == 0.0
