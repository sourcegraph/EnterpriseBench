"""Retrieval backends for the structural-vs-code-intel conditions.

Three retrieval primitives, deliberately kept distinct because they know
different things:

* **lexical** -- ``git grep -nE`` over local clones. Understands text.
* **structural** -- ``ast-grep`` pattern match over local clones. Understands
  syntax: node shape and metavariables. It resolves no symbol identity, no
  types, and no reference edges, and nothing here should be described as
  semantic code intelligence.
* **code intel** -- Sourcegraph's ``sg_go_to_definition`` /
  ``sg_find_references`` over the ``sg-evals`` mirrors of the same pinned
  revisions. It is the only primitive that *can* know resolved symbol
  identity, and on part of the corpus it does not: the instance serves the
  same two calls from a semantic SCIP index where one exists and from
  ``scip-syntax`` (tree-sitter, name shape only) where one does not, and the
  MCP seam returns no provenance either way. ``index_tier.py`` records which
  tier each store carries so a result can be re-cut by it; on the pilot's
  stores it is 17 semantic and 10 syntax-only. Describing this arm's answers
  as semantic without that cut would claim a mechanism it did not always have.

Reading and listing are held CONSTANT across every condition (local clones,
identical code path), so a condition's delta is attributable to its search or
navigation primitive rather than to how it reads files. That is also why
``grep`` here is the same ``git grep`` implementation
``toast_retrieval.run_local_policy`` already uses rather than a second one.

The substrate confound is real and is not hidden: lexical and structural read
disk, code intel reads the index. ``configs/repo_versions.json`` pins the
clones to the revisions the mirrors index, which bounds it. See
IMPLEMENTATION_NOTE.md.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOAST_DIR = HERE.parent / "toast_retrieval"
REPO_ROOT = HERE.parents[1]

sys.path.insert(0, str(TOAST_DIR))

from run_local_policy import LocalStores, _repo_dir  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "agents" / "harnesses" / "claude" / "mcp"))
import sg_cli  # noqa: E402

_TIMEOUT_SECONDS = 90
_TEXT_CAP = 300
_MAX_INFERRED_LANGUAGES = 3

#: ast-grep language names by source extension. Only languages ast-grep ships
#: are listed; an unlisted extension simply never becomes an inferred language,
#: which is a coverage limit to report, not an error to swallow.
_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".c": "c",
    # .h is ambiguous, and in this corpus it is overwhelmingly C++ (Envoy and
    # Ceph headers). ast-grep's cpp grammar parses C headers too, so guessing
    # cpp costs nothing on a genuine C header and avoids silently parsing a
    # C++ header as C, which would make the structural arm blind to most of
    # two of the largest repos in the slice.
    ".h": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".php": "php",
    ".scala": "scala",
    ".swift": "swift",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
}


class BackendUnavailable(RuntimeError):
    """A condition asked for a primitive whose tooling is not installed."""


def ast_grep_binary() -> str:
    """Path to the ast-grep executable, or raise with what to install.

    Checked at condition-construction time rather than at first tool call: a
    sweep that silently reports "no matches" for every structural query would
    be indistinguishable from a real finding.
    """
    found = shutil.which("ast-grep", path=str(TOAST_DIR / ".venv" / "bin"))
    if found:
        return found
    found = shutil.which("ast-grep")
    if found:
        return found
    raise BackendUnavailable(
        "ast-grep not found (pip install ast-grep-cli into "
        f"{TOAST_DIR / '.venv'}); the structural conditions cannot run."
    )


@lru_cache(maxsize=256)
def repo_languages(directory: str) -> tuple[str, ...]:
    """ast-grep language names present in *directory*, most files first.

    Tracked files only, matching what ``git grep`` and Sourcegraph both see, so
    a vendored or ignored tree does not decide the language for the repo.
    """
    result = subprocess.run(
        ["git", "-C", directory, "ls-files"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )
    counts: Counter[str] = Counter()
    for path in result.stdout.splitlines():
        language = _LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower())
        if language:
            counts[language] += 1
    return tuple(language for language, _ in counts.most_common())


def _relative(path: str, directory: Path) -> str:
    """Repo-relative form of an ast-grep hit path.

    ast-grep echoes the path it was given, which is absolute. Every other
    backend and the scorer speak repo-relative paths, so an absolute one would
    score a correct retrieval as a miss.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        return path
    try:
        return str(candidate.relative_to(directory))
    except ValueError:
        return path


def _hit(store_id: str, path: str, line_number: int, text: str) -> dict:
    """One retrieval hit in the shape the shared harness already renders.

    ``chunk_index`` is the 60-line window the toast_retrieval adapter uses; it
    is reproduced rather than redefined so a hit from any backend addresses the
    same chunk as a hit from any other.
    """
    return {
        "store_id": store_id,
        "filename": path,
        "metadata": {"path": path},
        "chunk_index": max(line_number - 1, 0) // 60,
        "score": None,
        "text": text[:_TEXT_CAP],
    }


# ---------------------------------------------------------------------------
# structural
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuralSeam:
    """ast-grep pattern search over the local clones."""

    binary: str

    async def ast_search(
        self,
        store_identifiers: tuple[str, ...],
        pattern: str,
        language: str | None,
        top_k: int,
    ) -> dict:
        hits: list[dict] = []
        errors: list[str] = []
        for store_id in store_identifiers:
            if len(hits) >= top_k:
                break
            directory = _repo_dir(store_id)
            if not directory.is_dir():
                errors.append(f"no local clone for {store_id}")
                continue
            languages = (
                [language]
                if language
                else list(repo_languages(str(directory))[:_MAX_INFERRED_LANGUAGES])
            )
            if not languages:
                errors.append(f"{store_id}: no ast-grep-supported source detected")
                continue
            for lang in languages:
                if len(hits) >= top_k:
                    break
                found, error = await asyncio.to_thread(
                    self._run, directory, pattern, lang
                )
                if error:
                    errors.append(f"{store_id} [{lang}]: {error}")
                    continue
                for match in found:
                    start = (match.get("range", {}).get("start", {}) or {}).get("line", 0)
                    hits.append(
                        _hit(
                            store_id,
                            _relative(match["file"], directory),
                            start + 1,
                            match.get("text", ""),
                        )
                    )
                    if len(hits) >= top_k:
                        break
        response: dict = {"data": hits}
        if errors:
            response["error"] = "; ".join(errors)
        return response

    def _run(self, directory: Path, pattern: str, language: str):
        try:
            result = subprocess.run(
                [
                    self.binary,
                    "run",
                    "--pattern",
                    pattern,
                    "--lang",
                    language,
                    "--json=compact",
                    str(directory),
                ],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return [], f"timed out after {_TIMEOUT_SECONDS}s"
        # ast-grep exits non-zero on an unparseable pattern or unknown
        # language. That is a message the model can act on (fix the pattern),
        # so it is returned, not raised.
        if result.returncode != 0:
            return [], (result.stderr.strip() or "ast-grep failed")[:300]
        if not result.stdout.strip():
            return [], None
        try:
            return json.loads(result.stdout), None
        except json.JSONDecodeError as exc:
            return [], f"unparseable ast-grep output: {exc}"


# ---------------------------------------------------------------------------
# precise code intel
# ---------------------------------------------------------------------------

# sg_go_to_definition / sg_find_references render results as blocks headed by
# "# <repo> - <path>" followed by "<line>: <text>". Parsed into the same hit
# shape as every other backend so one scorer and one trace analyzer read all
# three primitives.
_HEADER = re.compile(r"^#\s+(?P<repo>\S+)\s+[–-]\s+(?P<path>\S+)\s*$")
_LINE = re.compile(r"^(?P<line>\d+):\s?(?P<text>.*)$")


def parse_code_intel(text: str) -> list[dict]:
    hits: list[dict] = []
    repo = path = None
    for raw in text.splitlines():
        header = _HEADER.match(raw)
        if header:
            repo, path = header.group("repo"), header.group("path")
            continue
        body = _LINE.match(raw)
        if body and path:
            hits.append(
                _hit(repo or "", path, int(body.group("line")), body.group("text"))
            )
    return hits


@dataclass(frozen=True)
class CodeIntelSeam:
    """Sourcegraph precise navigation over the sg-evals mirrors."""

    async def navigate(
        self, tool: str, repo: str, path: str, symbol: str, top_k: int
    ) -> dict:
        def _call():
            return sg_cli.call_tool(tool, {"repo": repo, "path": path, "symbol": symbol})

        try:
            payload = await asyncio.to_thread(_call)
        except Exception as exc:  # network/index errors are data, not aborts
            return {"data": [], "error": f"{type(exc).__name__}: {exc}"}
        text, is_error = payload if isinstance(payload, tuple) else (payload, False)
        if is_error:
            return {"data": [], "error": str(text)[:300]}
        hits = parse_code_intel(str(text))
        if not hits:
            return {"data": [], "error": f"no results for {symbol!r} in {path}"}
        return {"data": hits[:top_k]}


# ---------------------------------------------------------------------------
# condition backend
# ---------------------------------------------------------------------------


@dataclass
class ConditionBackend:
    """Backend for one tool condition.

    Wraps ``LocalStores`` (read, list, git grep) and adds the extra seams the
    condition enables. ``dispatch_extra`` is the hook the shared harness loop
    calls for tool names it does not itself implement; returning ``None`` means
    "not mine", which keeps the harness's own unknown-tool error intact.
    """

    structural: StructuralSeam | None = None
    code_intel: CodeIntelSeam | None = None
    lexical: bool = True
    _local: LocalStores = field(default_factory=LocalStores, init=False, repr=False)

    @property
    def files(self):
        return self._local.files

    async def search(self, request) -> dict:
        return {
            "data": [],
            "error": "search_corpus is not available in this condition",
        }

    async def grep(self, request) -> dict:
        if not self.lexical:
            return {"data": [], "error": "grep is not available in this condition"}
        return await self._local.grep(request)

    async def dispatch_extra(self, name: str, store_identifiers, arguments: dict):
        top_k = int(arguments.get("top_k") or 25)
        if name == "ast_search":
            if self.structural is None:
                return {"error": "ast_search is not available in this condition"}
            return await self.structural.ast_search(
                tuple(store_identifiers),
                pattern=arguments.get("pattern", ""),
                language=arguments.get("language") or None,
                top_k=top_k,
            )
        if name in ("definition", "references"):
            if self.code_intel is None:
                return {"error": f"{name} is not available in this condition"}
            tool = (
                "sg_go_to_definition" if name == "definition" else "sg_find_references"
            )
            return await self.code_intel.navigate(
                tool,
                repo=arguments.get("repo", ""),
                path=arguments.get("path", ""),
                symbol=arguments.get("symbol", ""),
                top_k=top_k,
            )
        return None


# ---------------------------------------------------------------------------
# instrumentation
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    name: str
    arguments: dict
    seconds: float
    result_chars: int
    hit_paths: list[str]


class Instrumented:
    """Records every backend call: name, arguments, latency, result size.

    The transcript already records call order and arguments; what it cannot
    give is per-call wall time or how many characters of retrieval output
    entered the context. Both are needed to separate "structural search is
    cheaper" from "structural search found less", so they are measured on the
    identical code path for every condition rather than estimated afterwards.
    """

    def __init__(self, backend: ConditionBackend):
        self._backend = backend
        self.calls: list[ToolCall] = []
        self.files = _InstrumentedFiles(backend.files, self)

    def _record(
        self,
        name: str,
        arguments: dict,
        started: float,
        *,
        result_chars: int,
        hit_paths: list,
    ) -> None:
        self.calls.append(
            ToolCall(
                name=name,
                arguments=arguments,
                seconds=round(time.time() - started, 3),
                result_chars=result_chars,
                hit_paths=hit_paths,
            )
        )

    def _record_hits(self, name: str, arguments: dict, started: float, result) -> None:
        """Record a seam that returns ``{"data": [hit, ...]}``."""
        hits = result.get("data") if isinstance(result, dict) else None
        self._record(
            name,
            arguments,
            started,
            result_chars=len(json.dumps(result, ensure_ascii=False, default=str)),
            hit_paths=[
                h.get("filename")
                for h in hits or []
                if isinstance(h, dict) and h.get("filename")
            ],
        )

    async def search(self, request):
        started = time.time()
        result = await self._backend.search(request)
        self._record_hits(
            "search_corpus", {"query": getattr(request, "query", None)}, started, result
        )
        return result

    async def grep(self, request):
        started = time.time()
        result = await self._backend.grep(request)
        self._record_hits(
            "grep",
            {
                "pattern": getattr(request, "pattern", None),
                "case_sensitive": getattr(request, "case_sensitive", None),
            },
            started,
            result,
        )
        return result

    async def dispatch_extra(self, name: str, store_identifiers, arguments: dict):
        started = time.time()
        result = await self._backend.dispatch_extra(name, store_identifiers, arguments)
        if result is None:
            return None
        self._record_hits(name, dict(arguments), started, result)
        return result

    def to_log(self) -> list[dict]:
        return [
            {
                "tool": call.name,
                "arguments": call.arguments,
                "seconds": call.seconds,
                "result_chars": call.result_chars,
                "hit_paths": call.hit_paths,
            }
            for call in self.calls
        ]


class _InstrumentedFiles:
    def __init__(self, files, recorder: Instrumented):
        self._files = files
        self._recorder = recorder

    async def retrieve(self, request):
        started = time.time()
        result = await self._files.retrieve(request)
        path = getattr(request, "file_identifier", None)
        found = result.get("status") != "not_found"
        self._recorder._record(
            "read_file",
            {"repo": getattr(request, "store_identifier", None), "path": path},
            started,
            # The whole file enters the context on a read, and a condition that
            # searches badly pays for it here. Estimating this would flatter
            # exactly the arm that reads most, so it is measured.
            result_chars=sum(
                len(chunk.get("text", "")) for chunk in result.get("chunks", [])
            ),
            hit_paths=[path] if found and path else [],
        )
        return result

    async def list(self, request):
        started = time.time()
        result = await self._files.list(request)
        entries = result.get("data") or []
        self._recorder._record(
            "list_files",
            {"repo": getattr(request, "store_identifier", None)},
            started,
            result_chars=sum(len(e.get("filename", "")) + 1 for e in entries),
            hit_paths=[],
        )
        return result
