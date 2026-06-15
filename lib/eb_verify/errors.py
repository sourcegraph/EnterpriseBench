"""
Shared error types for the eb_verify library.
"""

from __future__ import annotations


class VerifierInfraError(RuntimeError):
    """A verifier could not produce a trustworthy score because of an
    infrastructure failure — a missing or corrupt git repo, an unreadable
    workspace, or a verifier that crashed mid-output — as opposed to a genuine
    low score.

    Callers MUST record this as an infra failure and re-run the task; they must
    never coerce it into a 0.0 (or any other) score. Conflating a broken
    verification environment with a real measurement is how a benchmark
    silently corrupts its own results.
    """
