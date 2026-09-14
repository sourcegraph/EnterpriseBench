"""Filesystem-level enforcement of the ``mcp_only`` tool-access arm.

The arm must vary exactly one thing against baseline: whether the agent can read
repository source from local disk. Two obvious mechanisms both fail:

* A ``--disallowed-tools`` denylist (Read/Grep/Glob/Bash/Task/...) strips the
  agent's shell along with its search tools, so the baseline-minus-mcp_only delta
  would partly measure "lost a shell" rather than "lost local search". It is also
  fail-open: it cannot deny a tool name that does not exist yet.
* Omitting the repos from the image blinds the scorer too, because checkpoints
  score against ``/workspace`` in the same container -- some checks then return 0
  and others award full credit for a missing file.

So the repos stay on disk and the toolset stays identical across every arm; only
the directory permissions differ. The kernel, not an enumerated list, is what
says no, which is why a tool the agent acquires in a future CLI release is gated
too.

Three identities share the container, and the gate has to keep all three intact
(see ``run_task.SCORING_USER`` for the reasoning behind the split):

* ``agent`` runs the agent. Under this gate it must NOT reach repo source.
* ``ebscorer`` runs the checks. It MUST still read the repo source it scores
  against, or the gate would merely relocate the blindness it exists to avoid.
* ``root`` owns everything and is never denied.

Hence ``chown root:ebscorer`` + ``chmod o-rwx``: root owns, the scoring group
reads, and ``agent`` (which is in neither) is "other" and gets nothing. Granting
the scorer *read* rather than *ownership* mirrors how ``_seal_grading_assets``
treats the graders themselves: the scorer may read what it scores, never rewrite
it.

Commands are argv lists, never shell strings: nothing here needs a shell, and a
repo path that reaches a chmod target should not be one quoting bug away from
being reinterpreted as syntax.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from validation import validate_repo_entry

#: Arms whose agent is denied local source. ``hybrid`` is MCP *plus* local by
#: definition, and ``baseline`` is local-only, so neither is gated.
GATED_MODES = frozenset(
    {
        "mcp_only",
        "mcp_default",
        "mcp_treatment",
    "mcp_treat_instr",
    "mcp_treat_edges",
    "mcp_treat_nudge",
        "mcp_code_finder",
        "mcp_assisted",
        "mcp_toast",
    }
)

#: Artifacts the agent can only produce by reading and writing inside a repo
#: tree. Under the gate that tree is unreadable, so such a task is not merely
#: harder -- it is impossible, and must be declared ineligible rather than
#: silently scored near zero.
LOCAL_WRITE_ARTIFACTS = frozenset({"code_patch"})


class IneligibleTask(Exception):
    """Raised when a task cannot coherently run in the requested arm."""


def should_gate(mode: str) -> bool:
    """Whether *mode* denies the agent local source access."""
    return mode in GATED_MODES


def repo_dirs(task_data: dict, workspace: str) -> list[str]:
    """Absolute container paths of the cloned repo trees under *workspace*.

    Mirrors ``dockerfile_generator._clone_commands``, which clones each repo into
    ``{workspace}/{path}``. The workspace root is passed in rather than redefined
    here, for the same reason ``lockdown_commands`` takes ``scoring_group``:
    ``run_task`` owns the container layout, and a second copy of it in this
    module is a copy that can drift.

    ``validate_repo_entry`` is the codebase's one definition of a safe repo entry
    (no slashes, no ``..``) and ``_parse_task`` already applied it to this same
    dict. It is re-applied rather than assumed because the path selects a chmod
    target: a second opinion here is cheap, and a *divergent* second opinion --
    an inline rule that drifts from the shared one -- is how a traversal
    eventually relabels something outside the workspace.
    """
    dirs: list[str] = []
    for repo in task_data.get("repos", []):
        validate_repo_entry(repo)
        dirs.append(f"{workspace}/{repo['path']}")
    return dirs


def lockdown_commands(dirs: list[str], scoring_group: str) -> list[list[str]]:
    """Commands (run as root) that deny the agent read on each repo tree.

    Both halves are load-bearing, and the chown is the half that is easy to talk
    yourself out of:

    * ``chown`` -- the Dockerfile switches to ``USER agent`` *before* it clones
      (``dockerfile_generator`` emits the clone RUNs after that line), so the
      agent starts out as the OWNER of every repo file, and an owner may always
      chmod its own files back. Stripping mode bits without taking ownership away
      leaves a gate the agent can simply undo. Ownership moves to root, and the
      group moves to the scoring group so the scorer keeps the read access the
      checkpoints need.
    * ``chmod o-rwx,g-w`` -- OSS trees are checked out world-readable, so
      re-owning alone would still leave ``agent`` reading through the "other"
      bits. ``o-rwx`` strips those. The group bits are NOT stripped (``go-rwx``
      would blind the scorer instead of the agent), but group *write* is, and
      that half is easy to miss:

      The scorer's access must not change between arms, or the gate has varied
      two things. In an ungated arm the tree is ``agent``-owned and the scorer
      reaches it as "other" -- ``r--`` on files, ``r-x`` on dirs, i.e. read-only.
      Under a container umask of 002 the clone is 0664/0775, so ``chown`` +
      ``o-rwx`` alone would leave the scoring group at ``rw-``, handing the
      scorer write access to the very tree it grades, which it never had in
      baseline. ``g-w`` puts it back to read-only, so the ONE thing this gate
      changes is the agent's access.

    ``/workspace`` itself is deliberately untouched: the agent still has to
    traverse it to reach ``instruction.md`` and to write ``agent_output/``.
    Locking the workspace root would zero the arm instead of gating it.

    Recursion is batched across every path: ``chown``/``chmod`` take many
    operands, so this is one exec each no matter how many repos a task has.
    """
    if not dirs:
        return []
    return [
        ["chown", "-R", f"root:{scoring_group}", *dirs],
        ["chmod", "-R", "o-rwx,g-w", *dirs],
    ]


def deliverable_blocked_by_gate(
    task_data: dict, graded_artifact_path: str, workspace: str = "/workspace"
) -> str | None:
    """The repo tree that swallows *graded_artifact_path*, or None.

    The lockdown chmods each repo tree ``o-rwx``, which denies the agent
    *write* as surely as read: it cannot create a file in a directory it
    cannot enter. A task whose graded deliverable sits inside a repo tree
    (``/workspace/frameworks/INCIDENT_REPORT.md``) therefore cannot be
    delivered at all under the gate -- every checkpoint greps a file that was
    never allowed to exist, and the arm records 0.0 for a run in which the
    agent may have done the work perfectly. That is the exact shape
    scorer_guard exists to prevent: a harness failure recorded as a measured
    agent failure.
    """
    for repo_dir in repo_dirs(task_data, workspace):
        if graded_artifact_path.startswith(f"{repo_dir}/"):
            return repo_dir
    return None


def check_eligibility(
    task_data: dict,
    mode: str,
    graded_artifact_path: str | None = None,
    workspace: str = "/workspace",
) -> None:
    """Raise :class:`IneligibleTask` if *task_data* cannot run in *mode*.

    Only ``required`` artifacts are consulted. ``required=["answer"],
    optional=["code_patch"]`` is a common and perfectly gate-able shape: the
    answer is deliverable without ever opening the repo tree.

    When *graded_artifact_path* is supplied it is additionally checked against
    the trees this gate locks down -- see :func:`deliverable_blocked_by_gate`.
    """
    if not should_gate(mode):
        return

    if graded_artifact_path:
        blocked_by = deliverable_blocked_by_gate(
            task_data, graded_artifact_path, workspace
        )
        if blocked_by:
            raise IneligibleTask(
                f"task's graded deliverable {graded_artifact_path!r} lives inside "
                f"{blocked_by!r}, which mode={mode!r} makes unwritable to the "
                "agent. The run would score 0.0 for a harness reason, so the "
                "task is ineligible until its deliverable moves outside the "
                "repo trees."
            )

    required = set(task_data.get("artifacts", {}).get("required", []))
    blocked = sorted(required & LOCAL_WRITE_ARTIFACTS)
    if blocked:
        raise IneligibleTask(
            f"task requires artifact(s) {blocked} that can only be produced by "
            f"reading and writing local source, which mode={mode!r} denies. "
            "Exclude this task from the arm rather than scoring it."
        )
