"""Measure how much search a task actually costs, per required file.

EnterpriseBench has no ">= N greps" criterion (checked: schema, every doc,
all task.toml, CI, beads). The nearest written bar is prose in
docs/TASK_TYPE_PRD.md:1476 -- "A grep-only agent ... must score < 0.40 on
every task" -- which is never run. This module operationalizes it.

The measurement: a required file is *lexically reachable* if some literal
lifted straight out of instruction.md, searched over the task's own indexed
repos, returns that file. Such a file costs one grep. A required file that
no prompt literal reaches has to be derived (follow an import, a call, a
config key), which is the multi-step search the benchmark claims to measure.

So per task:

    literals        distinct literals tried (one Sourcegraph query each)
    reached         required files some literal returned
    unreached       required files no literal returned -- the expensive ones

A task is retrieval-stressing when `unreached` is high. `reached` is not
free either (the agent still has to pick the right literal out of many), so
`literals` is reported as the search-space size, not discounted.

Queries hit the same demo.sourcegraph.com index the mcp_only/mcp_code_finder
arms use, via the same client, so the number describes the arena the arms
actually run in rather than a local clone.
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
sys.path.insert(0, str(ROOT / "scripts" / "infra"))
from mirror_naming import derive_mirror_name  # noqa: E402

from graphql import SourcegraphConfig, graphql  # noqa: E402
from sourcegraph_client import _SEARCH_QUERY  # noqa: E402

sys.path.insert(0, str(HERE))
from eb_metrics_shim import import_eb_metrics  # noqa: E402

required_files_from_ground_truth = import_eb_metrics(
    "eb_metrics.retrieval_extraction"
).required_files_from_ground_truth

# A literal shorter than this matches too much prose to be a search a real
# agent would issue; the same floor audit_disclosure.py uses for stems.
_MIN_LITERAL = 4
# Per-query file cap. Beyond this a "hit" is not a hit an agent would read.
_TOP_N = 10

_BACKTICK = re.compile(r"`([^`\n]{%d,80})`" % _MIN_LITERAL)
_QUOTED = re.compile(r'"([^"\n]{%d,80})"' % _MIN_LITERAL)
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]*_[a-z0-9_]+)\b")


def literals(instruction: str) -> list[str]:
    """Search terms a competent agent would lift from the prompt, deduped."""
    found: list[str] = []
    for pattern in (_BACKTICK, _QUOTED, _IDENT):
        for raw in pattern.findall(instruction):
            term = raw.strip()
            if len(term) >= _MIN_LITERAL and term not in found:
                found.append(term)
    return found


def _mirrors(task_dir: Path) -> list[str]:
    config = tomllib.loads((task_dir / "task.toml").read_text())
    return [
        "github.com/" + derive_mirror_name(r["url"], r["rev"])
        for r in config.get("repos", [])
    ]


def _search(cfg: SourcegraphConfig, scope: str, term: str) -> set[str]:
    """Paths returned for one literal. A failed query is not a silent miss."""
    query = f'{scope} content:"{term}" count:{_TOP_N * 5}'
    data = graphql(cfg, _SEARCH_QUERY, {"query": query})
    matches = ((data.get("search") or {}).get("results") or {}).get("results") or []
    paths = []
    for match in matches:
        path = (match.get("file") or {}).get("path")
        if path and path not in paths:
            paths.append(path)
    return set(paths[:_TOP_N])


def _matches_required(hit: str, required: str) -> bool:
    """Index paths are repo-relative; ground truth may be repo-qualified."""
    return hit == required or hit.endswith("/" + required) or required.endswith("/" + hit)


def probe(task_dir: Path, cfg: SourcegraphConfig, workers: int = 8) -> dict:
    required = required_files_from_ground_truth(task_dir / "ground_truth.json")
    terms = literals((task_dir / "instruction.md").read_text())
    mirrors = _mirrors(task_dir)
    scope = "repo:" + "|".join(f"^{m}$" for m in mirrors)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        hit_sets = list(pool.map(lambda t: _search(cfg, scope, t), terms))

    reached = {}
    for term, hits in zip(terms, hit_sets):
        for req in required:
            if req in reached:
                continue
            if any(_matches_required(h, req) for h in hits):
                reached[req] = term

    return {
        "task": f"{task_dir.parent.name}/{task_dir.name}",
        "repos": mirrors,
        "n_required": len(required),
        "n_literals": len(terms),
        "reached": reached,
        "unreached": [r for r in required if r not in reached],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tasks", nargs="+", type=Path, help="task directories")
    parser.add_argument("-o", "--out", type=Path, default=None)
    args = parser.parse_args()

    cfg = SourcegraphConfig.from_env()
    if not cfg.token:
        print("SOURCEGRAPH_ACCESS_TOKEN is not set", file=sys.stderr)
        return 2

    rows = []
    for task_dir in args.tasks:
        row = probe(task_dir, cfg)
        rows.append(row)
        n, u = row["n_required"], len(row["unreached"])
        print(f"{row['task']:64} req={n:2} lits={row['n_literals']:3} unreached={u}/{n}")

    if args.out:
        args.out.write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
