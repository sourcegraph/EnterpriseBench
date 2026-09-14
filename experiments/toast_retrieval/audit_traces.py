"""Validity audit of the sweep reports: are the retrieved paths real?

A recall number is only a retrieval measurement if the paths the policy
emitted actually exist in the repo it searched. A model that has read these
OSS repos in training can emit ``.go-version`` or ``package.json`` from
parametric memory without any search happening -- and if that guess lands on
a required file, it scores as retrieval. This audit separates the two
failure modes it can detect from the reports alone:

* **Nonexistent paths** -- the policy emitted a path that is not in the
  indexed repo at all. Proof that path *generation* (not retrieval) is in
  play for that policy, and a rate to compare across arms. Checked in two
  stages, because a path that fails an exact blob lookup is not yet a
  hallucination: ``sansio/app.py`` is a real Flask file cited without its
  ``src/flask/`` prefix. Stage two asks the index whether any file in the
  repo *ends with* the cited path; only a path that fails both is counted
  nonexistent. Paths that pass only stage two are reported separately as
  abbreviated citations, which cost precision but are real retrievals.
* **Guessable hits** -- credited required files whose paths are
  repo-root conventional names (``package.json``, ``go.mod``, ``.go-version``
  ...). These score identically whether searched for or recalled, so they are
  reported separately rather than silently counted.

Existence is checked against the same demo.sourcegraph.com index the policies
searched, via the blob query the adapter itself uses -- not a local clone,
which would be a different revision.

What this CANNOT check: whether an existing, non-guessable path was reached
by a tool call or recalled. That needs the tool-call transcript, which
``run_toast.py`` does not persist (``run_mcp_policy.py`` keeps
``raw_response`` only). Recorded as a gap rather than papered over.

Usage:
    python3 audit_traces.py --dir toast1=reports_hard_toast \\
        --dir code_finder=reports_hard_code_finder --json trace_audit.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from graphql import GraphQLError, SourcegraphConfig, graphql

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aligned_scoring import match_pairs  # noqa: E402

_EXISTS_QUERY = """
query Exists($repo: String!, $path: String!) {
  repository(name: $repo) {
    commit(rev: "HEAD") {
      blob(path: $path) { totalLines }
    }
  }
}
"""

# Stage two: does ANY file in the repo end with the cited path? Distinguishes
# a citation that dropped a prefix from a path the model invented.
_SUFFIX_QUERY = """
query SuffixExists($q: String!) {
  search(query: $q, version: V3) {
    results { matchCount }
  }
}
"""

# Conventional repo-root files a model can name without searching. Scoring
# them is not wrong -- they are genuinely required evidence in some tasks --
# but a hit here is not evidence the policy searched.
GUESSABLE = {
    "package.json", "go.mod", "go.sum", "go.work", ".go-version",
    "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml",
    "cargo.toml", "cargo.lock", "pom.xml", "build.gradle", "makefile",
    "dockerfile", "readme.md", "package-lock.json", "yarn.lock",
    "tox.ini", ".gitignore", "manifest.in", "gemfile",
}


def _exists(config: SourcegraphConfig, repo: str, path: str) -> bool | None:
    """True/False, or None when the check itself failed (never counted)."""
    try:
        data = graphql(config, _EXISTS_QUERY, {"repo": repo, "path": path})
    except GraphQLError:
        return None
    repository = data.get("repository")
    if not repository:
        return None
    commit = repository.get("commit")
    if not commit:
        return None
    return commit.get("blob") is not None


def _suffix_exists(config: SourcegraphConfig, repo: str, path: str) -> bool | None:
    """Does any file in `repo` end with `path`? None when the check failed."""
    pattern = re.escape(path.strip("/"))
    query = f"repo:^{re.escape(repo)}$ type:path file:(^|/){pattern}$ count:1"
    try:
        data = graphql(config, _SUFFIX_QUERY, {"q": query})
    except GraphQLError:
        return None
    results = (data.get("search") or {}).get("results")
    if results is None:
        return None
    return results.get("matchCount", 0) > 0


def collect_pairs(reports: dict[str, dict]) -> set[tuple[str, str]]:
    """Every (repo, path) a policy claimed, across the tasks it ran.

    A report's paths are not repo-qualified, so each path is checked against
    every store the task searched: existing in ANY of them counts as real,
    which is the charitable reading and keeps single-repo tasks exact.
    """
    pairs = set()
    for report in reports.values():
        for path in report["retrieved_paths"]:
            for store in report["store_identifiers"]:
                pairs.add((store, path))
    return pairs


def audit(name: str, reports: dict[str, dict], existence: dict, suffixes: dict) -> dict:
    total = exact = abbreviated = fake = unknown = 0
    guessable_hits = 0
    credited_total = 0
    offenders: dict[str, list[str]] = {}
    for task_id, report in reports.items():
        required = report["required_files"]
        for path in report["retrieved_paths"]:
            total += 1
            stores = report["store_identifiers"]
            verdicts = [existence.get((s, path)) for s in stores]
            if any(v is True for v in verdicts):
                exact += 1
            elif all(v is None for v in verdicts):
                unknown += 1
            elif any(suffixes.get((s, path)) is True for s in stores):
                abbreviated += 1
            elif all(suffixes.get((s, path)) is None for s in stores):
                unknown += 1
            else:
                fake += 1
                offenders.setdefault(task_id, []).append(path)
        # Counted per credited required FILE, the same unit file_recall uses,
        # so this column reads against the recall tables. The assignment is the
        # one score() makes, so a file cited twice is not credited twice.
        for req in set(match_pairs(report["retrieved_paths"], required).values()):
            credited_total += 1
            if req.lower().rsplit("/", 1)[-1] in GUESSABLE:
                guessable_hits += 1
    checked = exact + abbreviated + fake
    return {
        "policy": name,
        "n_tasks": len(reports),
        "paths_emitted": total,
        "paths_exact": exact,
        "paths_abbreviated": abbreviated,
        "paths_nonexistent": fake,
        "paths_uncheckable": unknown,
        "nonexistent_rate": round(fake / checked, 3) if checked else None,
        "abbreviated_rate": round(abbreviated / checked, 3) if checked else None,
        "credited_required_files": credited_total,
        "credited_that_are_guessable_root_files": guessable_hits,
        "guessable_share_of_credit": (
            round(guessable_hits / credited_total, 3) if credited_total else None
        ),
        "tasks_with_nonexistent_paths": len(offenders),
        "examples": {k: v[:4] for k, v in list(offenders.items())[:6]},
    }


def _load(directory: Path) -> dict[str, dict]:
    return {
        report["task_id"]: report
        for report in (json.loads(p.read_text()) for p in directory.glob("*.json"))
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    config = SourcegraphConfig.from_env()
    if not config.token:
        print("SOURCEGRAPH_ACCESS_TOKEN is not set.", file=sys.stderr)
        return 2

    loaded = {}
    for spec in args.dir:
        name, path = spec.split("=", 1)
        directory = Path(path)
        if not directory.is_dir():
            print(f"missing report dir: {path}", file=sys.stderr)
            return 2
        loaded[name] = _load(directory)

    all_pairs = sorted(set().union(*(collect_pairs(r) for r in loaded.values())))
    print(f"verifying {len(all_pairs)} (repo, path) pairs against the index...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        verdicts = list(pool.map(lambda p: _exists(config, *p), all_pairs))
    existence = dict(zip(all_pairs, verdicts))

    # Only paths that failed the exact lookup need the suffix probe, which is a
    # search rather than a blob read and is the expensive half.
    unresolved = [pair for pair in all_pairs if existence[pair] is not True]
    print(f"probing {len(unresolved)} unresolved paths for a prefix-dropped match...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        suffix_verdicts = list(pool.map(lambda p: _suffix_exists(config, *p), unresolved))
    suffixes = dict(zip(unresolved, suffix_verdicts))

    rows = [audit(name, reports, existence, suffixes) for name, reports in loaded.items()]
    cols = [
        "paths_emitted", "paths_abbreviated", "abbreviated_rate",
        "paths_nonexistent", "nonexistent_rate",
        "credited_required_files", "credited_that_are_guessable_root_files",
        "guessable_share_of_credit",
    ]
    print(f"\n{'policy':14}" + "".join(f"{c:>20}" for c in cols))
    for row in rows:
        print(f"{row['policy']:14}" + "".join(f"{row[c]!s:>20}" for c in cols))
    for row in rows:
        if row["examples"]:
            print(f"\n{row['policy']} nonexistent-path examples:")
            for task_id, paths in row["examples"].items():
                print(f"  {task_id}: {', '.join(paths)}")

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
