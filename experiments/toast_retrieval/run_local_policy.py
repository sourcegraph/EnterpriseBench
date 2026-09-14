"""Local-baseline arm: the same model and round budget, no search engine.

Condition 1 of the five-condition design: a frontier model given only what a
stock coding agent has on a checked-out workspace -- regex grep, file reads,
file listings over local clones (clone_repos.py materializes them). No
`search_corpus`: ripgrep has no semantic analog, and that absence is the
condition's definition, not a gap. Everything else is held identical to
run_claude_policy.py -- same model via OpenRouter, same 4-round budget, same
sampling, same system prompt, same scoring -- so the delta against the
adapter arm reads as "what does a search engine buy the same policy".

grep runs through `git grep -nE` per repo (tracked files only, which is what
Sourcegraph indexes too); reads and listings come straight off disk.

Usage:
    python3 clone_repos.py                       # once
    python3 run_local_policy.py --outdir reports_claude_local
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run_claude_policy import (  # noqa: E402  (loads .env.local on import)
    _DEFAULT_MODEL,
    _DEFAULT_TOP_K,
    TOOLS,
    build_generate_fn,
    run_one,
)

HERE = Path(__file__).resolve().parent
REPOS_DIR = HERE / "repos"

# Everything but search_corpus. submit_files, grep, read_file, list_files
# keep their exact adapter-arm schemas so the only varied factor is access.
LOCAL_TOOLS = [t for t in TOOLS if t["function"]["name"] != "search_corpus"]

_GREP_TIMEOUT_SECONDS = 60


def _repo_dir(store_id: str) -> Path:
    return REPOS_DIR / store_id.rsplit("/", 1)[-1]


class _LocalFiles:
    async def retrieve(self, request):
        directory = _repo_dir(request.store_identifier)
        target = directory / request.file_identifier
        if not directory.is_dir() or not target.is_file():
            return {"status": "not_found"}
        text = await asyncio.to_thread(
            target.read_text, encoding="utf-8", errors="replace"
        )
        return {"chunks": [{"chunk_index": 0, "text": text}]}

    async def list(self, request):
        directory = _repo_dir(request.store_identifier)
        if not directory.is_dir():
            return {"data": [], "error": f"no local clone for {request.store_identifier}"}

        def _walk() -> list[str]:
            result = subprocess.run(
                ["git", "-C", str(directory), "ls-files"],
                capture_output=True,
                text=True,
                timeout=_GREP_TIMEOUT_SECONDS,
            )
            return result.stdout.splitlines()

        paths = await asyncio.to_thread(_walk)
        return {"data": [{"filename": p} for p in paths[: request.limit]]}


class LocalStores:
    """Filesystem backend with the adapter's surface, minus semantic search."""

    files = _LocalFiles()

    async def search(self, request):
        return {
            "data": [],
            "error": "search_corpus is not available in the local baseline",
        }

    async def grep(self, request):
        hits: list[dict] = []
        errors: list[str] = []
        for store_id in request.store_identifiers:
            if len(hits) >= request.top_k:
                break
            directory = _repo_dir(store_id)
            if not directory.is_dir():
                errors.append(f"no local clone for {store_id}")
                continue
            command = ["git", "-C", str(directory), "grep", "-n", "-E"]
            if not request.case_sensitive:
                command.append("-i")
            command += ["-e", request.pattern, "--"]

            def _run(cmd=command) -> subprocess.CompletedProcess:
                return subprocess.run(
                    cmd, capture_output=True, text=True, timeout=_GREP_TIMEOUT_SECONDS
                )

            result = await asyncio.to_thread(_run)
            # git grep: 0 = matches, 1 = none, anything else = real error.
            if result.returncode not in (0, 1):
                errors.append(f"{store_id}: {result.stderr.strip()[:200]}")
                continue
            for line in result.stdout.splitlines():
                path, _, rest = line.partition(":")
                line_number, _, text = rest.partition(":")
                if not line_number.isdigit():
                    continue
                hits.append(
                    {
                        "store_id": store_id,
                        "filename": path,
                        "metadata": {"path": path},
                        "chunk_index": (int(line_number) - 1) // 60,
                        "score": None,
                        "text": text[:300],
                    }
                )
                if len(hits) >= request.top_k:
                    break
        response: dict = {"data": hits}
        if errors:
            response["error"] = "; ".join(errors)
        return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="candidate_tasks.json", type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--task", default=None, help="run a single task_id")
    args = parser.parse_args()

    candidates = json.loads(args.candidates.read_text())
    if args.task:
        candidates = [c for c in candidates if c["task_id"] == args.task]
        if not candidates:
            print(f"{args.task!r} not in {args.candidates}", file=sys.stderr)
            return 2

    missing = {
        s
        for c in candidates
        for s in c["store_identifiers"]
        if not _repo_dir(s).is_dir()
    }
    if missing:
        print(
            "missing local clones (run clone_repos.py first):\n  "
            + "\n  ".join(sorted(missing)),
            file=sys.stderr,
        )
        return 2

    stores = LocalStores()
    generate = build_generate_fn(args.model, tools=LOCAL_TOOLS)

    args.outdir.mkdir(parents=True, exist_ok=True)
    pending = [
        c for c in candidates if not (args.outdir / f"{c['task_id']}.json").is_file()
    ]
    print(
        f"{len(candidates) - len(pending)} cached, {len(pending)} to run "
        f"({args.model}, local)",
        file=sys.stderr,
    )

    def work(candidate: dict) -> None:
        task_id = candidate["task_id"]
        try:
            report = run_one(
                candidate, stores, generate, args.model, policy="claude_local"
            )
        except Exception as exc:  # one task must not void the sweep
            print(f"{task_id}: FAILED -- {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        (args.outdir / f"{task_id}.json").write_text(json.dumps(report, indent=2) + "\n")
        scores = report["ir_scores"]
        print(
            f"{task_id}: recall={scores['file_recall']:.3f} "
            f"overlap={scores['n_overlap']}/{scores['n_ground_truth']} "
            f"files={len(report['retrieved_paths'])} rounds={report['rounds_executed']} "
            f"{report['elapsed_seconds']}s",
            file=sys.stderr,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, pending))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
