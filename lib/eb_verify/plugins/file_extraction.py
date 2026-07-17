"""file_extraction scorer — did the agent name the right source files?

A scorer, not an artifact validator: the registry protocol in
:mod:`eb_verify.plugins` returns a pass/fail ``ValidationResult`` and has nowhere
to put a number, so this is a ``python -m`` CLI that checkpoint scripts exec
directly. The package reaches PYTHONPATH via :func:`eb_verify.runner.checkpoint_env`
(host) or ``run_task.py`` staging (sandbox).

    ANSWER_FILE=... GT_FILE=... python3 -m eb_verify.plugins.file_extraction \
        --keys source_files,files,error_source.files,code_paths,citations

Scoring is recall over ``required_files[].path``, partial credit, ``passed`` at
>= 0.5. There is no precision term, so over-listing is free — that is what every
file-discovery checkpoint in this benchmark already does, and diverging here
alone would stop the tasks being comparable (bead vdeyx changes it everywhere).

The module's reason to exist: a broken harness must never be recorded as an agent
zero. Agent failures score a real 0.0 and exit 0; harness failures score 0.0 with
:data:`~eb_verify.scorer_guard.INFRA_SENTINEL` in ``detail`` and exit nonzero.
:func:`load_json` draws the line. Every exit path prints JSON to stdout, failures
included, because a verdict that reaches no channel is scored as a re-run, not a 0.0.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from typing import Any, Iterable, List, NoReturn

# Import, never re-spell: scorer_guard greps for this exact string to tell a
# harness failure apart from an agent zero, and a drift would reintroduce the
# bug this module closes.
from eb_verify.scorer_guard import INFRA_SENTINEL

PASS_THRESHOLD = 0.5


class HarnessError(Exception):
    """The verifier could not run — our bug, never the agent's."""


class AgentFault(HarnessError):
    """The agent produced no usable answer — a real 0.0, not a harness failure.

    A subclass, so ground-truth faults (which are infra however they fail) can
    propagate to the base class without :func:`ground_truth_files` classifying them.
    """


class _JsonErrorParser(argparse.ArgumentParser):
    """argparse that raises instead of printing usage — every exit must carry JSON.

    Stock argparse answers a bad flag with usage on stderr and exit 2, and
    ``--help`` with text on stdout and exit 0 — which ``runner.py`` reads as a
    non-JSON success and scores 1.0. So: parse failures become HarnessErrors,
    and help is off (this docstring is the usage doc).
    """

    def error(self, message: str) -> NoReturn:  # noqa: D102 — argparse hook
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
        # A dead stdout pipe. Scoring is already safe without us: the verdict channel is
        # empty, and scorer_guard books empty stdout as verifier_did_not_run whatever the
        # exit code. What this rescues is the *diagnosis* — redirect the dead fd so the
        # interpreter's own shutdown flush cannot raise a second BrokenPipeError over this
        # message, and leave the sentinel on stderr so the logs say why.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull, sys.stdout.fileno())
            finally:
                os.close(devnull)
        except OSError:
            pass
        print(f"{INFRA_SENTINEL}: verdict could not be written to stdout", file=sys.stderr)
        return 2
    return 2 if infra else 0


# Both citation dialects the arms produce: grep/editor style ('_config.py:120',
# ':120-140', ':120:5' from `rg --vimgrep`) and blob anchors ('#L120', '#L120-L140'
# from GitHub, '?L120-140' from Sourcegraph). Sourcegraph is a first-class arm, so
# stripping the baseline arm's dialect but not the MCP arm's would be a mode-correlated
# scoring bias — an MCP regression no agent caused. Over-stripping is not a risk: the
# match is $-anchored behind a literal ':' or '#L'/'?L', so 'report-2024-2025.md' and
# 'v1.2-140/file.py' are untouched.
#
# The repetition MUST stay bounded. The pattern has no start anchor, so `re.sub` retries
# it at every ':'; with an unbounded `(?:[:-]L?\d+)*` each of those O(n) attempts runs to
# the end and unwinds when `$` fails — quadratic overall, reachable straight from a
# graded answer.json, and a 200KB non-matching tail blows the 120s checkpoint timeout
# into a false 0.0. Bounding makes each attempt O(1) and the scan linear. The bounds cost
# nothing real: no citation carries a 10-digit line number or 7 range parts, and an input
# past them degrades in the fail-safe direction — it strips a shorter tail (a false miss),
# never a longer one (a false match).
_CITATION_SUFFIX_RE = re.compile(
    r"""(?: : | [#?] L )        # grep-style ':120', or a GitHub/Sourcegraph anchor
        \d{1,9} (?: [:-] L? \d{1,9} ){0,6}  # the line, plus column and range parts
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


def _matches_parts(gt: List[str], agent: List[str]) -> bool:
    """Does an answer name the file ground truth names? — over pre-split components.

    Symmetric component-suffix match. Ground truth is repo-prefixed
    ('httpx/httpx/_config.py') because it indexes a multi-repo workspace, while an
    agent working inside /workspace/httpx answers repo-relative ('httpx/_config.py').
    Neither is wrong, so either component list may be the suffix of the other. Comparing
    components rather than raw strings is what keeps 'httpx/my_config.py' from
    satisfying a ground truth of '.../_config.py'.
    """
    if not gt or not agent:
        return False
    shorter, longer = (gt, agent) if len(gt) <= len(agent) else (agent, gt)
    return longer[-len(shorter):] == shorter


def score_answer(gt_paths: List[str], found: List[str]) -> tuple[set[str], List[str]]:
    """Which required files the agent identified, and which guesses were ambiguous.

    Credit is per *answer*, never per ground-truth entry: with httpx and httpcore
    both holding a ``_client.py``, crediting each entry independently would let the
    lone guess "_client.py" claim both and turn a non-answer into full marks.

    Each required file an answer matches is one the answer either *refines* — spells at
    least as precisely as the ground truth does, so the ground truth's components are a
    suffix of the answer's — or merely *abbreviates*. An answer is read against that
    split:

    * It names a required file **exactly**: credit that file. Nothing outranks the
      agent spelling a required path the way ground truth spells it.
    * It refines some and abbreviates others: it distinguishes none of them, and is
      credited to none. ``y/a.py`` refines a required "a.py" and abbreviates a required
      "x/y/a.py", and there is no ground for preferring either reading of it.
    * It only refines: credit the most specific one. This is what lets a required file
      be a tail of another (the babel/tokio tasks require a bare "package.json"
      alongside "packages/babel-parser/package.json") without every guess in a perfect
      answer looking ambiguous.
    * It only abbreviates: specificity is demanded only where it distinguishes
      something, so it still scores against the *one* required file it abbreviates
      ("setup.py" against a lone ground truth of "httpx/setup.py"), and is credited to
      none when it is a tail shared by several.

    A ground truth ending in ``workspace/<repo>/<file>`` abbreviates back into the
    absolute ``/workspace/<repo>/...`` shape run_task.py mandates — a latent false zero,
    pinned by a test and tracked as EnterpriseBench-d900w.

    Order-independent, and over-credit is impossible: a matched entry is never "used
    up", and ``matched`` is a set of ground-truth entries, so recall stays capped at
    1.0 however many answers point at one file.
    """
    gt_parts = {gt: components(gt) for gt in gt_paths}
    matched: set[str] = set()
    ambiguous: List[str] = []
    for af in found:
        af_parts = components(af)
        hits = [gt for gt in gt_paths if _matches_parts(gt_parts[gt], af_parts)]
        # Ties are impossible: two hits of equal depth would both be a suffix of `af`
        # and therefore the same path, which ground_truth_files rejects. So there is at
        # most one exact hit, and `refined` has a single deepest entry.
        refined = [gt for gt in hits if len(gt_parts[gt]) <= len(af_parts)]
        abbreviated = [gt for gt in hits if len(gt_parts[gt]) > len(af_parts)]
        exact = next((gt for gt in refined if gt_parts[gt] == af_parts), None)
        if exact is not None:
            matched.add(exact)
        elif refined and abbreviated:
            ambiguous.append(af)
        elif refined:
            matched.add(max(refined, key=lambda gt: len(gt_parts[gt])))
        elif len(abbreviated) == 1:
            matched.add(abbreviated[0])
        elif abbreviated:
            ambiguous.append(af)
    return matched, ambiguous


def load_json(path: str, what: str) -> Any:
    """Read a JSON file, or raise a HarnessError subclass. Never raises anything else.

    An escaping exception would kill the process before it printed JSON, which is the
    silent-scoring bug this module exists to close. The failure is *classified* so a
    caller can tell an agent fault from an infra fault:

    * unset path -> :class:`HarnessError` (the runner never said where to look — infra)
    * absent file -> :class:`AgentFault` (the agent wrote nothing — an agent zero)
    * present but ``OSError`` on read -> :class:`HarnessError` (permission/IO — infra)
    * unparseable bytes -> :class:`AgentFault` (bad content — the writer's fault)

    ValueError covers both JSONDecodeError and UnicodeDecodeError; RecursionError
    (deeply nested JSON) is neither that nor an OSError.
    """
    if not path or not path.strip():
        # Empty or whitespace-only: the runner never gave us a real path. That is a
        # misconfiguration, not the agent writing nothing — os.path.isfile(" ") is
        # False, so without this a blank env value would masquerade as an absent
        # answer file (an agent 0.0) instead of infra.
        raise HarnessError(f"{what} path is not set")
    if not os.path.isfile(path):
        raise AgentFault(f"{what} not found at {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, RecursionError) as exc:
        raise AgentFault(f"{what} at {path} is unreadable: {exc}") from exc
    except OSError as exc:
        # The file exists but the read itself failed — a permission/UID mismatch on a
        # docker-cp'd file, not the agent writing nothing. Infra, so the base class.
        raise HarnessError(f"{what} at {path} exists but is unreadable: {exc}") from exc


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
        if not key:
            # A non-empty string that normalizes to no components — '.', './', '..',
            # '/'. _matches_parts returns False whenever either side is empty, so it
            # can never be matched by anything and would sit in the denominator
            # forever, silently shrinking every agent's score on a 0.40-weight
            # checkpoint. 'if not path' above only catches the empty string.
            raise HarnessError(
                f"ground_truth.json required_files[{i}] ('{path}') normalizes to no "
                f"path components; it can never be matched and would distort the "
                f"recall denominator"
            )
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
    # Dedup, order-preserving: one path listed under several unioned keys is one guess.
    # score_answer would otherwise re-split and re-match it once per occurrence (wasted
    # work on the very input _CITATION_SUFFIX_RE's bound exists to keep cheap) and add
    # it to `ambiguous` more than once.
    return list(dict.fromkeys(found))


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


class _InputsEmitted(Exception):
    """Loading the scoring inputs already emitted a verdict; carry its exit code.

    Raised by :func:`load_scoring_inputs` so a scorer's ``main`` can share the
    load-and-classify prologue without also copying its three emit call sites —
    and, more importantly, without copying the AgentFault-before-HarnessError
    ordering, the one subtlety that must not drift between two scorers.
    """

    def __init__(self, code: int) -> None:
        self.code = code


def load_scoring_inputs(
    argv: List[str] | None, parser: argparse.ArgumentParser
) -> tuple[List[str], List[str], List[str]]:
    """Parse ``--keys``, load ground truth and the answer, return ``(keys, gt_paths, found)``.

    Every failure path emits the verdict itself (infra for a harness fault, a
    plain agent 0.0 for a missing/empty answer) and raises :class:`_InputsEmitted`
    with that exit code, so both file-scoring plugins share one copy of the
    fault-channel discipline. Shared by :func:`main` and
    :mod:`eb_verify.plugins.localization_evidence`.
    """
    try:
        args = parser.parse_args(argv)
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise HarnessError("--keys resolved to no usable keys")
        gt_paths = ground_truth_files(os.environ.get("GT_FILE", ""))
    except HarnessError as exc:
        raise _InputsEmitted(emit(0.0, str(exc), infra=True)) from exc

    # AgentFault must precede HarnessError — it is a subclass, and Python takes the
    # first matching clause.
    try:
        answer = load_json(os.environ.get("ANSWER_FILE", ""), "answer.json")
    except AgentFault as exc:
        raise _InputsEmitted(emit(0.0, f"No usable agent answer ({exc})")) from exc
    except HarnessError as exc:
        raise _InputsEmitted(emit(0.0, str(exc), infra=True)) from exc

    found = agent_files(answer, keys)
    if not found:
        raise _InputsEmitted(
            emit(0.0, f"Agent answer names no files under any of: {', '.join(keys)}")
        )
    return keys, gt_paths, found


def main(argv: List[str] | None = None) -> int:
    try:
        _keys, gt_paths, found = load_scoring_inputs(argv, build_parser())
    except _InputsEmitted as done:
        return done.code

    matched, ambiguous = score_answer(gt_paths, found)
    score = len(matched) / len(gt_paths)
    missed = [gt for gt in gt_paths if gt not in matched]

    detail = f"Found {len(matched)}/{len(gt_paths)} required source files"
    if missed:
        detail += f" (missed: {', '.join(missed)})"
    if ambiguous:
        # agent_files already deduped, so each guess appears once — one path named
        # under several keys is one ambiguous guess, not two distinct misses.
        detail += (
            f" (ambiguous, matched several required files so credited none: "
            f"{', '.join(ambiguous)})"
        )
    return emit(score, detail)


if __name__ == "__main__":
    sys.exit(main())
