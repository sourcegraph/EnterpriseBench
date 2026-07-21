"""Core record types for benchmark_qa_core checks.

These are deliberately small, frozen dataclasses so callers can compare,
serialise, and aggregate findings without worrying about identity or mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Finding:
    """A single QA finding produced by a check function.

    Attributes:
        severity: ``error`` for hard contract violations that should fail the
            task in CI, ``warning`` for likely-but-not-certain problems, and
            ``info`` for advisory observations.
        code: A short, stable identifier (e.g. ``"F2"``) that callers can pin
            in waivers or downstream gating. Codes never re-use across check
            functions; see the package docstring for the namespace.
        message: Human-readable description of the issue. Should not embed
            ANSI escapes — formatting is the caller's concern.
        location: Where the issue lives (file path, symbol id, JSON pointer
            into task-meta, …). ``None`` when the finding is global to the
            task or not tied to one location.
        suggested_fix: Optional one-line hint about how to resolve the
            finding. Free-form text; the lib does not parse it.
    """

    severity: Severity
    code: str
    message: str
    location: str | None = None
    suggested_fix: str | None = None
