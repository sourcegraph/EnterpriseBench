#!/usr/bin/env python3
"""Ecosystem diversity report for EnterpriseBench multi-repo tasks.

Shows repo_set_id distribution across all multi-repo tasks and flags
concentration risks (any single ecosystem exceeding a threshold).

Usage:
    python3 scripts/validation/ecosystem_diversity_report.py
    python3 scripts/validation/ecosystem_diversity_report.py --json
    python3 scripts/validation/ecosystem_diversity_report.py --threshold 25
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent.parent / "benchmarks"
MULTI_REPO_STRATA = {"dual_repo", "tri_repo", "multi_repo"}


def collect_tasks() -> list[dict]:
    """Collect task metadata from all active benchmarks."""
    tasks = []
    for toml_path in sorted(BENCHMARKS_DIR.rglob("task.toml")):
        # Skip archived tasks
        if "_archived" in str(toml_path):
            continue
        try:
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            continue

        stratum = data.get("difficulty_stratum", "")
        repo_set_id = data.get("repo_set_id", "unknown")
        task_section = data.get("task", {})
        repos = data.get("repos", [])

        tasks.append({
            "path": str(toml_path.relative_to(BENCHMARKS_DIR)),
            "id": task_section.get("id", ""),
            "difficulty_stratum": stratum,
            "repo_set_id": repo_set_id,
            "task_type": task_section.get("task_type", ""),
            "difficulty": task_section.get("difficulty", ""),
            "num_repos": len(repos),
            "languages": data.get("metadata", {}).get("languages", []),
        })

    return tasks


def is_k8s_ecosystem(repo_set_id: str) -> bool:
    """Determine if a repo_set_id belongs to the broader K8s ecosystem.

    Includes K8s core, container runtimes, gRPC (used by K8s/etcd), protobuf,
    GitOps (ArgoCD/Flux), and Docker — matching the concentration analysis in
    the diversification bead.
    """
    k8s_ids = {
        "kubernetes-ecosystem",
        "container-runtime-ecosystem",
        "docker-ecosystem",
        "grpc-ecosystem",
        "protobuf-ecosystem",
        "gitops-ecosystem",
    }
    return repo_set_id in k8s_ids


def generate_report(tasks: list[dict], threshold: float = 25.0) -> dict:
    """Generate the ecosystem diversity report."""
    multi_repo_tasks = [t for t in tasks if t["difficulty_stratum"] in MULTI_REPO_STRATA]
    total_multi = len(multi_repo_tasks)

    # repo_set_id distribution
    eco_counter = Counter(t["repo_set_id"] for t in multi_repo_tasks)

    # K8s concentration
    k8s_tasks = [t for t in multi_repo_tasks if is_k8s_ecosystem(t["repo_set_id"])]
    k8s_count = len(k8s_tasks)
    k8s_pct = (k8s_count / total_multi * 100) if total_multi else 0

    # Per-ecosystem breakdown with difficulty
    eco_details = {}
    for t in multi_repo_tasks:
        eco = t["repo_set_id"]
        eco_details.setdefault(eco, {"count": 0, "difficulties": [], "task_types": []})
        eco_details[eco]["count"] += 1
        eco_details[eco]["difficulties"].append(t["difficulty"])
        eco_details[eco]["task_types"].append(t["task_type"])

    # Violations: any ecosystem exceeding threshold
    violations = []
    for eco, count in eco_counter.most_common():
        pct = count / total_multi * 100
        if pct > threshold:
            violations.append({
                "ecosystem": eco,
                "count": count,
                "percentage": round(pct, 1),
                "threshold": threshold,
            })

    return {
        "total_tasks": len(tasks),
        "total_multi_repo": total_multi,
        "k8s_count": k8s_count,
        "k8s_percentage": round(k8s_pct, 1),
        "k8s_below_threshold": k8s_pct < threshold,
        "ecosystem_distribution": dict(eco_counter.most_common()),
        "ecosystem_details": eco_details,
        "violations": violations,
        "threshold": threshold,
        "unique_ecosystems": len(eco_counter),
    }


def print_report(report: dict) -> None:
    """Pretty-print the diversity report."""
    print(f"\n{'='*70}")
    print("ECOSYSTEM DIVERSITY REPORT")
    print(f"{'='*70}")
    print(f"Total tasks:           {report['total_tasks']}")
    print(f"Multi-repo tasks:      {report['total_multi_repo']}")
    print(f"Unique ecosystems:     {report['unique_ecosystems']}")
    print(f"K8s ecosystem tasks:   {report['k8s_count']} ({report['k8s_percentage']}%)")
    print(f"K8s below {report['threshold']}%:   {'PASS' if report['k8s_below_threshold'] else 'FAIL'}")

    print(f"\n--- Distribution (threshold: {report['threshold']}%) ---")
    total = report["total_multi_repo"]
    for eco, count in sorted(report["ecosystem_distribution"].items(),
                             key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        flag = " ** OVER THRESHOLD **" if pct > report["threshold"] else ""
        k8s_tag = " [K8s]" if is_k8s_ecosystem(eco) else ""
        print(f"  {eco:40s} {count:3d} ({pct:5.1f}%){k8s_tag}{flag}")

    if report["violations"]:
        print(f"\n--- VIOLATIONS ---")
        for v in report["violations"]:
            print(f"  {v['ecosystem']}: {v['count']} tasks ({v['percentage']}%) > {v['threshold']}%")
    else:
        print(f"\n  No ecosystems exceed {report['threshold']}% threshold.")

    # Difficulty coverage per ecosystem
    print(f"\n--- Difficulty Coverage (top ecosystems) ---")
    for eco, details in sorted(report["ecosystem_details"].items(),
                                key=lambda x: -x[1]["count"])[:10]:
        diffs = Counter(details["difficulties"])
        diff_str = ", ".join(f"{d}:{c}" for d, c in sorted(diffs.items()))
        print(f"  {eco:40s} [{diff_str}]")

    print(f"{'='*70}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ecosystem diversity report")
    parser.add_argument("--threshold", type=float, default=25.0,
                        help="Max percentage for any single ecosystem (default: 25)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    tasks = collect_tasks()
    report = generate_report(tasks, threshold=args.threshold)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    return 0 if report["k8s_below_threshold"] and not report["violations"] else 1


if __name__ == "__main__":
    sys.exit(main())
