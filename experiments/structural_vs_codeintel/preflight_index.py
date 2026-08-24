"""Probe which pinned mirrors carry a precise index, before any run is scored.

The code-intel condition can only navigate a repository Sourcegraph has
indexed. On an unindexed mirror ``definition`` and ``references`` return
nothing, and the arm would post a clean-looking 0.0 for a task it was never
given the means to answer. That is a harness failure wearing a measurement's
clothes, and the scoring trust boundary says such a run is *invalid*, not
zero -- so coverage is established up front, not inferred afterwards from
disappointing numbers.

The probe: pick a handful of top-level definitions out of the local clone
(``def``/``class``/``func``/``fn`` at column zero, a symbol whose definition
must resolve to the very line it was read from) and ask ``sg_go_to_definition``
for each. One resolution proves an index. Every probe coming back empty is
evidence of absence rather than proof, so the verdict is reported as
``unindexed`` with its probes attached, and it is the *runner's* job to route
the affected tasks out of the code-intel arm.

The probe never touches the structural or lexical arms: those read the clone
directly and are unaffected by index state.

Usage:
    python3 preflight_index.py                     # probe the pilot's stores
    python3 preflight_index.py --tasks slice_tasks.json --probes 5
    python3 preflight_index.py --check             # exit 1 if coverage changed
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOS = HERE.parent / "toast_retrieval" / "repos"
COVERAGE = HERE / "index_coverage.json"

sys.path.insert(0, str(HERE.parent.parent / "agents" / "harnesses" / "claude" / "mcp"))

#: Column-zero definitions only. An indented method needs its receiver to be
#: named unambiguously, and a probe that is ambiguous cannot distinguish "not
#: indexed" from "asked the wrong question".
_DEFINITION = re.compile(
    r"^(?:def|class|func|fn|pub fn|export function|function|message|service)"
    r"\s+([A-Za-z_][A-Za-z0-9_]{3,})"
    r"|^([a-zA-Z_][A-Za-z0-9_']{3,})\s*::"  # Haskell top-level signature
)
_SOURCE_SUFFIXES = (
    ".py", ".go", ".rs", ".ts", ".js", ".java", ".cc", ".cpp", ".hs", ".proto",
)
_NOISE = re.compile(r"(^|/)(tests?|vendor|third_party|node_modules|examples?)(/|$)|_test\.|test_")


@dataclass(frozen=True)
class Probe:
    path: str
    symbol: str
    resolved: bool
    detail: str


def clone_for(store_id: str) -> Path:
    return REPOS / store_id.rsplit("/", 1)[-1]


def candidates(clone: Path, limit: int) -> list[tuple[str, str]]:
    """(path, symbol) pairs whose definition sits at column zero in *clone*."""
    listing = subprocess.run(
        ["git", "-C", str(clone), "ls-files"], capture_output=True, text=True
    )
    if listing.returncode != 0:
        return []
    found: list[tuple[str, str]] = []
    for rel in listing.stdout.splitlines():
        if not rel.endswith(_SOURCE_SUFFIXES) or _NOISE.search(rel):
            continue
        try:
            text = (clone / rel).read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = _DEFINITION.match(line)
            if match:
                found.append((rel, match.group(1) or match.group(2)))
                break
        if len(found) >= limit:
            break
    return found


def probe_store(store_id: str, probes: int) -> dict:
    import sg_cli

    clone = clone_for(store_id)
    if not (clone / ".git").is_dir():
        return {
            "store_identifier": store_id,
            "status": "no_clone",
            "detail": f"{clone} is not a checkout; run toast_retrieval/clone_repos.py",
            "probes": [],
        }
    results: list[Probe] = []
    for path, symbol in candidates(clone, probes):
        try:
            text, is_error = sg_cli.call_tool(
                "sg_go_to_definition",
                {"repo": store_id, "path": path, "symbol": symbol},
            )
        except Exception as exc:  # transport failure is not evidence about the index
            results.append(Probe(path, symbol, False, f"{type(exc).__name__}: {exc}"))
            continue
        detail = str(text)[:200].replace("\n", " ")
        results.append(Probe(path, symbol, not is_error and bool(str(text).strip()), detail))
    if not results:
        status = "no_probe"
    elif any(p.resolved for p in results):
        status = "indexed"
    else:
        status = "unindexed"
    return {
        "store_identifier": store_id,
        "status": status,
        "probes": [p.__dict__ for p in results],
    }


def build(tasks: Path, probes: int, workers: int) -> dict:
    entries = json.loads(tasks.read_text())
    stores = sorted({s for t in entries for s in t["store_identifiers"]})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda s: probe_store(s, probes), stores))
    by_store = {r["store_identifier"]: r for r in results}
    return {
        "probes_per_store": probes,
        "stores": by_store,
        "code_intel_ineligible_tasks": ineligible(entries, by_store),
        "note": _NOTE,
    }


_NOTE = (
    "A task listed here has at least one store the code-intel arm cannot be "
    "shown to navigate: 'unindexed' means every probe came back empty, "
    "'no_probe' means this script found no column-zero definition to ask about "
    "and the index state is undetermined, 'no_clone' means the checkout is "
    "missing. All three make the code-intel run invalid rather than zero -- "
    "exclude it from that arm instead of recording the score."
)


def ineligible(entries: list[dict], by_store: dict[str, dict]) -> list[dict]:
    """Tasks the code-intel arm must not score, with the store that blocks each."""
    usable = {s for s, r in by_store.items() if r["status"] == "indexed"}
    return sorted(
        (
            {
                "task_id": t["task_id"],
                "stores": {
                    s: by_store[s]["status"]
                    for s in sorted(set(t["store_identifiers"]) - usable)
                },
            }
            for t in entries
            if not set(t["store_identifiers"]) <= usable
        ),
        key=lambda entry: entry["task_id"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=HERE / "pilot_tasks.json")
    parser.add_argument("--probes", type=int, default=3)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--check", action="store_true", help="fail if coverage changed")
    args = parser.parse_args()

    coverage = build(args.tasks, args.probes, args.workers)
    if args.check:
        if not COVERAGE.is_file():
            print(f"{COVERAGE.name} missing; run without --check", file=sys.stderr)
            return 1
        previous = json.loads(COVERAGE.read_text())
        # Only stores probed on THIS run are compared. The recorded coverage may
        # legitimately be a superset (probed over the full corpus, checked over a
        # pilot); a store it never covered is unknown, not drifted, and is
        # reported as such rather than folded into a change it is not.
        unknown = sorted(set(coverage["stores"]) - set(previous["stores"]))
        drift = sorted(
            store
            for store, record in coverage["stores"].items()
            if store in previous["stores"]
            and previous["stores"][store]["status"] != record["status"]
        )
        if unknown:
            print("no recorded coverage for: " + ", ".join(unknown), file=sys.stderr)
            return 1
        if drift:
            print("index coverage changed for: " + ", ".join(drift), file=sys.stderr)
            return 1
        print("index coverage unchanged")
        return 0

    COVERAGE.write_text(json.dumps(coverage, indent=2, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for record in coverage["stores"].values():
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    print(f"{len(coverage['stores'])} stores -> {COVERAGE.name}")
    for status, count in sorted(counts.items()):
        print(f"  {status:12s} {count}")
    blocked = coverage["code_intel_ineligible_tasks"]
    print(f"  code-intel ineligible tasks: {len(blocked)}")
    for entry in blocked:
        reasons = ", ".join(f"{s}={v}" for s, v in entry["stores"].items())
        print(f"    {entry['task_id']}: {reasons}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
