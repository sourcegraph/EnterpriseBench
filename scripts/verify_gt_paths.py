#!/usr/bin/env python3
"""Verify that ground_truth.json file paths exist in their real repos at pinned revisions."""

import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.tasks import find_task_dirs

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = PROJECT_ROOT / "benchmarks"

# Rate limit: GitHub API allows 5000/hr for authenticated users
API_CALLS = 0
MAX_CALLS_PER_BATCH = 50
SLEEP_BETWEEN_BATCHES = 2


def find_tasks() -> list[tuple[Path, Path]]:
    """Find all task directories with both task.toml and ground_truth.json."""
    results = []
    for task_dir in find_task_dirs(BENCHMARKS):
        toml_path = task_dir / "task.toml"
        gt_path = task_dir / "ground_truth.json"
        if gt_path.exists():
            results.append((toml_path, gt_path))
    return results


def parse_repos_from_toml(toml_path: Path) -> dict:
    """Parse repos from task.toml, return {local_name: (owner/repo, rev)}."""
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    repos = {}
    for repo in data.get("repos", []):
        url = repo.get("url", "")
        rev = repo.get("rev", "")
        local_path = repo.get("path", "")

        # Normalize URL to owner/repo
        owner_repo = url.replace("https://github.com/", "").replace("github.com/", "").rstrip("/")

        # The local name is either the 'path' field or the last component of URL
        local_name = local_path or owner_repo.split("/")[-1]
        repos[local_name] = (owner_repo, rev)

    return repos


def extract_file_paths(gt: dict, task_name: str) -> list[dict]:
    """Extract all file paths from ground truth, returning [{path, repo_hint, source_key}]."""
    paths = []

    # 1. required_files - can be list of dicts with .path/.repo or list of strings
    for item in gt.get("required_files", []):
        if isinstance(item, dict):
            paths.append({
                "path": item.get("path", ""),
                "repo_hint": item.get("repo", ""),
                "source_key": "required_files",
            })
        elif isinstance(item, str):
            paths.append({"path": item, "repo_hint": "", "source_key": "required_files"})

    # 2. sufficient_files - same structure
    for item in gt.get("sufficient_files", []):
        if isinstance(item, dict):
            paths.append({
                "path": item.get("path", ""),
                "repo_hint": item.get("repo", ""),
                "source_key": "sufficient_files",
            })
        elif isinstance(item, str):
            paths.append({"path": item, "repo_hint": "", "source_key": "sufficient_files"})

    # 3. producer_changed_files (api-contract tasks) - list of strings
    for item in gt.get("producer_changed_files", []):
        if isinstance(item, str):
            paths.append({"path": item, "repo_hint": gt.get("producer_repo", ""), "source_key": "producer_changed_files"})

    # 4. producer_changed_files_envoy (api-contract-006 variant)
    for item in gt.get("producer_changed_files_envoy", []):
        if isinstance(item, str):
            paths.append({"path": item, "repo_hint": gt.get("producer_repo", ""), "source_key": "producer_changed_files_envoy"})

    # 5. consumer_affected_files - can be dict of {repo: [files]} or list
    caf = gt.get("consumer_affected_files", {})
    if isinstance(caf, dict):
        for repo_name, files in caf.items():
            for f in files:
                paths.append({"path": f, "repo_hint": repo_name, "source_key": f"consumer_affected_files.{repo_name}"})
    elif isinstance(caf, list):
        for f in caf:
            paths.append({"path": f, "repo_hint": "", "source_key": "consumer_affected_files"})

    # 6. fix_files (incident-investigation, api-contract-007)
    for item in gt.get("fix_files", []):
        if isinstance(item, dict):
            paths.append({"path": item.get("path", ""), "repo_hint": item.get("repo", ""), "source_key": "fix_files"})
        elif isinstance(item, str):
            paths.append({"path": item, "repo_hint": "", "source_key": "fix_files"})

    # 7. dead_code (dead-code tasks) - list of dicts with .file
    for item in gt.get("dead_code", []):
        if isinstance(item, dict) and "file" in item:
            paths.append({"path": item["file"], "repo_hint": "", "source_key": "dead_code"})

    # 8. live_code - list of dicts with .file
    for item in gt.get("live_code", []):
        if isinstance(item, dict) and "file" in item:
            paths.append({"path": item["file"], "repo_hint": "", "source_key": "live_code"})

    # 9. pr_files (schema-evolution tasks) - list of strings
    for item in gt.get("pr_files", []):
        if isinstance(item, str):
            paths.append({"path": item, "repo_hint": "", "source_key": "pr_files"})

    # 10. pr_files_backend, pr_files_frontend (schema-evolution-009)
    for key in ["pr_files_backend", "pr_files_frontend"]:
        for item in gt.get(key, []):
            if isinstance(item, str):
                paths.append({"path": item, "repo_hint": "", "source_key": key})

    # 11. affected_files (rbac-audit, security tasks) - list of strings
    for item in gt.get("affected_files", []):
        if isinstance(item, str):
            paths.append({"path": item, "repo_hint": "", "source_key": "affected_files"})

    # 12. drift_points (config-drift) - list of dicts with .file
    for item in gt.get("drift_points", []):
        if isinstance(item, dict) and "file" in item:
            paths.append({"path": item["file"], "repo_hint": "", "source_key": "drift_points"})

    # 13. Nested ground_truth.required_files / ground_truth.sufficient_files (support-mapping)
    nested_gt = gt.get("ground_truth", {})
    if isinstance(nested_gt, dict):
        for item in nested_gt.get("required_files", []):
            if isinstance(item, dict):
                paths.append({
                    "path": item.get("path", ""),
                    "repo_hint": item.get("repo", ""),
                    "source_key": "ground_truth.required_files",
                })
        for item in nested_gt.get("sufficient_files", []):
            if isinstance(item, dict):
                paths.append({
                    "path": item.get("path", ""),
                    "repo_hint": item.get("repo", ""),
                    "source_key": "ground_truth.sufficient_files",
                })

    # 14. feature_flags[].files_affected (dead-code)
    for item in gt.get("feature_flags", []):
        if isinstance(item, dict):
            for f in item.get("files_affected", []):
                if isinstance(f, str):
                    paths.append({"path": f, "repo_hint": "", "source_key": "feature_flags.files_affected"})

    # Filter empty paths
    return [p for p in paths if p["path"]]


def resolve_owner_repo(repo_hint: str, toml_repos: dict, gt: dict) -> tuple[str, str]:
    """Resolve a repo hint to (owner/repo, rev) using the task.toml repos map."""
    # If repo_hint matches a local name in toml_repos
    if repo_hint in toml_repos:
        return toml_repos[repo_hint]

    # If repo_hint looks like owner/repo
    if "/" in repo_hint:
        for local_name, (owner_repo, rev) in toml_repos.items():
            if owner_repo == repo_hint:
                return owner_repo, rev

    # If gt has repo field (like "kubernetes/kubernetes" or "envoyproxy/envoy")
    gt_repo = gt.get("repo", "")
    if gt_repo:
        # Try matching in toml_repos
        for local_name, (owner_repo, rev) in toml_repos.items():
            if owner_repo == gt_repo:
                return owner_repo, rev

    # If only one repo, use it
    if len(toml_repos) == 1:
        return list(toml_repos.values())[0]

    # Fallback: try producer_repo for api-contract tasks
    producer = gt.get("producer_repo", "")
    if producer:
        for local_name, (owner_repo, rev) in toml_repos.items():
            if owner_repo == producer:
                return owner_repo, rev

    return "", ""


def check_path_exists(owner_repo: str, rev: str, path: str) -> tuple[bool, str]:
    """Check if a file path exists in a repo at a given rev via gh api."""
    global API_CALLS
    API_CALLS += 1

    if API_CALLS % MAX_CALLS_PER_BATCH == 0:
        time.sleep(SLEEP_BETWEEN_BATCHES)

    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/contents/{path}?ref={rev}",
             "-q", ".name",
             "-H", "Accept: application/vnd.github.v3+json"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "GH_NO_UPDATE_NOTIFIER": "1"}
        )
        if result.returncode == 0 and result.stdout.strip():
            return True, "exists"
        # Check if it's a 404
        if "Not Found" in result.stderr or "404" in result.stderr:
            return False, "not found"
        return False, f"error: {result.stderr.strip()[:100]}"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, f"exception: {str(e)[:100]}"


def main():
    tasks = find_tasks()
    print(f"Found {len(tasks)} tasks with ground_truth.json")

    all_checks = []  # (task_name, path_info, owner_repo, rev)
    skipped_tasks = []

    for toml_path, gt_path in tasks:
        task_name = f"{toml_path.parent.parent.name}/{toml_path.parent.name}"

        toml_repos = parse_repos_from_toml(toml_path)
        with open(gt_path) as f:
            gt = json.load(f)

        file_paths = extract_file_paths(gt, task_name)
        if not file_paths:
            continue

        for fp in file_paths:
            # Resolve which repo this path belongs to
            if fp["repo_hint"]:
                owner_repo, rev = resolve_owner_repo(fp["repo_hint"], toml_repos, gt)
            else:
                owner_repo, rev = resolve_owner_repo("", toml_repos, gt)

            if not owner_repo or not rev:
                skipped_tasks.append((task_name, fp["path"], fp["source_key"], "could not resolve repo/rev"))
                continue

            all_checks.append((task_name, fp, owner_repo, rev))

    print(f"Total file paths to verify: {len(all_checks)}")
    print(f"Skipped (unresolvable): {len(skipped_tasks)}")

    # Deduplicate: same (owner_repo, rev, path) across tasks
    unique_checks = {}
    for task_name, fp, owner_repo, rev in all_checks:
        key = (owner_repo, rev, fp["path"])
        if key not in unique_checks:
            unique_checks[key] = {"tasks": [], "source_key": fp["source_key"]}
        unique_checks[key]["tasks"].append(task_name)

    print(f"Unique (repo, rev, path) triples: {len(unique_checks)}")

    results = []
    broken = []
    verified_ok = 0

    for i, ((owner_repo, rev, path), info) in enumerate(sorted(unique_checks.items())):
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  [{i+1}/{len(unique_checks)}] Checking {owner_repo}@{rev}: {path}...", flush=True)

        exists, detail = check_path_exists(owner_repo, rev, path)
        entry = {
            "owner_repo": owner_repo,
            "rev": rev,
            "path": path,
            "exists": exists,
            "detail": detail,
            "source_key": info["source_key"],
            "tasks": info["tasks"],
        }
        results.append(entry)
        if exists:
            verified_ok += 1
        else:
            broken.append(entry)

    # Write report
    out = PROJECT_ROOT / "results/analysis/gt_path_verification.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        f.write("# Ground Truth File Path Verification Report\n\n")
        f.write(f"**Date**: 2026-03-29\n")
        f.write(f"**Total tasks scanned**: {len(tasks)}\n")
        f.write(f"**Total file paths extracted**: {len(all_checks)}\n")
        f.write(f"**Unique (repo, rev, path) triples**: {len(unique_checks)}\n")
        f.write(f"**Verified OK**: {verified_ok}\n")
        f.write(f"**Broken paths**: {len(broken)}\n")
        f.write(f"**Skipped (unresolvable repo)**: {len(skipped_tasks)}\n")
        f.write(f"**API calls made**: {API_CALLS}\n\n")

        if skipped_tasks:
            f.write("## Skipped Paths (Could Not Resolve Repo/Rev)\n\n")
            f.write("| Task | Path | Source Key | Reason |\n")
            f.write("|------|------|-----------|--------|\n")
            for task_name, path, source_key, reason in skipped_tasks:
                f.write(f"| {task_name} | `{path}` | {source_key} | {reason} |\n")
            f.write("\n")

        if broken:
            f.write("## Broken Paths\n\n")
            f.write("| Repo | Rev | Path | Source Key | Detail | Affected Tasks |\n")
            f.write("|------|-----|------|-----------|--------|----------------|\n")
            for b in broken:
                tasks_str = ", ".join(b["tasks"][:3])
                if len(b["tasks"]) > 3:
                    tasks_str += f" (+{len(b['tasks'])-3} more)"
                f.write(f"| {b['owner_repo']} | `{b['rev']}` | `{b['path']}` | {b['source_key']} | {b['detail']} | {tasks_str} |\n")
        else:
            f.write("## Result\n\nAll ground truth file paths verified successfully.\n")

        # Summary by task type
        f.write("\n## Summary by Source Key\n\n")
        by_key = {}
        for r in results:
            k = r["source_key"]
            if k not in by_key:
                by_key[k] = {"ok": 0, "broken": 0}
            if r["exists"]:
                by_key[k]["ok"] += 1
            else:
                by_key[k]["broken"] += 1

        f.write("| Source Key | OK | Broken |\n")
        f.write("|-----------|-----|--------|\n")
        for k in sorted(by_key):
            f.write(f"| {k} | {by_key[k]['ok']} | {by_key[k]['broken']} |\n")

    print(f"\nReport written to {out}")
    print(f"API calls made: {API_CALLS}")
    if broken:
        print(f"\nWARNING: {len(broken)} broken paths found!")
        sys.exit(1)
    else:
        print("\nAll paths verified OK.")


if __name__ == "__main__":
    main()
