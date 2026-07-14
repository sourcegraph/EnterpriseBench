"""file_extraction scorer — did the agent name the right source files?

A ``python -m`` CLI rather than a registry validator: checkpoint scripts exec it
directly, and the package reaches PYTHONPATH via ``eb_verify.runner`` (host) or
``run_task.py`` staging (sandbox).

    ANSWER_FILE=... GT_FILE=... python3 -m eb_verify.plugins.file_extraction \
        --keys source_files,files,error_source.files,code_paths,citations

Scoring is recall over ``required_files[].path``, partial credit, ``passed`` at
>= 0.5. There is no precision term, so over-listing is free — that is what every
file-discovery checkpoint in this benchmark already does, and diverging here
alone would stop the tasks being comparable (bead vdeyx changes it everywhere).

Agent failures (no answer, unparseable answer, nothing matched) score a real 0.0
and exit 0. Harness failures (ground truth missing, corrupt, or empty) score 0.0
with :data:`~eb_verify.scorer_guard.INFRA_SENTINEL` in ``detail`` and exit
nonzero. Keeping the two apart is the point of the module: a broken harness must
never be recorded as an agent zero (beads ssikq/kyo34). Every exit path prints
JSON to stdout, failures included, because ``runner.py`` otherwise falls back to
fabricating a score from the exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from typing import Any, Iterable, List

# Import, never re-spell: scorer_guard greps for this exact string to tell a
# harness failure apart from an agent zero, and a drift would reintroduce the
# bug this module closes.
from eb_verify.scorer_guard import INFRA_SENTINEL

PASS_THRESHOLD = 0.5


class HarnessError(Exception):
    """The verifier could not run — our bug, never the agent's."""


class _JsonErrorParser(argparse.ArgumentParser):
    """argparse that raises instead of printing usage — every exit must carry JSON.

    Stock argparse answers a bad flag with usage on stderr and exit 2, and
    ``--help`` with text on stdout and exit 0 — which ``runner.py`` reads as a
    non-JSON success and scores 1.0. So: parse failures become HarnessErrors,
    and help is off (this docstring is the usage doc).
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
        print(json.dumps(verdict))
        sys.stdout.flush()  # print() buffers: flush inside the guard, not at exit
    except OSError:
        # The verdict cannot reach the scoring channel. runner.py reads stderr as
        # `detail` when stdout is empty and scorer_guard scans it for the sentinel,
        # so an undeliverable score gets re-run instead of booked as a 0.0 the agent
        # never earned. Redirect the dead fd first, or the same BrokenPipeError
        # fires again at shutdown and masks this message.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        print(f"{INFRA_SENTINEL}: verdict could not be written to stdout", file=sys.stderr)
        return 2
    return 2 if infra else 0


# Both citation dialects the arms produce: grep/editor style ('_config.py:120',
# ':120-140' as in the captured 'reflector.go:417-418', ':120:5' from `rg --vimgrep`)
# and blob anchors ('#L120', '#L120-L140' from GitHub, '?L120-140' from Sourcegraph).
# Sourcegraph is a first-class arm, so stripping the baseline arm's dialect but not
# the MCP arm's would be a mode-correlated scoring bias — an MCP regression no agent
# caused. Over-stripping is not a risk: the match is $-anchored behind a literal ':'
# or '#L'/'?L', so 'report-2024-2025.md' and 'v1.2-140/file.py' are untouched.
_CITATION_SUFFIX_RE = re.compile(
    r"""(?: : | [#?] L )        # grep-style ':120', or a GitHub/Sourcegraph anchor
        \d+ (?: [:-] L? \d+ )*  # the line, plus any column and range parts
        $""",
    re.VERBOSE,
)


def components(path: str) -> List[str]:
    """Path components, with the decorations agents add stripped.

    Handles './', '..', a leading '/', backslashes, quotes, whitespace, and a
    trailing line/anchor citation suffix (see :data:`_CITATION_SUFFIX_RE`), so
    matching compares path structure rather than punctuation. Resolution is lexical
    (normpath, not realpath) because these paths name files in a repo that need not
    exist here.
    """
    cleaned = str(path).strip().strip("'\"").replace("\\", "/")
    cleaned = _CITATION_SUFFIX_RE.sub("", cleaned)
    return [p for p in posixpath.normpath(cleaned).split("/") if p not in ("", ".", "..")]


def matches(gt_path: str, agent_path: str) -> bool:
    """Does ``agent_path`` name the file that ``gt_path`` names?

    Symmetric component-suffix match. Ground truth is repo-prefixed
    ('httpx/httpx/_config.py') because it indexes a multi-repo workspace, while an
    agent working inside /workspace/httpx answers repo-relative ('httpx/_config.py').
    Neither is wrong, so either may be the suffix of the other. Comparing components
    rather than raw strings is what keeps 'httpx/my_config.py' from satisfying a
    ground truth of '.../_config.py'.
    """
    gt, agent = components(gt_path), components(agent_path)
    if not gt or not agent:
        return False
    shorter, longer = (gt, agent) if len(gt) <= len(agent) else (agent, gt)
    return longer[-len(shorter):] == shorter


def score_answer(gt_paths: List[str], found: List[str]) -> tuple[set[str], List[str]]:
    """Which required files the agent identified, and which guesses were ambiguous.

    Credit is per *answer*, never per ground-truth entry: with httpx and httpcore
    both holding a ``_client.py``, crediting each entry independently would let the
    lone guess "_client.py" claim both and turn a non-answer into full marks.

    An answer is credited to the most specific required file it *refines* — one whose
    path is a component-suffix of the answer's, i.e. the agent named that file at
    least as precisely as the ground truth spells it. An answer that refines nothing
    is an abbreviation, and specificity is demanded only where it distinguishes
    something: it still scores against the one required file it abbreviates
    ("setup.py" against a lone ground truth of "httpx/setup.py"), and is credited to
    none when it is a tail shared by several.

    Both halves of the refinement rule are load-bearing:

    * Demanding an exact match instead would credit nothing for the absolute
      ``/workspace/<repo>/...`` paths run_task.py *mandates* every agent emit, since
      those are strictly longer than any ground-truth entry.
    * Crediting the longest hit, with no refinement test, would hand "_client.py" the
      deeper of ``httpx/_client.py`` and ``httpcore/httpcore/_async/_client.py`` — a
      passing 0.5 for a one-word non-answer.

    It is refinement that lets a required file be a tail of another (the babel/tokio
    tasks require a bare "package.json" alongside "packages/babel-parser/package.json")
    without every guess in a perfect answer looking ambiguous.

    Order-independent, and over-credit is impossible: a matched entry is never "used
    up", and ``matched`` is a set of ground-truth entries, so recall stays capped at
    1.0 however many answers point at one file.
    """
    matched: set[str] = set()
    ambiguous: List[str] = []
    for af in found:
        hits = [gt for gt in gt_paths if matches(gt, af)]
        depth = len(components(af))
        # Ties are impossible: two refined entries of equal depth would both be a
        # suffix of `af` and therefore the same path, which ground_truth_files rejects.
        refined = [gt for gt in hits if len(components(gt)) <= depth]
        if refined:
            matched.add(max(refined, key=lambda gt: len(components(gt))))
        elif len(hits) == 1:
            matched.add(hits[0])  # an abbreviation of exactly one required file
        elif hits:
            ambiguous.append(af)  # a tail of several: it distinguishes none of them
    return matched, ambiguous


def load_json(path: str, what: str) -> Any:
    """Read a JSON file, or raise HarnessError. Never raises anything else.

    An escaping exception would kill the process before it printed JSON, which is
    the silent-scoring bug this module exists to close. ValueError covers both
    JSONDecodeError and UnicodeDecodeError; RecursionError (deeply nested JSON) is
    neither that nor an OSError.
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

    Strict where :func:`agent_files` is lenient: an agent's answer is untrusted
    input to interpret generously, but ground truth is our own artifact, and
    skipping a malformed entry would shrink the recall denominator and inflate
    every agent's score. So anything unexpected fails closed.
    """
    gt = load_json(gt_file, "ground_truth.json")
    if not isinstance(gt, dict):
        raise HarnessError("ground_truth.json is not a JSON object")

    required = gt.get("required_files")
    if not isinstance(required, list) or not required:
        raise HarnessError("ground_truth.json has no required_files to score against")

    paths: List[str] = []
    seen: dict[tuple[str, ...], str] = {}  # component list -> the repo that declared it
    for i, entry in enumerate(required):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise HarnessError(
                f"ground_truth.json required_files[{i}] has no string 'path'"
            )
        path = entry["path"].strip()
        if not path:
            raise HarnessError(f"ground_truth.json required_files[{i}] path is empty")

        # Two entries with the same components collapse into one required file only
        # when the same repo declares both — 'a/x.py' and './a/x.py' are one file, and
        # counting them twice would distort the denominator while making an answer that
        # matched both look ambiguous below.
        #
        # Any other collision fails closed. The same path from two *different* repos is
        # two files a component-suffix matcher cannot tell apart, and collapsing them
        # would shrink the denominator and inflate every agent's score. A collision we
        # cannot even adjudicate — because an entry omitted the 'repo' the task schema
        # requires — is the same hazard with less evidence, so it gets the same answer.
        key = tuple(components(path))
        repo = entry.get("repo")
        if not isinstance(repo, str) or not repo.strip():
            raise HarnessError(
                f"ground_truth.json required_files[{i}] ('{path}') has no string 'repo'; "
                f"it is required to tell same-named files in different repos apart"
            )
        repo = repo.strip()
        if key in seen:
            if seen[key] != repo:
                raise HarnessError(
                    f"ground_truth.json requires '{path}' from two different repos "
                    f"({seen[key]!r} and {repo!r}); a component-suffix matcher cannot "
                    f"distinguish them — repo-qualify the paths"
                )
            continue
        seen[key] = repo
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
    """Files the agent named, unioned across every key.

    Union, not first-key-wins: scoring is recall-only and :func:`score_answer`
    dedups on the matched ground-truth entry, so accumulating cannot over-credit,
    while stopping at the first non-empty key lets one wrong guess discard a
    correct answer sitting under a later, harness-advertised key.
    """
    if not isinstance(answer, dict):
        return []

    found: List[str] = []
    for key in keys:
        value = _lookup(answer, key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            continue
        found.extend(p for p in (_entry_path(e) for e in value) if p)
    return found


def build_parser() -> argparse.ArgumentParser:
    parser = _JsonErrorParser(
        prog="python -m eb_verify.plugins.file_extraction",
        description="Score the source files an agent named against ground truth.",
        add_help=False,
    )
    parser.add_argument(
        "--keys",
        required=True,
        help="Comma-separated answer keys, unioned — every key is read, not just the "
             "first populated one. Dotted keys traverse nested objects "
             "(e.g. error_source.files).",
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
        # Deduped: one path named under several keys is one ambiguous guess, and
        # listing it twice makes the detail line misread as two distinct misses.
        detail += (
            f" (ambiguous, matched several required files so credited none: "
            f"{', '.join(dict.fromkeys(ambiguous))})"
        )
    return emit(score, detail)


if __name__ == "__main__":
    sys.exit(main())
