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


def run_shipped_check(script: Path, answer, tmp_path) -> dict:
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
            {"path": path, "repo": repo} for repo, path in GT
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
        timeout=30,  # as every other check-script runner in tests/ does: a hung
                     # script must fail this gate, not stall CI on it forever
    )
    assert proc.stdout, f"{script.name} printed no verdict at all (stderr: {proc.stderr})"
    return json.loads(proc.stdout)


# One answer shape per key the shipped --keys must carry, so that dropping ANY key
# from a check script turns this gate red. Derived by mutation, not by reading the
# scripts: with a vector per key, `sed -i 's/,<key>//'` on a shipped script fails
# exactly the case that names it.
#
# Coverage is the point, not variety. Scorer-internal behaviour (the full citation
# dialect matrix, the ambiguity rule, the nested-ground-truth case) is exercised at
# the unit layer in tests/test_file_extraction.py, where it does not cost a
# merge-blocking gate a subprocess per case and cannot drift from a second copy of
# the same table. What lives here is only what depends on the shipped artifact.
#
# `source_files` carries a citation suffix so that one end-to-end dialect case does
# cross the real script — the suffix strip and the key list are the two halves of the
# same false zero.
ANSWER_SHAPES = {
    "source_files": {"source_files": [f"{path}:120-140" for path in ABSOLUTE]},
    "files": {"source_files": ["totally/unrelated.py"], "files": ABSOLUTE},
    "error_source.files": {"error_source": {"files": ABSOLUTE}},
    "code_paths": {"code_paths": [{"path": path} for path in ABSOLUTE]},
    "citations": {"citations": [
        {"repo": repo, "file": path, "evidence_span": "x" * 20} for repo, path in GT
    ]},
}


@pytest.mark.parametrize(
    "script",
    FILE_EXTRACTION_CHECKS,
    ids=[path.parent.parent.name for path in FILE_EXTRACTION_CHECKS],
)
@pytest.mark.parametrize("key", list(ANSWER_SHAPES), ids=list(ANSWER_SHAPES))
def test_omitted_mandated_keys_zero_a_spec_compliant_answer(script, key, tmp_path):
    """Every key the shipped --keys advertises must actually score.

    run_task.py's appendix mandates `code_paths` in every task's instructions, and
    require_grounded_citations adds a mandatory `citations` list, so an agent that
    followed its instructions to the letter must not be scored 0.0 on a 0.40-weight
    checkpoint because the check script omitted the key it was told to use. The other
    three keys are the shapes the scorer promises to accept; a key silently dropped
    from a script is a false zero for every agent that used it.

    The `files` case doubles as the first-key-wins guard: the answer is wrong under
    the earlier `source_files` and right under the later `files`, so it only scores
    if the keys are unioned rather than stopping at the first populated one.
    """
    payload = run_shipped_check(script, ANSWER_SHAPES[key], tmp_path)
    assert payload["score"] == 1.0, f"--keys is missing {key!r}: {payload['detail']}"
    assert payload["passed"] is True
