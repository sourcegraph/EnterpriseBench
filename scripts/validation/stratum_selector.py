#!/usr/bin/env python3
"""Enterprise win-regime stratum selector -- the Gate A-F harness (jn73.2).

Scores every task in the corpus against the enterprise-admission rule and emits
a versioned, per-task gate-evidence artifact:

    benchmarks/strata/enterprise_v1_candidates.json

named "candidates", not "stratum", because it is not final and must not be read
as though it were.

THE GATES

  STATIC (from task.toml + the repo tree)
    A  PARITY            baseline clones every repo the oracle files live in
    B  NON-LEAKY         leak_classifier bucket (see leak_classifier.py)
    D  ENTERPRISE CHAR.  CRNT-positive, or a dispersed single-repo oracle, and
                         never a calibration task

  MEASURED (from a run; each may be scored, failed, or genuinely unmeasured)
    C  HARD-TO-LOCALIZE  baseline turns-to-first-relevant, via compute_ttfr over
                         the locked run set's baseline traces. PARTIAL: scored
                         for the tasks that have a trace, PENDING for the rest.
    E  SOLVABLE          gold/strong-agent reward >= 0.6. No gold artifact exists
                         anywhere on disk, so this is PENDING for every task.
    F  VERIFIER-CALIB.   null<=0.1 / golden>=0.9 / adv<=0.5. PENDING for every
                         task. (The null and adversarial legs are cheap offline
                         computations -- only the golden leg needs Gate E's
                         artifact. See EnterpriseBench-jn73.2.3.)

THE INVARIANT THIS MODULE EXISTS TO ENFORCE

An unmeasured gate is not a passing gate. Gate C's history is the cautionary
tale: this bead's original plan asserted no baseline run existed and would have
emitted C as uniformly pending -- shipping the stratum with NO difficulty filter
while looking principled. In fact 109 baseline traces were sitting on disk, and
once scored they reject 46 tasks as trivially localizable. A gate that quietly
defaults to "fine" is worse than no gate.

So the guard is structural, not advisory:

  * GateStatus is a tri-state enum whose __bool__ is False for PENDING (an enum
    member is otherwise truthy, so `if gate.status:` would silently pass).
  * There is no per-gate `pass` boolean to misread -- only the enum.
  * A gate that is ABSENT counts as unmeasured, not as satisfied.
  * Every pending gate carries an `input_contract` naming the run that resolves
    it, so "pending" is never confused with "we forgot".
  * `admitted_task_ids()` RAISES StratumNotFinalError while any gate is
    unmeasured. `candidate_task_ids()` is the explicit non-final accessor.
    A flag can be ignored; a raised exception cannot.

THE RESULT (measured, at the time of writing)

35 candidates out of 116 tasks -- BELOW the >=50 the bead's acceptance criterion
asks for. This is not an artifact of the two ambiguous knobs below: the pool is
34-37 across all four of their combinations, because Gate C dominates. Of the 60
tasks with an OBSERVED turns-to-first-relevant the median is 3.5 and 49 are
trivial (<=7); NOT ONE reaches tier-1 (>=20) -- the band the bead cites as where
sgx went 9/9 is empty in this corpus. A further 23 tasks are not_found (the
baseline delegated to a subagent the extractor cannot see into) and are pending,
not credited as hard. The corpus cannot reach 50 by selection: see
EnterpriseBench-jn73.2.7 (author new tasks, or relax a named criterion on the
record; do NOT tune the gates until the count clears, which would invert an
admission rule into a quota).

The two knobs are explicit params, recorded in the artifact's provenance, and
`--sensitivity` prints the pool under every reading:

  gate_b_mode        "strict"   bucket in {behavioral, grep_hard} -- the bead's
                                literal wording
                     "ref_impl" also admits exact_path leaks whose token is a
                                recorded qa.waivers entry point -- what the
                                reference implementation (jn73.3) does

  module_granularity "parent_dir"  a module is a file's parent directory
                     "top_segment" a module is the top-level path segment

Usage:
    python3 scripts/validation/stratum_selector.py --corpus benchmarks \
        --out benchmarks/strata/enterprise_v1_candidates.json
    python3 scripts/validation/stratum_selector.py --corpus benchmarks --sensitivity
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Path setup ONLY when this file is run directly as a script (`python3
# scripts/validation/stratum_selector.py`), where neither the repo root nor lib/
# is importable yet. When imported as `scripts.validation.stratum_selector` --
# which is how the tests get it -- __package__ is set and we touch nothing.
#
# Doing this unconditionally at module level made importing this module mutate
# sys.path for the whole process, so tests/eb_metrics/ passed or failed depending
# on whether something had imported stratum_selector earlier in the pytest
# session. Test outcomes must not depend on collection order. Tests get their
# paths from tests/conftest.py instead.
if __package__ in (None, ""):  # pragma: no cover - script-mode only
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_REPO_ROOT / "lib"))

from eb_metrics.retrieval_extraction import compute_ttfr  # noqa: E402
from scripts.validation import crnt_validator  # noqa: E402
from scripts.validation import leak_classifier as lc  # noqa: E402

try:
    import tomllib
except ImportError:  # pragma: no cover - py<3.11
    import tomli as tomllib  # type: ignore[no-redef]


ARTIFACT_VERSION = "enterprise_v1"

#: The locked 3-arm run set. Gate C reads the baseline arm's traces from here.
#: It lives outside the worktree (untracked results/), so it is resolved from
#: the main checkout rather than assumed to be beside the corpus.
DEFAULT_RUNS_ROOT = Path("/home/ds/projects/EnterpriseBench/results/runs")

COMPUTABLE_GATES = ("A", "B", "D")
MEASUREMENT_GATES = ("C", "E", "F")
ALL_GATES = ("A", "B", "C", "D", "E", "F")

#: What run would resolve each unmeasured gate. A pending gate without one of
#: these is indistinguishable from an oversight.
INPUT_CONTRACTS: dict[str, str] = {
    "C": (
        "baseline calibration run: per-task agent trace -> compute_ttfr(trace, "
        "required_files) (jn73.1). Needs turns-to-first-relevant per task. "
        "Threshold: exclude <=7 (trivial); tier-1 >=20; tier-2 12-19. "
        "ALSO REQUIRED for the not_found cases: teach the path extractor to see "
        "inside Agent/Task/Skill/Workflow delegations (it currently recognizes "
        "only Read/Grep/Glob/Bash/WebFetch/WebSearch/MCP), otherwise a baseline "
        "that delegates its investigation is indistinguishable from one that "
        "never localized."
    ),
    "E": (
        "gold / strong-agent run: per-task reward on the oracle solution. "
        "Threshold: reward >= 0.6 (the CSB stratum floor was 0.132)."
    ),
    "F": (
        "verifier calibration run: score the null, golden and adversarial "
        "submissions per task. Threshold: null <= 0.1, golden >= 0.9, "
        "adversarial <= 0.5."
    ),
}

GATE_C_TRIVIAL_MAX = 7
GATE_C_TIER1_MIN = 20
GATE_C_TIER2_MIN = 12
GATE_E_REWARD_FLOOR = 0.6
GATE_F_NULL_MAX = 0.1
GATE_F_GOLDEN_MIN = 0.9
GATE_F_ADVERSARIAL_MAX = 0.5

GATE_D_MIN_FILES = 3
GATE_D_MIN_MODULES = 2


class StratumNotFinalError(RuntimeError):
    """Raised when an admitted set is requested from a stratum whose gates are
    not all measured. This is the guard that keeps a CANDIDATE pool from being
    consumed as if it were FINAL."""


class GateStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING_MEASUREMENT = "pending_measurement"

    @property
    def passed(self) -> bool:
        return self is GateStatus.PASS

    def __bool__(self) -> bool:
        # Without this, `if gate.status:` is True for every member -- including
        # PENDING_MEASUREMENT -- which is exactly the silent-pass bug.
        return self is GateStatus.PASS


@dataclass(frozen=True)
class GateEvidence:
    gate: str
    status: GateStatus
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    input_contract: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "gate": self.gate,
            "status": self.status.value,
            "reason": self.reason,
        }
        if self.evidence:
            d["evidence"] = self.evidence
        if self.input_contract:
            d["input_contract"] = self.input_contract
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GateEvidence:
        return cls(
            gate=d["gate"],
            status=GateStatus(d["status"]),
            reason=d.get("reason", ""),
            evidence=d.get("evidence", {}) or {},
            input_contract=d.get("input_contract", ""),
        )


@dataclass
class TaskGates:
    task_id: str
    suite: str
    gates: dict[str, GateEvidence]

    @property
    def is_candidate(self) -> bool:
        """No gate that could be scored has FAILED.

        A pending gate keeps a task in the pool (we simply do not know yet); a
        failed gate takes it out, even from a partially-scored gate. That
        asymmetry is the point: Gate C has a baseline trace for only some of the
        corpus, and the 46 tasks among those that are trivially localizable
        (ttfr <= 7) must drop out NOW rather than linger in the pool until the
        remaining traces happen to exist.

        Requires gate PRESENCE too: an empty or partial gates dict has no failed
        gates, so without this it would report is_candidate=True -- the same
        vacuous-truth defect fixed for is_final/admitted.
        """
        return not self.failed_gates and not self.missing_gates

    @property
    def admitted(self) -> bool:
        """ALL SIX gates pass. A pending gate is not a passing gate, so no task
        can be admitted until the measurements are run."""
        return all(self.gates[g].status.passed for g in ALL_GATES if g in self.gates) \
            and set(self.gates) >= set(ALL_GATES)

    @property
    def pending_gates(self) -> list[str]:
        return [g for g in ALL_GATES
                if g in self.gates
                and self.gates[g].status is GateStatus.PENDING_MEASUREMENT]

    @property
    def missing_gates(self) -> list[str]:
        """Gates absent from the dict entirely -- not pending, just not there.

        An ABSENT gate is strictly worse than a pending one: it carries no
        status, no reason and no input contract, so nothing downstream can even
        see that it was never evaluated. Finality must therefore require
        presence, not merely the absence of PENDING -- otherwise an artifact
        that simply omits its gates stamps itself FINAL.
        """
        return [g for g in ALL_GATES if g not in self.gates]

    @property
    def is_final(self) -> bool:
        return not self.pending_gates and not self.missing_gates

    @property
    def failed_gates(self) -> list[str]:
        return [g for g in ALL_GATES
                if g in self.gates and self.gates[g].status is GateStatus.FAIL]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "suite": self.suite,
            "is_candidate": self.is_candidate,
            "admitted": self.admitted,
            "is_final": self.is_final,
            "pending_gates": self.pending_gates,
            "failed_gates": self.failed_gates,
            "gates": {g: ev.to_dict() for g, ev in self.gates.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskGates:
        return cls(
            task_id=d["task_id"],
            suite=d.get("suite", ""),
            gates={g: GateEvidence.from_dict(ev) for g, ev in d["gates"].items()},
        )


@dataclass(frozen=True)
class SelectorParams:
    #: "strict" = bucket in {behavioral, grep_hard} (the bead's literal Gate B).
    #: "ref_impl" = also admit exact_path leaks waived via qa.waivers, which is
    #: what jn73.3 -- the reference implementation the bead names -- does.
    gate_b_mode: str = "strict"
    #: What counts as a "module" for single-repo dispersion.
    module_granularity: str = "parent_dir"

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_b_mode": self.gate_b_mode,
            "module_granularity": self.module_granularity,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SelectorParams:
        return cls(
            gate_b_mode=d.get("gate_b_mode", "strict"),
            module_granularity=d.get("module_granularity", "parent_dir"),
        )


@dataclass
class StratumArtifact:
    version: str
    params: SelectorParams
    tasks: list[TaskGates]
    source_sha: str

    @property
    def unmeasured_gates(self) -> list[str]:
        """Gates that are pending OR absent for at least one task.

        Absent counts as unmeasured. A gate nobody scored and nobody recorded
        is not a measured gate just because it left no trace.
        """
        seen: set[str] = set()
        for t in self.tasks:
            seen.update(t.pending_gates)
            seen.update(t.missing_gates)
        return [g for g in ALL_GATES if g in seen]

    @property
    def is_final(self) -> bool:
        return not self.unmeasured_gates

    def candidate_task_ids(self) -> list[str]:
        """The pool that clears every gate we can compute today.

        Legitimate to read off a non-final artifact -- the caller has named it
        `candidate`, which is what it is.
        """
        return [t.task_id for t in self.tasks if t.is_candidate]

    def admitted_task_ids(self) -> list[str]:
        """The FINAL stratum. Raises unless every gate has been measured.

        This is the structural guard: a consumer cannot obtain an admitted set
        from an artifact whose C/E/F were never run. It must either run the
        measurements or explicitly ask for `candidate_task_ids`.
        """
        if not self.is_final:
            raise StratumNotFinalError(
                f"stratum {self.version!r} is NOT final: gates "
                f"{', '.join(self.unmeasured_gates)} are unmeasured "
                f"(pending_measurement). Run the measurements named in each "
                f"gate's input_contract, or call candidate_task_ids() if you "
                f"knowingly want the pre-measurement pool."
            )
        return [t.task_id for t in self.tasks if t.admitted]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "stratum_status": "FINAL" if self.is_final else "CANDIDATE",
            "is_final": self.is_final,
            "unmeasured_gates": self.unmeasured_gates,
            "input_contracts": {g: INPUT_CONTRACTS[g] for g in self.unmeasured_gates
                                if g in INPUT_CONTRACTS},
            "source_sha": self.source_sha,
            "params": self.params.to_dict(),
            "summary": self.summary(),
            "tasks": [t.to_dict() for t in self.tasks],
        }

    def summary(self) -> dict[str, Any]:
        def count(g: str, status: GateStatus) -> int:
            return sum(1 for t in self.tasks
                       if g in t.gates and t.gates[g].status is status)
        return {
            "scored": len(self.tasks),
            "candidates": len(self.candidate_task_ids()),
            "admitted": len([t for t in self.tasks if t.admitted]),
            "gate_counts": {
                g: {
                    "pass": count(g, GateStatus.PASS),
                    "fail": count(g, GateStatus.FAIL),
                    "pending_measurement": count(g, GateStatus.PENDING_MEASUREMENT),
                }
                for g in ALL_GATES
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StratumArtifact:
        return cls(
            version=d["version"],
            params=SelectorParams.from_dict(d.get("params", {})),
            tasks=[TaskGates.from_dict(t) for t in d.get("tasks", [])],
            source_sha=d.get("source_sha", ""),
        )


# ---------------------------------------------------------------------------
# Gate scorers -- computable
# ---------------------------------------------------------------------------


def score_gate_a(declared_repos: set[str], oracle_repos: set[str],
                 has_oracle_files: bool = True) -> GateEvidence:
    """PARITY: the baseline arm must be able to see every oracle file locally.

    The baseline Dockerfile clones exactly the task's declared `[[repos]]`
    (scripts/sandbox/dockerfile_generator.generate_standard_dockerfile). If an
    oracle file lives in a repo that is never cloned, the baseline arm is
    solving blind -- the empty-baseline confound this gate exists to reject.
    """
    if not declared_repos:
        return GateEvidence(
            "A", GateStatus.FAIL,
            "task declares no repos: baseline arm would get an empty workspace",
            {"declared_repos": [], "oracle_repos": sorted(oracle_repos)},
        )
    if has_oracle_files and not oracle_repos:
        # `missing = {} - declared` is empty, so this would otherwise PASS
        # vacuously -- a parity gate that verified nothing. The schema requires
        # a `repo` on every required_files entry; a task without one cannot be
        # checked, and an uncheckable gate must not report success.
        return GateEvidence(
            "A", GateStatus.FAIL,
            "oracle files carry no `repo` tag, so parity cannot be verified "
            "(schemas/task.schema.json requires `repo` on every required_files entry)",
            {"declared_repos": sorted(declared_repos), "oracle_repos": []},
        )
    missing = oracle_repos - declared_repos
    if missing:
        return GateEvidence(
            "A", GateStatus.FAIL,
            f"oracle files live in repo(s) the baseline never clones: "
            f"{', '.join(sorted(missing))}",
            {"declared_repos": sorted(declared_repos),
             "oracle_repos": sorted(oracle_repos),
             "missing_repos": sorted(missing)},
        )
    return GateEvidence(
        "A", GateStatus.PASS,
        "baseline clones every repo the oracle files live in",
        {"declared_repos": sorted(declared_repos),
         "oracle_repos": sorted(oracle_repos)},
    )


def waiver_has_downstream_target(waived_token: str, oracle_files: list[str]) -> bool:
    """Is a qa.waivers entry structurally sound?

    The waiver rationale (jn73.3 Option B) is: the leaked path is the *given
    entry point*, and the scored discovery lies downstream of it. That only
    holds if there IS something downstream -- i.e. the task has at least one
    oracle file that is not the waived token. If the leaked path is the task's
    only oracle file, the task is grep-trivial and the waiver is simply wrong.

    That is a deterministic check, so it should be checked rather than trusted.
    """
    norm = lc.strip_repo_slug(waived_token).lower()
    others = [f for f in oracle_files
              if lc.strip_repo_slug(f).lower() != norm
              and not lc.strip_repo_slug(f).lower().endswith("/" + norm)]
    return bool(others)


def score_gate_b(result: lc.GateBResult | None,
                 mode: str,
                 oracle_files: list[str] | None = None) -> GateEvidence:
    """NON-LEAKY: the instruction must not localize the oracle for free."""
    if result is None:
        return GateEvidence(
            "B", GateStatus.FAIL,
            "no instruction+oracle could be loaded; Gate B cannot clear",
        )
    strict_pass = result.bucket in (lc.Bucket.BEHAVIORAL, lc.Bucket.GREP_HARD)
    passed = strict_pass if mode == "strict" else result.admit
    ev = {
        "bucket": result.bucket.value,
        "token": result.token,
        "waived": result.waived,
        "strict_pass": strict_pass,
        "ref_impl_pass": result.admit,
        "mode": mode,
    }
    reason = result.reason
    if result.waived:
        sound = waiver_has_downstream_target(result.token, oracle_files or [])
        ev["waiver_has_downstream_target"] = sound
        if not sound:
            # The waiver claims the leak is a mere entry point, but there is
            # nothing downstream of it. Refuse the waiver regardless of mode.
            return GateEvidence(
                "B", GateStatus.FAIL,
                f"waiver is unsound: leaked token {result.token!r} is the task's "
                f"only oracle file, so there is no downstream discovery to score",
                ev,
            )
    return GateEvidence("B", GateStatus.PASS if passed else GateStatus.FAIL,
                        reason, ev)


def _modules(paths: list[str], granularity: str) -> set[str]:
    out: set[str] = set()
    for p in paths:
        segs = [s for s in lc.strip_repo_slug(p).split("/") if s]
        if granularity == "top_segment":
            out.add(segs[0] if len(segs) > 1 else ".")
        else:  # parent_dir
            out.add("/".join(segs[:-1]) or ".")
    return out


def score_gate_d(declared_repos: set[str],
                 oracle_files: list[str],
                 granularity: str,
                 crnt_positive: bool,
                 difficulty_stratum: str) -> GateEvidence:
    """ENTERPRISE CHARACTER: CRNT-positive, or a dispersed single-repo oracle.

    A one-file, one-directory task is not an enterprise context-gathering
    problem however hard the individual file is.

    Two things this gate must get right, both of which the bead's wording
    glosses over:

    * "CRNT-positive (multi-repo)" is NOT "declares >= 2 repos". crnt_validator
      additionally requires every declared repo to be covered by a required_files
      entry -- a 3-repo task where one repo is only touched by sufficient_files
      is CRNT-negative. Declaring repos is not the same as needing them.
    * Nothing else in gates A-F tests SCALE, so this is the only place that can
      keep a calibration task (small-repo by construction) out of a stratum
      labelled "enterprise-complex".
    """
    ev: dict[str, Any] = {
        "n_repos": len(declared_repos),
        "repos": sorted(declared_repos),
        "crnt_positive": crnt_positive,
        "difficulty_stratum": difficulty_stratum,
        "n_oracle_files": len(oracle_files),
        "module_granularity": granularity,
    }

    if difficulty_stratum == "calibration":
        return GateEvidence(
            "D", GateStatus.FAIL,
            "calibration task: small-repo by design, so it carries no enterprise "
            "character regardless of dispersion",
            ev,
        )

    if crnt_positive:
        return GateEvidence(
            "D", GateStatus.PASS,
            f"CRNT-positive across {len(declared_repos)} repos: the task genuinely "
            f"requires more than one repo",
            ev,
        )

    mods = _modules(oracle_files, granularity)
    ev["n_modules"] = len(mods)
    ev["modules"] = sorted(mods)
    dispersed = len(oracle_files) >= GATE_D_MIN_FILES and len(mods) >= GATE_D_MIN_MODULES
    if dispersed:
        return GateEvidence(
            "D", GateStatus.PASS,
            f"single-repo but dispersed: {len(oracle_files)} oracle files across "
            f"{len(mods)} modules",
            ev,
        )
    return GateEvidence(
        "D", GateStatus.FAIL,
        f"not CRNT-positive and concentrated: {len(oracle_files)} oracle file(s) "
        f"across {len(mods)} module(s); needs >={GATE_D_MIN_FILES} files and "
        f">={GATE_D_MIN_MODULES} modules",
        ev,
    )


def unloadable_task_gates(task_id: str, suite: str, why: str) -> TaskGates:
    """A task whose oracle cannot be loaded FAILS loudly.

    The corpus runner used to `continue` past these, so they landed in neither
    the admitted nor the rejected set -- an invisible drop. A task with no
    loadable oracle cannot satisfy parity, non-leakiness or dispersion, and
    saying so is the only honest scoring.
    """
    reason = f"task oracle could not be loaded ({why}); gate cannot be evaluated"
    gates = {g: GateEvidence(g, GateStatus.FAIL, reason) for g in COMPUTABLE_GATES}
    for g in MEASUREMENT_GATES:
        gates[g] = _pending(g, f"task oracle could not be loaded ({why})")
    return TaskGates(task_id=task_id, suite=suite, gates=gates)


# ---------------------------------------------------------------------------
# Gate scorers -- measurements. PENDING unless handed a measurement.
# ---------------------------------------------------------------------------


def _pending(gate: str, reason: str,
             evidence: dict[str, Any] | None = None) -> GateEvidence:
    return GateEvidence(
        gate, GateStatus.PENDING_MEASUREMENT, reason,
        evidence or {},
        input_contract=INPUT_CONTRACTS[gate],
    )


def score_gate_c(measurement: dict[str, Any] | None) -> GateEvidence:
    """HARD-TO-LOCALIZE: baseline turns-to-first-relevant, from a real trace.

    This is a MEASUREMENT, computed by ``compute_ttfr`` (jn73.1) over the
    baseline arm's ``agent_trace.jsonl``. There is deliberately no static proxy:
    estimating localizability from oracle-file counts or instruction length
    would be a heuristic standing in for the very quantity the stratum is
    defined by.

    It is a PARTIAL gate. The locked run set covers only some of the corpus, so
    each task lands in one of three places, and collapsing them would be the
    whole bug this module exists to prevent:

      coverage="ok"         -> scored. ttfr <= 7 is trivial and FAILS.
      coverage="not_found"  -> the baseline made tool calls but the extractor
                               never saw it open an oracle file. PENDING -- see
                               below; this is NOT evidence of difficulty.
      coverage="no_trace"   -> genuinely unmeasured. PENDING.

    On ``not_found``. ``compute_ttfr``'s contract claims it is "a genuine miss ...
    distinct from an observability gap", and an earlier version of this gate took
    that at face value and scored it PASS ("the baseline never localized, so the
    task is maximally hard"). That was WRONG, and the traces falsify it: the
    baseline for dep-traversal-005 delegates its investigation to an ``Agent``
    subagent and only ``Write``s the result, and the extractor recognizes
    Read/Grep/Glob/Bash/WebFetch/WebSearch/MCP -- not Agent/Task/Skill/Workflow.
    The subagent's reads are simply invisible. Corroborating: the tasks in that
    same family whose traces ARE observable measure ttfr=2-4, i.e. trivial.

    So ``not_found`` cannot distinguish "never localized" (hard) from "localized
    somewhere we cannot see" (possibly trivial). Asserting difficulty from an
    unobserved trace is exactly the silent-pass this module exists to prevent, so
    it is PENDING: the task stays a candidate, but the stratum never claims it is
    hard on evidence it does not have.
    """
    if not measurement:
        return _pending("C", "no baseline trace for this task")
    coverage = measurement.get("coverage")
    # Carried through every branch -- including PENDING, where the run key is
    # precisely what you need to work out WHY a trace was not found.
    base = {"coverage": coverage, "run_key": measurement.get("run_key")}

    if coverage == "not_found":
        return _pending(
            "C",
            "baseline trace shows no oracle-file read, but the extractor cannot "
            "see inside Agent/Task/Skill delegations -- 'never localized' and "
            "'localized out of view' are indistinguishable here, so difficulty "
            "is NOT asserted",
            {**base, "ttfr": None, "tier": None},
        )
    if coverage != "ok" or measurement.get("ttfr") is None:
        return _pending(
            "C", f"no usable baseline trace for this task (coverage={coverage!r})",
            base,
        )

    ttfr = measurement["ttfr"]
    if ttfr <= GATE_C_TRIVIAL_MAX:
        return GateEvidence(
            "C", GateStatus.FAIL,
            f"trivial to localize: turns-to-first-relevant={ttfr} "
            f"(<={GATE_C_TRIVIAL_MAX})",
            {**base, "ttfr": ttfr, "tier": None},
        )
    tier: int | None
    if ttfr >= GATE_C_TIER1_MIN:
        tier = 1
    elif ttfr >= GATE_C_TIER2_MIN:
        tier = 2
    else:
        tier = None  # the unlabelled 8-11 band: not trivial, not yet tiered
    return GateEvidence(
        "C", GateStatus.PASS,
        f"hard to localize: turns-to-first-relevant={ttfr}"
        + (f" (tier-{tier})" if tier else " (above the trivial cut, untiered band 8-11)"),
        {**base, "ttfr": ttfr, "tier": tier},
    )


def score_gate_e(measurement: dict[str, Any] | None) -> GateEvidence:
    """SOLVABLE-NOT-FLOORED: a strong agent must actually be able to score."""
    if not measurement or measurement.get("gold_reward") is None:
        return _pending("E", "no gold / strong-agent run for this task")
    reward = measurement["gold_reward"]
    ok = reward >= GATE_E_REWARD_FLOOR
    return GateEvidence(
        "E", GateStatus.PASS if ok else GateStatus.FAIL,
        f"gold reward {reward:.3f} "
        f"{'>=' if ok else '<'} floor {GATE_E_REWARD_FLOOR}",
        {"gold_reward": reward},
    )


def score_gate_f(measurement: dict[str, Any] | None) -> GateEvidence:
    """VERIFIER-CALIBRATED: the scorer must separate null from golden."""
    if not measurement or any(measurement.get(k) is None
                              for k in ("null", "golden", "adversarial")):
        return _pending("F", "no verifier calibration run for this task")
    null, golden, adv = (measurement["null"], measurement["golden"],
                         measurement["adversarial"])
    failures = []
    if null > GATE_F_NULL_MAX:
        failures.append(f"null {null:.2f} > {GATE_F_NULL_MAX}")
    if golden < GATE_F_GOLDEN_MIN:
        failures.append(f"golden {golden:.2f} < {GATE_F_GOLDEN_MIN}")
    if adv > GATE_F_ADVERSARIAL_MAX:
        failures.append(f"adversarial {adv:.2f} > {GATE_F_ADVERSARIAL_MAX}")
    ev = {"null": null, "golden": golden, "adversarial": adv}
    if failures:
        return GateEvidence("F", GateStatus.FAIL,
                            "verifier miscalibrated: " + "; ".join(failures), ev)
    return GateEvidence("F", GateStatus.PASS, "verifier calibrated", ev)


# ---------------------------------------------------------------------------
# Corpus scoring
# ---------------------------------------------------------------------------


def _declared_repos(task_toml: dict[str, Any]) -> set[str]:
    """The repo identities the baseline actually clones.

    Keyed on ``[[repos]].path`` -- the directory the sandbox clones INTO
    (dockerfile_generator clones to /workspace/{path}) and the same key
    crnt_validator uses and that ground_truth's ``repo`` tag refers to.

    An earlier version keyed on ``name`` (which no task defines) and fell back to
    the URL basename, which silently diverges whenever a task deliberately picks
    a disambiguating path: docker/cli is cloned to `docker-cli`, aws/aws-cli to
    `awscli`. Gate A then computed `missing = {docker-cli} - {cli}` and FAILED 6
    tasks whose repos were, in fact, cloned. Falsely shrinking the pool in the
    very gate under audit -- and biased toward the multi-repo tasks that need
    disambiguating paths in the first place.
    """
    out: set[str] = set()
    for r in (task_toml.get("repos") or []):
        key = r.get("path") or r.get("name") \
            or r.get("url", "").rstrip("/").split("/")[-1]
        if key:
            out.add(key)
    return out


def _oracle_repos(task_dir: Path, task_toml: dict[str, Any]) -> set[str]:
    gt = lc.load_ground_truth(task_dir, task_toml)
    out: set[str] = set()
    for key in ("required_files", "sufficient_files"):
        for entry in (gt.get(key) or []):
            if isinstance(entry, dict) and entry.get("repo"):
                out.add(entry["repo"])
    return out


def _crnt_positive(task_toml: dict[str, Any]) -> bool:
    """Per-task CRNT boolean. Stricter than "declares >= 2 repos": every
    declared repo must be covered by a required_files entry.

    Deliberately NOT wrapped in a try/except. ``evaluate_crnt`` is deterministic
    over already-parsed TOML and raises for none of the 119 task.toml files in
    the corpus, so a swallow would be dead code today -- and a landmine
    tomorrow: a task that started raising would be silently scored CRNT-negative,
    quietly downgrading Gate D to its dispersion fallback instead of failing.
    A gate that cannot be evaluated must never evaluate to "no".
    """
    return bool(crnt_validator.evaluate_crnt(task_toml).passes_crnt)


def measure_ttfr(run_key: str, oracle_files: list[str],
                 runs_root: Path) -> dict[str, Any]:
    """Gate C's measurement, from the baseline arm's real trace.

    ``run_key`` is the task's ``[task].id``, NOT its directory name. The two
    diverge across much of the corpus (``benchmarks/.../calibration-001/`` holds
    ``id = "cal-err-flask-blueprint-001"``), and the locked run set is keyed by
    id: keying by directory finds 40 baseline traces, keying by id finds 89.

    Note the oracle is passed IN. ``compute_ttfr`` takes ``required_files`` as
    an argument, so Gate C can be fed the task.toml-aware oracle without
    touching ``retrieval_extraction``'s own ground-truth resolution -- which is
    what keeps this out of the already-published retrieval-recall aggregate.
    """
    trace = runs_root / run_key / "baseline" / "agent_trace.jsonl"
    r = compute_ttfr(trace, oracle_files)
    return {"coverage": r.coverage, "ttfr": r.turns_to_first_relevant,
            "run_key": run_key}


def score_task(task_dir: Path,
               params: SelectorParams,
               measurements: dict[str, dict[str, Any]] | None = None,
               runs_root: Path | None = None) -> TaskGates | None:
    task_toml_path = task_dir / "task.toml"
    if not task_toml_path.is_file():
        return None

    task_id = task_dir.name
    suite = task_dir.parent.name
    try:
        task_toml = tomllib.loads(task_toml_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return unloadable_task_gates(task_id, suite, f"unparseable task.toml: {exc}")

    oracle = lc.load_task(task_dir)
    if oracle is None:
        # M2: never a silent skip.
        return unloadable_task_gates(
            task_id, suite, "no instruction+ground_truth oracle could be loaded"
        )

    # The run set is keyed by [task].id, which is NOT the directory name.
    run_key = (task_toml.get("task") or {}).get("id") or task_id

    m = dict((measurements or {}).get(task_id, {}))
    if runs_root is not None and "C" not in m:
        m["C"] = measure_ttfr(run_key, oracle.oracle_files, runs_root)

    declared = _declared_repos(task_toml)
    # difficulty_stratum is a TOP-LEVEL key in task.toml, not nested.
    stratum = task_toml.get("difficulty_stratum", "")

    gates = {
        "A": score_gate_a(declared, _oracle_repos(task_dir, task_toml),
                          has_oracle_files=bool(oracle.oracle_files)),
        "B": score_gate_b(lc.decide_task_dir(task_dir), params.gate_b_mode,
                          oracle.oracle_files),
        "C": score_gate_c(m.get("C")),
        "D": score_gate_d(declared, oracle.oracle_files, params.module_granularity,
                          crnt_positive=_crnt_positive(task_toml),
                          difficulty_stratum=stratum),
        "E": score_gate_e(m.get("E")),
        "F": score_gate_f(m.get("F")),
    }
    return TaskGates(task_id=task_id, suite=suite, gates=gates)


def score_corpus(root: Path,
                 params: SelectorParams,
                 measurements: dict[str, dict[str, Any]] | None = None,
                 runs_root: Path | None = None) -> list[TaskGates]:
    out: list[TaskGates] = []
    for toml_path in sorted(root.rglob("task.toml")):
        task_dir = toml_path.parent
        rel = task_dir.relative_to(root) if task_dir.is_relative_to(root) else task_dir
        if rel.parts and rel.parts[0] in ("mined", "_archived", "strata"):
            continue
        scored = score_task(task_dir, params, measurements, runs_root)
        if scored is not None:
            out.append(scored)
    return out


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # pragma: no cover - git absent
        return "unknown"


def build_artifact(root: Path,
                   params: SelectorParams,
                   measurements: dict[str, dict[str, Any]] | None = None,
                   runs_root: Path | None = None) -> StratumArtifact:
    return StratumArtifact(
        version=ARTIFACT_VERSION,
        params=params,
        tasks=score_corpus(root, params, measurements, runs_root),
        source_sha=_git_sha(),
    )


def _sensitivity(root: Path, runs_root: Path | None) -> int:
    """Print the pool size under all four readings of the two ambiguous knobs.

    The acceptance criterion asks for >=50 tasks; whether that is met depends
    on choices the bead leaves open. Printing the band is the honest answer.
    """
    print("Candidate-pool sensitivity (no gate has FAILED; C is partial, E/F pending):\n")
    print(f"  {'gate_b_mode':<12} {'module_granularity':<20} {'pool':>5}")
    for b_mode in ("strict", "ref_impl"):
        for gran in ("top_segment", "parent_dir"):
            art = build_artifact(root, SelectorParams(b_mode, gran), runs_root=runs_root)
            n = len(art.candidate_task_ids())
            flag = "" if n >= 50 else "   <-- BELOW the >=50 acceptance criterion"
            print(f"  {b_mode:<12} {gran:<20} {n:>5}{flag}")
    print("\nNo reading is 'the' answer: the bead's literal Gate B wording says")
    print("strict, but the reference impl it names (jn73.3) admits waived")
    print("exact_path leaks. 'Module' is undefined for single-repo tasks.")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", default="benchmarks", help="corpus root")
    p.add_argument("--out", help="write the stratum artifact here")
    p.add_argument("--gate-b-mode", choices=("strict", "ref_impl"), default="strict")
    p.add_argument("--module-granularity", choices=("parent_dir", "top_segment"),
                   default="parent_dir")
    p.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT),
                   help="locked run set; Gate C reads baseline traces from here")
    p.add_argument("--sensitivity", action="store_true",
                   help="print pool size under all four readings and exit")
    args = p.parse_args(argv)

    root = Path(args.corpus)
    runs_root = Path(args.runs_root) if args.runs_root else None
    if runs_root and not runs_root.is_dir():
        print(f"WARNING: runs-root {runs_root} does not exist; Gate C will be "
              f"PENDING for every task.", file=sys.stderr)

    if args.sensitivity:
        return _sensitivity(root, runs_root)

    params = SelectorParams(gate_b_mode=args.gate_b_mode,
                            module_granularity=args.module_granularity)
    art = build_artifact(root, params, runs_root=runs_root)
    s = art.summary()

    print(f"Stratum {art.version} [{'FINAL' if art.is_final else 'CANDIDATE'}]")
    print(f"  params: {params.to_dict()}")
    print(f"  scored:     {s['scored']}")
    print(f"  candidates: {s['candidates']}  (no gate has FAILED)")
    print(f"  admitted:   {s['admitted']}  (all SIX gates PASS)")
    print(f"  unmeasured gates: {', '.join(art.unmeasured_gates) or 'none'}")
    print(f"\n  {'gate':<4} {'pass':>6} {'fail':>6} {'pending':>9}")
    for g in ALL_GATES:
        c = s["gate_counts"][g]
        print(f"    {g:<2} {c['pass']:>6} {c['fail']:>6} {c['pending_measurement']:>9}")

    if not art.is_final:
        print(f"\n  NOT FINAL. Gates {', '.join(art.unmeasured_gates)} are pending a")
        print("  measurement run; no task can be admitted until they are scored.")
        print("  Consumers must call candidate_task_ids(); admitted_task_ids() raises.")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(art.to_dict(), indent=2) + "\n", encoding="utf-8")
        print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
