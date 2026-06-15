"""
reproduction_script validator — file exists and is executable.
"""

from __future__ import annotations

import os
from pathlib import Path

from eb_verify.plugins import ValidationResult, safe_read


class ReproductionScriptValidator:
    artifact_type = "reproduction_script"

    def validate(self, workspace: Path) -> ValidationResult:
        candidates = list(workspace.glob("**/reproduce.*")) + \
                     list(workspace.glob("**/reproduction.*")) + \
                     list(workspace.glob("**/repro.*"))

        if not candidates:
            return ValidationResult(
                valid=False, detail="No reproduction script found"
            )

        script = candidates[0]
        try:
            # Verify path doesn't escape workspace via symlink
            safe_read(script, workspace)
        except ValueError as e:
            return ValidationResult(valid=False, detail=str(e))
        if not os.access(script, os.X_OK):
            return ValidationResult(
                valid=False,
                detail=f"Script {script.name} exists but is not executable",
            )

        return ValidationResult(valid=True, detail=f"Found executable: {script.name}")
