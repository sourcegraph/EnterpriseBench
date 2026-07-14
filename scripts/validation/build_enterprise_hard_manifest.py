#!/usr/bin/env python3
"""Build the enterprise-hard authored-stratum manifest (EnterpriseBench-jn73.2.7).

The corpus could not yield >=50 enterprise-win-regime tasks by selection: of 58
tasks with a measured turns-to-first-relevant, 46 are trivially localizable and
exactly one reaches tier-1. The decision was to AUTHOR hard tasks rather than
loosen an admission rule into a quota. This script emits the manifest of what was
authored, with per-task gate evidence.

Gate vocabulary is jn73.2's (scripts/validation/stratum_selector.py, unmerged):

    computable at authoring time : A (parity), B (non-leaky), D (dispersion)
    measurements, not properties : C (hard-to-localize), E (gold run), F (verifier)

Gate C is the whole point of this stratum and it is deliberately NOT asserted
here. It is a measurement over baseline agent traces, and no baseline run exists
for these tasks yet. Every task records ``gate_c.status = "pending_measurement"``
plus the input contract the calibration bead must satisfy to score it. An
authored task is a task DESIGNED into the tier-1 class, never one measured into
it -- conflating the two is how an admission rule becomes a quota.

Gate B note: the authoritative scorer is jn73.2's leak_classifier, which is not
merged. This script computes the deterministic EXACT_PATH/DIR subset of that
classifier (does the instruction name an oracle basename, a 2-segment path
fragment, or a distinctive oracle directory in path form?) and labels the
evidence ``scorer: "authoring_subset"`` so a later full run can supersede it
rather than silently agree with it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BENCHMARKS = PROJECT_ROOT / "benchmarks"
MANIFEST = BENCHMARKS / "strata" / "enterprise_hard_authored.json"

#: Mirrors stratum_selector.GATE_D_MIN_FILES / GATE_D_MIN_MODULES.
GATE_D_MIN_FILES = 3
GATE_D_MIN_MODULES = 2
#: Mirrors stratum_selector.GATE_C_TIER1_MIN — the class these tasks are designed for.
GATE_C_TIER1_MIN_TURNS = 20

#: Mirrors leak_classifier._GENERIC_DIRS: too common to localize anything.
GENERIC_DIRS = frozenset(
    {
        "src", "lib", "pkg", "internal", "cmd", "app", "core", "common", "util",
        "utils", "main", "java", "python", "go", "rust", "ts", "js", "test",
        "tests", "com", "org", "net", "io", "api", "server", "client",
    }
)

#: Validators whose validate() takes no require_grounded_citations kwarg.
#: runner.validate_artifacts() hard-fails a required artifact whose validator
#: cannot honor the flag when the task sets it, so the two are mutually exclusive.
NON_GROUNDING_ARTIFACTS = frozenset(
    {"code_patch", "config", "runbook", "call_graph", "topological_order",
     "reproduction_script"}
)


def _strip_repo_slug(path: str) -> str:
    p = path.replace("\\", "/")
    p = re.sub(r"^[^/]+--[0-9a-f]{6,}/", "", p)
    return p.split("::")[-1].strip("/")


def _names(instruction: str, word: str) -> bool:
    """Word-boundary match that is not part of a longer path/identifier."""
    return (
        re.search(
            r"(?<![\w/.\-])" + re.escape(word.lower()) + r"(?![\w])",
            instruction.lower(),
        )
        is not None
    )


def _modules(paths: list[str]) -> set[str]:
    """Parent-directory granularity, matching stratum_selector._modules."""
    out: set[str] = set()
    for p in paths:
        segs = [s for s in _strip_repo_slug(p).split("/") if s]
        out.add("/".join(segs[:-1]) or ".")
    return out


@dataclass(frozen=True)
class Gate:
    gate: str
    status: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status,
            "reason": self.reason,
            "evidence": self.evidence,
        }


def score_gate_a(declared: set[str], oracle_repos: set[str]) -> Gate:
    """PARITY: the baseline arm clones every repo an oracle file lives in.

    An oracle file in a repo the baseline never clones means the baseline is
    solving blind — the empty-baseline confound.
    """
    missing = oracle_repos - declared
    if not declared:
        return Gate("A", "fail", "task declares no repos", {"declared_repos": []})
    if missing:
        return Gate(
            "A",
            "fail",
            f"oracle files live in repo(s) never cloned: {', '.join(sorted(missing))}",
            {"declared_repos": sorted(declared), "missing_repos": sorted(missing)},
        )
    return Gate(
        "A",
        "pass",
        "baseline clones every repo the oracle files live in",
        {"declared_repos": sorted(declared), "oracle_repos": sorted(oracle_repos)},
    )


def score_gate_b(instruction: str, oracle_files: list[str]) -> Gate:
    """NON-LEAKY: the instruction must not localize the oracle for free.

    Deterministic subset of leak_classifier.classify: EXACT_PATH (basename or
    two consecutive path segments named verbatim) and DIR (a distinctive oracle
    directory named in path form). The SYMBOL bucket needs an oracle symbol list,
    which EB's ground_truth schema does not carry, so it cannot fire here either.
    """
    ins = instruction.lower()
    for f in oracle_files:
        segs = [s for s in _strip_repo_slug(f).split("/") if s]
        basename = segs[-1] if segs else ""
        if basename and _names(instruction, basename):
            return Gate(
                "B", "fail", f"instruction names oracle basename {basename!r}",
                {"bucket": "exact_path", "token": basename, "source_path": f,
                 "scorer": "authoring_subset"},
            )
        for i in range(len(segs) - 1):
            frag = "/".join(segs[i : i + 2]).lower()
            if frag and frag in ins:
                return Gate(
                    "B", "fail", f"instruction names path fragment {frag!r}",
                    {"bucket": "exact_path", "token": frag, "source_path": f,
                     "scorer": "authoring_subset"},
                )
    for f in oracle_files:
        segs = [s for s in _strip_repo_slug(f).split("/") if s]
        for d in segs[:-1]:
            if d.lower() not in GENERIC_DIRS and (d.lower() + "/") in ins:
                return Gate(
                    "B", "fail", f"instruction names oracle directory {d!r} in path form",
                    {"bucket": "dir", "token": d, "source_path": f,
                     "scorer": "authoring_subset"},
                )

    # No locational leak. Dispersed targets make it grep_hard rather than merely
    # behavioral — the preferred admit bucket.
    repos = {re.split(r"[/:]", f, 1)[0] for f in oracle_files}
    dispersed = len(oracle_files) >= 3 or len(repos) >= 2
    bucket = "grep_hard" if dispersed else "behavioral"
    return Gate(
        "B", "pass", f"no locational leak; bucket={bucket}",
        {"bucket": bucket, "token": "", "scorer": "authoring_subset"},
    )


def score_gate_d(
    declared: set[str], oracle_files: list[str], crnt_positive: bool, stratum: str
) -> Gate:
    """ENTERPRISE CHARACTER: CRNT-positive and a dispersed oracle.

    A one-file, one-directory task is not an enterprise context-gathering problem
    however hard the individual file is.
    """
    mods = _modules(oracle_files)
    ev = {
        "n_repos": len(declared),
        "crnt_positive": crnt_positive,
        "n_oracle_files": len(oracle_files),
        "n_modules": len(mods),
        "modules": sorted(mods),
        "difficulty_stratum": stratum,
        "module_granularity": "parent_dir",
    }
    if stratum == "calibration":
        return Gate("D", "fail", "calibration tasks are small-repo by construction", ev)
    if not crnt_positive:
        return Gate("D", "fail", "CRNT-negative: a declared repo has no required_files", ev)
    if len(oracle_files) < GATE_D_MIN_FILES:
        return Gate(
            "D", "fail",
            f"oracle has {len(oracle_files)} files, need >={GATE_D_MIN_FILES}", ev,
        )
    if len(mods) < GATE_D_MIN_MODULES:
        return Gate(
            "D", "fail",
            f"oracle spans {len(mods)} module(s), need >={GATE_D_MIN_MODULES}", ev,
        )
    return Gate("D", "pass", "CRNT-positive with a dispersed oracle", ev)


def gate_c_pending(task_id: str) -> Gate:
    """Gate C is a MEASUREMENT. It is never 'pass' in an authored manifest.

    The input contract below is what the downstream calibration bead needs in
    order to score it; recording it here is what makes 'pending' actionable
    rather than an excuse.
    """
    return Gate(
        "C",
        "pending_measurement",
        "hard-to-localize is measured from baseline traces; no baseline run exists "
        "for this task yet",
        {
            "designed_for": "tier_1",
            "tier1_min_turns": GATE_C_TIER1_MIN_TURNS,
            "input_contract": {
                "needs": "baseline-arm agent_trace.jsonl for this task_id",
                "scorer": "compute_ttfr (jn73.1) over the baseline arm",
                "admits_if": f"turns_to_first_relevant >= {GATE_C_TIER1_MIN_TURNS}",
            },
            "task_id": task_id,
        },
    )


def crnt_positive(config: dict[str, Any]) -> bool:
    """Every declared repo is covered by >=1 ground_truth.required_files entry.

    Mirrors scripts/validation/crnt_validator.evaluate_crnt.
    """
    repo_paths = {r.get("path", "") for r in config.get("repos", [])}
    if len(repo_paths) < 2:
        return False
    covered = {
        rf.get("repo", "")
        for rf in config.get("ground_truth", {}).get("required_files", [])
    }
    return all(rp in covered for rp in repo_paths)


def build_entry(task_dir: Path) -> dict[str, Any]:
    config = tomllib.loads((task_dir / "task.toml").read_text())
    instruction = (task_dir / "instruction.md").read_text()

    task = config.get("task", {})
    task_id = task.get("id", task_dir.name)
    stratum = config.get("difficulty_stratum", "")
    gt = config.get("ground_truth", {})

    required = gt.get("required_files", [])
    sufficient = gt.get("sufficient_files", [])
    oracle_files = [f["path"] for f in required + sufficient if f.get("path")]
    oracle_repos = {f.get("repo", "") for f in required if f.get("repo")}
    declared = {r.get("path", "") for r in config.get("repos", [])}

    artifacts = config.get("artifacts", {}).get("required", [])
    grounded = bool(gt.get("require_grounded_citations", False))
    conflict = grounded and bool(NON_GROUNDING_ARTIFACTS & set(artifacts))

    is_crnt = crnt_positive(config)
    gates = [
        score_gate_a(declared, oracle_repos),
        score_gate_b(instruction, oracle_files),
        gate_c_pending(task_id),
        score_gate_d(declared, oracle_files, is_crnt, stratum),
    ]

    return {
        "task_id": task_id,
        "suite": task.get("suite", ""),
        "task_type": task.get("task_type", ""),
        "difficulty": task.get("difficulty", ""),
        "difficulty_stratum": stratum,
        "path": str(task_dir.relative_to(PROJECT_ROOT)),
        "repos": [
            {"url": r.get("url", ""), "rev": r.get("rev", ""), "path": r.get("path", "")}
            for r in config.get("repos", [])
        ],
        "artifacts_required": artifacts,
        "requires_code_patch": "code_patch" in artifacts,
        "require_grounded_citations": grounded,
        "grounding_artifact_conflict": conflict,
        "crnt_positive": is_crnt,
        "n_required_files": len(required),
        "n_oracle_files": len(oracle_files),
        "required_files": [
            {"path": f.get("path", ""), "repo": f.get("repo", "")} for f in required
        ],
        "gates": {g.gate: g.to_dict() for g in gates},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "task_dirs",
        nargs="+",
        help="task directories authored into the enterprise-hard stratum",
    )
    ap.add_argument("--out", type=Path, default=MANIFEST)
    args = ap.parse_args()

    entries = []
    for raw in sorted(args.task_dirs):
        d = Path(raw).resolve()
        if not (d / "task.toml").is_file():
            print(f"ERROR: {d} has no task.toml", file=sys.stderr)
            return 1
        entries.append(build_entry(d))

    code_patch_n = sum(1 for e in entries if e["requires_code_patch"])
    computable = ("A", "B", "D")
    clean = [
        e
        for e in entries
        if all(e["gates"][g]["status"] == "pass" for g in computable)
        and not e["grounding_artifact_conflict"]
    ]

    manifest = {
        "schema_version": "1",
        "stratum": "enterprise_hard_authored",
        "bead": "EnterpriseBench-jn73.2.7",
        "description": (
            "Tasks AUTHORED into the tier-1 hard-to-localize class because the "
            "existing corpus could not reach >=50 enterprise-win-regime tasks by "
            "selection (36 candidates; 46/58 measured tasks are trivially "
            "localizable). Gates A/B/D are computed here. Gate C is a MEASUREMENT "
            "and is pending for every task -- designed-tier-1 is not measured-tier-1."
        ),
        "gate_vocabulary": {
            "computable": list(computable),
            "measurement": ["C", "E", "F"],
            "source": "scripts/validation/stratum_selector.py (jn73.2, unmerged)",
        },
        "counts": {
            "authored": len(entries),
            "passing_computable_gates": len(clean),
            "requiring_code_patch": code_patch_n,
        },
        "tasks": entries,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")

    print(f"wrote {args.out.relative_to(PROJECT_ROOT)}")
    print(f"  authored                 : {len(entries)}")
    print(f"  passing computable gates : {len(clean)}/{len(entries)}")
    print(f"  requiring code_patch     : {code_patch_n}")
    for e in entries:
        bad = [g for g in computable if e["gates"][g]["status"] != "pass"]
        flag = f"  FAILS {bad}" if bad else ""
        if e["grounding_artifact_conflict"]:
            flag += "  GROUNDING/ARTIFACT CONFLICT"
        cp = "code_patch" if e["requires_code_patch"] else "answer-only"
        print(f"  - {e['task_id']:<48} {cp:<12}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
