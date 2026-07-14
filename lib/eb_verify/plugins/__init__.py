"""
Artifact validator plugin registry.

Each plugin implements validate(workspace: Path) -> ValidationResult.
Plugins are registered by artifact type name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol


@dataclass
class ValidationResult:
    valid: bool
    detail: str = ""


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


class FileTooLargeError(ValueError):
    """Raised by safe_read when a file exceeds the caller's max_bytes cap."""


# Default cap for every safe_read call in this package (opt out with
# max_bytes=None). Generous relative to the hand-authored JSON/text artifacts
# (answer, incident report, etc.) that are meant to be at most a few KB; this
# bounds worst-case memory use if a symlink (or literal file) at the artifact
# path targets something huge.
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
    """
    workspace_resolved = workspace.resolve()
    fd = os.open(str(path), os.O_RDONLY)
    with os.fdopen(fd) as handle:
        real_path = Path(os.path.realpath(f"/proc/self/fd/{fd}"))
        if not str(real_path).startswith(str(workspace_resolved) + "/") and real_path != workspace_resolved:
            raise ValueError(
                f"Path escapes workspace: {path} -> {real_path}"
            )
        if max_bytes is not None:
            size = os.fstat(fd).st_size
            if size > max_bytes:
                raise FileTooLargeError(
                    f"File too large to read: {path} is {size} bytes (max {max_bytes})"
                )
        return handle.read()


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

# fact_triples needs numpy/scikit-learn/jsonschema, which minimal task
# sandboxes do not ship. Register it only when its deps are importable so
# dependency-free scorers (e.g. plugins.file_extraction run via `python -m`
# inside the sandbox) can import this package without the heavy stack.
# get_validator("fact_triples") returns None in that case and the runner
# reports the unknown artifact type explicitly.
#
# The scoring deps must be probed explicitly: fact_triples defers them to its scoring
# call sites, so importing it cleanly proves only that jsonschema is present. Relying
# on the ImportError alone once registered a validator that then raised from inside
# validate() — which runner.py calls unguarded, killing the run instead of recording a
# clean unscoreable result. Raising here routes a missing dep through the same warning
# as a missing jsonschema, so there is one "unavailable" path rather than two.
try:
    from eb_verify.fact_coverage import scoring_deps_available

    if not scoring_deps_available():
        raise ImportError("numpy, scipy and scikit-learn are required to score facts")
    from eb_verify.plugins.fact_triples import FactTriplesValidator
except ImportError as _fact_triples_exc:  # numpy/sklearn/jsonschema absent
    import warnings

    warnings.warn(
        f"fact_triples validator unavailable (missing dependency: "
        f"{_fact_triples_exc}); it will not be registered",
        RuntimeWarning,
        stacklevel=2,
    )
else:
    register(FactTriplesValidator())
