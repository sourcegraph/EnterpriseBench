"""
runbook validator — markdown with required sections.
"""

from __future__ import annotations

from pathlib import Path

from eb_verify.plugins import ValidationResult, safe_read


REQUIRED_SECTIONS = ["overview", "steps", "rollback"]


class RunbookValidator:
    artifact_type = "runbook"

    def validate(self, workspace: Path) -> ValidationResult:
        candidates = list(workspace.glob("**/runbook.md")) + \
                     list(workspace.glob("**/RUNBOOK.md")) + \
                     list(workspace.glob("**/output/runbook.md"))

        if not candidates:
            return ValidationResult(
                valid=False, detail="No runbook file found (expected runbook.md)"
            )

        try:
            content = safe_read(candidates[0], workspace).lower()
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
