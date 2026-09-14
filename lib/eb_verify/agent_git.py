"""Running ``git diff`` against a repo the agent controls.

A git repo configures its own code execution: ``.gitattributes`` picks a diff
driver per path, and that driver's ``textconv``/``command`` then names a program
git runs while producing the diff. Every repo a scorer diffs is the agent's, so
the agent picks the driver — and the driver decides what ``git diff`` prints,
independent of what is actually on disk. An agent that writes no fix at all can
therefore hand the grader a diff that contains one.

The driver name is the attacker's to choose, so there is no fixed config key the
caller could pin to neutralize it: the execution has to be refused at the call
site, on every call. Scoring runs unprivileged (``run_task.SCORING_USER``), so
this is depth rather than the only wall, but it keeps agent code out of the
grader's process entirely.

This module exists because "attach the flags at the call site" is only a rule if
there is exactly one call site to attach them to. ``code_patch`` had the rule and
a regression test for it; ``patch_evidence`` was then written as a *second* place
that diffs an agent repo, missed the flags, and the test could not see it — it
greps ``benchmarks/*/*/checks/*.sh`` for the string ``git diff``, and the new
checks call python. So both modules now share this one definition, and
``test_grading_asset_seal`` checks python call sites too (bead jn73.2.7).
"""

from __future__ import annotations

# --no-textconv refuses the per-path diff driver; --no-ext-diff refuses the same
# trick spelled via diff.external. Both are needed: neither implies the other.
GIT_NO_EXEC_FLAGS = ("--no-textconv", "--no-ext-diff")


def git_diff_argv(*args: str, repo: str | None = None) -> list[str]:
    """argv for a ``git diff`` against an agent-controlled repo.

    The one place the no-execute flags are attached, so a new diff call site
    cannot forget them. ``repo`` adds ``-C <repo>`` for callers that do not
    already run git with the repo as its cwd.
    """
    prefix = ["git"] + (["-C", repo] if repo is not None else [])
    return [*prefix, "diff", *GIT_NO_EXEC_FLAGS, *args]
