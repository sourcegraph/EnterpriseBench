"""Scorer trust boundary — one invariant, enforced in one place.

A benchmark score is valid ONLY if the pristine verifier ran on real agent
output. Any infrastructure, verifier, or judge failure must surface as a
``verifier_infra_error`` and route the run to the re-run channel — it must never
be recorded as a legitimate ``0.0`` (false-zero / under-credit) or as an
un-capped grep score (judge-outage inflation / over-credit).

Before this module the invariant was re-implemented by hand at every scoring
entry point (``_run_scoring``, ``_apply_llm_judge``, ``code_patch.validate``,
the docker-cp copy path) and each got it subtly wrong in a different way
(beads: s58f, hktt/pt0n, wbsq, apfp). This module owns the single definition of
"infra failure vs real score" so every entry point enforces it identically.

The routing contract in ``run_task.py`` reads ``scores['verifier_infra_error']``
(the dict produced by :meth:`InfraError.as_verifier_error`) and sets
``phase='verifier_infra_error'`` so the run is never marked complete/success.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

# Sentinel a verifier emits in its ``detail`` to declare, on purpose, that the
# verifier harness itself failed (not that the agent failed the task). Kept
# explicit so the guard never has to guess from an arbitrary Python traceback —
# a task about an ImportError would otherwise be misclassified as infra.
INFRA_SENTINEL = "VERIFIER_INFRA_ERROR"

# Harness-import failure signature from the docker-cp package-drop regression
# (bead hktt/pt0n): a check script could not import the eb_verify harness and
# test_runner recorded it as a silent 0.0. This string names OUR harness module,
# never a task's subject code, so scanning for it cannot false-positive a task
# whose *own* code raises ModuleNotFoundError.
_HARNESS_IMPORT_FAILURE = "No module named 'eb_verify"

# Explicit, harness-specific detail signatures that mean "the verifier did not
# really run". Deliberately NOT a generic traceback/ImportError match — those
# occur legitimately in error-provenance task subjects.
#
# A secondary net only: it catches a verifier that reached a verdict and declared
# its own harness failure. It can never catch a verifier that never ran at all —
# a denylist only names the modes we have already been burned by (s58f, then
# hktt/pt0n, then the missing interpreter). That is the attestation's job.
_INFRA_DETAIL_SIGNATURES = (INFRA_SENTINEL, _HARNESS_IMPORT_FAILURE)

# Machine key for the no-verdict rule below. The shell scoring path
# (test_runner.sh, bead glka.2) emits this same string, so the re-run channel
# filters both paths on one key. Change it here and there together.
NO_VERDICT_REASON = "verifier_did_not_run"

# Cap on evidence copied into an InfraError, so a runaway verifier cannot flood
# reward.txt and the results payload.
_EVIDENCE_CHARS = 2000

# Positive attestation, emitted per checkpoint by test_runner.sh: "this verifier
# reached a verdict". Its ABSENCE — not the presence of any known-bad string —
# routes a checkpoint to an infra error, so an unrecognised never-ran mode fails
# closed (into the re-run channel) instead of open (into a false 0.0).
_ATTESTATION = "verifier_ran"


@dataclass(frozen=True)
class InfraError:
    """A scoring-infrastructure failure. Never a legitimate score."""

    reason: str  # machine key, e.g. "empty_verifier_output"
    stage: str  # scoring stage, e.g. "deterministic_scoring" | "llm_judge"
    detail: str  # human-readable explanation
    context: dict = field(default_factory=dict)

    @property
    def cause(self) -> str:
        """The narrowest machine key this error carries.

        ``no_verdict`` errors all share one ``reason`` (NO_VERDICT_REASON), so the
        sub-classification that actually names the failure lives in
        ``context["cause"]``. Errors built directly (run_task's judge paths) carry
        no cause and fall back to ``reason``. Every reader wants this one string,
        so it is derived here rather than at each call site.
        """
        return self.context.get("cause", self.reason)

    def as_verifier_error(self) -> dict:
        """The dict shape ``run_task`` stores under ``verifier_infra_error``."""
        payload = {
            "reason": self.reason,
            "stage": self.stage,
            "detail": self.detail,
            "cause": self.cause,
        }
        payload.update(self.context)
        return payload


def enforce_verdict_invariant(
    kind: str, name: str, score: Optional[float], infra_error: Optional[InfraError]
) -> None:
    """Reject a result that does not encode "verdict XOR infra error".

    The single definition of the no-verdict encoding for every per-verifier
    result type: ``score`` is None exactly when ``infra_error`` is set. Both set
    is a laundered placeholder — a false zero one forgotten branch away from
    publication; neither set claims a verdict that does not exist.
    """
    if (score is None) != (infra_error is not None):
        raise ValueError(
            f"{kind} {name!r}: exactly one of score/infra_error must be set — a "
            f"verifier either reached a verdict or it did not "
            f"(score={score!r}, infra_error={infra_error!r})"
        )


class DiffProbeError(Exception):
    """A git/subprocess probe failed to *run* (missing git, EACCES, corrupt
    .git, I/O error, timeout) — distinct from "git ran and found no diff".

    Raised by ``code_patch`` probes so a sandbox/infra failure is never
    collapsed into the same "no changes" 0-score as a genuinely clean repo.
    """


GuardResult = Union[dict, InfraError]

# Float slop tolerated around the [0, 1] bounds. A scorer computing
# ``round(hits / total, 2)`` can land a hair outside; a scorer emitting 999
# cannot. Anything past this is broken, not a score.
_SCORE_EPSILON = 1e-6


def is_valid_score(value: object) -> bool:
    """Is ``value`` a real, finite number inside [0, 1]?

    A ``max(0.0, min(1.0, float(value)))`` clamp does not answer this question.
    It turns each of the values below into a free 1.0 or a false zero, and each
    is reachable from a real verifier or judge response because ``json.loads``
    parses bare ``NaN``/``Infinity`` by default. The bool and non-finite checks
    are load-bearing for exactly that reason:

    * ``nan`` — ``min(1.0, nan)`` is 1.0 in CPython, so arithmetic that divided
      by zero scores FULL MARKS.
    * ``inf`` — clamps to 1.0.
    * ``True`` — ``float(True) == 1.0``, so a judge emitting ``{"score": true}``
      to mean "passed" scores 1.0.
    * ``"1.0"`` / ``"abc"`` — a string is not a score. Coercing it gives full
      marks for the first and a false zero for the second.

    Callers: all three scoring entry points, so the invariant has one
    definition — ``guard_checkpoint_verdict`` (the Tier-1 verifier path, which
    ``runner.py`` now trusts instead of clamping on its own), the per-checkpoint
    guard above, and the judge path
    (:func:`eb_verify.judge.models.validate_score`).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    return -_SCORE_EPSILON <= value <= 1.0 + _SCORE_EPSILON


def _detail_infra_signature(detail: str) -> str | None:
    """Return the infra signature found in a checkpoint ``detail``, else None."""
    for sig in _INFRA_DETAIL_SIGNATURES:
        if sig in detail:
            return sig
    return None


def guard_verifier_output(
    stdout: str,
    returncode: int,
    *,
    stage: str = "deterministic_scoring",
) -> GuardResult:
    """Parse ``test.sh`` output into a scores dict, or an :class:`InfraError`.

    An :class:`InfraError` (never a numeric score) is returned when:

    * stdout is empty — the verifier produced nothing (crashed / mis-copied);
    * stdout is not valid JSON, or not a JSON object;
    * the parsed result carries a top-level ``error`` key — test.sh emits this
      for "cannot access repo" / "no .verifiers/ directory" (previously read by
      no caller, so it became a false 0.0);
    * a checkpoint does not attest ``verifier_ran: true`` — the primary gate;
    * a checkpoint's ``detail`` carries an explicit infra signature
      (:data:`INFRA_SENTINEL` / the docker-cp harness-import failure).

    Otherwise the parsed scores dict is returned unchanged.
    """
    text = stdout.strip()
    if not text:
        return InfraError(
            reason="empty_verifier_output",
            stage=stage,
            detail=f"test.sh produced no output (exit {returncode})",
            context={"returncode": returncode},
        )

    try:
        scores = json.loads(text)
    except json.JSONDecodeError as exc:
        return InfraError(
            reason="malformed_verifier_output",
            stage=stage,
            detail=f"test.sh output was not valid JSON: {exc}",
            context={"returncode": returncode, "raw_output": text[:_EVIDENCE_CHARS]},
        )

    if not isinstance(scores, dict):
        return InfraError(
            reason="malformed_verifier_output",
            stage=stage,
            detail=f"test.sh output was not a JSON object (got {type(scores).__name__})",
            context={"returncode": returncode, "raw_output": text[:_EVIDENCE_CHARS]},
        )

    reported_error = scores.get("error")
    if reported_error:
        return InfraError(
            reason="verifier_reported_error",
            stage=stage,
            detail=f"test.sh reported an error: {reported_error}",
            context={"returncode": returncode},
        )

    # A task with no checkpoints ran no verifiers, so its 0.0 measures nothing.
    # test.sh reports a top-level ``error`` when .verifiers/ is missing entirely,
    # but an existing-but-EMPTY .verifiers/ yields a well-formed
    # {"checkpoints": []} with task_score 0, which never enters the loop below.
    checkpoints = scores.get("checkpoints")
    if not checkpoints:
        return InfraError(
            reason="no_checkpoints_run",
            stage=stage,
            detail="test.sh reported no checkpoints — no verifier ran, so there is nothing to score",
            context={"returncode": returncode},
        )

    for cp in checkpoints:
        if not isinstance(cp, dict):
            continue
        name = cp.get("name", "?")

        # PRIMARY GATE — positive attestation. A checkpoint that cannot state it
        # reached a verdict does not get scored, whatever number it carries. Any
        # never-ran mode the runner detects (missing command, exit 127, no JSON
        # verdict) arrives here as a false attestation, so this one gate covers
        # them all — including modes not yet named.
        if cp.get(_ATTESTATION) is not True:
            return InfraError(
                reason="verifier_did_not_run",
                stage=stage,
                detail=(
                    f"checkpoint {name!r} carries no {_ATTESTATION}=true "
                    f"attestation — the verifier did not reach a verdict, so its "
                    f"score is not a measurement of the agent"
                ),
                context={"checkpoint": cp.get("name", "")},
            )

        # SECONDARY — a verifier that ran to a verdict but declared its own
        # harness failure in the detail. The runner cannot see this: to it, the
        # checkpoint produced well-formed JSON and exited 0. Reached today by
        # plugins.code_patch, which emits INFRA_SENTINEL on a diff-probe failure.
        sig = _detail_infra_signature(str(cp.get("detail", "")))
        if sig is not None:
            return InfraError(
                reason="verifier_crash",
                stage=stage,
                detail=(
                    f"checkpoint {name!r} reported a verifier "
                    f"infra failure (signature: {sig!r})"
                ),
                context={"checkpoint": cp.get("name", ""), "signature": sig},
            )

        # Same rule as guard_checkpoint_verdict. `run_task` sums `score * weight`
        # with no clamp, so a NaN/Infinity here writes a nan/inf task_score
        # straight into published results, and a 999 writes a 999.
        if "score" in cp and not is_valid_score(cp["score"]):
            bad_score = repr(cp["score"])[:_EVIDENCE_CHARS]
            return InfraError(
                reason="malformed_verifier_output",
                stage=stage,
                detail=(
                    f"checkpoint {cp.get('name', '?')!r} score was not a real "
                    f"number in [0.0, 1.0]: {bad_score}"
                ),
                context={
                    "checkpoint": cp.get("name", ""),
                    "returncode": returncode,
                    "score": bad_score,
                },
            )

    return scores


def no_verdict(
    cause: str,
    detail: str,
    *,
    checkpoint: str = "",
    stage: str = "deterministic_scoring",
    evidence: Optional[dict[str, object]] = None,
) -> InfraError:
    """Build the :class:`InfraError` for a verifier that never reached a verdict.

    ``cause`` is the machine-readable sub-classification (``empty_output``,
    ``verifier_timeout``, ``missing_verifier``, ...). The ``reason`` is always
    :data:`NO_VERDICT_REASON`, so the re-run channel can filter on one key while
    an operator still sees which way the verifier died.

    ``evidence`` (stderr, raw_output, returncode, ...) is an explicit dict, not
    ``**kwargs``: a verifier-supplied key named ``checkpoint`` or ``stage`` would
    otherwise collide with the named parameters and raise ``TypeError`` from
    inside the one code path whose job is to not blow up on a broken verifier.
    """
    return InfraError(
        reason=NO_VERDICT_REASON,
        stage=stage,
        detail=detail,
        # Evidence first so the named params win: a stray evidence key must not
        # rewrite which checkpoint or cause this error is about.
        context={**(evidence or {}), "cause": cause, "checkpoint": checkpoint},
    )


def guard_checkpoint_verdict(
    stdout: str,
    returncode: int,
    *,
    stderr: str = "",
    checkpoint: str = "",
    stage: str = "deterministic_scoring",
) -> GuardResult:
    """Parse ONE checkpoint verifier's output into a verdict dict, or an InfraError.

    The single definition of "this verifier reached a verdict" for the Python
    (:class:`~eb_verify.runner.CheckpointRunner`) path — the sibling of
    :func:`guard_verifier_output`, which guards the aggregate ``test.sh`` output.

    A verdict is a JSON object carrying a numeric ``score``. Anything else means
    the verifier did not reach one, and the caller must NOT invent a number for
    it. There is deliberately no exit-code fallback: reading ``exit 0`` as a 1.0
    hands full credit to a verifier that crashed before scoring anything, and
    ``exit 1`` as a 0.0 blames the agent for a broken harness (beads glka.2,
    hktt/pt0n, kyo34).

    ``passed`` is NOT required. The schema nominally demands it, but 22 active
    verifiers and the ``topological_order`` plugin emit ``score`` alone;
    requiring it here would false-positive them all into infra errors.

    ``passed`` IS always present in the returned verdict: an explicit JSON bool
    is honored, anything else (absent, or a non-bool like ``"yes"``/``1``) is
    derived as ``score > 0.0``. It is derived here, once, so that no caller
    invents the other half of the verdict and disagrees with the next caller.
    """

    def did_not_run(cause: str, detail: str, **evidence: object) -> InfraError:
        return no_verdict(
            cause,
            detail,
            checkpoint=checkpoint,
            stage=stage,
            evidence={"returncode": returncode, **evidence},
        )

    text = stdout.strip()
    err = stderr.strip()[:_EVIDENCE_CHARS]

    # Checked before emptiness: this is the docker-cp / PYTHONPATH regression
    # (beads hktt/pt0n, ssikq), and it surfaces as empty stdout + exit 1. The
    # cause lives in stderr, so report that rather than a bare "no output".
    if _HARNESS_IMPORT_FAILURE in stderr:
        return did_not_run(
            "harness_import_failure",
            f"verifier could not import the eb_verify harness (exit {returncode})",
            stderr=err,
        )

    if not text:
        return did_not_run(
            "empty_output",
            f"verifier produced no output (exit {returncode})",
            stderr=err,
        )

    raw = text[:_EVIDENCE_CHARS]

    try:
        verdict = json.loads(text)
    except json.JSONDecodeError as exc:
        return did_not_run(
            "malformed_output",
            f"verifier output was not valid JSON: {exc}",
            raw_output=raw,
        )

    if not isinstance(verdict, dict):
        return did_not_run(
            "malformed_output",
            f"verifier output was not a JSON object (got {type(verdict).__name__})",
            raw_output=raw,
        )

    if "score" not in verdict:
        return did_not_run(
            "no_score_field",
            "verifier output carried no 'score' field",
            raw_output=raw,
        )

    if not is_valid_score(verdict["score"]):
        return did_not_run(
            "non_numeric_score",
            "verifier 'score' was not a real number in [0.0, 1.0]: "
            f"{repr(verdict['score'])[:_EVIDENCE_CHARS]}",
            raw_output=raw,
        )

    sig = _detail_infra_signature(str(verdict.get("detail", "")))
    if sig is not None:
        return did_not_run(
            "verifier_crash",
            f"verifier reported an infra failure (signature: {sig!r})",
            signature=sig,
        )

    # Absorb the ±_SCORE_EPSILON slop admitted above, so the caller reads a score
    # already inside [0, 1] and no second clamp has to know this epsilon exists.
    verdict["score"] = max(0.0, min(1.0, float(verdict["score"])))
    declared = verdict.get("passed")
    verdict["passed"] = (
        declared if isinstance(declared, bool) else verdict["score"] > 0.0
    )
    return verdict


def run_verifier_subprocess(
    verifier: str,
    *,
    base_dir: Path,
    # tuple[str, ...], not Sequence[str]: a bare `str` satisfies Sequence[str],
    # so `argv_prefix="bash"` type-checks and then splats into
    # ['b', 'a', 's', 'h'] — a silently corrupted argv that surfaces as a
    # baffling exec_error naming 'b'. A tuple annotation rejects it up front.
    argv_prefix: tuple[str, ...] = (),
    argv_suffix: tuple[str, ...] = (),
    cwd: Path,
    env: Optional[dict[str, str]] = None,
    timeout: float,
    checkpoint: str = "",
    stage: str = "deterministic_scoring",
) -> GuardResult:
    """Run ONE verifier under ``base_dir`` and return its verdict, or an InfraError.

    The producing half of the boundary whose parsing half is
    :func:`guard_checkpoint_verdict`, for the two Python runners. Owns the whole
    ladder a verifier can fall off before it ever reaches a verdict:
    unresolvable path -> path escape -> missing -> timeout -> exec failure ->
    the guard. Every rung is an InfraError, never a raise and never a zero, so a
    runner cannot report a harness bug as a crash or as the agent's score.

    ``scripts/sandbox/test_runner.sh`` is a third runner, in bash, that still
    hand-rolls this ladder in-container and derives ``passed`` its own way — it
    cannot import this module, so it is NOT covered here.

    ``argv`` is ``[*argv_prefix, resolved_verifier, *argv_suffix]``: runner.py
    needs ``bash <script>`` plus its env contract, milestone.py needs the
    workspace as ``argv[1]``. The knobs exist to hold those two conventions
    still while the ladder is shared; converging them is a behavior change this
    function deliberately does not force.
    """

    def did_not_run(cause: str, detail: str, **evidence: object) -> InfraError:
        return no_verdict(
            cause, detail, checkpoint=checkpoint, stage=stage, evidence=evidence
        )

    # resolve() raises on a symlink loop (RuntimeError), an embedded null byte
    # (ValueError) and a too-long name (OSError ENAMETOOLONG); exists() raises
    # ENAMETOOLONG in its own right, because pathlib swallows only
    # ENOENT/ENOTDIR/ELOOP and lets every other errno through. Caught broadly:
    # every way a path probe can fail is a harness bug, and a harness bug is an
    # InfraError — never a crash, never the agent's zero.
    try:
        base = Path(base_dir).resolve()
        resolved = (base / verifier).resolve()
        # Strict containment: base_dir itself is a directory, never a verifier.
        if resolved == base or not resolved.is_relative_to(base):
            return did_not_run(
                "path_escape",
                f"verifier path escapes {base}: {verifier!r} -> {resolved}",
                verifier=str(verifier),
            )
        found = resolved.exists()
    except Exception as exc:
        return did_not_run(
            "unresolvable_path",
            f"verifier path could not be resolved: {exc}",
            verifier=str(verifier),
        )

    if not found:
        return did_not_run(
            "missing_verifier",
            f"verifier script not found: {resolved}",
            verifier=str(resolved),
        )

    try:
        proc = subprocess.run(
            [*argv_prefix, str(resolved), *argv_suffix],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            env=env,
        )
    except subprocess.TimeoutExpired:
        # No verdict, and not attributable to the agent: the one verifier that
        # runs agent-authored code bounds it with its own inner pytest
        # --timeout, so this budget only expires when the harness itself wedges.
        return did_not_run(
            "verifier_timeout",
            f"verifier timed out after {timeout}s",
            timeout_seconds=timeout,
        )
    except Exception as exc:
        # A non-executable or non-ELF verifier raises OSError, a bad env dict or
        # a vanished cwd raise other types. Every exec failure is a harness bug,
        # so all of them route here rather than up.
        return did_not_run(
            "exec_error",
            f"verifier could not be executed: {exc}",
            verifier=str(resolved),
        )

    return guard_checkpoint_verdict(
        proc.stdout,
        proc.returncode,
        stderr=proc.stderr,
        checkpoint=checkpoint,
        stage=stage,
    )
