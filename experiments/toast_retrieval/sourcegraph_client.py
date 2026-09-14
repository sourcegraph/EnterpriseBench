"""Sourcegraph binding for Toast's ``agent_harness.AsyncRetrievalClient`` protocol.

Toast-1 (github.com/mixedbread-ai/toast-harness, Apache-2.0) drives a fixed
tool surface — search_corpus, grep, get_chunks, read_document, filter_chunks,
prune_context, submit_ranking — over whatever `AsyncRetrievalClient` it is
handed. This module is that seam, backed by the same Sourcegraph GraphQL API
(`/.api/graphql`) and the same `sg-evals/` mirrors that EnterpriseBench's
`mcp_only`/`mcp_code_finder` arms already search — so a Toast-vs-Code-Finder
comparison measures search POLICY, not a different corpus or a different
retrieval substrate. See ``README.md`` for why GraphQL rather than proxying
the MCP JSON-RPC tools byte-for-byte (simpler, same backend index).

Identity scheme: a "store" is one indexed repo (its `sg-evals/...` mirror
name, e.g. ``github.com/sg-evals/git--v2.41.0``); a "file" is
``{store_id}::{repo-relative path}``; a "chunk" is a fixed-size line window
within a file, addressed by ``{file_id}::{chunk_index}``. These three fields
are the identity key Toast uses for deduplication and handle-minting (see
``agent_harness.retrieval.ChunkPayload``) and must stay stable across a
search hit and a later ``get_chunks``/``read_document`` call for the same
file — that is why search results reference chunk indices computed with the
same ``CHUNK_LINES`` window used by ``files.retrieve``, rather than minting
independent ranges per call.

Not implemented faithfully, by design:
- ``metadata_facets`` returns empty. Sourcegraph repos carry no per-chunk
  metadata schema (that is a Mixedbread-store concept); returning `{}` is a
  true fact about this corpus, not a stub standing in for one.
- ``list_chunks`` never honors ``sort_by`` — it raises
  ``UnprocessableEntityError``, the protocol's documented escape hatch for a
  backend that cannot sort server-side, and the harness ranks client-side.
- Search-result chunk ``text`` is the concatenated line-match previews
  Sourcegraph returns, not the full chunk body — matching real Toast usage,
  where ``get_chunks``/``read_document`` fetch full text for hits ``search``
  already located.
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from graphql import GraphQLError, SourcegraphConfig, graphql

# sg_cli owns the MCP JSON-RPC/SSE transport (auth headers, retries, payload
# extraction). Importing it keeps one implementation rather than a second copy
# of the transport living in this experiment.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents/harnesses/claude/mcp"))
import sg_cli  # noqa: E402

_UNSORTABLE_RESPONSE = httpx.Response(
    422, request=httpx.Request("POST", "https://sourcegraph.local/list_chunks")
)

CHUNK_LINES = 60

# patternType matters more than anything else in this binding. `standard`
# treats a multi-word query as a phrase, so a search_corpus call -- which
# arrives as natural language, because that is what a retrieval seam takes --
# matches only where that exact phrasing appears in source. Measured on this
# instance: "optimization pass manager" against gcc and "client session
# configuration" against boto3 both return matchCount=0, while "avatar source"
# against zulip returns 5 files purely because `default_avatar_source` is a
# real identifier there. That is a coin flip on phrasing, not retrieval, and
# it silently scored two sweep tasks 0.0. `keyword` is Sourcegraph's mode for
# multi-term queries: same three queries return gcc/passes.cc,
# docs/source/guide/session.rst, and zerver/lib/avatar.py at rank 1.
_SEARCH_QUERY = """
query Search($query: String!) {
  search(query: $query, version: V3, patternType: keyword) {
    results {
      matchCount
      limitHit
      results {
        __typename
        ... on FileMatch {
          repository { name }
          file { path }
          lineMatches { preview lineNumber }
        }
      }
    }
  }
}
"""

_GREP_QUERY = """
query Grep($query: String!) {
  search(query: $query, version: V3, patternType: regexp) {
    results {
      matchCount
      limitHit
      results {
        __typename
        ... on FileMatch {
          repository { name }
          file { path }
          lineMatches { preview lineNumber }
        }
      }
    }
  }
}
"""

_BLOB_QUERY = """
query Blob($repo: String!, $path: String!) {
  repository(name: $repo) {
    commit(rev: "HEAD") {
      blob(path: $path) {
        content
        totalLines
      }
    }
  }
}
"""

# `recursive` lives on `entries`, not `tree`: the demo instance rejected the
# tree-level argument with "Unknown argument \"recursive\"" on 2026-08-17,
# after verify_adapter.py had passed with it on 2026-08-14 -- the instance's
# schema moved underneath us. Verified live in the entries-level form.
_TREE_QUERY = """
query Tree($repo: String!) {
  repository(name: $repo) {
    commit(rev: "HEAD") {
      tree(path: "") {
        entries(first: 2000, recursive: true) {
          path
          isDirectory
        }
      }
    }
  }
}
"""


_MATCHES_PER_FILE = 10
_MIN_MATCH_BUDGET = 100

# nls_search renders results as markdown-ish text, not JSON:
#   "# <repo> – <path>" followed by "<lineno>: <content>" blocks.
# The separator is an EN DASH, and a path may legitimately contain a hyphen,
# so split on the en dash specifically.
_NLS_HEADER = re.compile(r"^#\s+(?P<repo>\S+)\s+–\s+(?P<path>.+?)\s*$")
_NLS_LINE = re.compile(r"^(?P<lineno>\d+):\s?(?P<content>.*)$")


def _nls_chunk(store_id: str, path: str, chunk_index: int, text: str) -> dict:
    """One nls_search chunk, shaped exactly like a _search_hits_to_chunks hit."""
    return {
        "id": _chunk_id(store_id, path, chunk_index),
        "store_id": store_id,
        "file_id": _file_id(store_id, path),
        "chunk_index": chunk_index,
        "score": 0.0,  # replaced with a rank-derived score by the caller
        "text": text,
        "filename": path,
        "metadata": {"repo": store_id, "path": path, "source": "nls_search"},
    }


def _parse_nls_results(text: str, store_identifiers) -> list[dict]:
    """Parse nls_search's rendered output into chunks with stable identity.

    Line numbers are mapped onto the same fixed CHUNK_LINES window the rest of
    this binding uses, so a chunk surfaced by search and the same chunk later
    fetched via files.retrieve resolve to one id. nls_search returns several
    disjoint line blocks per file; each lands in whichever window contains it,
    and blocks sharing a window are merged rather than emitted twice.
    """
    known = set(store_identifiers)
    chunks: dict[str, dict] = {}
    store_id: str | None = None
    path: str | None = None
    for raw_line in text.splitlines():
        header = _NLS_HEADER.match(raw_line)
        if header:
            repo = header.group("repo")
            # Trust the request's store set over the rendered repo name: the
            # tool may echo a display name that is not the store identifier.
            store_id = repo if repo in known else next(iter(known), repo)
            path = header.group("path")
            continue
        if store_id is None or path is None:
            continue
        body = _NLS_LINE.match(raw_line)
        if not body:
            continue
        lineno = int(body.group("lineno"))
        chunk_index = max(0, lineno - 1) // CHUNK_LINES
        key = _chunk_id(store_id, path, chunk_index)
        chunk = chunks.get(key)
        if chunk is None:
            chunks[key] = _nls_chunk(store_id, path, chunk_index, body.group("content"))
        else:
            chunk["text"] = f"{chunk['text']}\n{body.group('content')}"
    return list(chunks.values())


def _match_budget(top_k) -> int:
    """Sourcegraph `count:` needed for `top_k` distinct files to be reachable.

    Sourcegraph counts matches; the harness counts documents. A file can
    absorb many matches, so the budget is scaled rather than passed through.
    """
    if not top_k or top_k < 1:
        return _MIN_MATCH_BUDGET
    return max(_MIN_MATCH_BUDGET, int(top_k) * _MATCHES_PER_FILE)


def _repo_scope(store_identifiers) -> str:
    names = "|".join(f"^{name}$" for name in store_identifiers)
    return f"repo:{names}"


def _file_id(store_id: str, path: str) -> str:
    return f"{store_id}::{path}"


def _chunk_id(store_id: str, path: str, chunk_index: int) -> str:
    return f"{_file_id(store_id, path)}::{chunk_index}"


def _chunks_from_content(store_id: str, path: str, content: str) -> list[dict]:
    lines = content.splitlines()
    chunks = []
    for start in range(0, len(lines), CHUNK_LINES):
        window = lines[start : start + CHUNK_LINES]
        index = start // CHUNK_LINES
        chunks.append(
            {
                "id": _chunk_id(store_id, path, index),
                "store_id": store_id,
                "file_id": _file_id(store_id, path),
                "chunk_index": index,
                "text": "\n".join(window),
                "filename": path,
                "metadata": {
                    "repo": store_id,
                    "path": path,
                    "start_line": start + 1,
                    "end_line": start + len(window),
                },
            }
        )
    return chunks or [
        {
            "id": _chunk_id(store_id, path, 0),
            "store_id": store_id,
            "file_id": _file_id(store_id, path),
            "chunk_index": 0,
            "text": "",
            "filename": path,
            "metadata": {"repo": store_id, "path": path, "start_line": 1, "end_line": 0},
        }
    ]


def _search_hits_to_chunks(store_identifiers, results: dict) -> list[dict]:
    hits = []
    matches = ((results.get("search") or {}).get("results") or {}).get("results") or []
    for rank, match in enumerate(matches):
        if match.get("__typename") != "FileMatch":
            continue
        store_id = (match.get("repository") or {}).get("name")
        path = (match.get("file") or {}).get("path")
        if not store_id or not path:
            continue
        line_matches = match.get("lineMatches") or []
        first_line = line_matches[0]["lineNumber"] if line_matches else 0
        chunk_index = first_line // CHUNK_LINES
        preview_text = "\n".join(lm.get("preview", "") for lm in line_matches[:10])
        hits.append(
            {
                "id": _chunk_id(store_id, path, chunk_index),
                "store_id": store_id,
                "file_id": _file_id(store_id, path),
                "chunk_index": chunk_index,
                "score": round(1.0 / (1 + rank), 4),
                "text": preview_text,
                "filename": path,
                "metadata": {
                    "repo": store_id,
                    "path": path,
                    "line_matches": [lm.get("lineNumber") for lm in line_matches],
                },
            }
        )
    return hits


@dataclass(frozen=True, slots=True)
class SourcegraphAsyncStoreFiles:
    config: SourcegraphConfig

    async def retrieve(self, request) -> dict:
        store_id = request.store_identifier
        path = request.file_identifier
        # Callers pass either the bare repo-relative path or our own
        # "{store_id}::{path}" file_id back to us (Toast round-trips
        # whichever handle it minted) — accept both.
        if path.startswith(f"{store_id}::"):
            path = path[len(store_id) + 2 :]

        data = await asyncio.to_thread(
            graphql, self.config, _BLOB_QUERY, {"repo": store_id, "path": path}
        )
        blob = ((data.get("repository") or {}).get("commit") or {}).get("blob")
        if blob is None:
            return {
                "id": _file_id(store_id, path),
                "store_id": store_id,
                "filename": path,
                "chunks": [],
                "status": "not_found",
            }

        all_chunks = _chunks_from_content(store_id, path, blob["content"])
        return_chunks = request.return_chunks
        if return_chunks is not True:
            wanted = set(return_chunks)
            all_chunks = [c for c in all_chunks if c["chunk_index"] in wanted]

        return {
            "id": _file_id(store_id, path),
            "store_id": store_id,
            "filename": path,
            "chunks": all_chunks,
        }

    async def list(self, request) -> dict:
        data = await asyncio.to_thread(
            graphql, self.config, _TREE_QUERY, {"repo": request.store_identifier}
        )
        entries = (((data.get("repository") or {}).get("commit") or {}).get("tree") or {}).get(
            "entries"
        ) or []
        files = [
            {"id": _file_id(request.store_identifier, e["path"]), "filename": e["path"]}
            for e in entries
            if not e.get("isDirectory")
        ][: request.limit]
        return {"data": files}


@dataclass(frozen=True, slots=True)
class SourcegraphAsyncStores:
    config: SourcegraphConfig

    async def search(self, request) -> dict:
        """search_corpus -> Sourcegraph nls_search, falling back to text search.

        This seam receives natural language, because that is what a retrieval
        client takes. Sourcegraph's text-search pattern types each answer a
        different question than the one being asked, and measurably so on this
        corpus: `standard` reads a multi-word query as a phrase (matchCount=0
        for "optimization pass manager" against gcc), while `keyword` ANDs
        every term, which starves the long instruction-derived queries that
        single-repo tasks produce. nls_search is the natural-language seam --
        OR semantics with stemming -- and it returns gcc/passes.cc and
        zerver/lib/avatar.py, both required files the other two paths missed.

        The text-search path stays as a fallback so an MCP outage degrades the
        arm instead of zeroing it; `error` records when that happened, since a
        fallback result is not the same measurement.
        """
        scope = _repo_scope(request.store_identifiers)
        query = f"{scope} {request.query or ''}".strip()
        try:
            text, is_error = await asyncio.to_thread(
                sg_cli.call_tool, "sg_nls_search", {"query": query}
            )
        except Exception as exc:  # transport failure, not a tool-level error
            text, is_error = "", True
            nls_error = f"{type(exc).__name__}: {exc}"
        else:
            nls_error = text if is_error else None

        if not is_error:
            hits = _parse_nls_results(text, request.store_identifiers)
            for rank, hit in enumerate(hits):
                hit["score"] = round(1.0 / (1 + rank), 4)
            if hits:
                return {"data": hits[: request.top_k]}

        return await self._text_search(request, scope, nls_error)

    async def _text_search(self, request, scope: str, nls_error: str | None) -> dict:
        # Without an explicit count: Sourcegraph's default budget is spent on
        # matches, not files -- 30 line matches inside one file satisfies it,
        # so a top_k=25 retrieval call comes back holding a single document.
        # Ask for enough matches that top_k distinct files are reachable.
        limit = _match_budget(request.top_k)
        query = f"{scope} {request.query or ''} count:{limit}".strip()
        try:
            data = await asyncio.to_thread(
                graphql, self.config, _SEARCH_QUERY, {"query": query}
            )
        except GraphQLError as exc:
            return {"data": [], "error": str(exc)}
        hits = _search_hits_to_chunks(request.store_identifiers, data)
        result = {"data": hits[: request.top_k]}
        if nls_error:
            result["error"] = f"nls_search unavailable, used text search: {nls_error}"
        return result

    async def metadata_facets(self, request) -> dict:
        del request
        return {"facets": {}}

    async def grep(self, request) -> dict:
        # request.targets names which CHUNK FIELDS to grep (e.g. "text",
        # "generated") -- a Mixedbread-store concept with no Sourcegraph
        # analogue (there is one field: file content). Ignoring it is
        # correct; mapping it onto `file:` filters is not -- an earlier
        # version did that and silently zeroed every grep call whose
        # targets didn't happen to match a real path.
        scope = _repo_scope(request.store_identifiers)
        flags = "" if request.case_sensitive else "case:no "
        query = f"{scope} {flags}{request.pattern}".strip()
        try:
            data = await asyncio.to_thread(graphql, self.config, _GREP_QUERY, {"query": query})
        except GraphQLError as exc:
            return {"data": [], "error": str(exc)}
        hits = _search_hits_to_chunks(request.store_identifiers, data)
        return {"data": hits[: request.top_k]}

    async def list_chunks(self, request) -> dict:
        if request.sort_by is not None:
            # Imported here, not at module scope: the Sourcegraph queries in
            # this module are reused by tooling that has no Toast-1 SDK
            # installed (grep_floor.py), and a module-level import made those
            # unrunnable for a symbol only this branch needs.
            from mixedbread import UnprocessableEntityError

            raise UnprocessableEntityError(
                "Sourcegraph binding cannot sort chunks server-side",
                response=_UNSORTABLE_RESPONSE,
                body=None,
            )
        hits: list[dict] = []
        for store_id in request.store_identifiers:
            data = await asyncio.to_thread(graphql, self.config, _TREE_QUERY, {"repo": store_id})
            entries = (
                ((data.get("repository") or {}).get("commit") or {}).get("tree") or {}
            ).get("entries") or []
            for entry in entries:
                if entry.get("isDirectory"):
                    continue
                hits.append(
                    {
                        "id": _chunk_id(store_id, entry["path"], 0),
                        "store_id": store_id,
                        "file_id": _file_id(store_id, entry["path"]),
                        "chunk_index": 0,
                        "filename": entry["path"],
                        "text": "",
                        "metadata": {"repo": store_id, "path": entry["path"]},
                    }
                )
                if len(hits) >= request.top_k:
                    break
            if len(hits) >= request.top_k:
                break
        return {"data": hits[: request.top_k]}

    @property
    def files(self) -> SourcegraphAsyncStoreFiles:
        return SourcegraphAsyncStoreFiles(self.config)


@dataclass(frozen=True, slots=True)
class SourcegraphRetrievalClient:
    """The ``AsyncRetrievalClient`` EnterpriseBench hands to Toast.

    ``store_identifiers`` passed into any call are `sg-evals/` mirror repo
    names (``github.com/sg-evals/<repo>--<rev>``) — the same names
    ``mcp_only``/``mcp_code_finder`` scope their Sourcegraph MCP calls to. A
    multi-repo task (e.g. a tri-repo dependency-traversal task) passes all of
    its repos' mirror names in one ``store_identifiers`` tuple, exactly as it
    would give Toast multiple document stores to search across.
    """

    config: SourcegraphConfig

    @property
    def stores(self) -> SourcegraphAsyncStores:
        return SourcegraphAsyncStores(self.config)
