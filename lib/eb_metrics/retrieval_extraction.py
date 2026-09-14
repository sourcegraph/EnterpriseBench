r"""Extract an agent's retrieved-file set and a task's relevant-file set.

Two structural readers plus one compute helper, feeding
:mod:`eb_metrics.ir_metrics`:

* :func:`retrieved_files_from_trace` — parse a Claude-Code ``agent_trace.jsonl``
  and recover the ordered, first-seen-unique list of file paths the agent
  accessed (``Read``/``Grep``/``Glob`` inputs, MCP ``read_file`` inputs, paths
  scraped from MCP search-result payloads, and — an EB extension beyond the CSB
  port — the files read by ``Bash`` commands, delegated to
  :mod:`eb_metrics.bash_reads`, without which the baseline arm's retrieval is
  largely unobservable).
* :func:`required_files_from_ground_truth` — read a task ``ground_truth.json``
  and return its ``required_files`` as the relevant set. Following CSB's
  dict-flattening, the repo qualifier is dropped and the repo-relative
  ``path`` is used (see the normalization limitation noted below).
* :func:`compute_run_ir_scores` — join the two and return :class:`IRScores`,
  or ``None`` when there is no retrieval signal to measure (missing trace,
  no file-opening tool calls, or a task with no required files). Returning
  ``None`` rather than a vacuous ``0.0``/``1.0`` keeps unobserved runs out
  of the retrieval-recall aggregate.

Shell parsing lives next door, split by what each layer decides:
:mod:`eb_metrics.shell_lex` decides syntax, :mod:`eb_metrics.bash_reads` decides
which programs and arguments count as a read.

ZFC compliance: parsing is mechanical field extraction and structural
validation — no semantic classification or learned scoring. The judgement calls
stay in code by design: :mod:`eb_metrics.bash_reads` is policy (which programs
count as a retrieval) and ``_looks_like_file`` is a shape filter. A scoring
primitive a model re-judged per call would not reproduce across rescores, and
the back-computations depend on that.

Known limitations, both undercounts:

* Inherited from CSB ``_normalize``: repos whose repo-relative paths lead with
  a non-code directory (e.g. grafana's ``public/``) can fail to match a
  ``/workspace/<repo>/`` retrieved path. See ``tests/eb_metrics/test_ir_metrics.py``.
* **The Bash floor.** Some commands do not name the files they read: ``xargs``
  takes them on stdin, a glob (``packages/*/package.json``) is expanded by the
  filesystem, ``find … -exec cat {} \;`` substitutes whatever find matched, and
  a path built around a command substitution (``cat "$(pwd)/config.py"``) is
  finished by another command's output. No lexer can resolve these from the
  command text — the file set is not *in* the text. They extract to nothing,
  which understates the Bash-mediated arms.

  The alternative is worse than the floor: erase the substitution and keep the
  leftover literal and you get ``/config.py``, a path nothing opened. An
  undercount is a known bias; a fabricated read is a wrong number that looks
  like a right one.

  The only sound recovery is to read the command's *output* from the trace
  (the paths grep printed), tracked as EnterpriseBench-jqyhg. Documented rather
  than papered over: ``test_bash_read_files_known_floor`` and
  ``test_bash_substitution_adjacent_to_text_is_not_a_file`` pin the shapes, so
  the day that recovery lands, it announces itself there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from eb_metrics.bash_reads import bash_read_files
from eb_metrics.ir_metrics import IRScores, compute_ir_scores, normalize_path

__all__ = [
    "retrieved_files_from_trace",
    "required_files_from_ground_truth",
    "resolve_ground_truth",
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


_PATH_JSON_RE = re.compile(r'"path"\s*:\s*"([^"]+)"')
_FILE_JSON_RE = re.compile(r'"file"\s*:\s*"([^"]+)"')
_GH_BLOB_RE = re.compile(r"github\.com/[^/]+/[^/]+/(?:blob|raw)/[^/]+/(.+)")
_GH_URL_IN_TEXT_RE = re.compile(
    r"github\.com/[^/]+/[^/]+/(?:blob|raw|tree)/[^/]+/([^\s\"<>]+\.\w{1,5})"
)
_BACKTICK_PATH_RE = re.compile(r"`([a-zA-Z][\w/.-]+/[\w.-]+\.\w{1,5})`")
_PATH_PREFIX_RE = re.compile(r"(?:^|\n|\\n)Path: ([^\n\\]+\.\w{1,5})")
_QUERY_PATH_RE = re.compile(r"([a-zA-Z][\w/.-]+/[\w.-]+\.\w{1,5})")


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

    def _process_tool_use(tool_name: str, tool_input: Any) -> None:
        if isinstance(tool_input, str):
            try:
                tool_input = json.loads(tool_input)
            except (ValueError, RecursionError):
                # JSONDecodeError is a ValueError subclass. A 4300-digit int
                # literal raises a bare ValueError (CPython int-str guard) and a
                # deeply-nested payload raises RecursionError; this parse runs
                # outside the per-line try/except, so an uncaught error here
                # aborts the whole trace scan. Treat both as malformed input.
                tool_input = {}
        if not isinstance(tool_input, dict):
            return
        if tool_name in ("WebFetch", "WebSearch"):
            url = tool_input.get("url", "")
            if url:
                m = _GH_BLOB_RE.search(url)
                if m:
                    _add(m.group(1))
            query = tool_input.get("query", "")
            if isinstance(query, str):
                for m in _QUERY_PATH_RE.finditer(query):
                    candidate = m.group(1)
                    if "/" in candidate and len(candidate) > 10:
                        _add(candidate)
        elif tool_name in _LOCAL_FILE_TOOLS:
            fp = tool_input.get("file_path") or tool_input.get("path") or ""
            if fp:
                _add(fp)
        elif tool_name == "Bash":
            cmd = tool_input.get("command", "")
            if isinstance(cmd, str) and cmd:
                for fp in bash_read_files(cmd):
                    _add(fp)
        elif tool_name in _MCP_READ_TOOLS:
            fp = tool_input.get("path", "")
            if fp:
                _add(fp)

    def _process_tool_result(tool_name: str, content: str) -> None:
        if tool_name in _MCP_SEARCH_TOOLS or tool_name in _MCP_READ_TOOLS:
            for m in _PATH_JSON_RE.finditer(content):
                _add(m.group(1))
            for m in _FILE_JSON_RE.finditer(content):
                _add(m.group(1))
            for m in _PATH_PREFIX_RE.finditer(content):
                _add(m.group(1).strip())
            for m in _BACKTICK_PATH_RE.finditer(content):
                _add(m.group(1))
        elif tool_name in ("WebSearch", "WebFetch"):
            for m in _GH_URL_IN_TEXT_RE.finditer(content):
                _add(m.group(1))
            for m in _BACKTICK_PATH_RE.finditer(content):
                _add(m.group(1))
        elif tool_name in ("Glob", "Grep"):
            for fline in content.splitlines():
                fline = fline.strip()
                if fline and "/" in fline and "." in fline and not fline.startswith("#"):
                    _add(fline)

    tool_id_to_name: dict[str, str] = {}

    for line in trace_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            # JSONDecodeError is a ValueError subclass. A 4300-digit int literal
            # raises a bare ValueError (CPython int-str guard) and a deeply-
            # nested line raises RecursionError; skip both as malformed input.
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
                    _process_tool_use(name, block.get("input", {}))

        elif msg_type == "user":
            message = entry.get("message", entry)
            for block in _content_blocks(message):
                if block.get("type") == "tool_result":
                    name = tool_id_to_name.get(block.get("tool_use_id", ""), "")
                    if name:
                        _process_tool_result(name, _result_text(block.get("content", "")))

        elif msg_type == "tool_use":
            name = entry.get("tool_name", "") or entry.get("name", "")
            tid = entry.get("id", "")
            if tid and name:
                tool_id_to_name[tid] = name
            _process_tool_use(name, entry.get("input", {}) or entry.get("tool_input", {}))

        elif msg_type == "tool_result":
            tid = entry.get("tool_use_id", "")
            name = (
                entry.get("tool_name", "")
                or entry.get("name", "")
                or tool_id_to_name.get(tid, "")
            )
            if name:
                raw = entry.get("content", "") or entry.get("result", "")
                _process_tool_result(name, _result_text(raw))

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
    """
    retrieved_raw = retrieved_files_from_trace(trace_path)
    if not retrieved_raw:
        return None
    required = required_files_from_ground_truth(gt_path)
    if not required:
        return None

    retrieved = _align_retrieved_to_required(retrieved_raw, required)
    return compute_ir_scores(retrieved, required, task_id, config_name)
