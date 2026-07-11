"""Extract an agent's retrieved-file set and a task's relevant-file set.

Structural readers plus compute helpers, feeding :mod:`eb_metrics.ir_metrics`:

* :func:`retrieved_files_from_trace` — parse a Claude-Code ``agent_trace.jsonl``
  and recover the ordered, first-seen-unique list of file paths the agent
  accessed (``Read``/``Grep``/``Glob`` inputs, MCP ``read_file`` inputs, paths
  scraped from MCP search-result payloads, and — an EB extension beyond the CSB
  port — file arguments to shell read-commands like ``cat``/``grep``, without
  which the baseline arm's retrieval is largely unobservable).
* :func:`required_files_from_ground_truth` — read a task ``ground_truth.json``
  and return its ``required_files`` as the relevant set. Following CSB's
  dict-flattening, the repo qualifier is dropped and the repo-relative
  ``path`` is used (see the normalization limitation noted below).
* :func:`compute_ttfr` — walk the same trace a second time (turns are cheap;
  files are small), tracking per-tool-call step index, cumulative token
  usage, and wall-clock timestamp, and report the step/tokens/seconds cost
  incurred before the first file matching ``required_files`` appears —
  turns-/tokens-to-first-relevant (jn73.1). EB's ``agent_trace.jsonl``
  entries already carry real ISO-8601 ``timestamp`` fields and per-message
  ``usage`` blocks, so this needs no CSB-style trajectory.json synthesis or
  seconds-per-step calibration.
* :func:`compute_run_ir_scores` — join trace and ground truth into
  :class:`IRScores` (recall/precision/F1/MRR/nDCG/localization@k) plus its
  TTFR fields, or ``None`` when there is no retrieval signal to measure
  (missing trace, no file-opening tool calls, or a task with no required
  files). Returning ``None`` rather than a vacuous ``0.0``/``1.0`` keeps
  unobserved runs out of the retrieval-recall aggregate.

ZFC compliance: parsing is mechanical field extraction and structural
validation (``_looks_like_file`` is a has-extension check) — no semantic
classification or learned scoring.

Known limitation (inherited from CSB ``_normalize``): a *bare*
``normalize_path`` round-trip of a ``repo::path`` ground-truth string whose
repo-relative path leads with a non-code directory (e.g. grafana's
``public/``) does not equal the bare ``normalize_path`` of the matching
``/workspace/<repo>/...`` retrieved string — see
``test_normalize_known_limitation_non_code_dir_lead`` in
``tests/eb_metrics/test_ir_metrics.py``. This module's actual join never
hits that path: :func:`required_files_from_ground_truth` already drops the
``repo::`` qualifier (EB's ``ground_truth.json`` carries ``repo`` as a
separate structured field, never a ``repo::path`` string), and
:func:`_align_retrieved_to_required` canonicalizes any retrieved path whose
*components* trail-match a required path — including non-code-dir leads —
to the required path's own string before the exact-match metrics run, so
both sides normalize identically. Documented here, not silently patched
over, because the raw ``normalize_path`` primitive itself still carries the
asymmetry for any caller that feeds it unaligned ``repo::path`` strings
directly (e.g. a future CSB-format importer).
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from eb_metrics.ir_metrics import IRScores, compute_ir_scores, normalize_path

__all__ = [
    "retrieved_files_from_trace",
    "required_files_from_ground_truth",
    "resolve_ground_truth",
    "TTFRResult",
    "compute_ttfr",
    "compute_run_ir_scores",
]


def resolve_ground_truth(
    benchmarks_dir: Path, task_id: str, suite: str | None = None
) -> Path | None:
    """Locate a task's ``ground_truth.json`` under ``benchmarks_dir``.

    Tries the canonical ``<benchmarks>/<suite>/<task_id>/`` layout first (when
    a suite is known), then falls back to a recursive search by task id (covers
    ``_archived`` and any non-standard nesting). Shared by the trace-quality
    adapter and the per-config retrieval rollup.
    """
    benchmarks_dir = Path(benchmarks_dir)
    if suite:
        direct = benchmarks_dir / suite / task_id / "ground_truth.json"
        if direct.is_file():
            return direct
    return next(benchmarks_dir.glob(f"**/{task_id}/ground_truth.json"), None)

# MCP tools that read a specific remote file (path in the tool input).
_MCP_READ_TOOLS = frozenset(
    {
        "mcp__sourcegraph__sg_read_file", "mcp__sourcegraph__read_file",
        "mcp__github__get_file_contents",
    }
)
# MCP tools that search — file paths appear in their tool_result payloads.
_MCP_SEARCH_TOOLS = frozenset(
    {
        "mcp__sourcegraph__sg_keyword_search", "mcp__sourcegraph__keyword_search",
        "mcp__sourcegraph__sg_nls_search", "mcp__sourcegraph__nls_search",
        "mcp__sourcegraph__sg_find_references", "mcp__sourcegraph__find_references",
        "mcp__sourcegraph__sg_go_to_definition", "mcp__sourcegraph__go_to_definition",
        "mcp__sourcegraph__sg_list_files", "mcp__sourcegraph__list_files",
        "mcp__sourcegraph__sg_diff_search", "mcp__sourcegraph__diff_search",
        "mcp__sourcegraph__sg_commit_search", "mcp__sourcegraph__commit_search",
        "mcp__sourcegraph__sg_compare_revisions", "mcp__sourcegraph__compare_revisions",
        "mcp__github__search_code", "mcp__github__get_repository_tree",
    }
)
_LOCAL_FILE_TOOLS = frozenset({"Read", "Grep", "Glob"})

# Shell programs that read file contents. Baseline agents open files via these
# (`cat foo.py`, `grep pat src/x.go`) rather than the structured Read tool, so
# without this the baseline arm's retrieval is largely unobservable. EB-specific
# extension beyond the CSB port (which does not parse Bash); path candidates
# still pass the shared ``_looks_like_file`` gate, so flags and search patterns
# (no extension) are filtered out.
_BASH_READ_CMDS = frozenset(
    {
        "cat", "head", "tail", "less", "more", "bat", "nl", "view", "cut",
        "column", "xxd", "od", "strings", "wc", "grep", "egrep", "fgrep",
        "rg", "ag", "sed", "awk", "diff",
    }
)
# Shell control operators that separate sub-commands.
_SHELL_SPLIT_RE = re.compile(r"\|\||&&|[|;&]")

_PATH_JSON_RE = re.compile(r'"path"\s*:\s*"([^"]+)"')
_FILE_JSON_RE = re.compile(r'"file"\s*:\s*"([^"]+)"')
_GH_BLOB_RE = re.compile(r"github\.com/[^/]+/[^/]+/(?:blob|raw)/[^/]+/(.+)")
_GH_URL_IN_TEXT_RE = re.compile(
    r"github\.com/[^/]+/[^/]+/(?:blob|raw|tree)/[^/]+/([^\s\"<>]+\.\w{1,5})"
)
_BACKTICK_PATH_RE = re.compile(r"`([a-zA-Z][\w/.-]+/[\w.-]+\.\w{1,5})`")
_PATH_PREFIX_RE = re.compile(r"(?:^|\n|\\n)Path: ([^\n\\]+\.\w{1,5})")
_QUERY_PATH_RE = re.compile(r"([a-zA-Z][\w/.-]+/[\w.-]+\.\w{1,5})")


def _bash_read_files(command: str) -> list[str]:
    """Extract file arguments from shell read-commands in ``command``.

    Splits on shell control operators, and for each sub-command whose program
    is in :data:`_BASH_READ_CMDS`, yields its non-flag tokens. Callers gate
    each token through ``_looks_like_file`` (via ``_add``), so search patterns
    and options are dropped. Conservative by construction — a sub-command not
    led by a known read program contributes nothing (a script being *run* is
    not a retrieval).
    """
    files: list[str] = []
    for sub in _SHELL_SPLIT_RE.split(command):
        sub = sub.strip()
        if not sub:
            continue
        try:
            toks = shlex.split(sub)
        except ValueError:
            toks = sub.split()
        if not toks:
            continue
        prog = toks[0].rsplit("/", 1)[-1]
        if prog not in _BASH_READ_CMDS:
            continue
        for tok in toks[1:]:
            if tok.startswith("-"):
                continue
            files.append(tok)
    return files


def _looks_like_file(path: str) -> bool:
    """Heuristic: does the normalized path look like a real file path?

    Verbatim port of CSB ``ir_metrics._looks_like_file``: requires an
    extension, rejects URLs, grep-style ``:line:`` lines, code-snippet
    characters, and anything over 200 chars.
    """
    if not path or len(path) < 2:
        return False
    basename = path.rsplit("/", 1)[-1] if "/" in path else path
    if "." not in basename:
        return False
    if path.startswith(("http:", "https:", "ftp:")):
        return False
    if re.search(r"-\d+-", path) or re.search(r":\d+:", path):
        return False
    if any(c in path for c in ("(", ")", "{", "}", "=", ";", "#", "\\", "  ")):
        return False
    if len(path) > 200:
        return False
    return True


def _extract_paths_from_tool_use(tool_name: str, tool_input: Any) -> list[str]:
    """Candidate raw file paths surfaced by a tool call's *input*.

    Pure extraction — no dedup, no ``_looks_like_file`` gate — shared by
    :func:`retrieved_files_from_trace` (which gates+dedups via its own
    ``_add``) and :func:`compute_ttfr` (which gates per-candidate via
    :func:`_looks_like_file` directly, no dedup needed for a first-match scan).
    """
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except json.JSONDecodeError:
            tool_input = {}
    if not isinstance(tool_input, dict):
        return []

    paths: list[str] = []
    if tool_name in ("WebFetch", "WebSearch"):
        url = tool_input.get("url", "")
        if url:
            m = _GH_BLOB_RE.search(url)
            if m:
                paths.append(m.group(1))
        query = tool_input.get("query", "")
        if isinstance(query, str):
            for m in _QUERY_PATH_RE.finditer(query):
                candidate = m.group(1)
                if "/" in candidate and len(candidate) > 10:
                    paths.append(candidate)
    elif tool_name in _LOCAL_FILE_TOOLS:
        fp = tool_input.get("file_path") or tool_input.get("path") or ""
        if fp:
            paths.append(fp)
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if isinstance(cmd, str) and cmd:
            paths.extend(_bash_read_files(cmd))
    elif tool_name in _MCP_READ_TOOLS:
        fp = tool_input.get("path", "")
        if fp:
            paths.append(fp)
    return paths


def _extract_paths_from_tool_result(tool_name: str, content: str) -> list[str]:
    """Candidate raw file paths scraped from a tool call's *result* payload.

    Pure extraction counterpart to :func:`_extract_paths_from_tool_use` — see
    its docstring for the shared-by-two-callers rationale.
    """
    paths: list[str] = []
    if tool_name in _MCP_SEARCH_TOOLS or tool_name in _MCP_READ_TOOLS:
        for m in _PATH_JSON_RE.finditer(content):
            paths.append(m.group(1))
        for m in _FILE_JSON_RE.finditer(content):
            paths.append(m.group(1))
        for m in _PATH_PREFIX_RE.finditer(content):
            paths.append(m.group(1).strip())
        for m in _BACKTICK_PATH_RE.finditer(content):
            paths.append(m.group(1))
    elif tool_name in ("WebSearch", "WebFetch"):
        for m in _GH_URL_IN_TEXT_RE.finditer(content):
            paths.append(m.group(1))
        for m in _BACKTICK_PATH_RE.finditer(content):
            paths.append(m.group(1))
    elif tool_name in ("Glob", "Grep"):
        for fline in content.splitlines():
            fline = fline.strip()
            if fline and "/" in fline and "." in fline and not fline.startswith("#"):
                paths.append(fline)
    return paths


def retrieved_files_from_trace(trace_path: Path) -> list[str]:
    """Parse ``agent_trace.jsonl`` → ordered, first-seen-unique file paths.

    Local tools (``Read``/``Grep``/``Glob``) and MCP ``read_file`` contribute
    their input path; MCP search tools contribute paths scraped from their
    result payloads. Dedup is on the normalized form; the original string is
    kept in first-seen order so downstream rank metrics (MRR/MAP/@k) are
    faithful.
    """
    trace_path = Path(trace_path)
    if not trace_path.is_file():
        return []

    files: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        norm = normalize_path(path)
        if norm and norm not in seen and _looks_like_file(norm):
            seen.add(norm)
            files.append(path.strip())

    tool_id_to_name: dict[str, str] = {}

    for line in trace_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        msg_type = entry.get("type", "")

        if msg_type == "assistant":
            message = entry.get("message", entry)
            for block in _content_blocks(message):
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    tid = block.get("id", "")
                    if tid and name:
                        tool_id_to_name[tid] = name
                    for fp in _extract_paths_from_tool_use(name, block.get("input", {})):
                        _add(fp)

        elif msg_type == "user":
            message = entry.get("message", entry)
            for block in _content_blocks(message):
                if block.get("type") == "tool_result":
                    name = tool_id_to_name.get(block.get("tool_use_id", ""), "")
                    if name:
                        for fp in _extract_paths_from_tool_result(
                            name, _result_text(block.get("content", ""))
                        ):
                            _add(fp)

        elif msg_type == "tool_use":
            name = entry.get("tool_name", "") or entry.get("name", "")
            tid = entry.get("id", "")
            if tid and name:
                tool_id_to_name[tid] = name
            for fp in _extract_paths_from_tool_use(
                name, entry.get("input", {}) or entry.get("tool_input", {})
            ):
                _add(fp)

        elif msg_type == "tool_result":
            tid = entry.get("tool_use_id", "")
            name = (
                entry.get("tool_name", "")
                or entry.get("name", "")
                or tool_id_to_name.get(tid, "")
            )
            if name:
                raw = entry.get("content", "") or entry.get("result", "")
                for fp in _extract_paths_from_tool_result(name, _result_text(raw)):
                    _add(fp)

    return files


def _content_blocks(message: Any) -> list[dict[str, Any]]:
    if not isinstance(message, Mapping):
        return []
    blocks = message.get("content", [])
    if not isinstance(blocks, list):
        return []
    return [b for b in blocks if isinstance(b, dict)]


def _result_text(raw: Any) -> str:
    if isinstance(raw, list):
        return " ".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in raw
        )
    return raw if isinstance(raw, str) else str(raw)


def _ground_truth_entries(
    gt_path: Path,
    *,
    include_sufficient: bool,
) -> list[tuple[str | None, str]]:
    """Read ``ground_truth.json`` → list of ``(repo, path)`` relevant entries.

    ``repo`` is ``None`` when the entry (or a bare-string entry) carries no
    repo qualifier. Returns ``[]`` on missing/unparseable files.
    """
    gt_path = Path(gt_path)
    if not gt_path.is_file():
        return []
    try:
        payload = json.loads(gt_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, Mapping):
        return []

    block = payload.get("ground_truth", payload)
    if not isinstance(block, Mapping):
        block = payload

    keys = ["required_files"]
    if include_sufficient:
        keys.append("sufficient_files")

    entries: list[tuple[str | None, str]] = []
    for key in keys:
        raw = block.get(key)
        if not isinstance(raw, list):
            continue
        for entry in raw:
            repo: str | None = None
            if isinstance(entry, Mapping):
                path = entry.get("path") or entry.get("file")
                repo_val = entry.get("repo")
                repo = repo_val if isinstance(repo_val, str) and repo_val else None
            elif isinstance(entry, str):
                path = entry
            else:
                path = None
            if isinstance(path, str) and path.strip():
                entries.append((repo, path.strip()))
    return entries


def required_files_from_ground_truth(
    gt_path: Path,
    *,
    include_sufficient: bool = False,
) -> list[str]:
    """Read ``ground_truth.json`` → relevant file paths (the recall target).

    Uses ``required_files`` ("MUST be found") by default; set
    ``include_sufficient`` to also fold in ``sufficient_files``. Following
    CSB's dict-flattening, each entry contributes its repo-relative ``path``
    (the ``repo`` qualifier is dropped). Returns ``[]`` if the file is
    missing, unparseable, or carries no required files.
    """
    return [path for _, path in _ground_truth_entries(gt_path, include_sufficient=include_sufficient)]


def _path_components(path: str) -> tuple[str, ...]:
    """Split a path into lowercased components, stripping any container prefix.

    ``/workspace/`` (and a bare ``workspace/``) is dropped; the rest is
    lowercased and split on ``/`` with empties removed. Used for
    component-suffix matching between retrieved paths and ground truth.
    """
    p = path.strip().lower()
    for prefix in ("/workspace/", "workspace/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    return tuple(c for c in p.strip("/").split("/") if c)


def _align_retrieved_to_required(
    retrieved: list[str], required: list[str]
) -> list[str]:
    """Canonicalize retrieved paths that suffix-match a required path.

    A retrieved path *matches* a required (repo-relative) path when the
    required path's components are a trailing slice of the retrieved path's
    components — so ``/workspace/zulip/zerver/actions/x.py`` matches
    ``zerver/actions/x.py`` and ``gcc/gcc/passes.def`` matches
    ``gcc/passes.def``, uniformly across local ``/workspace/<repo>/`` reads,
    repo-relative MCP reads, and repos whose name collides with an inner
    directory. A matched retrieved path is rewritten to the required path's
    own string so the ported exact-match metrics count it; unmatched paths
    keep their form (correctly non-relevant). Order is preserved and the
    result is de-duplicated on components for faithful rank metrics.
    """
    req_index = [(r, _path_components(r)) for r in required]
    aligned: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for path in retrieved:
        comps = _path_components(path)
        canon = path
        for req_str, req_comps in req_index:
            n = len(req_comps)
            if n and len(comps) >= n and comps[-n:] == req_comps:
                canon = req_str
                break
        key = _path_components(canon)
        if key and key not in seen:
            seen.add(key)
            aligned.append(canon)
    return aligned


def _path_matches_required(
    path: str, required_norm: set[str], required_comps: list[tuple[str, ...]]
) -> bool:
    """Does ``path`` count as a hit against the required set?

    Mirrors exactly the two ways a retrieved path ends up counted as
    relevant downstream: direct ``normalize_path`` equality (the ported IR
    math's own equality test), or the component-suffix match
    :func:`_align_retrieved_to_required` performs before that (so TTFR's
    notion of "found" never disagrees with recall's).
    """
    if normalize_path(path) in required_norm:
        return True
    comps = _path_components(path)
    for req_comps in required_comps:
        n = len(req_comps)
        if n and len(comps) >= n and comps[-n:] == req_comps:
            return True
    return False


def _parse_timestamp(value: Any) -> float | None:
    """Parse an ISO-8601 ``timestamp`` string (as EB's ``agent_trace.jsonl``
    entries carry) to a Unix epoch float. ``None`` on anything unparseable.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _usage_tokens(usage: Any) -> int | None:
    """Sum the four Anthropic usage-token fields on one assistant message.

    ``None`` (not ``0``) when ``usage`` carries none of the four fields, so
    a trace with no usage telemetry at all doesn't silently masquerade as
    "zero tokens used."
    """
    if not isinstance(usage, Mapping):
        return None
    fields = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    if not any(f in usage for f in fields):
        return None
    total = 0
    for f in fields:
        v = usage.get(f, 0)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += v
    return total


def _usage_request_key(entry: Mapping[str, Any]) -> str | None:
    """The per-API-call grouping key for token dedup, when the trace has one.

    EB's Claude-Code ``agent_trace.jsonl`` logs one top-level JSONL line per
    *content block* (``thinking``, ``text``, ``tool_use`` — and a single
    turn can even emit several ``tool_use`` blocks back to back before the
    next user turn), not one line per API call. Every line belonging to the
    same API call carries the identical (non-incremental) ``usage``
    snapshot and the same ``requestId``. Summing ``usage`` once per *line*
    therefore overcounts tokens by however many content-block lines (and
    tool_use blocks) share that call — 2-3x was observed on real EB traces.
    Returns ``None`` when the entry carries no ``requestId`` (e.g. a
    synthetic test trace, or a transcript format that doesn't emit one), in
    which case the caller falls back to counting every line once.
    """
    rid = entry.get("requestId")
    return rid if isinstance(rid, str) and rid else None


@dataclass(frozen=True)
class TTFRResult:
    """Turns-/tokens-/wall-time-to-first-relevant for one run's trace.

    ``coverage`` always explains the result — never a bare ``None`` with no
    story attached (the #1 risk directive: report coverage, don't go
    silently null):

    * ``"ok"``             — a relevant file was found; the three numeric
      fields are populated (``tokens_to_first_relevant`` may still be
      ``None`` if the trace carries no ``usage`` telemetry at all).
    * ``"not_found"``       — a genuine miss: the agent made tool calls but
      never opened a required file. Distinct from an observability gap.
    * ``"no_trace"``        — the trace file is missing/unreadable.
    * ``"no_ground_truth"`` — the task declares no required files.
    * ``"no_tool_calls"``   — the trace has zero tool-use steps to measure.
    """

    turns_to_first_relevant: int | None
    tokens_to_first_relevant: int | None
    ttfr_seconds: float | None
    coverage: str


def compute_ttfr(trace_path: Path, required_files: list[str]) -> TTFRResult:
    """Turns/tokens/seconds spent before the first required file is opened.

    Walks ``agent_trace.jsonl`` once in file order, counting each ``tool_use``
    block as one turn/step (1-based), accumulating token usage from each
    assistant message's ``usage`` block, and using the entry's own
    ``timestamp`` for wall-clock — no CSB-style trajectory.json synthesis or
    seconds-per-step calibration needed, since EB's trace already carries
    both natively. A step's candidate files are the union of its tool_use
    *input* paths (known immediately) and its paired tool_result's scraped
    paths (known once the result arrives); both pass through the same
    :func:`_looks_like_file` gate used elsewhere so junk (flags, grep
    patterns) can't spuriously register a hit.

    Always returns a :class:`TTFRResult` — never a bare ``None`` — so the
    caller can tell an observability gap (``"no_trace"``, ``"no_tool_calls"``)
    apart from a genuine miss (``"not_found"``) apart from success (``"ok"``).
    """
    trace_path = Path(trace_path)
    if not trace_path.is_file():
        return TTFRResult(None, None, None, "no_trace")
    if not required_files:
        return TTFRResult(None, None, None, "no_ground_truth")

    required_norm = {normalize_path(f) for f in required_files}
    required_comps = [_path_components(f) for f in required_files]

    t0: float | None = None
    tokens_cumulative = 0
    any_usage_seen = False
    step_index = 0
    tool_id_to_name: dict[str, str] = {}
    # step position (index into `steps`) keyed by tool_use_id, so a later
    # tool_result can attach its scraped paths to the step it belongs to.
    tid_to_pos: dict[str, int] = {}
    # requestId values already counted toward tokens_cumulative — see
    # _usage_request_key: one API call can log several assistant-type
    # lines (thinking/text/tool_use, even several tool_use blocks) sharing
    # one usage snapshot, so each requestId's tokens count once, not once
    # per line.
    seen_request_ids: set[str] = set()
    # Each element: [step_index, timestamp|None, tokens_cumulative|None, [raw paths]]
    steps: list[list[Any]] = []

    for line in trace_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        ts = _parse_timestamp(entry.get("timestamp"))
        if t0 is None and ts is not None:
            t0 = ts

        msg_type = entry.get("type", "")

        if msg_type == "assistant":
            message = entry.get("message", entry)
            usage_tokens = _usage_tokens(message.get("usage") if isinstance(message, Mapping) else None)
            if usage_tokens is not None:
                request_key = _usage_request_key(entry)
                already_counted = request_key is not None and request_key in seen_request_ids
                if not already_counted:
                    any_usage_seen = True
                    tokens_cumulative += usage_tokens
                    if request_key is not None:
                        seen_request_ids.add(request_key)

            for block in _content_blocks(message):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                tid = block.get("id", "")
                step_index += 1
                if tid and name:
                    tool_id_to_name[tid] = name
                raw_paths = [
                    p
                    for p in _extract_paths_from_tool_use(name, block.get("input", {}))
                    if _looks_like_file(normalize_path(p))
                ]
                steps.append([step_index, ts, tokens_cumulative, raw_paths])
                if tid:
                    tid_to_pos[tid] = len(steps) - 1

        elif msg_type == "user":
            message = entry.get("message", entry)
            for block in _content_blocks(message):
                if block.get("type") != "tool_result":
                    continue
                tid = block.get("tool_use_id", "")
                name = tool_id_to_name.get(tid, "")
                if not name:
                    continue
                raw_paths = [
                    p
                    for p in _extract_paths_from_tool_result(name, _result_text(block.get("content", "")))
                    if _looks_like_file(normalize_path(p))
                ]
                pos = tid_to_pos.get(tid)
                if pos is not None and raw_paths:
                    steps[pos][3].extend(raw_paths)

        elif msg_type == "tool_use":
            # Flat (non-Harbor) transcript shape — mirrors the equivalent
            # branch in retrieved_files_from_trace so a trace shape that
            # yields retrieval signal there isn't a silent TTFR blind spot.
            name = entry.get("tool_name", "") or entry.get("name", "")
            tid = entry.get("id", "")
            step_index += 1
            if tid and name:
                tool_id_to_name[tid] = name
            raw_paths = [
                p
                for p in _extract_paths_from_tool_use(
                    name, entry.get("input", {}) or entry.get("tool_input", {})
                )
                if _looks_like_file(normalize_path(p))
            ]
            steps.append([step_index, ts, tokens_cumulative, raw_paths])
            if tid:
                tid_to_pos[tid] = len(steps) - 1

        elif msg_type == "tool_result":
            tid = entry.get("tool_use_id", "")
            name = (
                entry.get("tool_name", "")
                or entry.get("name", "")
                or tool_id_to_name.get(tid, "")
            )
            if not name:
                continue
            raw = entry.get("content", "") or entry.get("result", "")
            raw_paths = [
                p
                for p in _extract_paths_from_tool_result(name, _result_text(raw))
                if _looks_like_file(normalize_path(p))
            ]
            pos = tid_to_pos.get(tid)
            if pos is not None and raw_paths:
                steps[pos][3].extend(raw_paths)

    if step_index == 0:
        return TTFRResult(None, None, None, "no_tool_calls")

    for s_index, s_ts, s_tokens, s_paths in steps:
        if any(_path_matches_required(p, required_norm, required_comps) for p in s_paths):
            ttfr_seconds = round(s_ts - t0, 3) if (s_ts is not None and t0 is not None) else None
            tokens_val = s_tokens if any_usage_seen else None
            return TTFRResult(s_index, tokens_val, ttfr_seconds, "ok")

    return TTFRResult(None, None, None, "not_found")


def compute_run_ir_scores(
    trace_path: Path,
    gt_path: Path,
    task_id: str,
    config_name: str,
) -> IRScores | None:
    """Join a run's trace and a task's ground truth into :class:`IRScores`.

    Returns ``None`` — not a vacuous score — when there is no retrieval
    signal to measure: the trace is missing, the agent made no file-opening
    tool calls, or the task declares no required files. A genuine "opened
    files but none relevant" run yields real ``0.0`` recall and is reported.

    TTFR fields (``turns_to_first_relevant``, ``tokens_to_first_relevant``,
    ``ttfr_seconds``, ``ttfr_coverage``) are filled from :func:`compute_ttfr`
    on the same trace/ground-truth pair, so a caller who only wants recall
    still gets TTFR for free.
    """
    retrieved_raw = retrieved_files_from_trace(trace_path)
    if not retrieved_raw:
        return None
    required = required_files_from_ground_truth(gt_path)
    if not required:
        return None

    retrieved = _align_retrieved_to_required(retrieved_raw, required)
    scores = compute_ir_scores(retrieved, required, task_id, config_name)
    ttfr = compute_ttfr(trace_path, required)
    return replace(
        scores,
        turns_to_first_relevant=ttfr.turns_to_first_relevant,
        tokens_to_first_relevant=ttfr.tokens_to_first_relevant,
        ttfr_seconds=ttfr.ttfr_seconds,
        ttfr_coverage=ttfr.coverage,
    )
