"""Audit prompt->ground-truth lexical disclosure, tiered below exact paths.

Why this exists (EnterpriseBench-vkhav): audit_instructions.py's GT-leakage
check matches only exact full paths (``if path in instruction``), and that
script is wired into neither the Makefile nor CI. The toast_retrieval
experiment found 0 full-path but 33/65 basename/stem disclosures across its
20-task sample -- a prompt that names ``realms.py``, or asks about "the
avatar helper", hands a lexical search policy its answer even though the
exact-path check reads clean. For agentic-execution tasks that is often
intentional scenario setup (the measured skill is the fix or the analysis,
not the search); for any task scored on retrieval (file_recall over
``required_files``) it caps measurable difficulty. So this audit REPORTS
tiers for every task, and the CI gate ENFORCES a threshold only for tasks
enrolled in a slice manifest (``configs/retrieval_slice.json``).

Tiers per required file, matched case-insensitively against instruction.md:

* ``full_path``   -- the repo-relative path appears verbatim
* ``basename``    -- the final path component appears ("realms.py")
* ``stem``        -- the extensionless basename (>=4 chars) appears at a
  word boundary ("avatar" for avatar.py); the length floor keeps a
  two-letter stem like "io" from matching inside ordinary prose
* ``undisclosed`` -- none of the above

Exit codes follow the repo convention: 0 = clean, 1 = a real violation
(an enrolled task over threshold, or an enrolled task whose ground truth
cannot be read -- manifest drift must not read as a silent pass), 2 = the
audit itself could not run.

Usage:
    PYTHONPATH=lib python3 scripts/audit_disclosure.py                     # report, all tasks
    PYTHONPATH=lib python3 scripts/audit_disclosure.py --json out.json
    PYTHONPATH=lib python3 scripts/audit_disclosure.py --gate configs/retrieval_slice.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "lib"))


def _import_eb_metrics(module_name: str):
    """Import an eb_metrics submodule without executing eb_metrics/__init__.py.

    The package init eagerly imports trace_quality_adapter -> codeprobe, an
    undeclared dependency absent from CI and most local environments. A stub
    package module with the real __path__ lets submodules (and their
    package-internal ``from eb_metrics.x import y`` lines) resolve normally.
    Same technique as experiments/toast_retrieval/run_toast.py -- the audit
    must read ground truth through the same extractor scoring uses.
    """
    if "eb_metrics" not in sys.modules:
        package = types.ModuleType("eb_metrics")
        package.__path__ = [str(PROJECT_ROOT / "lib" / "eb_metrics")]
        package.__package__ = "eb_metrics"
        sys.modules["eb_metrics"] = package
    return importlib.import_module(module_name)


required_files_from_ground_truth = _import_eb_metrics(
    "eb_metrics.retrieval_extraction"
).required_files_from_ground_truth

BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"

_MIN_STEM_LEN = 4


def disclosure(instruction: str, required: list[str]) -> dict:
    """Tiered lexical disclosure of ``required`` file paths in ``instruction``."""
    text = instruction.lower()
    tiers = {"full_path": 0, "basename": 0, "stem": 0, "undisclosed": 0}
    per_file = []
    for path in required:
        lower = path.lower()
        base = lower.rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0]
        if lower in text:
            tier = "full_path"
        elif base in text:
            tier = "basename"
        elif len(stem) >= _MIN_STEM_LEN and re.search(rf"\b{re.escape(stem)}\b", text):
            tier = "stem"
        else:
            tier = "undisclosed"
        tiers[tier] += 1
        per_file.append({"path": path, "tier": tier})
    n = len(required)
    return {
        "n_required": n,
        "tiers": tiers,
        "disclosed_fraction": round((n - tiers["undisclosed"]) / n, 3) if n else 0.0,
        "per_file": per_file,
    }


def audit_task(task_dir: Path) -> dict:
    """Disclosure report for one task dir. Raises on unreadable inputs."""
    instruction = (task_dir / "instruction.md").read_text()
    required = required_files_from_ground_truth(task_dir / "ground_truth.json")
    report = disclosure(instruction, required)
    report["task_id"] = task_dir.name
    report["suite"] = task_dir.parent.name
    return report


def audit_corpus(benchmarks_dir: Path = BENCHMARKS_DIR) -> list[dict]:
    """Every non-archived task with readable required-files ground truth."""
    rows = []
    for gt_path in sorted(benchmarks_dir.glob("**/ground_truth.json")):
        if "_archived" in gt_path.parts:
            continue
        try:
            row = audit_task(gt_path.parent)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if row["n_required"]:
            rows.append(row)
    return rows


def gate(rows: list[dict], manifest_path: Path) -> list[str]:
    """Enforce the manifest threshold; return violation messages (empty = pass).

    Manifest entries are suite-qualified ("suite/task-id") because bare task
    ids are not unique -- calibration-002 exists in three suites with
    different disclosure.
    """
    manifest = json.loads(manifest_path.read_text())
    max_fraction = manifest["max_disclosed_fraction"]
    by_key = {f"{r['suite']}/{r['task_id']}": r for r in rows}
    violations = []
    for key in manifest["tasks"]:
        row = by_key.get(key)
        if row is None:
            violations.append(
                f"{key}: enrolled in {manifest_path.name} but has no auditable "
                "required-files ground truth (manifest drift)"
            )
        elif row["disclosed_fraction"] > max_fraction:
            violations.append(
                f"{key}: disclosed_fraction {row['disclosed_fraction']} "
                f"> {max_fraction} (tiers: {row['tiers']})"
            )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="write per-task rows")
    parser.add_argument(
        "--gate",
        type=Path,
        default=None,
        help="slice manifest: {'max_disclosed_fraction': f, 'tasks': [ids]}; "
        "exit 1 if any enrolled task exceeds it",
    )
    args = parser.parse_args()

    rows = audit_corpus()
    if not rows:
        print("No auditable tasks found under benchmarks/ -- refusing to pass.", file=sys.stderr)
        return 2

    rows.sort(key=lambda r: r["disclosed_fraction"], reverse=True)
    print(f"{'task':48}{'req':>4}{'path':>6}{'base':>6}{'stem':>6}{'hidden':>7}{'disclosed':>10}")
    for r in rows:
        t = r["tiers"]
        print(
            f"{r['task_id']:48}{r['n_required']:>4}{t['full_path']:>6}"
            f"{t['basename']:>6}{t['stem']:>6}{t['undisclosed']:>7}"
            f"{r['disclosed_fraction']:>10}"
        )
    total = sum(r["n_required"] for r in rows)
    hidden = sum(r["tiers"]["undisclosed"] for r in rows)
    print(
        f"\ncorpus: {total - hidden}/{total} required files disclosed by their own "
        f"prompt across {len(rows)} tasks"
    )

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")

    if args.gate:
        violations = gate(rows, args.gate)
        if violations:
            print(f"\nDISCLOSURE GATE FAILED ({args.gate}):", file=sys.stderr)
            for v in violations:
                print(f"  {v}", file=sys.stderr)
            return 1
        enrolled = len(json.loads(args.gate.read_text())["tasks"])
        print(f"\ndisclosure gate: {enrolled} enrolled tasks all within threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
