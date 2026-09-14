"""Run Toast-1 over curated tasks' Sourcegraph stores and IR-score the result.

Scores with lib/eb_metrics.ir_metrics and reads ground truth through
lib/eb_metrics.retrieval_extraction -- the same metric functions and the same
extractor compute_run_ir_scores uses to score a code_finder/mcp_code_finder
run from its agent_trace.jsonl. Both arms therefore read ground truth through
one code path; a second, hand-rolled parser here would confound the
comparison, and did (required_files is a list of {path,...} dicts in most of
the corpus but a list of plain strings in the schema-evolution tasks).

Usage:
    export SOURCEGRAPH_ACCESS_TOKEN=...      # MXBAI_API_KEY: env or .env.local
    export AGENT_HARNESS_REQUIRE_EXACT_TOKENIZER=0

    python3 run_toast.py --task dep-traversal-tri-openssl-001
    python3 run_toast.py --all --outdir reports/ -o sweep.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

from agent_harness import aio  # noqa: E402
from agent_harness.llm import sync_generation_as_async  # noqa: E402

from graphql import SourcegraphConfig  # noqa: E402
from sourcegraph_client import SourcegraphRetrievalClient  # noqa: E402
from toast_generate import build_generate_fn  # noqa: E402




# Scored through aligned_scoring, not compute_ir_scores directly. Ground truth
# is repo-qualified for part of the corpus and repo-relative for the rest, and
# normalize_path's directory allowlist strips a real directory off the
# repo-relative side -- so a correct retrieval can score as a miss. See
# aligned_scoring's module docstring.
from aligned_scoring import SCORING_CONTRACT, score as score_retrieval
# The same extractor compute_run_ir_scores uses to score the mcp_code_finder
# arm. Both arms must read ground truth identically or the comparison is
# confounded -- and required_files is not one shape: 4 of the 20 candidates
# (every schema-evolution-* task) store it as a list of plain strings while
# the rest use {path, repo, ...} dicts.
from eb_metrics_shim import import_eb_metrics  # noqa: E402

required_files_from_ground_truth = import_eb_metrics(
    "eb_metrics.retrieval_extraction"
).required_files_from_ground_truth


def _token_counting_mode() -> str:
    """Resolve the tokenizer up front and report which mode the run will use.

    agent_harness budgets every prompt through its token counter and, by
    default, refuses to start without an exact tokenizer. Left alone that
    raises deep inside the rollout, after Sourcegraph work is already done.
    Resolving it here fails fast, and the returned mode is recorded in the
    report so the input/output token counts are never read as exact when they
    were produced by the chars/4 estimate.
    """
    from agent_harness import config as harness_config
    from agent_harness.config import searcher_agent_config
    from agent_harness.token_counter import ensure_rollout_token_counter

    ensure_rollout_token_counter(searcher_agent_config().get("model"))
    return "exact" if harness_config.TOKEN_COUNTER is not None else "chars_per_4_estimate"


def _load_candidate(candidates_path: Path, task_id: str) -> dict:
    candidates = json.loads(candidates_path.read_text())
    for c in candidates:
        if c["task_id"] == task_id:
            return c
    raise SystemExit(f"{task_id!r} not found in {candidates_path}")


def _task_query(task_dir: Path) -> str:
    """Toast's `query` — the task's prompt, from instruction.md.

    CLAUDE.md's own warning applies here too: task.toml's [task].prompt is
    stale CSB-migration metadata; instruction.md is what an agent actually
    sees, so it is what Toast should see.
    """
    instruction = task_dir / "instruction.md"
    if instruction.is_file():
        return instruction.read_text()
    toml_path = task_dir / "task.toml"
    data = tomllib.loads(toml_path.read_text())
    return data.get("task", {}).get("prompt", "")


def _required_files(task_dir: Path) -> list[str]:
    return required_files_from_ground_truth(task_dir / "ground_truth.json")


def _retrieved_paths(chunks: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for chunk in chunks:
        path = (chunk.get("metadata") or {}).get("path") or chunk.get("filename")
        if path and path not in seen:
            seen[path] = None
    return list(seen)


class VacuousGroundTruth(Exception):
    """A task whose ground truth yields no required files.

    compute_ir_scores over an empty relevant set reports recall 1.0 and
    n_ground_truth 0 -- a number that reads as a perfect run. Raised before
    any model call, the same way compute_run_ir_scores returns None rather
    than a vacuous score.
    """


async def run_one(
    candidate: dict,
    *,
    client,
    generate,
    token_counting: str,
    usage_sink: list[dict] | None = None,
) -> dict:
    """Run one candidate through Toast-1 and return its scored report."""
    task_id = candidate["task_id"]
    task_dir = REPO_ROOT / candidate["path"]
    required_files = _required_files(task_dir)
    if not required_files:
        raise VacuousGroundTruth(task_id)

    # Tasks run sequentially, so clearing the shared sink here scopes its
    # contents to this task's model calls.
    if usage_sink is not None:
        usage_sink.clear()

    started = time.monotonic()
    result = await aio.run_fast_agentic_search(
        _task_query(task_dir),
        store_identifiers=candidate["store_identifiers"],
        client=client,
        generation_fn=generate,
    )
    elapsed = round(time.monotonic() - started, 1)

    retrieved = _retrieved_paths(result.chunks)
    scores = score_retrieval(retrieved, required_files, task_id, "toast1_sourcegraph")
    report = {
        "task_id": task_id,
        "suite": candidate.get("suite"),
        "repo_count": candidate.get("repo_count", len(candidate["store_identifiers"])),
        "store_identifiers": candidate["store_identifiers"],
        "retrieved_paths": retrieved,
        "required_files": required_files,
        "ir_scores": scores.to_dict(),
        "scoring": SCORING_CONTRACT,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "rounds_executed": result.rounds_executed,
        "token_counting": token_counting,
        "elapsed_seconds": elapsed,
    }
    if usage_sink:
        # API-reported counts from the served model itself; the harness fields
        # above stay whatever the budgeter estimated, and this block is the
        # exact ledger.
        report["api_usage"] = {
            "prompt_tokens": sum(u["prompt_tokens"] for u in usage_sink),
            "completion_tokens": sum(u["completion_tokens"] for u in usage_sink),
            "model_calls": len(usage_sink),
            "token_counting": "provider_reported",
        }
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task", help="task_id from candidate_tasks.json")
    selection.add_argument(
        "--all", action="store_true", help="sweep every task in --candidates"
    )
    parser.add_argument("--candidates", default="candidate_tasks.json", type=Path)
    parser.add_argument("-o", "--output", default=None, type=Path)
    parser.add_argument(
        "--outdir",
        default=None,
        type=Path,
        help="with --all: per-task report directory; existing reports are "
        "skipped, so an interrupted sweep resumes without re-paying for "
        "tasks it already scored",
    )
    args = parser.parse_args()

    candidates = json.loads(args.candidates.read_text())
    if args.task:
        candidates = [_load_candidate(args.candidates, args.task)]

    token_counting = _token_counting_mode()

    sg_config = SourcegraphConfig.from_env()
    if not sg_config.token:
        print("SOURCEGRAPH_ACCESS_TOKEN is not set.", file=sys.stderr)
        return 2

    client = SourcegraphRetrievalClient(sg_config)
    # build_generate_fn is a *sync* seam (openai.OpenAI) and raises loudly if
    # MXBAI_API_KEY is unset. aio.run_fast_agentic_search awaits its
    # generation_fn directly -- only the sync_api/execution_policy entry points
    # adapt sync seams -- so an unwrapped closure would TypeError on the first
    # model turn, after the call was already paid for.
    usage_sink: list[dict] = []
    generate = sync_generation_as_async(build_generate_fn(usage_sink=usage_sink))

    if args.outdir:
        args.outdir.mkdir(parents=True, exist_ok=True)

    reports: list[dict] = []
    failures: list[dict] = []
    for index, candidate in enumerate(candidates, start=1):
        task_id = candidate["task_id"]
        destination = args.outdir / f"{task_id}.json" if args.outdir else None
        if destination and destination.is_file():
            reports.append(json.loads(destination.read_text()))
            print(f"[{index}/{len(candidates)}] {task_id}: cached", file=sys.stderr)
            continue
        try:
            report = await run_one(
                candidate,
                client=client,
                generate=generate,
                token_counting=token_counting,
                usage_sink=usage_sink,
            )
        except VacuousGroundTruth:
            print(
                f"[{index}/{len(candidates)}] {task_id}: SKIPPED -- ground_truth.json "
                "yielded no required_files, so any IR score would be vacuous.",
                file=sys.stderr,
            )
            failures.append({"task_id": task_id, "reason": "vacuous_ground_truth"})
            continue
        except Exception as exc:  # one task's failure must not void the sweep
            print(
                f"[{index}/{len(candidates)}] {task_id}: FAILED -- "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            failures.append(
                {"task_id": task_id, "reason": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if destination:
            destination.write_text(json.dumps(report, indent=2) + "\n")
        reports.append(report)
        scores = report["ir_scores"]
        print(
            f"[{index}/{len(candidates)}] {task_id}: recall={scores['file_recall']:.3f} "
            f"overlap={scores['n_overlap']}/{scores['n_ground_truth']} "
            f"tokens={report['input_tokens']}",
            file=sys.stderr,
        )

    payload = reports[0] if (args.task and reports) else {
        "reports": reports,
        "failures": failures,
        "token_counting": token_counting,
    }
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
    print(text)
    return 1 if failures and not reports else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
