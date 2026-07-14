"""file_extraction under-credit vectors.

The under-credit half of this corpus: a verifier that zeros a correct,
spec-compliant answer books a harness bug as an agent failure. Each vector
asserts the correct 1.0 against an answer that a buggy verifier scored 0.0.
Run as an un-skippable gate (CI), separately from the marker-filtered suite.

Every vector drives the *shipped* check scripts — discovered by content, then
executed through the runner's own ``checkpoint_env`` — rather than restating their
arguments here. An earlier draft restated the scripts' ``--keys`` as a local
constant, so it exercised the module's parsing (never broken) instead of the key
list (the actual bug), and stayed green when the shipped list lost
``code_paths``/``citations``. So the key list appears nowhere below: drop a key from
a check script and these go red.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# The runner's own env builder, not a copy of it: a hand-rolled PYTHONPATH here
# would keep passing after the runner stopped exporting one.
from eb_verify.runner import checkpoint_env

REPO_ROOT = Path(__file__).resolve().parents[2]

# Discovered by content, never by name. A task rename, or the 26 further check
# scripts bead rmz1x wants pointed at this scorer, are covered automatically; a
# hardcoded task list would leave every future adopter unguarded.
FILE_EXTRACTION_CHECKS = sorted(
    path
    for path in REPO_ROOT.glob("benchmarks/*/*/checks/*.sh")
    if "eb_verify.plugins.file_extraction" in path.read_text(encoding="utf-8")
)

each_shipped_check = pytest.mark.parametrize(
    "script",
    FILE_EXTRACTION_CHECKS,
    ids=[path.parent.parent.name for path in FILE_EXTRACTION_CHECKS],
)

# (repo, path) pairs, as required_files entries really are: repo-prefixed paths, as
# both live ground truths spell them, because the scorer indexes a multi-repo
# workspace. The two files sit in different repos, so nothing here is ambiguous.
GT = [("httpx", "httpx/httpx/_config.py"), ("httpcore", "httpcore/httpcore/_client.py")]

# The shape run_task.py's mandatory output appendix dictates to every agent:
# "All file paths MUST be absolute and anchored at /workspace/<repo>/...".
ABSOLUTE = [f"/workspace/{path}" for _, path in GT]


def test_the_corpus_actually_found_the_scorers_call_sites():
    """Non-vacuity guard. Without it a rename empties the glob, every vector below
    passes by iterating nothing, and the gate goes green guarding nothing."""
    assert len(FILE_EXTRACTION_CHECKS) >= 2, (
        "no shipped check script execs eb_verify.plugins.file_extraction, so the "
        "vectors below would all pass vacuously"
    )


def run_shipped_check(script: Path, required_files, answer, tmp_path) -> dict:
    """Score ``answer`` by running the real check script, exactly as the runner does.

    ``bash <script>`` with ``cwd=workspace`` and ``checkpoint_env`` mirrors
    ``CheckpointRunner.run_checkpoint``, so the script's own ``--keys`` reaches the
    scorer untouched — a key dropped from the shipped artifact surfaces here as the
    false zero it would be in a real run.
    """
    task_dir = tmp_path / "task"
    task_dir.mkdir(exist_ok=True)
    (task_dir / "ground_truth.json").write_text(
        json.dumps({"required_files": [
            {"path": path, "repo": repo} for repo, path in required_files
        ]})
    )

    workspace = tmp_path / "ws"
    (workspace / "agent_output").mkdir(parents=True, exist_ok=True)
    (workspace / "agent_output" / "answer.json").write_text(json.dumps(answer))

    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        cwd=str(workspace),
        env=checkpoint_env(workspace, task_dir, "integrity-vector"),
    )
    assert proc.stdout, f"{script.name} printed no verdict at all (stderr: {proc.stderr})"
    return json.loads(proc.stdout)


@each_shipped_check
def test_omitted_mandated_keys_zero_a_spec_compliant_answer(script, tmp_path):
    """run_task.py's appendix mandates `code_paths` for every task, and
    require_grounded_citations adds a mandatory `citations` list. An agent that
    follows those instructions to the letter must not score 0.0 on a 0.40-weight
    checkpoint because the shipped --keys omitted the key it was told to use."""
    code_paths_answer = {"code_paths": [{"path": path} for path in ABSOLUTE]}
    payload = run_shipped_check(script, GT, code_paths_answer, tmp_path)
    assert payload["score"] == 1.0, f"code_paths: {payload['detail']}"

    citations_answer = {"citations": [
        {"repo": repo, "file": path, "evidence_span": "x" * 20} for repo, path in GT
    ]}
    payload = run_shipped_check(script, GT, citations_answer, tmp_path)
    assert payload["score"] == 1.0, f"citations: {payload['detail']}"


@each_shipped_check
def test_first_key_wins_discards_a_correct_answer(script, tmp_path):
    """A wrong guess under an earlier key must not discard a correct answer under
    a later one. First-key-wins scored this 0.0 with the full answer in the JSON."""
    answer = {"source_files": ["totally/unrelated.py"], "files": ABSOLUTE}
    payload = run_shipped_check(script, GT, answer, tmp_path)
    assert payload["score"] == 1.0, payload["detail"]
    assert payload["passed"] is True


@each_shipped_check
@pytest.mark.parametrize("suffix", [
    ":120",         # a bare line number
    ":120-140",     # a line range — observed verbatim in captured results/
    ":120:5",       # line:column, as grep -n / rg --vimgrep emit it
    "#L120",        # GitHub blob anchor
    "#L120-L140",   # GitHub range anchor
    "?L120-140",    # Sourcegraph range anchor
])
def test_citation_suffix_does_not_break_a_match(script, suffix, tmp_path):
    """Agents cite an exact line alongside the evidence span. Unstripped, the suffix
    fails the match and zeros a right answer. Every arm's dialect is here: stripping
    the baseline arm's grep-style citation but not the MCP arm's Sourcegraph anchor
    would be a mode-correlated scoring bias, an MCP regression no agent caused."""
    answer = {"source_files": [f"{path}{suffix}" for path in ABSOLUTE]}
    payload = run_shipped_check(script, GT, answer, tmp_path)
    assert payload["score"] == 1.0, f"suffix {suffix!r}: {payload['detail']}"


@each_shipped_check
def test_nested_ground_truth_does_not_zero_a_perfect_answer(script, tmp_path):
    """When one required file's path is a component-suffix of another's, a fully
    specified answer must still score: the ambiguity rule exists to stop a vague guess
    claiming several required files, not to punish a precise one. Under the old rule
    every guess in this perfect, instruction-compliant answer matched more than one
    required file and was booked 'ambiguous' — 0/3 for a flawless answer.

    The ground truth is technical_debt/refactor-orchestration-tri-babel-001's shape
    verbatim (the tokio task repeats it with Cargo.toml).
    """
    nested_gt = [
        ("babel", "packages/babel-parser/package.json"),
        ("webpack", "package.json"),
        ("nextjs", "packages/next/package.json"),
    ]
    answer = {"code_paths": [
        {"path": f"/workspace/{repo}/{path}"} for repo, path in nested_gt
    ]}
    payload = run_shipped_check(script, nested_gt, answer, tmp_path)
    assert payload["score"] == 1.0, payload["detail"]
    assert "ambiguous" not in payload["detail"]
