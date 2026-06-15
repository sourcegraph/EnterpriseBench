"""
Artifact validator plugin registry.

Each plugin implements validate(workspace: Path) -> ValidationResult.
Plugins are registered by artifact type name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol


@dataclass
class ValidationResult:
    valid: bool
    detail: str = ""
    # True when the artifact could not be checked at all (e.g. the parser
    # library is unavailable) — distinct from a confirmed-valid result. Callers
    # should surface this as a warning rather than silently treating the
    # artifact as valid.
    indeterminate: bool = False


class ArtifactValidator(Protocol):
    """Protocol for artifact validators."""

    artifact_type: str

    def validate(self, workspace: Path) -> ValidationResult: ...


# Plugin registry — populated by imports below
_registry: Dict[str, ArtifactValidator] = {}


def register(validator: ArtifactValidator) -> None:
    _registry[validator.artifact_type] = validator


def get_validator(artifact_type: str) -> Optional[ArtifactValidator]:
    return _registry.get(artifact_type)


def list_validators() -> list[str]:
    return list(_registry.keys())


def safe_read(path: Path, workspace: Path) -> str:
    """Read a file, asserting the resolved path stays within workspace (symlink-safe)."""
    resolved = path.resolve()
    workspace_resolved = workspace.resolve()
    if not str(resolved).startswith(str(workspace_resolved) + "/") and resolved != workspace_resolved:
        raise ValueError(
            f"Path escapes workspace: {path} -> {resolved}"
        )
    return resolved.read_text()


# Import all plugins to trigger registration
from eb_verify.plugins.code_patch import CodePatchValidator
from eb_verify.plugins.config_validator import ConfigValidator
from eb_verify.plugins.incident_report import IncidentReportValidator
from eb_verify.plugins.runbook import RunbookValidator
from eb_verify.plugins.reproduction_script import ReproductionScriptValidator
from eb_verify.plugins.security_assessment import SecurityAssessmentValidator
from eb_verify.plugins.answer import AnswerValidator
from eb_verify.plugins.call_graph import CallGraphValidator
from eb_verify.plugins.topological_order import TopologicalOrderValidator

register(CodePatchValidator())
register(ConfigValidator())
register(IncidentReportValidator())
register(RunbookValidator())
register(ReproductionScriptValidator())
register(SecurityAssessmentValidator())
register(AnswerValidator())
register(CallGraphValidator())
register(TopologicalOrderValidator())
