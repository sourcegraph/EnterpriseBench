"""The one definition of whether a finished run may be counted in a mean.

``run_task`` decides this when it writes a result; the analysis has to decide it
again when it reads one back. Two copies of the rule is one copy that drifts, so
both call in here.

Reading the raw ``status`` field is not enough, for two reasons the callers
cannot see from their own side:

* Most failure branches (OOM, timeout, build-failed) set only ``phase`` and
  ``success``, so the status has to be *derived* rather than looked up. Deriving
  means a new failure branch cannot forget to flag itself out of the means:
  there is no allow-list to fall off.
* ``status`` is absent entirely from results written before the field existed,
  and those are the majority of what is on disk today.

An INVALID run is not a zero. It is a run that did not happen -- the gate could
not be proved, the agent never started, the verifier broke. Scoring it as 0.0
does not report a worse agent, it reports a smaller sample as a worse agent.
"""

from __future__ import annotations

#: Persisted status of a run that must be re-run, never scored.
RUN_STATUS_INVALID = "invalid"


def effective_status(status: str | None, success: bool, phase: str) -> str:
    """The status of a run, derived when it was never explicitly stamped.

    An explicit *status* wins. Otherwise a run is INVALID unless it succeeded or
    reached ``phase == "complete"``. The disjunction is what keeps
    ``dry_run_complete`` valid: it sets ``success=True`` and is not a failure,
    though it never scores.
    """
    if status:
        return status
    if success or phase == "complete":
        return ""
    return RUN_STATUS_INVALID


def is_scoreable(status: str | None, success: bool, phase: str) -> bool:
    """Whether this run belongs in an aggregate.

    Case is normalized because two writers stamp the field and they disagree:
    ``run_task._effective_status`` persists ``""`` or lowercase ``"invalid"``,
    while the verifier schema writes uppercase ``"VALID"``/``"INVALID"``. Without
    the fold, a lowercase ``"invalid"`` silently reads as some unknown-but-fine
    status and gets counted -- which is the failure this module exists to stop.
    """
    return effective_status(status, success, phase).upper() != (
        RUN_STATUS_INVALID.upper()
    )
