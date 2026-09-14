"""Curate a Toast-1-vs-Code-Finder task set from EnterpriseBench's own corpus.

Original plan was to author new gold-evidence tasks against sourcegraph/
sourcegraph; that repo isn't indexed on demo.sourcegraph.com (only smaller
sourcegraph-owned tool repos and sg-evals/ mirrors are — see README.md). We
pivoted to reusing EnterpriseBench tasks that already carry real
`required_files` ground truth (lib/eb_metrics/retrieval_extraction.py) AND
whose repos are indexed sg-evals/ mirrors reachable from the same
demo.sourcegraph.com instance mcp_only/mcp_code_finder already use — no new
task authoring, no new mirroring, no confound from a different corpus.

This script:
1. Finds every non-archived task with a `required_files`-bearing
   ground_truth.json.
2. Derives each repo's sg-evals mirror name and confirms it is actually
   cloned on the instance (a live GraphQL check, not a config assumption).
3. Buckets by repo count (single/dual/tri+) as a proxy for search
   complexity, per the stratification the original proposal wanted
   (localized vs multi-file vs cross-repo).
4. Writes a candidate list as JSON.

Usage:
    SOURCEGRAPH_ACCESS_TOKEN=... python3 select_tasks.py --limit 20 -o candidate_tasks.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "infra"))
from mirror_naming import derive_mirror_name  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
# Canonical tier logic + the same required-files extractor scoring uses
# (EnterpriseBench-yso6z: disclosure is a required selection criterion --
# repo-count strata alone picked a 51%-disclosed corpus).
from audit_disclosure import disclosure, required_files_from_ground_truth  # noqa: E402

from graphql import GraphQLError, SourcegraphConfig, graphql  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"

_INDEX_QUERY = """
query Cloned($name: String!) {
  repository(name: $name) { name mirrorInfo { cloned } }
}
"""


def _find_candidate_tasks(include_customer_escalation: bool = False) -> list[Path]:
    """Every non-archived task dir with real required_files ground truth.

    customer_escalation is excluded by default (its GT schema is not
    file-evidence-shaped in general -- see recon notes) but can be opted in:
    the shared extractor does resolve required files for its calibration-*
    tasks, and several of them sit in the low-disclosure pool."""
    tasks = []
    for gt_path in BENCHMARKS_DIR.glob("**/ground_truth.json"):
        if "_archived" in gt_path.parts:
            continue
        if not include_customer_escalation and "customer_escalation" in gt_path.parts:
            continue
        try:
            payload = json.loads(gt_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        block = payload.get("ground_truth", payload)
        if isinstance(block, dict) and block.get("required_files"):
            tasks.append(gt_path.parent)
    return sorted(tasks)


def _task_repos(task_dir: Path) -> list[dict]:
    toml_path = task_dir / "task.toml"
    if not toml_path.is_file():
        return []
    data = tomllib.loads(toml_path.read_text())
    return data.get("repos", [])


def _mirror_name(repo: dict) -> str | None:
    url = repo.get("url")
    rev = repo.get("rev")
    if not url or not rev:
        return None
    return "github.com/" + derive_mirror_name(url, rev)


def _check_cloned(config: SourcegraphConfig, mirror_name: str) -> bool:
    try:
        data = graphql(config, _INDEX_QUERY, {"name": mirror_name})
    except GraphQLError:
        return False
    repo = data.get("repository")
    return bool(repo and (repo.get("mirrorInfo") or {}).get("cloned"))


def build_candidates(
    config: SourcegraphConfig,
    max_workers: int = 8,
    include_customer_escalation: bool = False,
) -> list[dict]:
    task_dirs = _find_candidate_tasks(include_customer_escalation)
    mirror_check_cache: dict[str, bool] = {}

    def check(name: str) -> tuple[str, bool]:
        return name, _check_cloned(config, name)

    all_mirror_names: set[str] = set()
    per_task_mirrors: dict[Path, list[str]] = {}
    for task_dir in task_dirs:
        repos = _task_repos(task_dir)
        names = [n for n in (_mirror_name(r) for r in repos) if n]
        per_task_mirrors[task_dir] = names
        all_mirror_names.update(names)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for name, cloned in pool.map(check, sorted(all_mirror_names)):
            mirror_check_cache[name] = cloned

    candidates = []
    for task_dir in task_dirs:
        names = per_task_mirrors[task_dir]
        if not names or not all(mirror_check_cache.get(n) for n in names):
            continue
        required = required_files_from_ground_truth(task_dir / "ground_truth.json")
        report = disclosure((task_dir / "instruction.md").read_text(), required)
        candidates.append(
            {
                "task_id": task_dir.name,
                "suite": task_dir.parent.name,
                "path": str(task_dir.relative_to(REPO_ROOT)),
                "store_identifiers": names,
                "repo_count": len(names),
                "required_file_count": len(required),
                "disclosed_fraction": report["disclosed_fraction"],
            }
        )
    return candidates


def stratify(candidates: list[dict]) -> dict[str, list[dict]]:
    strata = {"single_repo": [], "dual_repo": [], "multi_repo": []}
    for c in candidates:
        if c["repo_count"] <= 1:
            strata["single_repo"].append(c)
        elif c["repo_count"] == 2:
            strata["dual_repo"].append(c)
        else:
            strata["multi_repo"].append(c)
    return strata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="total tasks to select")
    parser.add_argument(
        "--max-disclosure",
        type=float,
        default=1 / 3,
        help="drop tasks whose prompt discloses more than this fraction of "
        "required files (full-path/basename/stem tiers; 1.0 disables). "
        "Default 1/3 -- the hard-slice threshold.",
    )
    parser.add_argument(
        "--all-suites",
        action="store_true",
        help="include customer_escalation tasks (excluded by default)",
    )
    parser.add_argument("-o", "--output", default="candidate_tasks.json")
    args = parser.parse_args()

    config = SourcegraphConfig.from_env()
    if not config.token:
        print("SOURCEGRAPH_ACCESS_TOKEN is not set.", file=sys.stderr)
        return 2

    candidates = build_candidates(
        config, include_customer_escalation=args.all_suites
    )
    dropped = [c for c in candidates if c["disclosed_fraction"] > args.max_disclosure]
    candidates = [c for c in candidates if c["disclosed_fraction"] <= args.max_disclosure]
    print(
        f"disclosure filter (<= {args.max_disclosure:.3f}): kept "
        f"{len(candidates)}, dropped {len(dropped)}",
        file=sys.stderr,
    )
    strata = stratify(candidates)
    for name, items in strata.items():
        print(f"{name}: {len(items)} available", file=sys.stderr)

    per_stratum = max(1, args.limit // 3)
    selected = (
        strata["single_repo"][:per_stratum]
        + strata["dual_repo"][:per_stratum]
        + strata["multi_repo"][: args.limit - 2 * per_stratum]
    )
    Path(args.output).write_text(json.dumps(selected, indent=2) + "\n")
    print(f"Wrote {len(selected)} candidates to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
