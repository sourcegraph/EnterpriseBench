"""Verifier infrastructure error types.

Raised when the verification harness cannot produce a *sound* measurement —
misconfigured ground truth, an unavailable Tier-2 judge, or corrupt curated
data. These are categorically different from a legitimate low score: an infra
error means the score is *unknown*, not *bad*. Callers must route the run to
the re-run channel (e.g. tag ``scores["verifier_infra_error"]`` / set
``phase = "verifier_infra_error"``) rather than record an inflated default-credit
or un-capped grep result as the real measurement.
"""

from __future__ import annotations

from typing import Any


def infra_error_tag(
    reason: str, detail: str, *, stage: str = "scoring", **extra: Any
) -> dict[str, Any]:
    """The canonical machine-routable payload for a verifier infra failure.

    One definition of the ``{reason, stage, detail, ...}`` shape, shared by
    :class:`VerifierInfraError` and the orchestration
    ``scores["verifier_infra_error"]`` dict-tag in run_task. The two
    control-flow mechanisms (raise vs. tag) stay distinct — they suit different
    call contexts — but the data shape does not drift.
    """
    return {"reason": reason, "stage": stage, "detail": detail, **extra}


class VerifierInfraError(Exception):
    """The verifier could not compute a trustworthy score.

    Carries a machine-routable ``reason`` and the verification ``stage`` so
    callers can classify the failure and route the run to re-run instead of
    silently passing it through.
    """

    def __init__(self, reason: str, detail: str, *, stage: str = "scoring") -> None:
        self.reason = reason
        self.stage = stage
        self.detail = detail
        super().__init__(f"[{stage}:{reason}] {detail}")
