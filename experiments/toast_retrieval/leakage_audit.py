"""Measure how much of each task's ground truth its own prompt discloses.

A retrieval benchmark where the prompt names the answer's files measures
string-matching, not search. For every candidate task this reports what
fraction of required files appear in instruction.md -- as a full path, as a
basename, or as a stem (`payment_processor` for payment_processor.py). The
stem tier is the interesting one: a prompt that never types "http.c" but asks
about "the http helper" still hands a lexical policy its answer.

The numbers feed two decisions: which existing tasks qualify for the
hard-retrieval slice (low disclosure), and which need a clue-stripped paired
variant authored before they can be used.

Usage:
    python3 leakage_audit.py                # table over candidate_tasks.json
    python3 leakage_audit.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_toast import _required_files  # noqa: E402  (same extractor as scoring)


sys.path.insert(0, str(REPO_ROOT / "scripts"))
from audit_disclosure import disclosure  # noqa: E402  (canonical implementation)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="candidate_tasks.json", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    rows = []
    for candidate in json.loads(args.candidates.read_text()):
        task_dir = REPO_ROOT / candidate["path"]
        instruction = (task_dir / "instruction.md").read_text()
        report = disclosure(instruction, _required_files(task_dir))
        report["task_id"] = candidate["task_id"]
        rows.append(report)

    rows.sort(key=lambda r: r["disclosed_fraction"], reverse=True)
    print(f"{'task':44}{'req':>4}{'path':>6}{'base':>6}{'stem':>6}{'hidden':>7}{'disclosed':>10}")
    for r in rows:
        t = r["tiers"]
        print(
            f"{r['task_id']:44}{r['n_required']:>4}{t['full_path']:>6}"
            f"{t['basename']:>6}{t['stem']:>6}{t['undisclosed']:>7}"
            f"{r['disclosed_fraction']:>10}"
        )
    total_required = sum(r["n_required"] for r in rows)
    total_hidden = sum(r["tiers"]["undisclosed"] for r in rows)
    fully_hidden_tasks = sum(1 for r in rows if r["disclosed_fraction"] == 0.0)
    print(
        f"\ncorpus: {total_required - total_hidden}/{total_required} required files "
        f"disclosed by their own prompt; {fully_hidden_tasks}/{len(rows)} tasks "
        "disclose nothing"
    )

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
