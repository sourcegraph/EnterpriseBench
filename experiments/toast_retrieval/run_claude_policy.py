"""Frontier-model arm over the exact retrieval adapter Toast-1 used.

The Toast-vs-code_finder comparison leaves one question open: was Toast-1's
0.500 the model's fault or the adapter's? This arm answers it by holding the
tools fixed and swapping the model -- a Claude model (via OpenRouter, the only
key in .env.local that serves one) drives the same `SourcegraphRetrievalClient`
seams Toast drove: `search_corpus` (nls_search with text fallback), `grep`,
file read, file list. Same instruction.md, same repo scope, same 4-round
budget Toast used on all 20 tasks, same sampling params as toast_generate.py
(temperature 0.7, top_p 0.95, max_tokens 4096).

Readout:
  - Claude-over-adapter ~= code_finder (0.758): the adapter was never the
    bottleneck; Toast-1 the model was. The "Toast with internal access"
    experiment is dead.
  - Claude-over-adapter ~= toast-1 (0.500): the access was the bottleneck;
    Toast's number was scaffold-limited and first-class access is worth
    testing.

The scaffold prompt here is new (Toast's lives inside agent_harness and is not
extractable), so the fix is minimal: a three-sentence system prompt that only
states the job, the scope, and the submit contract.

Usage:
    export SOURCEGRAPH_ACCESS_TOKEN=...     # OPENROUTER_API_KEY in .env.local
    python3 run_claude_policy.py --outdir reports_claude_adapter
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env.local")

from graphql import SourcegraphConfig  # noqa: E402
from sourcegraph_client import SourcegraphRetrievalClient  # noqa: E402




# Scored through aligned_scoring, not compute_ir_scores directly. Ground truth
# is repo-qualified for part of the corpus and repo-relative for the rest, and
# normalize_path's directory allowlist strips a real directory off the
# repo-relative side -- so a correct retrieval can score as a miss. See
# aligned_scoring's module docstring.
from aligned_scoring import SCORING_CONTRACT, score as score_retrieval
from eb_metrics_shim import import_eb_metrics  # noqa: E402

required_files_from_ground_truth = import_eb_metrics(
    "eb_metrics.retrieval_extraction"
).required_files_from_ground_truth

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# The model EB's publish campaign pins; overridable for a model-tier sweep.
_DEFAULT_MODEL = "anthropic/claude-sonnet-5"
# Toast's harness executed exactly 4 rounds on every one of the 20 reports in
# reports/. Matching it keeps the interaction budget out of the ablation.
_MAX_ROUNDS = 4
_RESULT_CHAR_CAP = 50_000
_DEFAULT_TOP_K = 25


class PolicyFailure(Exception):
    """The run produced no usable answer. Recorded, never scored as 0.0."""


# The tool surface is the adapter's capability set, one tool per seam. Names
# and descriptions are retrieval-neutral; nothing hints at ground truth.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_corpus",
            "description": (
                "Natural-language search over the in-scope repositories. "
                "Returns matching chunks with repo, path, and a text preview."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": _DEFAULT_TOP_K},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Regular-expression search over file contents of the in-scope "
                "repositories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "top_k": {"type": "integer", "default": _DEFAULT_TOP_K},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read one file's full content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "repository identifier"},
                    "path": {"type": "string", "description": "repo-relative path"},
                },
                "required": ["repo", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List file paths in one repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "limit": {"type": "integer", "default": 500},
                },
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_files",
            "description": (
                "Submit the final answer: every file required by the task, "
                "most important first. Ends the search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "repo": {"type": "string"},
                                "path": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    }
                },
                "required": ["files"],
            },
        },
    },
]


# The adapter's methods read attributes off request objects
# (mixedbread SDK types in Toast's case); these shims carry the same fields.
@dataclass(frozen=True, slots=True)
class _SearchRequest:
    store_identifiers: tuple
    query: str
    top_k: int


@dataclass(frozen=True, slots=True)
class _GrepRequest:
    store_identifiers: tuple
    pattern: str
    case_sensitive: bool
    top_k: int


@dataclass(frozen=True, slots=True)
class _FileRequest:
    store_identifier: str
    file_identifier: str
    return_chunks: object = True


@dataclass(frozen=True, slots=True)
class _ListRequest:
    store_identifier: str
    limit: int


def _serialize(payload) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > _RESULT_CHAR_CAP:
        return text[:_RESULT_CHAR_CAP] + "...[truncated]"
    return text


def _hit_view(hit: dict) -> dict:
    return {
        "repo": hit.get("store_id"),
        "path": (hit.get("metadata") or {}).get("path") or hit.get("filename"),
        "chunk_index": hit.get("chunk_index"),
        "score": hit.get("score"),
        "text": hit.get("text"),
    }


async def _dispatch(stores, store_identifiers: tuple, name: str, arguments: dict) -> str:
    """Route one tool call onto the adapter and serialize the result.

    A backend error comes back as a serialized error the model can react to,
    matching how the adapter's own search/grep seams already degrade. It must
    not abort the task: a task voided by one flaky call is a lost measurement,
    and the transcript records the error either way.
    """
    try:
        return await _dispatch_inner(stores, store_identifiers, name, arguments)
    except Exception as exc:
        return _serialize({"error": f"{type(exc).__name__}: {exc}"})


async def _dispatch_inner(
    stores, store_identifiers: tuple, name: str, arguments: dict
) -> str:
    if name == "search_corpus":
        result = await stores.search(
            _SearchRequest(
                store_identifiers=tuple(store_identifiers),
                query=arguments["query"],
                top_k=int(arguments.get("top_k") or _DEFAULT_TOP_K),
            )
        )
        view = [_hit_view(h) for h in result.get("data", [])]
        if result.get("error"):
            return _serialize({"error": result["error"], "data": view})
        return _serialize(view)

    if name == "grep":
        result = await stores.grep(
            _GrepRequest(
                store_identifiers=tuple(store_identifiers),
                pattern=arguments["pattern"],
                case_sensitive=bool(arguments.get("case_sensitive", False)),
                top_k=int(arguments.get("top_k") or _DEFAULT_TOP_K),
            )
        )
        view = [_hit_view(h) for h in result.get("data", [])]
        if result.get("error"):
            return _serialize({"error": result["error"], "data": view})
        return _serialize(view)

    if name == "read_file":
        result = await stores.files.retrieve(
            _FileRequest(
                store_identifier=arguments["repo"], file_identifier=arguments["path"]
            )
        )
        if result.get("status") == "not_found":
            return _serialize({"error": f"not found: {arguments['path']}"})
        chunks = sorted(result.get("chunks", []), key=lambda c: c["chunk_index"])
        return _serialize(
            {
                "repo": arguments["repo"],
                "path": arguments["path"],
                "content": "\n".join(c["text"] for c in chunks),
            }
        )

    if name == "list_files":
        result = await stores.files.list(
            _ListRequest(
                store_identifier=arguments["repo"],
                limit=int(arguments.get("limit") or 500),
            )
        )
        return _serialize([f["filename"] for f in result.get("data", [])])

    # A backend may add seams this router does not know (the
    # structural-vs-code-intel conditions add ast_search / definition /
    # references). It answers for the ones it owns and returns None for the
    # rest, so an actually-unknown tool still surfaces as an error here rather
    # than being silently absorbed.
    extra = getattr(stores, "dispatch_extra", None)
    if extra is not None:
        handled = await extra(name, store_identifiers, arguments)
        if handled is not None:
            return _serialize(handled)

    return _serialize({"error": f"unknown tool {name!r}"})


def _submitted_files(arguments: dict) -> list[str]:
    seen: dict[str, None] = {}
    for entry in arguments.get("files") or []:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if path:
            seen.setdefault(path, None)
    return list(seen)


def _system_prompt(store_identifiers) -> str:
    repos = ", ".join(store_identifiers)
    return (
        "You are a code retrieval policy. Given a task, find the source files "
        "required to carry it out. Search only these repositories: "
        f"{repos}. When you are confident, call submit_files with every "
        "required file, most important first, using repo-relative paths "
        "exactly as the tools returned them."
    )


async def run_agent(stores, generate, *, instruction: str, store_identifiers) -> dict:
    """Drive one task through the tool loop; return answer plus transcript."""
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt(store_identifiers)},
        {"role": "user", "content": instruction},
    ]
    input_tokens = output_tokens = 0
    for round_number in range(1, _MAX_ROUNDS + 1):
        force_submit = round_number == _MAX_ROUNDS
        response = await asyncio.to_thread(generate, messages, force_submit=force_submit)
        message = response.choices[0].message
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens += usage.prompt_tokens or 0
            output_tokens += usage.completion_tokens or 0

        tool_calls = message.tool_calls or []
        assistant: dict = {"role": "assistant", "content": message.content or ""}
        if tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
        messages.append(assistant)

        submitted: list[str] | None = None
        for call in tool_calls:
            arguments = json.loads(call.function.arguments or "{}")
            if call.function.name == "submit_files":
                submitted = _submitted_files(arguments)
                continue
            result = await _dispatch(stores, store_identifiers, call.function.name, arguments)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )
        if submitted is not None:
            return {
                "retrieved_paths": submitted,
                "rounds_executed": round_number,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "transcript": messages,
            }
        if not tool_calls:
            # A prose turn is the model declaring itself done without
            # submitting; remind it of the contract rather than ending empty.
            messages.append(
                {
                    "role": "user",
                    "content": "Answer by calling submit_files with the required files.",
                }
            )
    raise PolicyFailure(f"no submit_files after {_MAX_ROUNDS} rounds")


def build_generate_fn(model: str, *, tools: list[dict] | None = None):
    """Sync generation seam over OpenRouter; mirrors toast_generate.py exactly
    in sampling params so the ablation swaps only the model.

    `tools` lets another arm present a different tool surface (the local
    baseline drops search_corpus -- ripgrep has no semantic analog, and that
    absence is the condition's definition, not a gap).
    """
    import os

    import openai

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (checked env and .env.local).")
    client = openai.OpenAI(base_url=_OPENROUTER_BASE_URL, api_key=api_key)
    tool_surface = tools if tools is not None else TOOLS

    def generate(messages, *, force_submit: bool = False):
        tool_choice = (
            {"type": "function", "function": {"name": "submit_files"}}
            if force_submit
            else "auto"
        )
        return client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_surface,
            tool_choice=tool_choice,
            parallel_tool_calls=True,
            temperature=0.7,
            top_p=0.95,
            max_tokens=4096,
        )

    return generate


def run_one(
    candidate: dict, stores, generate, model: str, *, policy: str = "claude_adapter"
) -> dict:
    task_id = candidate["task_id"]
    task_dir = REPO_ROOT / candidate["path"]
    required_files = required_files_from_ground_truth(task_dir / "ground_truth.json")
    if not required_files:
        raise PolicyFailure("ground_truth.json yielded no required_files")

    instruction = (task_dir / "instruction.md").read_text()
    started = time.time()
    result = asyncio.run(
        run_agent(
            stores,
            generate,
            instruction=instruction,
            store_identifiers=candidate["store_identifiers"],
        )
    )
    elapsed = time.time() - started

    retrieved = result["retrieved_paths"]
    scores = score_retrieval(retrieved, required_files, task_id, policy)
    return {
        "task_id": task_id,
        "policy": policy,
        "model": model,
        "suite": candidate.get("suite"),
        "repo_count": candidate.get("repo_count", len(candidate["store_identifiers"])),
        "store_identifiers": candidate["store_identifiers"],
        "retrieved_paths": retrieved,
        "required_files": required_files,
        "ir_scores": scores.to_dict(),
        "scoring": SCORING_CONTRACT,
        "rounds_executed": result["rounds_executed"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "token_counting": "provider_reported",
        "elapsed_seconds": round(elapsed, 1),
        # Full conversation, tool results included, so any dispute about what
        # the model saw or claimed is settled by the report, not a re-run.
        "transcript": result["transcript"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="candidate_tasks.json", type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--task", default=None, help="run a single task_id")
    args = parser.parse_args()

    sg_config = SourcegraphConfig.from_env()
    if not sg_config.token:
        print("SOURCEGRAPH_ACCESS_TOKEN is not set.", file=sys.stderr)
        return 2

    stores = SourcegraphRetrievalClient(sg_config).stores
    generate = build_generate_fn(args.model)

    args.outdir.mkdir(parents=True, exist_ok=True)
    candidates = json.loads(args.candidates.read_text())
    if args.task:
        candidates = [c for c in candidates if c["task_id"] == args.task]
        if not candidates:
            print(f"{args.task!r} not in {args.candidates}", file=sys.stderr)
            return 2
    pending = [
        c for c in candidates if not (args.outdir / f"{c['task_id']}.json").is_file()
    ]
    print(
        f"{len(candidates) - len(pending)} cached, {len(pending)} to run "
        f"({args.model})",
        file=sys.stderr,
    )

    def work(candidate: dict) -> None:
        task_id = candidate["task_id"]
        try:
            report = run_one(candidate, stores, generate, args.model)
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
