"""Re-score stored sweep reports with the symmetric path matcher, offline.

Every sweep before this fix scored through ``compute_ir_scores`` directly, so
a correct retrieval whose repo's top-level directory is not on
``normalize_path``'s 22-name allowlist was counted as a miss. See
``aligned_scoring`` for the mechanism.

Reports already store ``retrieved_paths`` and ``required_files``, so this
recomputes with no API calls and no re-run. Files are rewritten in place with
the corrected ``ir_scores``; the pre-fix values are preserved under
``ir_scores_unaligned`` so the correction stays auditable.

Usage:
    python3 rescore_aligned.py --dry-run reports_hard_toast
    python3 rescore_aligned.py reports_hard_toast reports_hard_code_finder
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from aligned_scoring import SCORING_CONTRACT, align, score


def rescore_report(report: dict) -> tuple[dict, float, float]:
    """Return (updated report, old file_recall, new file_recall)."""
    required = report["required_files"]
    scores = score(
        report["retrieved_paths"], required, report["task_id"],
        report.get("policy", "toast1"),
    ).to_dict()
    old = report["ir_scores"]["file_recall"]
    report.setdefault("ir_scores_unaligned", report["ir_scores"])
    report["ir_scores"] = scores
    report["retrieved_paths_aligned"] = align(report["retrieved_paths"], required)
    report["scoring"] = SCORING_CONTRACT
    if "retrieved_paths_union" in report:
        union = report["retrieved_paths_union"]
        report.setdefault("ir_scores_union_unaligned", report["ir_scores_union"])
        report["ir_scores_union"] = score(
            union, required, report["task_id"],
            f"{report.get('policy', 'toast1')}_union",
        ).to_dict()
    return report, old, scores["file_recall"]


def rescore_rollup(rollup: Path, reports: dict[str, dict]) -> bool:
    """Refresh a sweep roll-up (`-o sweep.json`) from the rescored reports.

    The roll-up embeds a copy of every report, so leaving it alone would strand
    pre-fix scores in a file that reads like a sweep record.
    """
    if not rollup.is_file():
        return False
    payload = json.loads(rollup.read_text())
    if not isinstance(payload, dict) or "reports" not in payload:
        return False
    payload["reports"] = [reports.get(r["task_id"], r) for r in payload["reports"]]
    rollup.write_text(json.dumps(payload, indent=2) + "\n")
    return True


def rescore_dir(directory: Path, write: bool, rollup: Path | None = None) -> dict:
    olds, news, improved = [], [], 0
    rescored: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        report = json.loads(path.read_text())
        if "ir_scores" not in report:
            continue
        report, old, new = rescore_report(report)
        olds.append(old)
        news.append(new)
        if new > old:
            improved += 1
        rescored[report["task_id"]] = report
        if write:
            path.write_text(json.dumps(report, indent=2) + "\n")
    if write and rollup is not None:
        rescore_rollup(rollup, rescored)
    if not olds:
        return {"dir": str(directory), "n": 0}
    return {
        "dir": str(directory),
        "n": len(olds),
        "file_recall_before": round(statistics.mean(olds), 3),
        "file_recall_after": round(statistics.mean(news), 3),
        "delta": round(statistics.mean(news) - statistics.mean(olds), 3),
        "tasks_improved": improved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dirs", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="report, do not write")
    parser.add_argument(
        "--rollup", type=Path, default=None,
        help="sweep roll-up (-o sweep.json) to refresh; only valid with one dir",
    )
    args = parser.parse_args()
    if args.rollup and len(args.dirs) != 1:
        parser.error("--rollup takes exactly one report directory")

    rows = [
        rescore_dir(d, write=not args.dry_run, rollup=args.rollup)
        for d in args.dirs
        if d.is_dir()
    ]
    print(f"{'dir':34}{'n':>4}{'before':>9}{'after':>9}{'delta':>9}{'improved':>10}")
    for row in rows:
        if not row.get("n"):
            continue
        print(
            f"{row['dir']:34}{row['n']:>4}{row['file_recall_before']:>9}"
            f"{row['file_recall_after']:>9}{row['delta']:>+9}{row['tasks_improved']:>10}"
        )
    if args.dry_run:
        print("\n(dry run, no files written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
