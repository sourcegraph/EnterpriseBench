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
once scored they reject 49 tasks as trivially localizable. A gate that quietly
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
asks for. This is not an artifact of the ambiguous knob below: the pool holds at
34-37 across its readings, because Gate C dominates. Of the 60 tasks with an
OBSERVED turns-to-first-relevant the median is 3.5 and 49 are trivial (<=7); NOT
ONE reaches tier-1 (>=20) -- the band the bead cites as where sgx went 9/9 is
empty in this corpus. A further 23 tasks are not_found (the baseline delegated to
a subagent the extractor cannot see into) and are pending, not credited as hard.
The corpus cannot reach 50 by selection: see EnterpriseBench-jn73.2.7 (author new
tasks, or relax a named criterion on the record; do NOT tune the gates until the
count clears, which would invert an admission rule into a quota).

Gate B has ONE rule, implemented once, in `leak_classifier.decide()`: reject the
exact_path / symbol / dir buckets, admit behavioral / grep_hard. `make
verify-leak` and this selector both call it, so an author and the selector cannot
be told different things about the same task. The waiver-aware variant that used
to live behind a `--gate-b-mode` flag was withdrawn -- see leak_classifier's
module docstring and EnterpriseBench-jn73.2.12.

The remaining knob is an explicit param, recorded in the artifact's provenance,
and `--sensitivity` prints the pool under every reading:

  module_granularity "parent_dir"  a module is a file's parent directory
                     "top_segment" a module is the top-level path segment

Usage:
    python3 scripts/validation/stratum_selector.py --corpus benchmarks \
        --out benchmarks/strata/enterprise_v1_candidates.json
    python3 scripts/validation/stratum_selector.py --corpus benchmarks --sensitivity
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Path setup ONLY in script mode, where the repo root is not yet importable.
if __package__ in (None, ""):  # pragma: no cover - script-mode only
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_REPO_ROOT / "lib"))

from scripts.validation.stratum_gates import (  # noqa: E402
    measure_corpus_ttfr,
    measure_ttfr,
    score_corpus,
    score_task,
)
from scripts.validation.stratum_model import (  # noqa: E402
    ALL_GATES,
    ARTIFACT_VERSION,
    GATE_THRESHOLDS,
    Provenance,
    SelectorParams,
    StratumArtifact,
)

def _git_sha() -> tuple[str, bool]:
    """(HEAD sha, is_dirty) for the REPO, not for whatever cwd we were run from."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              check=True, cwd=_REPO_ROOT).stdout
    try:
        return git("rev-parse", "HEAD").strip(), bool(git("status", "--porcelain").strip())
    except Exception:  # pragma: no cover - git absent
        return "unknown", True


def trace_coverage(measurements: dict[str, dict[str, Any]] | None) -> dict[str, int]:
    """How many traces resolved, and how they landed. Zero `ok`+`not_found` means
    the runs_root held no traces at all -- see `resolved_traces`."""
    counts = {"ok": 0, "not_found": 0, "no_trace": 0}
    for m in (measurements or {}).values():
        cov = (m.get("C") or {}).get("coverage")
        if cov in counts:
            counts[cov] += 1
    return counts


def resolved_traces(coverage: dict[str, int]) -> int:
    """Traces that were actually FOUND on disk.

    `no_trace` means the file was absent; `ok` and `not_found` both mean a real
    trace was read (they differ only in whether the baseline ever opened an
    oracle file). So this is the count that tells an empty runs_root from a
    populated one.
    """
    return coverage.get("ok", 0) + coverage.get("not_found", 0)


def _traces_digest(measurements: dict[str, dict[str, Any]] | None) -> str:
    """One hash over every trace that fed Gate C."""
    pairs = sorted(
        (m["C"]["run_key"], m["C"].get("trace_sha256", ""))
        for m in (measurements or {}).values()
        if m.get("C", {}).get("trace_sha256")
    )
    if not pairs:
        return ""
    joined = "\n".join(f"{k}:{h}" for k, h in pairs)
    return hashlib.sha256(joined.encode()).hexdigest()


def build_artifact(root: Path,
                   params: SelectorParams,
                   measurements: dict[str, dict[str, Any]] | None = None,
                   runs_root: Path | None = None) -> StratumArtifact:
    if measurements is None and runs_root is not None:
        measurements = measure_corpus_ttfr(root, runs_root)
    sha, dirty = _git_sha()
    return StratumArtifact(
        version=ARTIFACT_VERSION,
        params=params,
        tasks=score_corpus(root, params, measurements),
        provenance=Provenance(
            source_sha=sha,
            source_dirty=dirty,
            corpus_root=str(root),
            runs_root=str(runs_root) if runs_root else "",
            gate_thresholds=dict(GATE_THRESHOLDS),
            trace_coverage=trace_coverage(measurements),
            traces_digest=_traces_digest(measurements),
        ),
    )



def _sensitivity(root: Path, measurements: dict[str, dict[str, Any]]) -> int:
    """Print the pool size under both readings of the one ambiguous knob.

    The acceptance criterion asks for >=50 tasks; whether that is met depends
    on a choice the bead leaves open. Printing the band is the honest answer.

    Gate C is knob-independent, so its traces are measured once by the caller and
    reused across readings rather than re-read per combination.
    """
    print("Candidate-pool sensitivity (no gate has FAILED; C is partial, E/F pending):\n")
    print(f"  {'module_granularity':<20} {'pool':>5}")
    for gran in ("top_segment", "parent_dir"):
        art = build_artifact(root, SelectorParams(gran), measurements=measurements)
        n = len(art.candidate_task_ids())
        flag = "" if n >= 50 else "   <-- BELOW the >=50 acceptance criterion"
        print(f"  {gran:<20} {n:>5}{flag}")
    print("\n'Module' is undefined for single-repo tasks, so neither reading is")
    print("'the' answer. Gate C dominates the pool either way.")
    return 0


def _resolve_runs_root(runs_root: Path, root: Path) -> dict[str, dict[str, Any]]:
    """Measure Gate C, or refuse to run.

    Both failure modes here USED to be silent, and both produced a 46-candidate
    artifact indistinguishable from the correct 35-candidate one:

      * a runs_root that does not exist -> a stderr WARNING, then Gate C PENDING
        for every task and the pool inflates by the 49 tasks Gate C would fail;
      * a runs_root that exists but is EMPTY (or points at the wrong tree) ->
        not even a warning, because the directory check passed.

    Provenance cannot fix this: provenance is inert, and by the time anyone reads
    it the number is already published. So it is a precondition, and it exits
    non-zero without writing anything.
    """
    if not runs_root.is_dir():
        raise SystemExit(
            f"stratum_selector: --runs-root {runs_root} does not exist.\n"
            f"  Gate C reads the baseline arm's traces from there. Without them "
            f"every task scores PENDING, no task fails Gate C, and the candidate "
            f"pool silently inflates. Refusing to score.")

    measurements = measure_corpus_ttfr(root, runs_root)
    cov = trace_coverage(measurements)
    if resolved_traces(cov) == 0:
        raise SystemExit(
            f"stratum_selector: --runs-root {runs_root} exists but holds no "
            f"baseline traces ({cov}).\n"
            f"  Expected <runs-root>/<task-id>/baseline/agent_trace.jsonl. Gate C "
            f"would be PENDING for all {len(measurements)} tasks and the pool "
            f"would inflate. Refusing to score.")
    return measurements


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", default="benchmarks", help="corpus root")
    p.add_argument("--out", help="write the stratum artifact here")
    p.add_argument("--module-granularity", choices=("parent_dir", "top_segment"),
                   default="parent_dir")
    # Required, and with no default: the default used to be a hardcoded absolute
    # path to one machine's checkout, so the same command on any other machine
    # scored a different pool and said nothing about it.
    p.add_argument("--runs-root", required=True,
                   help="locked run set; Gate C reads baseline traces from here")
    p.add_argument("--sensitivity", action="store_true",
                   help="print pool size under both knob readings and exit")
    args = p.parse_args(argv)

    root = Path(args.corpus)
    runs_root = Path(args.runs_root)
    measurements = _resolve_runs_root(runs_root, root)

    if args.sensitivity:
        return _sensitivity(root, measurements)

    params = SelectorParams(module_granularity=args.module_granularity)
    art = build_artifact(root, params, measurements=measurements, runs_root=runs_root)
    s = art.summary()

    print(f"Stratum {art.version} [{'FINAL' if art.is_final else 'CANDIDATE'}]")
    print(f"  params: {params.to_dict()}")
    print(f"  traces: {art.provenance.trace_coverage} "
          f"digest={art.provenance.traces_digest[:12] or '-'}")
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
