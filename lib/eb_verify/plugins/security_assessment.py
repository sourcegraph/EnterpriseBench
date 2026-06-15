"""
security_assessment validator — CVE mapping, prioritization fields.
"""

from __future__ import annotations

import json
from pathlib import Path

from eb_verify.plugins import ValidationResult, safe_read

REQUIRED_FIELDS = ["vulnerabilities", "severity_summary", "recommendations"]


class SecurityAssessmentValidator:
    artifact_type = "security_assessment"

    def validate(self, workspace: Path) -> ValidationResult:
        candidates = list(workspace.glob("**/security_assessment.json")) + \
                     list(workspace.glob("**/security-assessment.json"))

        if not candidates:
            return ValidationResult(
                valid=False, detail="No security assessment file found"
            )

        try:
            data = json.loads(safe_read(candidates[0], workspace))
        except (json.JSONDecodeError, ValueError) as e:
            return ValidationResult(valid=False, detail=f"Invalid JSON: {e}")

        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            return ValidationResult(
                valid=False,
                detail=f"Missing required fields: {', '.join(missing)}",
            )

        return ValidationResult(valid=True, detail="Security assessment structure valid")
