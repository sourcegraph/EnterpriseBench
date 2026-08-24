"""Record which tier of index answers the code-intel arm, per store.

``preflight_index.py`` establishes that ``definition`` resolves. It cannot say
what resolved it, because the MCP seam returns text content and no provenance.
That distinction is load-bearing: Sourcegraph serves precise navigation from a
semantic SCIP index (``scip-python``, ``scip-go``, ``scip-typescript``,
``scip-rust``, ``scip-ruby``, ``scip-java``) and *also* serves search-based
navigation from ``scip-syntax``, a tree-sitter pass that knows a name's shape
and not its resolved symbol identity. Both answer the same call and look
identical in a usage count. Only one of them is the mechanism the code-intel
result usually gets attributed to.

So the tier is read from the instance's GraphQL API rather than inferred from
the answers, and it is recorded per store so a contrast can be re-cut by it.
The pilot's whole-corpus codeintel-over-lexical gap does not survive that cut
unchanged, which is exactly why the tier is a committed artifact and not a
footnote.

A store is ``semantic`` if any COMPLETED index carries a semantic indexer,
``syntactic`` if its only COMPLETED indexes are ``scip-syntax``, ``none`` if
nothing completed, and ``unknown`` when the API could not be asked. ``unknown``
is not folded into any of the other three: an unanswered query is an absent
measurement, and a stratified comparison that silently absorbed it would report
a tier effect partly made of network failures.

Usage:
    python3 index_tier.py                    # sweep the pilot's stores
    python3 index_tier.py --tasks slice_tasks.json
    python3 index_tier.py --check            # exit 1 if any tier changed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIERS = HERE / "index_tier.json"

sys.path.insert(0, str(HERE.parent.parent / "agents" / "harnesses" / "claude" / "mcp"))

#: Indexers that emit resolved symbol identity. `scip-syntax` is deliberately
#: absent: it is a tree-sitter pass, and calling it semantic is the terminology
#: error this whole module exists to prevent.
SEMANTIC_INDEXERS = frozenset(
    {
        "scip-python",
        "scip-go",
        "scip-typescript",
        "scip-rust",
        "scip-ruby",
        "scip-java",
        "scip-clang",
        "scip-dotnet",
        "scip-php",
        "lsif-go",
        "lsif-java",
    }
)
SYNTACTIC_INDEXERS = frozenset({"scip-syntax"})

_TIMEOUT = 60
# demo.sourcegraph.com's WAF 403s the default Python-urllib user agent, the
# same way it does for the MCP seam.
_USER_AGENT = "sg-cli/1.0"

_QUERY = """
query IndexTier($name: String!) {
  repository(name: $name) {
    defaultBranch { target { oid } }
    codeIntelSummary {
      lastIndexScan
      recentActivity {
        ... on PreciseIndex { state inputCommit indexer { name } }
      }
    }
  }
}
"""


def graphql_endpoint() -> str:
    """The instance's GraphQL URL, derived from the same env the MCP seam reads."""
    explicit = os.environ.get("SG_GRAPHQL_URL", "").strip()
    if explicit:
        return explicit
    import sg_cli

    url = os.environ.get("SG_URL", "").strip() or sg_cli.DEFAULT_SG_URL
    root = url.split("/.api/", 1)[0]
    return f"{root}/.api/graphql"


def _token() -> str:
    return (
        os.environ.get("SOURCEGRAPH_ACCESS_TOKEN")
        or os.environ.get("SRC_ACCESS_TOKEN")
        or ""
    )


def query(name: str) -> dict:
    """Raw GraphQL response for one store. Raises on transport failure."""
    body = json.dumps({"query": _QUERY, "variables": {"name": name}}).encode()
    request = urllib.request.Request(
        graphql_endpoint(),
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"token {_token()}",
            "User-Agent": _USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read().decode())


def classify(payload: dict) -> dict:
    """Tier verdict for one store, from its GraphQL payload.

    Only COMPLETED indexes count. An ERRORED or QUEUED index is a job, not an
    index, and treating one as coverage would credit the arm with navigation it
    never had.
    """
    if payload.get("errors"):
        detail = "; ".join(str(e.get("message", e)) for e in payload["errors"])
        return {"tier": "unknown", "detail": detail[:200], "indexers": [], "head": None}
    repository = (payload.get("data") or {}).get("repository")
    if not repository:
        return {
            "tier": "unknown",
            "detail": "repository not found on this instance",
            "indexers": [],
            "head": None,
        }
    head = ((repository.get("defaultBranch") or {}).get("target") or {}).get("oid")
    activity = (repository.get("codeIntelSummary") or {}).get("recentActivity") or []
    completed = [
        (((index.get("indexer") or {}).get("name") or "?"), index.get("inputCommit"))
        for index in activity
        if index.get("state") == "COMPLETED"
    ]
    indexers = sorted({name for name, _ in completed})
    semantic = sorted(set(indexers) & SEMANTIC_INDEXERS)
    if semantic:
        tier = "semantic"
    elif set(indexers) & SYNTACTIC_INDEXERS:
        tier = "syntactic"
    else:
        tier = "none"
    return {
        "tier": tier,
        "head": head,
        "indexers": indexers,
        "semantic_indexers": semantic,
        # A semantic index built on some other revision still resolves symbols,
        # but not necessarily the ones at the pinned commit. Recorded so the
        # weaker case is visible rather than assumed away.
        "semantic_at_head": sorted(
            {name for name, commit in completed
             if name in SEMANTIC_INDEXERS and head and commit == head}
        ),
    }


def store_tier(store_id: str) -> dict:
    try:
        payload = query(store_id)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "tier": "unknown",
            "detail": f"{type(exc).__name__}: {exc}",
            "indexers": [],
            "head": None,
        }
    return classify(payload)


#: A task spans every store it names, so its tier is the weakest one it needs.
#: `mixed` is kept separate from both ends on purpose: collapsing it into
#: `syntactic` would overstate how much of the corpus lacks precise navigation,
#: and collapsing it into `semantic` would overstate how much has it.
def task_tier(stores: list[str], by_store: dict[str, dict]) -> str:
    tiers = {by_store.get(s, {}).get("tier", "unknown") for s in stores}
    if "unknown" in tiers:
        return "unknown"
    if tiers <= {"semantic"}:
        return "semantic"
    if "semantic" in tiers:
        return "mixed"
    return "syntactic"


def build(tasks: Path, workers: int) -> dict:
    entries = json.loads(tasks.read_text())
    stores = sorted({s for t in entries for s in t["store_identifiers"]})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(store_tier, stores))
    by_store = dict(zip(stores, results))
    return {
        "semantic_indexers": sorted(SEMANTIC_INDEXERS),
        "stores": by_store,
        "tasks": {t["task_id"]: task_tier(t["store_identifiers"], by_store) for t in entries},
        "note": _NOTE,
    }


_NOTE = (
    "Tier is what kind of index answers a navigation call, not whether one "
    "answers: 'semantic' means a COMPLETED SCIP index that resolves symbol "
    "identity, 'syntactic' means scip-syntax only (tree-sitter, name shape, no "
    "resolution), 'none' means nothing completed, 'unknown' means the API could "
    "not be asked and the measurement is absent. A task's tier is the weakest "
    "tier among the stores it spans. Use it to stratify a contrast; it is not "
    "an eligibility gate -- preflight_index.py owns that."
)


def summarise(tiers: dict) -> list[str]:
    lines = []
    counts: dict[str, int] = {}
    for record in tiers["stores"].values():
        counts[record["tier"]] = counts.get(record["tier"], 0) + 1
    lines.append(f"{len(tiers['stores'])} stores")
    for tier, count in sorted(counts.items()):
        lines.append(f"  {tier:10s} {count}")
    task_counts: dict[str, int] = {}
    for tier in tiers["tasks"].values():
        task_counts[tier] = task_counts.get(tier, 0) + 1
    lines.append(f"{len(tiers['tasks'])} tasks")
    for tier, count in sorted(task_counts.items()):
        lines.append(f"  {tier:10s} {count}")
    return lines


def drifted(current: dict, previous: dict) -> tuple[list[str], list[str]]:
    """(stores with no recorded tier, stores whose tier changed)."""
    unknown = sorted(set(current["stores"]) - set(previous["stores"]))
    drift = sorted(
        f"{store}: {previous['stores'][store]['tier']} -> {record['tier']}"
        for store, record in current["stores"].items()
        if store in previous["stores"]
        and previous["stores"][store]["tier"] != record["tier"]
    )
    return unknown, drift


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=HERE / "pilot_tasks.json")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--json", type=Path, default=TIERS)
    parser.add_argument("--check", action="store_true", help="fail if any tier changed")
    args = parser.parse_args()

    if not _token():
        print(
            "SOURCEGRAPH_ACCESS_TOKEN is unset; the tier cannot be read and "
            "must not be guessed",
            file=sys.stderr,
        )
        return 2

    tiers = build(args.tasks, args.workers)

    if args.check:
        if not args.json.is_file():
            print(f"{args.json.name} missing; run without --check", file=sys.stderr)
            return 1
        unknown, drift = drifted(tiers, json.loads(args.json.read_text()))
        if unknown:
            print("no recorded tier for: " + ", ".join(unknown), file=sys.stderr)
            return 1
        if drift:
            print("index tier changed for:\n  " + "\n  ".join(drift), file=sys.stderr)
            return 1
        print("index tiers unchanged")
        return 0

    args.json.write_text(json.dumps(tiers, indent=2, sort_keys=True) + "\n")
    print(f"-> {args.json.name}")
    for line in summarise(tiers):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
