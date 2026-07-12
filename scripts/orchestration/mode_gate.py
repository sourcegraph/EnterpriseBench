"""Filesystem-level enforcement of the ``mcp_only`` tool-access arm.

The arm must vary exactly one thing against baseline: whether the agent can read
repository source from local disk. Two obvious mechanisms both fail:

* A ``--disallowed-tools`` denylist (Read/Grep/Glob/Bash/Task/...) strips the
  agent's shell along with its search tools, so the baseline-minus-mcp_only delta
  would partly measure "lost a shell" rather than "lost local search". It is also
  fail-open: it cannot deny a tool name that does not exist yet.
* Omitting the repos from the image blinds the verifier too, because checkpoints
  score against ``/workspace`` in the same container -- some checks then return 0
  and others award full credit for a missing file.

So the repos stay on disk and the toolset stays identical across every arm; only
the directory permissions differ. The agent runs as ``agent`` and is denied read
on the repo trees; the verifier is re-exec'd as ``root`` and reads them normally.
The kernel, not an enumerated list, is what says no -- which is why a tool the
agent acquires in a future CLI release is gated too.

Commands are argv lists, never shell strings: nothing here needs a shell, and a
repo path that reaches a chmod target should not be one quoting bug away from
being reinterpreted as syntax.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from validation import validate_repo_entry

WORKSPACE = "/workspace"

#: The agent process runs as this unprivileged user (``docker exec -u agent``).
AGENT_USER = "agent"

#: The verifier must NOT share the agent's UID. ``dockerfile_generator`` emits
#: ``USER agent``, so a bare ``docker exec`` would inherit the very UID the gate
#: denies and the verifier would score a workspace it cannot read.
VERIFIER_USER = "root"

#: Arms whose agent is denied local source. ``hybrid`` is MCP *plus* local by
#: definition, and ``baseline`` is local-only, so neither is gated.
GATED_MODES = frozenset({"mcp_only"})

#: Artifacts the agent can only produce by reading and writing inside a repo
#: tree. Under the gate that tree is unreadable, so such a task is not merely
#: harder -- it is impossible, and must be declared ineligible rather than
#: silently scored near zero.
LOCAL_WRITE_ARTIFACTS = frozenset({"code_patch"})

#: Everything the verifier reads or executes. ``test_runner.sh`` globs
#: ``.verifiers/*.sh`` and runs each one, and ``.task`` holds the ground truth.
#: None of it is the agent's business, and all of it runs as root.
VERIFIER_ASSETS = (
    f"{WORKSPACE}/.verifiers",
    f"{WORKSPACE}/.task",
    f"{WORKSPACE}/.eb_verify",
    f"{WORKSPACE}/test.sh",
)


class IneligibleTask(Exception):
    """Raised when a task cannot coherently run in the requested arm."""


def _deny_agent(paths: list[str]) -> list[list[str]]:
    """Commands (run as root) that put *paths* out of the agent's reach.

    Both halves are load-bearing, and the chown is the half that is easy to talk
    yourself out of:

    * ``chown`` — the Dockerfile switches to ``USER agent`` *before* it clones
      (``dockerfile_generator`` emits the clone RUNs after that line), so the
      agent starts out as the OWNER of every repo file. An owner may always
      chmod its own files, so stripping mode bits without taking ownership away
      leaves a gate the agent can simply undo. Ownership must move to root.
    * ``chmod`` — OSS trees are checked out 0644/0755, i.e. world-readable, so
      root ownership alone still leaves the agent reading via the "other" bits.

    Recursion is batched across every path: ``chown``/``chmod`` take many
    operands, so this is one exec each no matter how many repos a task has.
    """
    return [
        ["chown", "-R", f"{VERIFIER_USER}:{VERIFIER_USER}", *paths],
        ["chmod", "-R", "go-rwx", *paths],
    ]


def verifier_lockdown_commands() -> list[list[str]]:
    """Take the verifier's own assets away from the agent, in every arm.

    These were historically chown'd to ``agent`` because the verifier used to
    run as ``agent`` too. It now runs as root, which makes agent ownership both
    unnecessary and dangerous:

    * ``test_runner.sh`` executes every ``.verifiers/*.sh`` it globs, so an
      agent-writable check script is arbitrary code that later runs as root.
    * ``.task`` holds the ground truth. An agent that can write it can forge a
      perfect score; an agent that can merely *read* it does not have to solve
      the task at all.

    The sticky bit on ``/workspace`` is what makes the rest hold: ``/workspace``
    must stay agent-writable (tasks legitimately write artifacts there), and
    write permission on a directory would otherwise let the agent unlink and
    replace a root-owned ``test.sh`` regardless of that file's own mode.
    """
    return [
        # go-rwx covers test.sh too: root owns it and root reads it regardless of
        # its own mode bits, so no further chmod of the file itself is needed.
        *_deny_agent(list(VERIFIER_ASSETS)),
        # Agent may still create its own files in /workspace; it may no longer
        # delete or rename anyone else's.
        ["chmod", "+t", WORKSPACE],
    ]


def should_gate(mode: str) -> bool:
    """Whether *mode* denies the agent local source access."""
    return mode in GATED_MODES


def repo_dirs(task_data: dict) -> list[str]:
    """Absolute container paths of the cloned repo trees.

    Mirrors ``dockerfile_generator._clone_commands``, which clones each repo into
    ``/workspace/{path}``.

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
        dirs.append(f"{WORKSPACE}/{repo['path']}")
    return dirs


def lockdown_commands(dirs: list[str]) -> list[list[str]]:
    """Commands (run as root) that deny the agent read on each repo tree.

    ``/workspace`` itself is deliberately untouched: the agent still has to
    traverse it to reach ``instruction.md`` and to write ``agent_output/``.
    Locking the workspace root would zero the arm instead of gating it.
    """
    if not dirs:
        return []
    return _deny_agent(dirs)


def check_eligibility(task_data: dict, mode: str) -> None:
    """Raise :class:`IneligibleTask` if *task_data* cannot run in *mode*.

    Only ``required`` artifacts are consulted. ``required=["answer"],
    optional=["code_patch"]`` is a common and perfectly gate-able shape: the
    answer is deliverable without ever opening the repo tree.
    """
    if not should_gate(mode):
        return

    required = set(task_data.get("artifacts", {}).get("required", []))
    blocked = sorted(required & LOCAL_WRITE_ARTIFACTS)
    if blocked:
        raise IneligibleTask(
            f"task requires artifact(s) {blocked} that can only be produced by "
            f"reading and writing local source, which mode={mode!r} denies. "
            "Exclude this task from the arm rather than scoring it."
        )
