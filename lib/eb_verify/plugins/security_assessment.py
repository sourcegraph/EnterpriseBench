"""
security_assessment validator — CVE mapping, prioritization fields (stub).

Citations convention (require_grounded_citations=True):
    Citations are gated PER FINDING: each entry in the ``vulnerabilities``
    list must carry a non-empty ``citations`` list, each citation
    ``{"repo": ..., "file": ..., "evidence_span": ...}`` (same schema as
    incident_report/answer). Rationale: unlike those artifacts, a security
    assessment has guaranteed per-claim structure — each vulnerability is an
    independent claim about specific code, and a single top-level citations
    list would let one grounded citation launder N fabricated findings.
    Because the finding itself is the claim, an empty per-finding citations
    list (a claim with zero evidence) fails the gate; this deliberately
    diverges from the top-level convention, where an empty list means "no
    claims" and is trivially grounded. An empty vulnerabilities list has
    nothing to ground and passes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eb_verify.groundedness import check_groundedness, parse_citations
from eb_verify.artifact_io import safe_read, select_unique
from eb_verify.plugins import ValidationResult

REQUIRED_FIELDS = ["vulnerabilities", "severity_summary", "recommendations"]


def ground_findings(data: dict[str, Any], workspace: Path) -> list[str]:
    """Ground every finding's citations; return per-finding issues (empty = ok)."""
    vulns = data.get("vulnerabilities")
    if not isinstance(vulns, list):
        return [
            "vulnerabilities must be a list of finding objects when grounded "
            f"citations are enforced, got {type(vulns).__name__}"
        ]
    issues: list[str] = []
    for i, finding in enumerate(vulns):
        if not isinstance(finding, dict):
            issues.append(
                f"vulnerabilities[{i}] must be an object, got {type(finding).__name__}"
            )
            continue
        if "citations" not in finding:
            issues.append(
                f"vulnerabilities[{i}] missing 'citations' (required when "
                "grounded citations are enforced)"
            )
            continue
        try:
            citations = parse_citations(finding["citations"])
        except ValueError as e:
            issues.append(f"vulnerabilities[{i}]: {e}")
            continue
        if not citations:
            issues.append(
                f"vulnerabilities[{i}] has an empty citations list; each "
                "finding needs at least one evidence citation"
            )
            continue
        grounded = check_groundedness(citations, workspace)
        if not grounded.all_grounded:
            issues.append(f"vulnerabilities[{i}] ungrounded: {grounded.summary()}")
    return issues


class SecurityAssessmentValidator:
    artifact_type = "security_assessment"

    def validate(
        self, workspace: Path, require_grounded_citations: bool = False
    ) -> ValidationResult:
        """Validate structure; optionally gate per-finding evidence citations.

        When *require_grounded_citations* is True, every entry in
        ``vulnerabilities`` must carry a non-empty ``citations`` list whose
        evidence_spans appear verbatim in the cited workspace files (see
        module docstring); failures report per-finding, per-citation reasons.
        """
        # Decoy-shadow-safe selection: reject >1 candidate as ambiguous.
        path, sel_err = select_unique(
            workspace, ["security_assessment.json", "security-assessment.json"]
        )
        if sel_err is not None:
            return ValidationResult(valid=False, detail=sel_err)
        if path is None:
            return ValidationResult(
                valid=False, detail="No security assessment file found"
            )

        try:
            data = json.loads(safe_read(path, workspace))
        except (json.JSONDecodeError, ValueError) as e:
            return ValidationResult(valid=False, detail=f"Invalid JSON: {e}")

        if not isinstance(data, dict):
            return ValidationResult(
                valid=False, detail="Assessment must be a JSON object"
            )

        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            return ValidationResult(
                valid=False,
                detail=f"Missing required fields: {', '.join(missing)}",
            )

        # Optional groundedness gate (task ground truth: require_grounded_citations)
        if require_grounded_citations:
            issues = ground_findings(data, workspace)
            if issues:
                return ValidationResult(valid=False, detail="; ".join(issues))

        return ValidationResult(valid=True, detail="Security assessment structure valid")
