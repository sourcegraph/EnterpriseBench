"""
code_patch validator — checks that git diffs exist and apply cleanly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from eb_verify.plugins import ValidationResult
from eb_verify.scorer_guard import INFRA_SENTINEL, DiffProbeError


# Diff size thresholds (in lines of diff output)
DEFAULT_MAX_DIFF_LINES = 10_000
DEFAULT_MIN_DIFF_LINES = 1


# Every repo this module diffs is the agent's, and a git repo configures its own
# code execution: .gitattributes picks a diff driver per path, and that driver's
# textconv / command then names a program git runs while producing the diff. The
# driver name is the attacker's to choose, so there is no fixed config key the
# caller could pin to neutralize it — the execution has to be refused at the call
# site. Scoring is unprivileged (run_task.SCORING_USER), so this is depth rather
# than the only wall, but it keeps agent code out of the grader's process
# entirely. --no-ext-diff covers the same trick via diff.external.
_GIT_NO_EXEC_FLAGS = ("--no-textconv", "--no-ext-diff")


def _git_diff_argv(*args: str) -> list[str]:
    """argv for a ``git diff`` against an agent-controlled repo.

    The one place the no-execute flags are attached, so a new diff call site
    cannot forget them.
    """
    return ["git", "diff", *_GIT_NO_EXEC_FLAGS, *args]


def _run_git_diff(args: list[str], repo_path: Path) -> str:
    """Run a ``git diff`` variant and return stdout, or raise DiffProbeError.

    ``args`` are the arguments *after* ``git diff``.

    A git subprocess that fails to *run* (missing git, EACCES, corrupt .git,
    I/O error, timeout) or that exits non-zero is an infrastructure failure —
    NOT the same as "git ran and found no diff" (exit 0, empty stdout). Raising
    keeps the two apart so a sandbox failure is never collapsed into a false
    "no changes" 0-score (apfp #4).
    """
    try:
        proc = subprocess.run(
            _git_diff_argv(*args),
            capture_output=True, text=True, cwd=str(repo_path), timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise DiffProbeError(f"git diff {' '.join(args)} timed out in {repo_path}") from exc
    except OSError as exc:
        raise DiffProbeError(f"git diff {' '.join(args)} failed to run in {repo_path}: {exc}") from exc
    if proc.returncode != 0:
        raise DiffProbeError(
            f"git diff {' '.join(args)} exited {proc.returncode} in {repo_path}: "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def _get_diff_stat(repo_path: Path) -> Optional[str]:
    """Return combined diff --stat output for a repo, or None if there is no
    diff. Raises :class:`DiffProbeError` if git itself fails."""
    unstaged = _run_git_diff(["--stat", "HEAD"], repo_path)
    staged = _run_git_diff(["--cached", "--stat"], repo_path)
    combined = (unstaged.strip() + "\n" + staged.strip()).strip()
    return combined if combined else None


def _get_diff_lines(repo_path: Path) -> int:
    """Return total number of diff lines (staged + unstaged). Raises
    :class:`DiffProbeError` if git itself fails."""
    unstaged = _run_git_diff(["HEAD"], repo_path)
    staged = _run_git_diff(["--cached"], repo_path)
    return len(unstaged.splitlines()) + len(staged.splitlines())


def check_patch_applies(repo_path: Path) -> tuple[bool, str]:
    """Use git stash + git stash pop to verify the working tree diff can round-trip.

    For staged changes, use git apply --check on a generated patch.
    Returns (applies_cleanly, detail).
    """
    try:
        # Generate the diff and verify it can apply via --check
        diff_result = subprocess.run(
            _git_diff_argv("HEAD"),
            capture_output=True, text=True, cwd=str(repo_path), timeout=30,
        )
        diff_text = diff_result.stdout
        if not diff_text.strip():
            # Try staged only
            diff_result = subprocess.run(
                _git_diff_argv("--cached"),
                capture_output=True, text=True, cwd=str(repo_path), timeout=30,
            )
            diff_text = diff_result.stdout

        if not diff_text.strip():
            return True, "no diff to check"

        apply_result = subprocess.run(
            ["git", "apply", "--check", "--allow-empty"],
            input=diff_text,
            capture_output=True, text=True, cwd=str(repo_path), timeout=30,
        )
        if apply_result.returncode == 0:
            return True, "patch applies cleanly"
        return False, f"patch does not apply: {apply_result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "git apply --check timed out"
    except Exception as e:
        return False, f"error checking patch: {e}"


def check_diff_size(
    repo_path: Path,
    max_lines: int = DEFAULT_MAX_DIFF_LINES,
    min_lines: int = DEFAULT_MIN_DIFF_LINES,
) -> tuple[bool, str]:
    """Check that the diff size is within reasonable bounds.

    Returns (reasonable, detail).
    """
    total = _get_diff_lines(repo_path)
    if total < min_lines:
        return False, f"diff is suspiciously small ({total} lines)"
    if total > max_lines:
        return False, f"diff is suspiciously large ({total} lines, max={max_lines})"
    return True, f"diff size OK ({total} lines)"


class CodePatchValidator:
    artifact_type = "code_patch"

    def validate(
        self,
        workspace: Path,
        check_applies: bool = False,
        max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
        min_diff_lines: int = DEFAULT_MIN_DIFF_LINES,
    ) -> ValidationResult:
        """
        Check that at least one repo in workspace has uncommitted or staged changes.

        When check_applies=True, also verify patches apply cleanly via git apply --check.
        Always checks diff size reasonableness.
        """
        if not workspace.is_dir():
            return ValidationResult(valid=False, detail=f"Workspace not found: {workspace}")

        repos_with_changes: list[str] = []
        warnings: list[str] = []

        for item in workspace.iterdir():
            if not item.is_dir():
                continue
            git_dir = item / ".git"
            if not git_dir.exists():
                continue

            # A git-probe infra failure must NOT masquerade as "no changes".
            # Emit the shared infra sentinel so the scorer trust boundary routes
            # the run to re-run instead of recording a false 0 (apfp #4).
            try:
                stat = _get_diff_stat(item)
            except DiffProbeError as exc:
                return ValidationResult(
                    valid=False,
                    detail=f"{INFRA_SENTINEL}: {exc}",
                )
            if not stat:
                continue

            repos_with_changes.append(item.name)

            # Diff size check
            try:
                size_ok, size_detail = check_diff_size(item, max_diff_lines, min_diff_lines)
            except DiffProbeError as exc:
                return ValidationResult(
                    valid=False,
                    detail=f"{INFRA_SENTINEL}: {exc}",
                )
            if not size_ok:
                warnings.append(f"{item.name}: {size_detail}")

            # Patch applies check
            if check_applies:
                applies, apply_detail = check_patch_applies(item)
                if not applies:
                    warnings.append(f"{item.name}: {apply_detail}")

        if not repos_with_changes:
            return ValidationResult(
                valid=False,
                detail="No code changes detected in any repo under workspace",
            )

        detail = f"Code changes found in: {', '.join(repos_with_changes)}"
        if warnings:
            detail += "; WARNINGS: " + "; ".join(warnings)
            return ValidationResult(valid=True, detail=detail)

        return ValidationResult(valid=True, detail=detail)
