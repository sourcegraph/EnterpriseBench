"""Score Sourcegraph's own search policies on the same tasks Toast-1 ran.

The product question is whether Toast-1 belongs in our MCP server -- replacing
`code_finder`, augmenting it, or losing to `deepsearch`. That is a comparison
between *policies*: agents that take a task, decide their own queries, and
return a set of task-relevant files. Toast-1 is one such policy;
`sg_code_finder` and `sg_deepsearch` are the two Sourcegraph already ships.

All three see the same instruction.md, are scoped to the same repos, and are
scored by the same IR functions against the same ground truth extractor
run_toast.py uses, so the reports drop into one table.

Neither policy here needs Docker or a Claude account: they are MCP tools this
process can call directly, the same transport agents/harnesses/claude/mcp/sg_cli
uses in the mcp_code_finder arm.

Usage:
    export SOURCEGRAPH_ACCESS_TOKEN=...
    python3 run_mcp_policy.py --policy code_finder --outdir reports_code_finder
    python3 run_mcp_policy.py --policy deepsearch  --outdir reports_deepsearch
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agents/harnesses/claude/mcp"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env.local")

import sg_cli  # noqa: E402




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

_JSON_FENCE = re.compile(r"```json\s*(?P<body>\{.*?\})\s*```", re.DOTALL)
_PATH_FIELD = re.compile(r'"path"\s*:\s*"(?P<path>[^"]+)"')
_DEEPSEARCH_ID = re.compile(r"[0-9a-fA-F][0-9a-fA-F-]{15,}")
# Instance file links: .../r/<repo>/-/blob/<path>, optionally ?L10-20 or #frag.
_BLOB_URL = re.compile(r"/-/blob/(?P<path>[^)\s?#\]]+)")
# Inline citations: `fastapi/routing.py:227-264`, `src/google/protobuf/x.proto`.
# Requires a directory separator and an extension so backticked symbol names
# (`deserialize_seq`, `envoy.config.cluster.v3.Cluster`) cannot match.
_BACKTICK_SPAN = re.compile(r"`([^`\n]+)`")
_INLINE_PATH = re.compile(
    r"^(?P<path>(?:[\w.+-]+/)+[\w.+-]+\.[A-Za-z0-9]{1,8})(?::[\dL,\s-]+)?$"
)
_DEEPSEARCH_POLL_SECONDS = 60
_DEEPSEARCH_MAX_POLLS = 8


class PolicyFailure(Exception):
    """The policy returned no usable result. Recorded, never scored as 0.0."""


def _prompt(task_dir: Path, store_identifiers: list[str]) -> str:
    """The instruction Toast-1 saw, plus the repo scope it got structurally.

    Toast receives store_identifiers as an argument; these tools take one
    string, so the scope is appended rather than left implicit. Identical text
    goes to both policies.
    """
    instruction = (task_dir / "instruction.md").read_text()
    repos = ", ".join(store_identifiers)
    return f"{instruction}\n\nSearch only these repositories: {repos}"


def _ordered_unique(paths) -> list[str]:
    seen: dict[str, None] = {}
    for path in paths:
        if path:
            seen.setdefault(path, None)
    return list(seen)


def _parse_citations(text: str) -> tuple[list[str], list[str]]:
    """Return (the policy's answer set, every path it referenced).

    These tools do not answer in one format. Across the 20 tasks, code_finder
    cited files three different ways, and which one it picks varies per call:

      1. a ```json block with a `files` array (plus `symbols` and `chain`),
      2. markdown links to instance blobs, `/r/<repo>/-/blob/<path>?L10-L20`,
      3. bare inline code spans, `fastapi/routing.py:227-264`.

    All three are read here. Only reading (1) scored 19 of 20 tasks as zero;
    adding (2) still left 6 at zero, all of which had cited files in form (3).
    Every form is anchored on a real path token rather than a scan for
    path-shaped words, so prose naming a directory or a dotted symbol cannot
    inflate the retrieved set.
    """
    structured: list[str] = []
    for match in _JSON_FENCE.finditer(text):
        try:
            payload = json.loads(match.group("body"))
        except json.JSONDecodeError:
            continue
        for entry in payload.get("files") or []:
            if isinstance(entry, dict):
                structured.append(entry.get("path"))

    linked = [m.group("path") for m in _BLOB_URL.finditer(text)]
    inline = [
        m.group("path")
        for span in _BACKTICK_SPAN.finditer(text)
        if (m := _INLINE_PATH.match(span.group(1).strip()))
    ]
    everything = _ordered_unique(
        [m.group("path") for m in _PATH_FIELD.finditer(text)] + linked + inline
    )
    answer = _ordered_unique(structured) or _ordered_unique(linked + inline)
    return answer, everything


def _run_code_finder(prompt: str) -> tuple[str, list[str], list[str]]:
    text, is_error = sg_cli.call_tool("sg_code_finder", {"task": prompt})
    if is_error:
        raise PolicyFailure(f"sg_code_finder: {text[:200]}")
    primary, union = _parse_citations(text)
    return text, primary, union


def _run_deepsearch(prompt: str) -> tuple[str, list[str], list[str]]:
    """Deepsearch may answer inline or hand back an identifier to poll.

    Both shapes are live: the tool description documents the async polling
    contract, but the endpoint also returns finished answers directly. Try the
    launch payload first so a synchronous answer is not thrown away waiting on
    a poll that has nothing left to report.
    """
    launch, is_error = sg_cli.call_tool("sg_deepsearch", {"question": prompt})
    if is_error:
        raise PolicyFailure(f"sg_deepsearch launch: {launch[:200]}")

    primary, union = _parse_citations(launch)
    if primary:
        return launch, primary, union

    match = _DEEPSEARCH_ID.search(launch)
    if not match:
        raise PolicyFailure(f"no citations and no identifier in: {launch[:200]}")
    identifier = match.group(0)
    for _ in range(_DEEPSEARCH_MAX_POLLS):
        time.sleep(_DEEPSEARCH_POLL_SECONDS)
        text, is_error = sg_cli.call_tool(
            "sg_deepsearch_read", {"identifier": identifier}
        )
        if is_error:
            continue
        primary, union = _parse_citations(text)
        if primary:
            return text, primary, union
    raise PolicyFailure(f"deepsearch did not resolve after {_DEEPSEARCH_MAX_POLLS} polls")


_POLICIES = {"code_finder": _run_code_finder, "deepsearch": _run_deepsearch}


def run_one(candidate: dict, policy: str) -> dict:
    task_id = candidate["task_id"]
    task_dir = REPO_ROOT / candidate["path"]
    required_files = required_files_from_ground_truth(task_dir / "ground_truth.json")
    if not required_files:
        raise PolicyFailure("ground_truth.json yielded no required_files")

    started = time.time()
    raw, retrieved, union = _POLICIES[policy](
        _prompt(task_dir, candidate["store_identifiers"])
    )
    elapsed = time.time() - started

    scores = score_retrieval(retrieved, required_files, task_id, policy)
    union_scores = score_retrieval(union, required_files, task_id, f"{policy}_union")
    return {
        "task_id": task_id,
        "policy": policy,
        "suite": candidate.get("suite"),
        "repo_count": candidate.get("repo_count", len(candidate["store_identifiers"])),
        "store_identifiers": candidate["store_identifiers"],
        "retrieved_paths": retrieved,
        "retrieved_paths_union": union,
        "required_files": required_files,
        "ir_scores": scores.to_dict(),
        "scoring": SCORING_CONTRACT,
        "ir_scores_union": union_scores.to_dict(),
        "elapsed_seconds": round(elapsed, 1),
        "response_chars": len(raw),
        # Kept verbatim so a parser change is a rescore, not another sweep.
        # The first pass here read only a JSON block and scored 19 of 20 tasks
        # as zero because both policies actually cite files as blob links.
        "raw_response": raw,
    }


def rescore(outdir: Path) -> int:
    """Re-parse and re-score stored raw responses. No tool calls, no cost."""
    changed = 0
    for path in sorted(outdir.glob("*.json")):
        report = json.loads(path.read_text())
        raw = report.get("raw_response")
        if raw is None:
            print(f"{report['task_id']}: no raw_response stored, skipped", file=sys.stderr)
            continue
        retrieved, union = _parse_citations(raw)
        before = report["retrieved_paths"]
        report["retrieved_paths"] = retrieved
        report["retrieved_paths_union"] = union
        report["ir_scores"] = score_retrieval(
            retrieved, report["required_files"], report["task_id"], report["policy"]
        ).to_dict()
        report["ir_scores_union"] = score_retrieval(
            union, report["required_files"], report["task_id"], f"{report['policy']}_union"
        ).to_dict()
        report["scoring"] = SCORING_CONTRACT
        path.write_text(json.dumps(report, indent=2) + "\n")
        if before != retrieved:
            changed += 1
            print(
                f"{report['task_id']}: {len(before)} -> {len(retrieved)} files, "
                f"recall={report['ir_scores']['file_recall']:.3f}",
                file=sys.stderr,
            )
    print(f"rescored {changed} changed reports in {outdir}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=sorted(_POLICIES))
    parser.add_argument("--candidates", default="candidate_tasks.json", type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="re-parse stored raw_response payloads instead of calling the tool",
    )
    args = parser.parse_args()

    if args.rescore:
        return rescore(args.outdir)
    if not args.policy:
        parser.error("--policy is required unless --rescore is given")

    if not sg_cli._endpoint()[1]:
        print("SOURCEGRAPH_ACCESS_TOKEN is not set.", file=sys.stderr)
        return 2

    args.outdir.mkdir(parents=True, exist_ok=True)
    candidates = json.loads(args.candidates.read_text())
    pending = [
        c for c in candidates if not (args.outdir / f"{c['task_id']}.json").is_file()
    ]
    print(
        f"{len(candidates) - len(pending)} cached, {len(pending)} to run",
        file=sys.stderr,
    )

    def work(candidate: dict) -> None:
        task_id = candidate["task_id"]
        try:
            report = run_one(candidate, args.policy)
        except Exception as exc:  # one task must not void the sweep
            print(f"{task_id}: FAILED -- {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        (args.outdir / f"{task_id}.json").write_text(json.dumps(report, indent=2) + "\n")
        scores = report["ir_scores"]
        print(
            f"{task_id}: recall={scores['file_recall']:.3f} "
            f"overlap={scores['n_overlap']}/{scores['n_ground_truth']} "
            f"files={len(report['retrieved_paths'])} {report['elapsed_seconds']}s",
            file=sys.stderr,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, pending))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
