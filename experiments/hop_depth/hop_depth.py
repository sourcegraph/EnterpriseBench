"""Measure how many search-and-read rounds separate a prompt from its answer.

`grep_floor.py` asks a binary question: does any literal lifted out of
instruction.md return a required file. That splits tasks into reachable and
unreachable, but says nothing about how far the unreachable ones are, and it
scores a task "hard" when the prompt is merely thin -- a task offering one
literal reaches nothing by construction.

Hop depth measures the distance instead. It replays the loop an agent actually
runs:

    depth 0   files a prompt literal returns
    depth k   files reachable by searching an identifier that first appeared
              in a file reached at depth k-1

That loop is the thing code_finder collapses into one call, so the depth of a
task's required files is a direct statement of how much searching the task
costs -- measured before any arm runs, against the same index the arms use, and
without reference to any tool's results. Selecting on measured savings would be
circular; selecting on this is not.

Identifier edges rather than SCIP definition edges, deliberately: SCIP coverage
varies by language and this corpus spans Go, Python, Rust, TypeScript, Java and
C++. An identifier search is what the agent issues anyway, and it is uniform
across every repo in the index.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TOAST = ROOT / "experiments" / "toast_retrieval"
sys.path.insert(0, str(ROOT / "scripts" / "infra"))
sys.path.insert(0, str(TOAST))

from graphql import SourcegraphConfig, graphql  # noqa: E402
from mirror_naming import derive_mirror_name  # noqa: E402
from sourcegraph_client import _BLOB_QUERY, _SEARCH_QUERY  # noqa: E402

sys.path.insert(0, str(TOAST))
from grep_floor import _TOP_N, _matches_required, literals  # noqa: E402

from eb_metrics_shim import import_eb_metrics  # noqa: E402

required_files_from_ground_truth = import_eb_metrics(
    "eb_metrics.retrieval_extraction"
).required_files_from_ground_truth

MAX_DEPTH = 3
# Per round, per frontier file. A frontier file yields many identifiers; an
# agent tries a handful of the most promising, not all of them. Ranking by
# frequency inside the file approximates "what this file is about".
IDENTIFIERS_PER_FILE = 6
# Frontier width. Without this one broad round makes the next round quadratic.
FRONTIER_CAP = 12

_IDENT = re.compile(r"\b([A-Z][a-z0-9]+[A-Za-z0-9]*|[a-z][a-z0-9]*_[a-z0-9_]+)\b")
# Identifiers so common that searching them is not a hop anyone would make.
_STOP = frozenset(
    """the and for not with this that from have has was were will can may
    string int bool err error nil none true false self return func def class
    import package public private static void new get set add remove list
    map value key name type test data file path line code text size len""".split()
)


def _search_located(cfg: SourcegraphConfig, scope: str, term: str) -> list[tuple[str, str]]:
    """(repo, path) for one literal.

    grep_floor's search returns paths alone, which is enough for a yes/no
    reachability check. A walk has to read the file it landed on, and on a
    multi-repo task the path alone does not say which repo holds it.
    """
    query = f'{scope} content:"{term}" count:{_TOP_N * 5}'
    data = graphql(cfg, _SEARCH_QUERY, {"query": query})
    matches = ((data.get("search") or {}).get("results") or {}).get("results") or []
    located: list[tuple[str, str]] = []
    for match in matches:
        path = (match.get("file") or {}).get("path")
        repo = (match.get("repository") or {}).get("name")
        if path and repo and (repo, path) not in located:
            located.append((repo, path))
    return located[:_TOP_N]


def _blob(cfg: SourcegraphConfig, repo: str, path: str) -> str:
    data = graphql(cfg, _BLOB_QUERY, {"repo": repo, "path": path})
    repository = (data.get("repository") or {}).get("commit") or {}
    blob = repository.get("blob") or {}
    return blob.get("content") or ""


def _identifiers(text: str) -> list[str]:
    counts: dict[str, int] = {}
    for raw in _IDENT.findall(text):
        if len(raw) < 5 or raw.lower() in _STOP:
            continue
        counts[raw] = counts.get(raw, 0) + 1
    ranked = sorted(counts, key=lambda term: (-counts[term], term))
    return ranked[:IDENTIFIERS_PER_FILE]


def probe(task_dir: Path, cfg: SourcegraphConfig, workers: int = 8) -> dict:
    """Depth at which each required file first becomes reachable."""
    required = required_files_from_ground_truth(task_dir / "ground_truth.json")
    config = tomllib.loads((task_dir / "task.toml").read_text())
    mirrors = [
        "github.com/" + derive_mirror_name(r["url"], r["rev"])
        for r in config.get("repos", [])
    ]
    scope = "repo:" + "|".join(f"^{m}$" for m in mirrors)
    terms = literals((task_dir / "instruction.md").read_text())

    depth_of: dict[str, int] = {}
    seen_files: set[tuple[str, str]] = set()
    seen_terms: set[str] = set(terms)
    frontier = terms
    rounds: list[dict] = []

    for depth in range(MAX_DEPTH + 1):
        if not frontier:
            break
        with ThreadPoolExecutor(max_workers=workers) as pool:
            hit_sets = list(
                pool.map(lambda t: _search_located(cfg, scope, t), frontier)
            )
        reached_now = {hit for hits in hit_sets for hit in hits}
        for req in required:
            if req in depth_of:
                continue
            if any(_matches_required(path, req) for _repo, path in reached_now):
                depth_of[req] = depth
        rounds.append(
            {"depth": depth, "searches": len(frontier), "files": len(reached_now)}
        )
        if len(depth_of) == len(required) or depth == MAX_DEPTH:
            break

        fresh = [hit for hit in reached_now if hit not in seen_files][:FRONTIER_CAP]
        seen_files.update(reached_now)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            blobs = list(pool.map(lambda hit: _blob(cfg, hit[0], hit[1]), fresh))
        next_terms: list[str] = []
        for text in blobs:
            for term in _identifiers(text):
                if term not in seen_terms:
                    seen_terms.add(term)
                    next_terms.append(term)
        frontier = next_terms

    depths = [depth_of.get(req) for req in required]
    resolved = [d for d in depths if d is not None]
    return {
        "task": f"{task_dir.parent.name}/{task_dir.name}",
        "n_required": len(required),
        "n_literals": len(terms),
        "depth_by_file": {req: depth_of.get(req) for req in required},
        "unreached_within_max": sum(1 for d in depths if d is None),
        "max_depth": max(resolved) if resolved else None,
        "mean_depth": round(sum(resolved) / len(resolved), 2) if resolved else None,
        "rounds": rounds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tasks", nargs="+", type=Path)
    parser.add_argument("-o", "--out", type=Path, default=None)
    args = parser.parse_args()

    cfg = SourcegraphConfig.from_env()
    if not cfg.token:
        print("SOURCEGRAPH_ACCESS_TOKEN is not set", file=sys.stderr)
        return 2

    rows = []
    for task_dir in args.tasks:
        try:
            row = probe(task_dir, cfg)
        except Exception as exc:  # noqa: BLE001 - one bad task must not stop a sweep
            print(f"{task_dir}: FAILED {exc}", file=sys.stderr)
            continue
        rows.append(row)
        print(
            f"{row['task']:60} req={row['n_required']:2} lits={row['n_literals']:3} "
            f"maxdepth={row['max_depth']} mean={row['mean_depth']} "
            f"unreached={row['unreached_within_max']}"
        )
    if args.out:
        args.out.write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
