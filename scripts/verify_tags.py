#!/usr/bin/env python3
"""Verify that pinned revision tags exist for all task.toml repos."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.tasks import find_task_dirs

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = PROJECT_ROOT / "benchmarks"


def find_task_tomls() -> list[Path]:
    """Find all task.toml files from active task directories."""
    return [task_dir / "task.toml" for task_dir in find_task_dirs(BENCHMARKS)]


def normalize_url(url: str) -> str:
    """Normalize a repo URL to full https form."""
    url = url.strip()
    if url.startswith("https://"):
        return url
    if url.startswith("github.com/"):
        return f"https://{url}"
    if url.startswith("http://"):
        return url.replace("http://", "https://")
    # Handle sg-evals mirrors or other patterns
    if "/" in url and not url.startswith("http"):
        return f"https://github.com/{url}"
    return url


def check_tag(url: str, rev: str) -> tuple[bool, str]:
    """Check if a tag exists. Returns (exists, detail)."""
    full_url = normalize_url(url)

    # Try as tag first
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", full_url, rev],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and rev in result.stdout:
            return True, "tag found"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, f"error: {e}"

    # Try as branch
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--refs", full_url, rev],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and rev in result.stdout:
            return True, "branch found"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, f"error: {e}"

    # Try as commit hash (short or full)
    if len(rev) >= 7 and all(c in "0123456789abcdef" for c in rev.lower()):
        # For commit hashes, we can't easily verify via ls-remote
        # but we can try gh api
        try:
            owner_repo = full_url.replace("https://github.com/", "").rstrip("/")
            result = subprocess.run(
                ["gh", "api", f"repos/{owner_repo}/commits/{rev}", "--jq", ".sha"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                return True, "commit found"
        except Exception:
            pass
        return False, "commit hash - could not verify"

    return False, "not found as tag or branch"


def main():
    task_tomls = find_task_tomls()
    print(f"Found {len(task_tomls)} task.toml files")

    # Collect all unique (url, rev) pairs
    repo_entries = []  # (task_name, url, rev)
    for toml_path in task_tomls:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        task_name = toml_path.parent.name
        suite = toml_path.parent.parent.name

        repos = data.get("repos", [])
        for repo in repos:
            url = repo.get("url", "")
            rev = repo.get("rev", "")
            if url and rev:
                repo_entries.append((f"{suite}/{task_name}", url, rev))

    print(f"Found {len(repo_entries)} repo entries to verify")

    # Deduplicate by (url, rev) for efficiency
    unique_pairs = {}
    for task_name, url, rev in repo_entries:
        key = (normalize_url(url), rev)
        if key not in unique_pairs:
            unique_pairs[key] = []
        unique_pairs[key].append(task_name)

    print(f"Unique (url, rev) pairs: {len(unique_pairs)}")

    results = []
    missing = []

    for i, ((url, rev), tasks) in enumerate(sorted(unique_pairs.items())):
        print(f"  [{i+1}/{len(unique_pairs)}] Checking {url} @ {rev}...", end=" ", flush=True)
        exists, detail = check_tag(url, rev)
        status = "OK" if exists else "MISSING"
        print(f"{status} ({detail})")
        results.append({
            "url": url,
            "rev": rev,
            "exists": exists,
            "detail": detail,
            "tasks": tasks,
        })
        if not exists:
            missing.append({"url": url, "rev": rev, "detail": detail, "tasks": tasks})

    # Write report
    out = PROJECT_ROOT / "results/analysis/tag_verification.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        f.write("# Tag/Revision Verification Report\n\n")
        f.write(f"**Date**: 2026-03-29\n")
        f.write(f"**Total tasks scanned**: {len(task_tomls)}\n")
        f.write(f"**Total repo entries**: {len(repo_entries)}\n")
        f.write(f"**Unique (url, rev) pairs**: {len(unique_pairs)}\n")
        f.write(f"**Verified OK**: {sum(1 for r in results if r['exists'])}\n")
        f.write(f"**Missing/broken**: {len(missing)}\n\n")

        if missing:
            f.write("## Missing Tags/Revisions\n\n")
            f.write("| URL | Rev | Detail | Affected Tasks |\n")
            f.write("|-----|-----|--------|----------------|\n")
            for m in missing:
                tasks_str = ", ".join(m["tasks"][:5])
                if len(m["tasks"]) > 5:
                    tasks_str += f" (+{len(m['tasks'])-5} more)"
                f.write(f"| {m['url']} | `{m['rev']}` | {m['detail']} | {tasks_str} |\n")
        else:
            f.write("## Result\n\nAll tags/revisions verified successfully.\n")

        f.write("\n## All Verified Pairs\n\n")
        f.write("| URL | Rev | Status | Tasks |\n")
        f.write("|-----|-----|--------|-------|\n")
        for r in results:
            status = "OK" if r["exists"] else "MISSING"
            tasks_str = ", ".join(r["tasks"][:3])
            if len(r["tasks"]) > 3:
                tasks_str += f" (+{len(r['tasks'])-3} more)"
            f.write(f"| {r['url']} | `{r['rev']}` | {status} | {tasks_str} |\n")

    print(f"\nReport written to {out}")
    if missing:
        print(f"\nWARNING: {len(missing)} missing tags/revisions found!")
        sys.exit(1)
    else:
        print("\nAll tags/revisions verified OK.")


if __name__ == "__main__":
    main()
