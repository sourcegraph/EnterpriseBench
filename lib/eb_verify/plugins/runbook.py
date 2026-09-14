"""
runbook validator — markdown with required sections (stub with basic validation).
"""

from __future__ import annotations

from pathlib import Path

from eb_verify.artifact_io import safe_read, select_unique
from eb_verify.plugins import ValidationResult


REQUIRED_SECTIONS = ["overview", "steps", "rollback"]


class RunbookValidator:
    artifact_type = "runbook"

    def validate(self, workspace: Path) -> ValidationResult:
        # Decoy-shadow-safe selection (``**/output/runbook.md`` was a subset of
        # ``**/runbook.md`` and only self-collided).
        path, sel_err = select_unique(workspace, ["runbook.md", "RUNBOOK.md"])
        if sel_err is not None:
            return ValidationResult(valid=False, detail=sel_err)
        if path is None:
            return ValidationResult(
                valid=False, detail="No runbook file found (expected runbook.md)"
            )

        try:
            content = safe_read(path, workspace).lower()
        except ValueError as e:
            return ValidationResult(valid=False, detail=str(e))
        headers = [line.strip("# ").strip() for line in content.splitlines() if line.startswith("#")]

        missing = []
        for section in REQUIRED_SECTIONS:
            if not any(section in h for h in headers):
                missing.append(section)

        if missing:
            return ValidationResult(
                valid=False,
                detail=f"Runbook missing sections: {', '.join(missing)}",
            )

        return ValidationResult(valid=True, detail="Runbook has required sections")
