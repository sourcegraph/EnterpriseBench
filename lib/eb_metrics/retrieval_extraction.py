r"""Extract an agent's retrieved-file set and a task's relevant-file set.

Two structural readers plus one compute helper, feeding
:mod:`eb_metrics.ir_metrics`:

* :func:`retrieved_files_from_trace` — parse a Claude-Code ``agent_trace.jsonl``
  and recover the ordered, first-seen-unique list of file paths the agent
  accessed (``Read``/``Grep``/``Glob`` inputs, MCP ``read_file`` inputs — whose
  repo-relative ``path`` is re-qualified with its ``repo`` so every arm's paths
  are workspace-relative and comparable — paths scraped from MCP search-result
  payloads, and — an EB extension beyond the CSB port — the files read by
  ``Bash`` commands, delegated to :mod:`eb_metrics.bash_reads`, without which
  the baseline arm's retrieval is largely unobservable).
* :func:`required_files_from_ground_truth` — read a task ``ground_truth.json``
  and return its ``required_files`` as the relevant set, each entry qualified
  with the directory its repo is cloned into so the join stays repo-aware.
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

Known limitation, an undercount:

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
import tomllib
from pathlib import Path
from typing import Any, Iterable, Mapping

from eb_metrics.bash_reads import bash_read_files
from eb_metrics.ir_metrics import (
    WORKSPACE_PREFIXES,
    IRScores,
    compute_ir_scores,
    normalize_path,
)

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


def _mcp_repo_name(repo: str) -> str:
    """MCP ``repo`` identifier *or* ``task.toml`` url → the upstream repo basename.

    ``github.com/sg-evals/requests--v2.31.0`` → ``requests``: drop the host/org
    qualifier, a ``.git`` suffix, and the ``--<rev>`` pin the eval corpus appends.
    GitHub MCP's bare ``repo`` name passes through unchanged. Returns ``""`` when
    there is nothing to prefix with.

    This is the repo's *upstream* name, which is not always the directory it is
    cloned into (``next.js`` is cloned as ``nextjs``); resolve it through
    :func:`_resolve_repo_dir` to reach the workspace directory.

    The grammar is ``scripts/infra/mirror_naming.derive_mirror_name``'s, run
    backwards: that is what mints an MCP identifier *from* a ``task.toml`` url, so
    the two must agree letter for letter or the join misses. (Which is why
    :func:`_repo_dirs` keys the url through this same function rather than a
    lookalike of it — a url and the identifier minted from it cannot then drift.)
    mirror_naming is the SSOT for the grammar; it is importable, but only via the
    ``sys.path`` hack its other callers use, and pointing ``lib/`` at ``scripts/``
    inverts the layering. EnterpriseBench-k2d8 tracks moving it under ``lib/``.
    """
    name = repo.strip().rstrip("/").rsplit("/", 1)[-1]
    return name.removesuffix(".git").split("--", 1)[0]


def _repo_dirs(task_dir: Path) -> dict[str, str]:
    """``task.toml`` → {upstream repo basename: workspace clone dir}.

    ``[[repos]]`` declares ``url`` (what an MCP ``repo`` identifier is minted
    from — so both sides key off :func:`_mcp_repo_name` of it, and cannot drift
    apart) and ``path`` (the directory the repo is cloned into under
    ``/workspace/``). The two usually coincide, but 8 repos in the corpus are
    cloned under a different name (``next.js`` → ``nextjs``, ``cli`` →
    ``docker-cli``); without this map their MCP reads and their ground truth land
    on different keys and silently score zero.

    Returns ``{}`` when there is no readable ``task.toml`` — callers then fall
    back to the upstream name, which is correct for every unaliased repo.
    """
    try:
        with (task_dir / "task.toml").open("rb") as fh:
            repos = tomllib.load(fh).get("repos", [])
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    if not isinstance(repos, list):
        return {}
    dirs = {}
    for repo in repos:
        if not isinstance(repo, dict):
            continue
        url, clone_dir = repo.get("url"), repo.get("path")
        if isinstance(url, str) and isinstance(clone_dir, str) and url and clone_dir:
            dirs[_mcp_repo_name(url)] = clone_dir
    return dirs


def _resolve_repo_dir(repo: str, repo_dirs: Mapping[str, str]) -> str:
    """Any name for a repo → the directory it is cloned into.

    Ground truth names a repo by its clone dir (usually) or by its upstream
    basename (``err-provenance-dual-docker-001`` says ``cli``, cloned as
    ``docker-cli``); an MCP identifier always carries the upstream basename. A
    name that already *is* a clone dir wins over the alias map, so an upstream
    basename colliding with another repo's clone dir cannot hijack it.
    """
    if repo in repo_dirs.values():
        return repo
    return repo_dirs.get(repo, repo)


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


def retrieved_files_from_trace(
    trace_path: Path, repo_dirs: Mapping[str, str] | None = None
) -> list[str]:
    """Parse ``agent_trace.jsonl`` → ordered, first-seen-unique file paths.

    Local tools (``Read``/``Grep``/``Glob``) and MCP ``read_file`` contribute
    their input path; MCP search tools contribute paths scraped from their
    result payloads. Dedup is on the normalized form; the original string is
    kept in first-seen order so downstream rank metrics (MRR/MAP/@k) are
    faithful.

    ``repo_dirs`` (see :func:`_repo_dirs`) resolves an MCP ``repo`` identifier
    to the directory the repo is cloned into, so an MCP read is qualified the
    same way a local ``/workspace/<dir>/`` read already is. Omit it and the
    upstream repo name is used, which is right for every unaliased repo.

    Whether an emitted path names its repo is load bearing —
    :func:`_align_retrieved_to_required` reads it back off the path. Paths from a
    *tool input* (local read, MCP ``read_file``) are emitted workspace-qualified
    and so do name it. Paths *scraped from a result payload* are emitted as the
    payload wrote them, which for MCP search results is repo-relative; qualifying
    those too needs the repo of each hit, which the scrape regexes do not capture
    (EnterpriseBench-jgq1y).
    """
    repo_dirs = repo_dirs or {}
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
            except json.JSONDecodeError:
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
                repo = tool_input.get("repo")
                repo_dir = (
                    _resolve_repo_dir(_mcp_repo_name(repo), repo_dirs)
                    if isinstance(repo, str)
                    else ""
                )
                # Emit where the file actually lives in the sandbox, so an MCP read
                # is indistinguishable from a local read of the same file — down to
                # naming, on its face, the repo it came from. The prefix goes on
                # unconditionally: a repo dir legitimately repeats as an inner dir
                # ("tonic/tonic/Cargo.toml"), so skipping it when fp already starts
                # with the repo name would corrupt exactly those.
                _add(f"/workspace/{repo_dir}/{fp.lstrip('/')}" if repo_dir else fp)

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


def _workspace_path(clone_dir: str | None, path: str) -> str:
    """``(repo, path)`` ground-truth entry → path relative to the workspace root.

    ``path`` follows one of two conventions, both live in the corpus, so qualify
    it with ``clone_dir`` only when it does not already lead with it:

    * already workspace-relative — ``repo="otel-go"``,
      ``path="otel-go/sdk/trace/x.go"``. The upstream repo (``opentelemetry-go``)
      has no ``otel-go/`` directory, so that lead can only be the clone dir.
    * repo-relative — ``repo="kubernetes"``, ``path="pkg/kubelet/worker.go"``

    Known gap: a lead that equals the repo's *own* name is ambiguous, and nothing
    outside the corpus can resolve it. ``repo="tokio"``, ``path="tokio/Cargo.toml"``
    may mean the root manifest (lead = clone dir) or the crate manifest (lead =
    the ``tokio/`` crate dir), and the tokio repo genuinely has both. Same for
    ``repo="cli"``, ``path="cli/command/x.go"``: docker/cli is cloned as
    ``docker-cli`` AND has a top-level ``cli/`` dir, so the lead reads equally as
    a stale alias qualifier or a real directory — here it is the directory, and
    prefixing (not substituting) is what makes it resolve.

    The path is therefore left as authored, and since the join suffix-matches,
    credit flows in BOTH directions for this shape: a read of the root manifest is
    credited against an entry meaning the crate manifest, and a read of the crate
    manifest against one meaning the root. Recall is unaffected either way, and
    the error is symmetric across arms, so it does not bias the arm comparison
    this join exists to protect. Only normalizing the corpus to one convention
    closes it (EnterpriseBench-y64cp).
    """
    if not clone_dir:
        return path
    return path if path.partition("/")[0] == clone_dir else f"{clone_dir}/{path}"


def _required_paths(
    entries: list[tuple[str | None, str]], repo_dirs: Mapping[str, str]
) -> list[str]:
    """``(repo, path)`` ground-truth entries → workspace-relative required paths.

    Split out so a caller that already holds the parsed entries (see
    :func:`compute_run_ir_scores`, which needs their repos too) does not have to
    re-read and re-parse ``ground_truth.json`` to get the paths as well.
    """
    return [
        _workspace_path(_resolve_repo_dir(repo, repo_dirs) if repo else repo, path)
        for repo, path in entries
    ]


def required_files_from_ground_truth(
    gt_path: Path,
    *,
    include_sufficient: bool = False,
    repo_dirs: Mapping[str, str] | None = None,
) -> list[str]:
    """Read ``ground_truth.json`` → relevant file paths (the recall target).

    Uses ``required_files`` ("MUST be found") by default; set
    ``include_sufficient`` to also fold in ``sufficient_files``. Each entry is
    resolved to a workspace-relative path via :func:`_workspace_path`, so the
    ``repo`` qualifier survives into the join instead of being dropped, and
    through ``repo_dirs`` (see :func:`_repo_dirs`) so a repo that ground truth
    names upstream (``cli``) lands on the same key as the directory it is cloned
    into (``docker-cli``). Returns ``[]`` if the file is missing, unparseable,
    or carries no required files.
    """
    entries = _ground_truth_entries(gt_path, include_sufficient=include_sufficient)
    return _required_paths(entries, repo_dirs or {})


def _path_components(path: str) -> tuple[str, ...]:
    """Canonicalize a path and split it into components for suffix matching.

    Canonicalization is :func:`~eb_metrics.ir_metrics.normalize_path`'s, not a
    lookalike of it: align and score must agree on what one path *is*, or a read
    that aligns onto a required key can still fail to score against it (a
    ``repo::path`` ground-truth entry, a ``chromium--2d05e315/`` slug, a diff
    ``a/`` lead — all of which normalize_path folds and a bare split would not).
    """
    return tuple(c for c in normalize_path(path).split("/") if c)


def _names_its_repo(path: str) -> bool:
    """True when the path is workspace-qualified, so its lead *is* its repo.

    Local ``Read`` inputs and MCP reads (which :func:`retrieved_files_from_trace`
    rewrites to ``/workspace/<dir>/...``) state their repo as fact. Anything else
    — a ``cat`` run from inside a clone, a path scraped from a search payload —
    omits it, so the repo has to be inferred. ``WORKSPACE_PREFIXES`` is the same
    tuple :func:`~eb_metrics.ir_metrics.normalize_path` strips, so this question
    cannot be answered differently here than it is there.
    """
    return path.strip().startswith(WORKSPACE_PREFIXES)


def _align_retrieved_to_required(
    retrieved: list[str], required: list[str], clone_dirs: Iterable[str] = ()
) -> list[str]:
    """Canonicalize retrieved paths that match a required path.

    A retrieved path *matches* when the required path's components are a trailing
    slice of its own — ``/workspace/zulip/zerver/x.py`` matches
    ``zulip/zerver/x.py``, ``tonic/tonic/Cargo.toml`` matches
    ``tonic/Cargo.toml``. A match is rewritten to the required path's own string
    so the exact-match metrics count it; unmatched paths keep their form
    (correctly non-relevant). Order is preserved and the result de-duplicated on
    components, for faithful rank metrics.

    Requiring the *whole* required path — repo included — to be the suffix is what
    keeps the join repo-aware: a read of ``prometheus/pkg/x.go`` does not satisfy
    a requirement for ``grafana/pkg/x.go``.

    Retrievals that do not name their repo (see :func:`_names_its_repo`) would be
    zeroed out by that rule, so they fall back to matching a required path's
    repo-relative remainder instead. ``clone_dirs`` — every directory a repo of
    this task is cloned into — is what tells the two cases apart; without it every
    path looks repo-less and wrong-repo reads get credited again.
    """
    dirs = {d.lower() for d in clone_dirs}
    req_index = [(r, _path_components(r)) for r in required]
    # Repo-relative view of each repo-qualified required path, for the fallback:
    # (required, its repo, its remainder below that repo).
    rel_index = [
        (r, comps[0], comps[1:])
        for r, comps in req_index
        if len(comps) > 1 and comps[0] in dirs
    ]

    def _match(path: str, comps: tuple[str, ...]) -> str | None:
        for req_str, req_comps in req_index:
            if req_comps and comps[-len(req_comps):] == req_comps:
                return req_str
        if _names_its_repo(path):
            return None  # it named a repo, and not the required one
        # Repo-relative fallback. The path is written from the repo root, so it
        # must equal the remainder *outright* — a trailing-slice match here would
        # let "packages/babel/package.json" satisfy a neighbouring repo's root
        # "package.json". A lead naming a DIFFERENT repo is a read of that repo,
        # and a path fitting two repos ("Cargo.toml" in a 3-crate workspace) is
        # evidence for neither, so require a unique candidate.
        hits = {
            req_str
            for req_str, req_repo, rel in rel_index
            if comps == rel and (comps[0] not in dirs or comps[0] == req_repo)
        }
        return hits.pop() if len(hits) == 1 else None

    aligned: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for path in retrieved:
        canon = _match(path, _path_components(path)) or path
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

    Both sides of the join are keyed on the repo's workspace directory, read
    from the ``task.toml`` beside ``gt_path`` (see :func:`_repo_dirs`), so a
    retrieval and a ground-truth entry naming the same repo differently still
    meet.
    """
    repo_dirs = _repo_dirs(Path(gt_path).parent)
    retrieved_raw = retrieved_files_from_trace(trace_path, repo_dirs)
    if not retrieved_raw:
        return None

    # Parsed once: the paths are the recall target and the repos name the clone
    # dirs below, and re-reading the file for the second of those would be a
    # redundant read + JSON parse on every scored pair.
    entries = _ground_truth_entries(gt_path, include_sufficient=False)
    required = _required_paths(entries, repo_dirs)
    if not required:
        return None

    # Every directory a repo of this task is cloned into: what a retrieved path
    # must lead with to count as naming its repo. Ground truth also names them,
    # so the join stays repo-aware for a task that ships no task.toml.
    clone_dirs = set(repo_dirs.values()) | {
        _resolve_repo_dir(repo, repo_dirs) for repo, _ in entries if repo
    }
    retrieved = _align_retrieved_to_required(retrieved_raw, required, clone_dirs)
    return compute_ir_scores(retrieved, required, task_id, config_name)
