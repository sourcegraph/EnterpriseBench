"""Workspace-safe artifact reading.

The primitive every artifact consumer shares: read a file only after proving the
path stays inside the workspace. It lives below both the plugin registry and the
verifier primitives (groundedness) because both need it and neither may depend on
the other.

Stdlib only, by design. Anything imported here is paid by every validator.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional, Sequence


class FileTooLargeError(ValueError):
    """Raised by safe_read when a file exceeds the caller's max_bytes cap."""


class NotRegularFileError(ValueError):
    """Raised by safe_read when the target is not a regular file.

    A FIFO, socket, or device is never a verifiable artifact, and reading one
    can block forever — see safe_read for why that matters.
    """


def select_unique(
    workspace: Path,
    patterns: Sequence[str],
    exclude_dir: Optional[Path] = None,
) -> tuple[Optional[Path], Optional[str]]:
    """Select the single workspace file matching any of *patterns*.

    *patterns* are workspace-relative globs that all name the same logical
    artifact (e.g. ``["incident_report.json", "incident-report.json"]`` or
    ``["reproduce.*", "repro.*"]``). Each is globbed recursively as
    ``**/<pattern>`` and the union is deduped by resolved path. *exclude_dir*,
    when given, drops any candidate resolving inside that subtree — used to
    skip a sealed ``ground_truth/`` so an agent artifact and reference file of
    the same name are not conflated.

    Decoy-shadow-safe. ``pathlib.glob('**/x')`` yields the workspace anchor
    directory before its subdirectories, so the old ``candidates[0]`` graded a
    workspace-root decoy that shadowed the real artifact pinned in a subdir.
    This rejects the >1-candidate case as ambiguous (fail-closed) instead of
    silently grading the first glob hit. Dedup by ``Path.resolve()`` matters
    for two reasons: it collapses superset/subset pattern pairs (a lone file
    under ``output/`` matches both ``**/x`` and ``**/output/x`` — that is one
    file, not an ambiguity), and it collapses a symlink alias with its target.
    A *copied* decoy at a distinct real path is correctly still a second
    candidate, so genuine ambiguity is preserved.

    *patterns* are trusted (plugin-hardcoded) tokens, not agent input. An
    absolute pattern or one with a ``..`` component would let the ``**/``
    glob resolve outside the workspace, so those are rejected as a
    programming error (``ValueError``) rather than silently returning an
    escaping path — matching the safe-by-construction posture of safe_read.

    Returns ``(path, error)`` with at most one non-None. Both None means no
    candidate exists, so the caller can try another artifact spec or report
    "not found" in its own words.
    """
    for pattern in patterns:
        if os.path.isabs(pattern) or ".." in Path(pattern).parts:
            raise ValueError(f"unsafe artifact pattern: {pattern!r}")

    excluded = exclude_dir.resolve() if exclude_dir is not None else None
    by_real: dict[Path, Path] = {}
    for pattern in patterns:
        for match in workspace.glob(f"**/{pattern}"):
            if not match.is_file():
                continue
            real = match.resolve()
            if excluded is not None and real.is_relative_to(excluded):
                continue
            by_real.setdefault(real, match)
    candidates = list(by_real.values())
    if not candidates:
        return None, None
    if len(candidates) > 1:
        rels = sorted(str(c.relative_to(workspace)) for c in candidates)
        return None, (
            f"ambiguous {', '.join(patterns)}: multiple candidates {rels}; "
            "write exactly one"
        )
    return candidates[0], None


# Default cap for every safe_read call (opt out with max_bytes=None). Generous
# relative to the hand-authored JSON/text artifacts (answer, incident report,
# etc.) that are meant to be at most a few KB; this bounds worst-case memory use
# if a symlink (or literal file) at the artifact path targets something huge.
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024  # 10 MiB


def safe_read(
    path: Path, workspace: Path, max_bytes: Optional[int] = MAX_ARTIFACT_BYTES
) -> str:
    """Read a file, asserting the resolved path stays within workspace (symlink-safe).

    Defaults to capping reads at MAX_ARTIFACT_BYTES so callers are protected
    unless they explicitly opt into unbounded reads with max_bytes=None. When
    max_bytes is set, files larger than the cap raise FileTooLargeError
    without being read. Containment is checked FIRST: an escaping path always
    reports the escape and is never stat'd for a size verdict.

    Opens *path* once via a file descriptor and derives the real path,
    containment check, size check, and read all from that same fd (rather
    than re-resolving/re-opening the path at each step). This closes the
    TOCTOU window a path-based resolve-then-reopen sequence would leave
    between the containment check and the read.

    Only regular files are read. The open is non-blocking because the target
    can be a file type the agent chose: opening a FIFO for reading otherwise
    blocks until a writer appears, and no writer ever does. Containment does
    not help here — an agent needs no privilege to mkfifo *inside* its own
    workspace. Every caller reads under a scoring timeout, so a blocking open
    turns a failed artifact into an infra error, and an infra error earns a
    re-run: the agent that plants the pipe trades a certain zero for a fresh
    attempt. O_NONBLOCK is a no-op on the regular files that survive the check.
    """
    workspace_resolved = workspace.resolve()
    fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd) as handle:
        real_path = Path(os.path.realpath(f"/proc/self/fd/{fd}"))
        if not str(real_path).startswith(str(workspace_resolved) + "/") and real_path != workspace_resolved:
            raise ValueError(
                f"Path escapes workspace: {path} -> {real_path}"
            )
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise NotRegularFileError(
                f"Not a regular file: {path} (mode {stat.filemode(mode)})"
            )
        if max_bytes is not None:
            size = os.fstat(fd).st_size
            if size > max_bytes:
                raise FileTooLargeError(
                    f"File too large to read: {path} is {size} bytes (max {max_bytes})"
                )
        return handle.read()
