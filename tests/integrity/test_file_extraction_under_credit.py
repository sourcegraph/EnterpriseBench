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
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

# The runner's own env builder, not a copy of it: a hand-rolled PYTHONPATH here
# would keep passing after the runner stopped exporting one. _LIB_DIR is the runner's
# own notion of where eb_verify lives — asserting against a copy of it would be the
# same drift this whole corpus exists to catch.
from eb_verify.runner import _LIB_DIR, checkpoint_env

# The scorer's OWN argument parser, so shipped_keys() reads --keys exactly as the
# scorer does — no second, drift-prone parser. HarnessError is what it raises on a
# malformed invocation.
from eb_verify.plugins.file_extraction import HarnessError, build_parser

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


def shipped_keys(script: Path, tmp_path) -> set[str]:
    """The ``--keys`` a shipped check script actually passes to the scorer.

    Captured from the REAL post-shell-expansion argv via a PATH shim standing in for
    ``python3``, never parsed from the script text. That is what makes it robust to
    every shell form a text scan trips on — ``--keys=a,b``, a ``$KEYS`` variable, a
    line-continuation reformat — because the shim reads argv after the shell is done
    with it. (An earlier plan to regex the script text was rejected for exactly that
    fragility.)
    """
    module = "eb_verify.plugins.file_extraction"
    bindir = tmp_path / "shimbin"
    bindir.mkdir(exist_ok=True)
    dump = tmp_path / "argv.txt"
    # A bash shim (never `#!/usr/bin/env python3`, which would re-invoke itself and
    # recurse). It records argv ONLY for the scorer invocation and delegates every
    # other python3 call to the real interpreter, so a future adopter that runs python3
    # for setup before the scorer is not silently swallowed — the corpus auto-adopts
    # check scripts by content-glob, so this test must survive scripts it has not seen.
    shim = bindir / "python3"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do\n'
        f'  if [ "$a" = {shlex.quote(module)} ]; then\n'
        f'    printf "%s\\n" "$@" > {shlex.quote(str(dump))}\n'
        "    exit 0\n"
        "  fi\n"
        "done\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    shim.chmod(0o755)

    workspace = tmp_path / "ws"
    (workspace / "agent_output").mkdir(parents=True, exist_ok=True)
    task_dir = tmp_path / "task"
    task_dir.mkdir(exist_ok=True)
    env = checkpoint_env(workspace, task_dir, "shipped-keys-probe")
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]

    subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, cwd=str(workspace),
        env=env, timeout=30, check=True,
    )
    if not dump.exists():
        return set()  # the script never invoked the scorer; the caller asserts non-vacuity
    argv = dump.read_text().splitlines()
    # Slice to the scorer's own arguments (everything after the module token) and hand
    # them to the scorer's real parser, so `--keys a,b` and `--keys=a,b` both normalize
    # the one way the scorer itself normalizes them. The captured argv is already
    # post-shell-expansion, so a `$KEYS` variable or a line-continuation is long gone.
    scorer_argv = argv[argv.index(module) + 1:] if module in argv else argv
    try:
        raw = build_parser().parse_known_args(scorer_argv)[0].keys or ""
    except HarnessError:
        return set()  # no --keys at all; non-vacuity assertion in the test reports it
    return {k.strip() for k in raw.split(",") if k.strip()}


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


@pytest.mark.parametrize(
    "script",
    FILE_EXTRACTION_CHECKS,
    ids=[path.parent.parent.name for path in FILE_EXTRACTION_CHECKS],
)
def test_every_shipped_key_has_a_covering_vector(script, tmp_path):
    """A key ADDED to a shipped ``--keys`` must not go uncovered.

    The parametrized test above catches a key REMOVED — its answer shape drops to 0.0.
    This catches the other direction: a new key with no vector in ``ANSWER_SHAPES``
    would be a silent false zero for every agent that answered under it, and nothing
    would notice (adding ``,evidence_files`` to a shipped script left the gate green).
    Together the two force ``ANSWER_SHAPES`` to equal the shipped key set — one
    advertised case per shipped entry, no more and no fewer.

    The shipped keys are DISCOVERED BY EXECUTION (``shipped_keys`` captures the real
    argv), never restated here, so this cannot drift from the artifact the way a copied
    key list would.
    """
    keys = shipped_keys(script, tmp_path)
    assert keys, f"{script} passed no --keys to the scorer"  # non-vacuity
    uncovered = keys - set(ANSWER_SHAPES)
    assert not uncovered, (
        f"{script.parent.parent.name} ships --keys {sorted(keys)} but ANSWER_SHAPES "
        f"exercises no answer shape for {sorted(uncovered)}; a dropped or added key "
        f"would false-zero a spec-compliant answer with no gate catching it"
    )


def test_checkpoint_env_prepends_the_lib_dir_to_pythonpath(tmp_path):
    """A DIRECT guard on the PYTHONPATH export, not one riding on an import succeeding.

    Every execution-based vector above imports eb_verify via `python -m`, and CI
    pip-installs lib/, so that import resolves with OR without PYTHONPATH — a change
    that silently dropped the export from checkpoint_env would ship green through all of
    them. This asserts the built env dict itself against the runner's own _LIB_DIR, so
    the export is guarded regardless of how eb_verify happens to be installed. Pure unit
    check: it never runs on the shipped scorer import path, so it cannot itself become a
    false-zero source in the sandbox.
    """
    env = checkpoint_env(tmp_path / "ws", tmp_path / "task", "probe")
    first = env["PYTHONPATH"].split(os.pathsep)[0]
    assert first == str(_LIB_DIR), (
        f"checkpoint_env must prepend the lib dir to PYTHONPATH; got {env['PYTHONPATH']!r}"
    )
