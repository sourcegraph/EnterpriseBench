"""The stratum artifact's data model -- gate evidence, params, provenance.

Split out of stratum_selector so the tri-state that guards this whole exercise --
a gate whose measurement never ran must never be mistaken for a gate that passed --
lives in one small, readable place. GateStatus.__bool__ returns False for
PENDING_MEASUREMENT, and `admitted_task_ids()` RAISES on a non-final artifact
rather than returning a plausible-looking list.

This module knows nothing about scoring or the corpus: it depends on neither
`stratum_gates` nor `stratum_selector`, and that direction is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

ARTIFACT_VERSION = "enterprise_v1"

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

#: Every constant that can move the pool, recorded in the artifact. A reader who
#: cannot see the thresholds cannot tell a 35 from a 46.
GATE_THRESHOLDS: dict[str, Any] = {
    "gate_c_trivial_max": GATE_C_TRIVIAL_MAX,
    "gate_c_tier1_min": GATE_C_TIER1_MIN,
    "gate_c_tier2_min": GATE_C_TIER2_MIN,
    "gate_d_min_files": GATE_D_MIN_FILES,
    "gate_d_min_modules": GATE_D_MIN_MODULES,
    "gate_e_reward_floor": GATE_E_REWARD_FLOOR,
    "gate_f_null_max": GATE_F_NULL_MAX,
    "gate_f_golden_min": GATE_F_GOLDEN_MIN,
    "gate_f_adversarial_max": GATE_F_ADVERSARIAL_MAX,
}


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
        """ALL SIX gates are PRESENT and pass. A pending gate is not a passing
        gate, and an absent one is not either -- so no task can be admitted
        until every measurement has been run and recorded."""
        return all(g in self.gates and self.gates[g].status.passed for g in ALL_GATES)

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
    #: What counts as a "module" for single-repo dispersion.
    module_granularity: str = "parent_dir"

    def to_dict(self) -> dict[str, Any]:
        return {"module_granularity": self.module_granularity}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SelectorParams:
        return cls(module_granularity=d.get("module_granularity", "parent_dir"))


@dataclass(frozen=True)
class Provenance:
    """Everything that has to be true for a reader to reproduce this artifact.

    Recording the params alone is not enough, and that gap was a real defect: a
    run whose ``runs_root`` did not exist silently scored Gate C as PENDING for
    every task, dropping its 49 FAILs and inflating the pool from 35 to 46 --
    while emitting params, version and source_sha byte-identical to the correct
    run. Only a stderr warning distinguished them.

    So the inputs are recorded, not just the knobs:

    ``traces_digest`` is the load-bearing one. Coverage counts say only WHETHER a
    trace was resolved, never WHAT it said -- a re-run baseline whose ttfr moves
    3 -> 25 flips Gate C with ``coverage="ok"`` both times, changing the pool
    while leaving the counts identical. The digest is taken over the trace
    CONTENT, so two artifacts built from different traces cannot look alike.
    """

    source_sha: str
    #: A dirty tree emits the same sha as a clean one, so the sha alone does not
    #: identify the code that ran.
    source_dirty: bool
    corpus_root: str
    #: "" when Gate C was not measured at all (every task PENDING).
    runs_root: str
    gate_thresholds: dict[str, Any] = field(default_factory=dict)
    #: {"ok": n, "not_found": n, "no_trace": n} over the scored corpus.
    trace_coverage: dict[str, int] = field(default_factory=dict)
    #: sha256 over the sorted (run_key, sha256(trace_bytes)) pairs.
    traces_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sha": self.source_sha,
            "source_dirty": self.source_dirty,
            "corpus_root": self.corpus_root,
            "runs_root": self.runs_root,
            "gate_thresholds": self.gate_thresholds,
            "trace_coverage": self.trace_coverage,
            "traces_digest": self.traces_digest,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Provenance:
        return cls(
            source_sha=d.get("source_sha", ""),
            source_dirty=bool(d.get("source_dirty", False)),
            corpus_root=d.get("corpus_root", ""),
            runs_root=d.get("runs_root", ""),
            gate_thresholds=d.get("gate_thresholds", {}),
            trace_coverage=d.get("trace_coverage", {}),
            traces_digest=d.get("traces_digest", ""),
        )


@dataclass
class StratumArtifact:
    version: str
    params: SelectorParams
    tasks: list[TaskGates]
    provenance: Provenance

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
            "provenance": self.provenance.to_dict(),
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
            provenance=Provenance.from_dict(d.get("provenance", {})),
        )


