"""file_extraction scorer — did the agent name the right source files?

Run as a ``python -m`` CLI by checkpoint scripts, not through the validator
registry. ``eb_verify.runner`` puts the package on PYTHONPATH for every
checkpoint it execs; in the sandbox ``run_task.py`` stages it and does the same.

    ANSWER_FILE=... GT_FILE=... python3 -m eb_verify.plugins.file_extraction \
        --keys source_files,files,error_source.files

Scoring: recall over ground truth — the fraction of ``required_files[].path``
that the agent named. Partial credit; ``passed`` at >= 0.5. An answer only earns
a required file if it identifies that file and no other (see :func:`score_answer`).

Recall carries no precision term, so over-listing is free: an agent that names
every plausible file in the repo scores 1.0 without investigating. That is the
semantics every file-discovery checkpoint in this benchmark already uses, so it
is left alone here rather than silently diverging in one module — changing it is
bead vdeyx, and it has to change everywhere at once or the tasks stop being
comparable.

Two failure modes are deliberately kept apart:

* The *agent* failed (no answer.json, unparseable answer, nothing matched)
  -> a legitimate ``0.0`` on exit 0.
* The *harness* failed (ground truth missing, corrupt, or carrying no
  required_files) -> score 0.0 whose ``detail`` carries
  :data:`~eb_verify.scorer_guard.INFRA_SENTINEL`, on a nonzero exit.

The distinction is the point of the module: a broken harness must never be
recorded as an agent scoring zero (beads ssikq/kyo34). JSON always goes to
stdout, even when exiting nonzero, so ``runner.py``'s json-parse branch wins
deterministically instead of falling back to fabricating a score from the exit
code.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
from typing import Any, Iterable, List

# scorer_guard greps stderr/detail for this exact string to tell "the harness
# broke" apart from "the agent scored zero". Import it rather than copying it:
# the two must agree, and a silent drift would book our own infra failures as
# agent zeros — the bug this module exists to close.
from eb_verify.scorer_guard import INFRA_SENTINEL

PASS_THRESHOLD = 0.5


class HarnessError(Exception):
    """The verifier could not run — our bug, never the agent's."""


class _JsonErrorParser(argparse.ArgumentParser):
    """An argparse parser that never writes bare usage text to the score channel.

    Stock argparse answers a bad flag with usage on stderr and ``sys.exit(2)``,
    and ``--help`` with text on stdout and ``sys.exit(0)`` — which ``runner.py``
    would read as a non-JSON success and fabricate a 1.0 from. Every exit from
    this CLI has to carry JSON, so parse failures become HarnessErrors and help
    is switched off (the module docstring is the usage doc).
    """

    def error(self, message: str):  # noqa: D102 — argparse hook
        raise HarnessError(f"bad verifier invocation: {message}")


def emit(score: float, detail: str, *, infra: bool = False) -> int:
    """Print the verdict as JSON and return the process exit code."""
    if infra:
        detail = f"{INFRA_SENTINEL}: {detail}"
    verdict = {
        "score": round(score, 4),
        "passed": (not infra) and score >= PASS_THRESHOLD,
        "detail": detail,
    }
    try:
        # Flush inside the guard: print() buffers, so a broken pipe would
        # otherwise surface as an "Exception ignored" traceback during
        # interpreter shutdown, long after this function returned cleanly.
        print(json.dumps(verdict))
        sys.stdout.flush()
    except OSError:
        # stdout is gone (broken pipe, closed fd), so the verdict cannot reach
        # the scoring channel. Say so on stderr: runner.py falls back to stderr
        # for `detail`, and scorer_guard scans it for this sentinel, so an
        # undeliverable score is re-run rather than booked as a 0.0 the agent
        # never earned. Point the dead fd at /dev/null first, or the same
        # BrokenPipeError fires again at shutdown and masks this message.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        print(f"{INFRA_SENTINEL}: verdict could not be written to stdout", file=sys.stderr)
        return 2
    return 2 if infra else 0


def components(path: str) -> List[str]:
    """Split a path into comparable components, tolerating agent formatting.

    Normalizes away the decorations agents add ('./', '..', a leading '/',
    backslashes, surrounding whitespace or quotes) so matching compares path
    structure rather than punctuation. normpath, not realpath: resolution is
    lexical because these paths name files in a repo that need not exist here.
    posixpath, not os.path, so a Windows host does not start emitting backslashes.
    """
    cleaned = str(path).strip().strip("'\"").replace("\\", "/")
    return [p for p in posixpath.normpath(cleaned).split("/") if p not in ("", ".", "..")]


def matches(gt_path: str, agent_path: str) -> bool:
    """Does ``agent_path`` name the file that ``gt_path`` names?

    Symmetric path-component suffix match. Ground truth is repo-prefixed
    ('httpx/httpx/_config.py') because it indexes a multi-repo workspace; an agent
    working inside /workspace/httpx naturally answers repo-relative
    ('httpx/_config.py'). Neither is wrong, so either may be the suffix of the other.

    Comparing components (not raw string endswith) is what keeps
    'httpx/my_config.py' from satisfying a GT of '.../_config.py' — the trap the
    ~26 sibling check blobs fall into with `af.endswith(gt)`.
    """
    gt, agent = components(gt_path), components(agent_path)
    if not gt or not agent:
        return False
    shorter, longer = (gt, agent) if len(gt) <= len(agent) else (agent, gt)
    return longer[-len(shorter):] == shorter


def score_answer(gt_paths: List[str], found: List[str]) -> "tuple[set, List[str]]":
    """Which required files the agent identified, and which guesses were ambiguous.

    Credit is per *answer*, not per ground-truth entry, and only an answer that
    picks out exactly one required file earns it. Scoring each GT entry
    independently would let one under-specified guess claim several at once: with
    httpx and httpcore both holding a ``_client.py``, the single answer
    "_client.py" would otherwise score both repos' files and turn a non-answer
    into full marks.

    The flip side is that specificity is only demanded where it distinguishes
    something. "setup.py" against a lone GT of "httpx/setup.py" is unambiguous
    and scores — the agent named the file; there was no second candidate to tell
    it apart from. That is why this is an ambiguity rule and not a
    minimum-path-depth rule: depth is a proxy that both over-credits (two repos
    sharing a 2-component tail) and under-credits (a repo-root file, whose only
    natural repo-relative answer is a bare name).
    """
    matched: set = set()
    ambiguous: List[str] = []
    for af in found:
        hits = [gt for gt in gt_paths if matches(gt, af)]
        if len(hits) > 1:
            ambiguous.append(af)
        elif hits:
            matched.add(hits[0])
    return matched, ambiguous


def load_json(path: str, what: str) -> Any:
    """Read a JSON file, or raise HarnessError. Never raises anything else.

    The except clause is deliberately broad: any escaping exception would kill
    the process before it printed JSON, which is the exact silent-scoring bug
    this module was written to close. ValueError covers both JSONDecodeError and
    UnicodeDecodeError (non-UTF-8 bytes); RecursionError (deeply nested JSON) is
    neither that nor an OSError, and would otherwise escape.
    """
    if not path:
        raise HarnessError(f"{what} path is not set")
    try:
        if not os.path.isfile(path):
            raise HarnessError(f"{what} not found at {path}")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, RecursionError) as exc:
        raise HarnessError(f"{what} at {path} is unreadable: {exc}") from exc


def ground_truth_files(gt_file: str) -> List[str]:
    """The distinct required_files[].path entries, in order.

    Strict where :func:`agent_files` is lenient. An agent's answer is untrusted
    input to be interpreted generously; ground truth is OUR artifact, and a
    malformed entry means the task is mis-authored. Silently skipping such an
    entry would quietly shrink the denominator and inflate every agent's score,
    so anything unexpected fails closed as a harness error instead.
    """
    gt = load_json(gt_file, "ground_truth.json")
    if not isinstance(gt, dict):
        raise HarnessError("ground_truth.json is not a JSON object")

    required = gt.get("required_files")
    if not isinstance(required, list) or not required:
        raise HarnessError("ground_truth.json has no required_files to score against")

    paths: List[str] = []
    seen: set = set()
    for i, entry in enumerate(required):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise HarnessError(
                f"ground_truth.json required_files[{i}] has no string 'path'"
            )
        path = entry["path"].strip()
        if not path:
            raise HarnessError(f"ground_truth.json required_files[{i}] path is empty")
        # Two spellings of one file ('a/x.py' and './a/x.py') are one required
        # file: counting both would distort the recall denominator, and an answer
        # matching both would look ambiguous to the scorer below.
        key = tuple(components(path))
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _entry_path(entry: Any) -> str:
    """A file entry is either a bare path or a dict carrying one."""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in ("path", "file", "filename"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _lookup(answer: Any, dotted_key: str) -> Any:
    """Resolve a possibly-dotted key ('error_source.files') against the answer."""
    node = answer
    for part in dotted_key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def agent_files(answer: Any, keys: Iterable[str]) -> List[str]:
    """Files the agent named, from the first key that yields any.

    Keys are tried in the order given, and an empty value falls through to the
    next: an agent that writes `"source_files": []` alongside a populated
    `"files"` meant the latter.
    """
    if not isinstance(answer, dict):
        return []

    for key in keys:
        value = _lookup(answer, key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            continue
        paths = [p for p in (_entry_path(e) for e in value) if p]
        if paths:
            return paths
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = _JsonErrorParser(
        prog="python -m eb_verify.plugins.file_extraction",
        description="Score the source files an agent named against ground truth.",
        add_help=False,
    )
    parser.add_argument(
        "--keys",
        required=True,
        help="Comma-separated answer keys to try, in order. Dotted keys traverse "
             "nested objects (e.g. error_source.files).",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise HarnessError("--keys resolved to no usable keys")
        gt_paths = ground_truth_files(os.environ.get("GT_FILE", ""))
    except HarnessError as exc:
        return emit(0.0, str(exc), infra=True)

    # Past this point every failure is the agent's, and scores a real 0.0.
    try:
        answer = load_json(os.environ.get("ANSWER_FILE", ""), "answer.json")
    except HarnessError as exc:
        return emit(0.0, f"No usable agent answer ({exc})")

    found = agent_files(answer, keys)
    if not found:
        return emit(0.0, f"Agent answer names no files under any of: {', '.join(keys)}")

    matched, ambiguous = score_answer(gt_paths, found)
    score = len(matched) / len(gt_paths)
    missed = [gt for gt in gt_paths if gt not in matched]

    detail = f"Found {len(matched)}/{len(gt_paths)} required source files"
    if missed:
        detail += f" (missed: {', '.join(missed)})"
    if ambiguous:
        detail += (
            f" (ambiguous, matched several required files so credited none: "
            f"{', '.join(ambiguous)})"
        )
    return emit(score, detail)


if __name__ == "__main__":
    sys.exit(main())
