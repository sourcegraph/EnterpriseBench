"""patch_evidence scorer — did the agent write real code at the fix site?

The patch-side counterpart to :mod:`eb_verify.plugins.file_extraction`, and a
scorer rather than an artifact validator for the same reason: the registry
protocol has nowhere to put a number, so checkpoint scripts exec this as a CLI.

Two subcommands, because a patch checkpoint asks two different questions:

    # "is there a real code change at the fix site?" -> a JSON verdict
    python3 -m eb_verify.plugins.patch_evidence site \
        --repo /workspace/rack --path lib/rack/utils.rb --lang ruby

    # "what code did the agent actually add?" -> the stripped text, to grep
    python3 -m eb_verify.plugins.patch_evidence code \
        --repo /workspace/rack --path lib/rack/utils.rb --lang ruby --added

``site`` emits the verdict itself because its tiers are identical on every task.
``code`` prints text instead, because the *semantic* tiers are not: each task
matches its own fix (``.split('-')``, a ``__proto__`` guard, ``%25``) and often
nests the match, which no set of flags expresses readably. So the per-task regex
stays in the per-task script, where an author and a reviewer can both see it,
and only the thing every task got wrong — reading a comment as code — moves
here. Callers run under ``set -euo pipefail``, so a nonzero exit here aborts the
check with empty stdout, which ``scorer_guard`` books as ``verifier_did_not_run``
rather than as an agent's 0.0.

Why it exists: ``check_patch_site`` awarded 1.0 for a filename appearing in
``git diff --name-only``, and ``check_patch_semantics`` grepped raw ``+`` lines
for a token. Appending one comment line at the true fix site therefore scored
1.0 on both — 60-65% of task weight — while the vulnerable code sat untouched
(bead jn73.2.7, D2). All 14 enterprise-hard patch tasks shared the shape.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, NoReturn

from eb_verify.agent_git import git_diff_argv
from eb_verify.patch_diff import (
    UnknownLanguage,
    added_code,
    has_code_change,
    language_for,
    removed_code,
)
from eb_verify.plugins.file_extraction import HarnessError, emit
from eb_verify.scorer_guard import INFRA_SENTINEL

# A fix-site diff is small; a repo-wide one is bounded by the agent's own edits.
# The cap exists so a runaway diff cannot hang the checkpoint into a false zero.
_GIT_TIMEOUT = 60.0

# Full credit: real code at the declared fix site.
_SCORE_AT_SITE = 1.0
# Partial: real code somewhere in the repo, but not at the fix site. Keeps "the
# agent patched the wrong place" distinguishable from "the agent did nothing",
# which is what the same-basename decoy control pins.
_SCORE_WRONG_SITE = 0.2


def _git_diff(repo: Path, *args: str) -> str:
    """Diff the agent's repo, or raise :class:`HarnessError`.

    Every diff of an agent repo in this module goes through here, so the
    no-execute flags cannot be forgotten by a new caller — see
    :mod:`eb_verify.agent_git` for why a forgotten one hands the agent its own
    score.

    Every failure mode here is ours, never the agent's: a missing repo means the
    sandbox did not stage it, and a git that cannot run means the image is
    broken. Booking either as 0.0 would blame the agent for our bug — the same
    false-zero ``scorer_guard.DiffProbeError`` exists to prevent on the
    ``code_patch`` plugin's probes.
    """
    if not (repo / ".git").is_dir():
        raise HarnessError(
            f"workspace repo not staged at {repo} — the agent's diff cannot be read"
        )
    argv = git_diff_argv(*args, repo=str(repo))
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"git {' '.join(args)} timed out after {_GIT_TIMEOUT}s") from exc
    except OSError as exc:
        raise HarnessError(f"git could not be executed in {repo}: {exc}") from exc
    if proc.returncode != 0:
        raise HarnessError(
            f"git {' '.join(args)} failed in {repo} (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def _diff(repo: Path, paths: List[str]) -> str:
    """The agent's uncommitted diff, limited to ``paths``."""
    return _git_diff(repo, "HEAD", "--", *paths)


def _changed_files(repo: Path) -> List[str]:
    return [line for line in _git_diff(repo, "--name-only", "HEAD").splitlines() if line]


def _code_changed_at(repo: Path, paths: List[str], lang: str) -> bool:
    return has_code_change(_diff(repo, paths), lang)


def _code_changed_elsewhere(repo: Path, targets: List[str]) -> bool:
    """Did the agent write real code in this repo, away from the fix site?

    Language comes from each file's extension: the task names only its fix
    site's language, and this question ranges over files it never mentioned. A
    file whose extension names no code (README.md, a .yml fixture) is not a code
    change and cannot earn partial credit.
    """
    for changed in _changed_files(repo):
        if changed in targets:
            continue
        lang = language_for(changed)
        if lang is not None and has_code_change(_diff(repo, [changed]), lang):
            return True
    return False


def _score_site(repo: Path, paths: List[str], lang: str) -> int:
    where = " or ".join(paths)
    if _code_changed_at(repo, paths, lang):
        return emit(_SCORE_AT_SITE, f"diff makes a real code change at {where}")
    if _code_changed_elsewhere(repo, paths):
        return emit(_SCORE_WRONG_SITE, f"repo has a real code change, but not at {where}")
    # One tier for "did nothing" and for "changed only comments/blank lines at
    # the site". Both mean the agent wrote no code, and the checkpoint pays for
    # code — locating the site is what the root-cause checkpoint already pays for.
    return emit(0.0, f"no code change at {where} (comments and blank lines are not code)")


def _print_code(repo: Path, paths: List[str], lang: str, *, added: bool) -> int:
    """Print the added/removed code at ``paths``, comments stripped, for grepping."""
    diff = _diff(repo, paths)
    print(added_code(diff, lang) if added else removed_code(diff, lang))
    return 0


class _JsonErrorParser(argparse.ArgumentParser):
    """argparse that raises instead of exiting — see file_extraction's copy.

    Stock argparse answers a bad flag with usage on stderr and exit 2; for the
    ``site`` path that would reach ``runner.py`` as a non-JSON verdict.
    """

    def error(self, message: str) -> NoReturn:  # noqa: D102 — argparse hook
        raise HarnessError(f"bad verifier invocation: {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = _JsonErrorParser(
        prog="python -m eb_verify.plugins.patch_evidence",
        description="Score an agent's uncommitted diff, reading comments as prose.",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("site", "code"):
        p = sub.add_parser(name, add_help=False)
        p.add_argument("--repo", required=True, help="path to the workspace repo")
        # Repeatable: a task may accept more than one legitimate fix site (the
        # httpcore5 pool fix lands in StrictConnPool or LaxConnPool).
        p.add_argument(
            "--path",
            required=True,
            action="append",
            dest="paths",
            help="repo-relative fix-site path; repeat for alternative sites",
        )
        p.add_argument("--lang", required=True, help="language of the fix site")
        if name == "code":
            side = p.add_mutually_exclusive_group(required=True)
            side.add_argument("--added", action="store_true")
            side.add_argument("--removed", action="store_true")
    return parser


def main(argv: List[str] | None = None) -> int:
    # Read the subcommand off argv rather than off the parsed args: it decides
    # which channel an error is reported on, and a parse failure has no parsed
    # args to ask. Taking it from args.mode instead left `mode` None whenever
    # parsing itself failed, so a broken `code` invocation answered on `site`'s
    # channel — a JSON verdict on stdout, which that subcommand's caller reads
    # as diff text.
    raw = sys.argv[1:] if argv is None else list(argv)
    mode: str | None = raw[0] if raw and raw[0] in ("site", "code") else None
    try:
        args = build_parser().parse_args(argv)
        mode = args.mode
        repo = Path(args.repo)
        if mode == "site":
            return _score_site(repo, args.paths, args.lang)
        return _print_code(repo, args.paths, args.lang, added=args.added)
    # UnknownLanguage lands here with the harness faults, not with the agent's
    # score: the task named a language this module cannot strip, which is our
    # bug. Defaulting to "no comments" would silently restore the hole this
    # module closes, on whichever task added the language.
    except (HarnessError, UnknownLanguage) as exc:
        if mode == "code":
            # No verdict channel on this path: print the sentinel to stderr and
            # exit nonzero so the caller's `set -e` aborts with empty stdout,
            # which scorer_guard books as verifier_did_not_run.
            print(f"{INFRA_SENTINEL}: {exc}", file=sys.stderr)
            return 2
        return emit(0.0, str(exc), infra=True)


if __name__ == "__main__":
    sys.exit(main())
