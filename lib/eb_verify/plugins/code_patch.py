"""
code_patch validator — checks that git diffs exist and apply cleanly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from eb_verify.errors import VerifierInfraError
from eb_verify.plugins import ValidationResult


# Diff size thresholds (in lines of diff output)
DEFAULT_MAX_DIFF_LINES = 10_000
DEFAULT_MIN_DIFF_LINES = 1

# Per-invocation timeout for read-only git commands (trust-boundary cap).
GIT_TIMEOUT_SECONDS = 30


def _run_git(args: list[str], repo_path: Path) -> str:
    """Run a read-only git command and return its stdout.

    Raises ``VerifierInfraError`` on any infrastructure failure — git binary
    missing, the path not being a git repo, a corrupt repo, permission errors,
    or a timeout. This is the critical distinction: a *clean* working tree makes
    git exit 0 with empty stdout, whereas a *broken* environment makes git exit
    non-zero. Swallowing the latter into an empty diff would let a verification
    env that cannot run silently score as valid-with-no-changes.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, cwd=str(repo_path),
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:  # git binary not on PATH
        raise VerifierInfraError(f"git executable not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VerifierInfraError(
            f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s "
            f"in {repo_path}"
        ) from exc
    if proc.returncode != 0:
        raise VerifierInfraError(
            f"git {' '.join(args)} failed in {repo_path} "
            f"(exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _get_diff_stat(repo_path: Path) -> Optional[str]:
    """Return combined diff --stat output for a repo, or None when the tree is
    clean. Raises ``VerifierInfraError`` if git itself fails (see ``_run_git``)."""
    unstaged = _run_git(["diff", "--stat", "HEAD"], repo_path)
    staged = _run_git(["diff", "--cached", "--stat"], repo_path)
    combined = (unstaged.strip() + "\n" + staged.strip()).strip()
    return combined if combined else None


def _get_diff_lines(repo_path: Path) -> int:
    """Return total number of diff lines (staged + unstaged). Raises
    ``VerifierInfraError`` if git itself fails (see ``_run_git``)."""
    unstaged = _run_git(["diff", "HEAD"], repo_path)
    staged = _run_git(["diff", "--cached"], repo_path)
    return len(unstaged.splitlines()) + len(staged.splitlines())


def check_patch_applies(repo_path: Path) -> tuple[bool, str]:
    """Verify the repo's working-tree/staged diff applies cleanly via
    ``git apply --check``. Returns (applies_cleanly, detail).

    A non-zero ``git apply --check`` exit is a *real* result (the patch does not
    apply) and is returned as such. Failures that prevent the check from running
    at all — git missing or timing out — raise ``VerifierInfraError`` instead of
    being mislabelled as a patch that does not apply.
    """
    diff_text = _run_git(["diff", "HEAD"], repo_path)
    if not diff_text.strip():
        diff_text = _run_git(["diff", "--cached"], repo_path)
    if not diff_text.strip():
        return True, "no diff to check"

    try:
        apply_result = subprocess.run(
            ["git", "apply", "--check", "--allow-empty"],
            input=diff_text,
            capture_output=True, text=True, cwd=str(repo_path),
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise VerifierInfraError(f"git executable not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VerifierInfraError(
            f"git apply --check timed out after {GIT_TIMEOUT_SECONDS}s "
            f"in {repo_path}"
        ) from exc
    if apply_result.returncode == 0:
        return True, "patch applies cleanly"
    return False, f"patch does not apply: {apply_result.stderr.strip()}"


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

            stat = _get_diff_stat(item)
            if not stat:
                continue

            repos_with_changes.append(item.name)

            # Diff size check
            size_ok, size_detail = check_diff_size(item, max_diff_lines, min_diff_lines)
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
