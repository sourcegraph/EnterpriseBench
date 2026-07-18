"""
reproduction_script validator — file exists and is executable (stub).
"""

from __future__ import annotations

import os
from pathlib import Path

from eb_verify.artifact_io import safe_read, select_unique
from eb_verify.plugins import ValidationResult


class ReproductionScriptValidator:
    artifact_type = "reproduction_script"

    def validate(self, workspace: Path) -> ValidationResult:
        # Decoy-shadow-safe selection: reject >1 candidate as ambiguous. The
        # X_OK check below stays here (reproduction-specific policy), after the
        # safe_read symlink-containment check.
        script, sel_err = select_unique(
            workspace, ["reproduce.*", "reproduction.*", "repro.*"]
        )
        if sel_err is not None:
            return ValidationResult(valid=False, detail=sel_err)
        if script is None:
            return ValidationResult(
                valid=False, detail="No reproduction script found"
            )

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
