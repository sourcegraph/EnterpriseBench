"""Shallow-clone the sg-evals snapshots the candidate tasks scope to.

The local-baseline arm (run_local_policy.py) greps real checkouts; this
materializes them under repos/<name>, one directory per store identifier,
depth-1 on the default branch. The sg-evals repos ARE the pinned snapshots --
the version is baked into the repository name -- so the default branch is the
content Sourcegraph indexed. (Dependabot occasionally opens branches on these
forks; only the default branch matters.)

Idempotent: an existing repos/<name>/.git is skipped, so a partial run
resumes.

Usage:
    python3 clone_repos.py            # clones into ./repos, 4 at a time
    python3 clone_repos.py --tasks hard_slice_tasks.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOS_DIR = HERE / "repos"


def store_ids(manifest: Path) -> list[str]:
    stores: set[str] = set()
    for candidate in json.loads(manifest.read_text()):
        stores.update(candidate["store_identifiers"])
    return sorted(stores)


def clone(store_id: str) -> tuple[str, str]:
    name = store_id.rsplit("/", 1)[-1]
    destination = REPOS_DIR / name
    if (destination / ".git").is_dir():
        return store_id, "cached"
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", f"https://{store_id}", str(destination)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return store_id, f"FAILED: {result.stderr.strip()[:200]}"
    return store_id, "cloned"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=HERE / "candidate_tasks.json")
    args = parser.parse_args()
    REPOS_DIR.mkdir(exist_ok=True)
    failures = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for store_id, status in pool.map(clone, store_ids(args.tasks)):
            print(f"{store_id}: {status}", file=sys.stderr)
            failures += status.startswith("FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
