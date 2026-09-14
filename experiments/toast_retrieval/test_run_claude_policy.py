"""Offline tests for the frontier-model-over-adapter arm.

Everything here runs without a network: the retrieval client and the
generation seam are stubbed. The loop glue is what these pin -- answer
extraction, tool dispatch onto the adapter's request shapes, the forced
submit on the final round, and result truncation.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from run_claude_policy import (
    _MAX_ROUNDS,
    _dispatch,
    _submitted_files,
    _serialize,
    run_agent,
)


def test_submit_files_dedups_and_keeps_order():
    files = _submitted_files(
        {
            "files": [
                {"repo": "github.com/sg-evals/fastapi--0.109.0", "path": "fastapi/routing.py"},
                {"repo": "github.com/sg-evals/httpx--0.26.0", "path": "httpx/_content.py"},
                {"repo": "github.com/sg-evals/fastapi--0.109.0", "path": "fastapi/routing.py"},
            ]
        }
    )
    assert files == ["fastapi/routing.py", "httpx/_content.py"]


def test_submit_files_accepts_plain_strings_too():
    files = _submitted_files({"files": ["gcc/passes.def", {"path": "gcc/passes.cc"}]})
    assert files == ["gcc/passes.def", "gcc/passes.cc"]


def test_serialize_caps_oversized_results():
    text = _serialize({"content": "x" * 200_000})
    assert len(text) <= 50_000 + len("...[truncated]")
    assert text.endswith("...[truncated]")


class _StubFiles:
    async def retrieve(self, request):
        assert request.store_identifier == "github.com/sg-evals/git--v2.41.0"
        assert request.file_identifier == "http.c"
        return {
            "id": "github.com/sg-evals/git--v2.41.0::http.c",
            "chunks": [{"chunk_index": 0, "text": "line one"}, {"chunk_index": 1, "text": "line two"}],
        }

    async def list(self, request):
        return {"data": [{"filename": "http.c"}, {"filename": "remote.c"}]}


class _StubStores:
    files = _StubFiles()

    async def search(self, request):
        assert request.store_identifiers == ("github.com/sg-evals/git--v2.41.0",)
        return {
            "data": [
                {
                    "store_id": "github.com/sg-evals/git--v2.41.0",
                    "filename": "http.c",
                    "chunk_index": 2,
                    "score": 1.0,
                    "text": "curl_easy_setopt(...)",
                    "metadata": {"path": "http.c"},
                }
            ]
        }

    async def grep(self, request):
        assert request.pattern == "sslCAInfo"
        assert request.case_sensitive is True
        return {"data": []}


STORES = ("github.com/sg-evals/git--v2.41.0",)


def _run(coro):
    return asyncio.run(coro)


def test_dispatch_search_returns_repo_path_and_preview():
    text = _run(_dispatch(_StubStores(), STORES, "search_corpus", {"query": "ssl verify"}))
    payload = json.loads(text)
    assert payload == [
        {
            "repo": "github.com/sg-evals/git--v2.41.0",
            "path": "http.c",
            "chunk_index": 2,
            "score": 1.0,
            "text": "curl_easy_setopt(...)",
        }
    ]


def test_dispatch_grep_passes_pattern_and_case():
    text = _run(
        _dispatch(_StubStores(), STORES, "grep", {"pattern": "sslCAInfo", "case_sensitive": True})
    )
    assert json.loads(text) == []


def test_dispatch_read_file_joins_chunk_windows():
    text = _run(
        _dispatch(
            _StubStores(),
            STORES,
            "read_file",
            {"repo": "github.com/sg-evals/git--v2.41.0", "path": "http.c"},
        )
    )
    payload = json.loads(text)
    assert payload["path"] == "http.c"
    assert payload["content"] == "line one\nline two"


def test_dispatch_list_files_returns_paths():
    text = _run(
        _dispatch(
            _StubStores(),
            STORES,
            "list_files",
            {"repo": "github.com/sg-evals/git--v2.41.0"},
        )
    )
    assert json.loads(text) == ["http.c", "remote.c"]


def test_dispatch_surfaces_backend_errors_instead_of_raising():
    """A flaky GraphQL call degrades the turn, never voids the task.

    Ten of twenty sweep tasks died this way before this behavior existed: one
    list_files call hit a schema mismatch and the whole task was lost.
    """

    class _Exploding:
        class files:
            @staticmethod
            async def list(request):
                raise RuntimeError("Unknown argument \"recursive\"")

    text = _run(_dispatch(_Exploding(), STORES, "list_files", {"repo": "r"}))
    payload = json.loads(text)
    assert "recursive" in payload["error"]


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
    )


def _response(tool_calls, content=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
    )


def test_loop_dispatches_then_returns_on_submit():
    calls = []

    def generate(messages, *, force_submit):
        calls.append(force_submit)
        if len(calls) == 1:
            return _response([_tool_call("a", "search_corpus", {"query": "ssl"})])
        return _response(
            [_tool_call("b", "submit_files", {"files": [{"repo": "r", "path": "http.c"}]})]
        )

    result = asyncio.run(
        run_agent(
            _StubStores(), generate, instruction="find ssl config", store_identifiers=STORES
        )
    )
    assert result["retrieved_paths"] == ["http.c"]
    assert result["rounds_executed"] == 2
    assert calls == [False, False]
    assert result["input_tokens"] == 200
    assert result["output_tokens"] == 20
    # the search's tool result made it back into the conversation
    roles = [m["role"] for m in result["transcript"]]
    assert roles.count("tool") == 1


def test_final_round_forces_submit():
    forced = []

    def generate(messages, *, force_submit):
        forced.append(force_submit)
        if force_submit:
            return _response(
                [_tool_call("z", "submit_files", {"files": [{"path": "remote.c"}]})]
            )
        return _response([_tool_call("s", "search_corpus", {"query": "remotes"})])

    result = asyncio.run(
        run_agent(
            _StubStores(), generate, instruction="q", store_identifiers=STORES
        )
    )
    assert result["retrieved_paths"] == ["remote.c"]
    assert forced == [False] * (_MAX_ROUNDS - 1) + [True]
    assert result["rounds_executed"] == _MAX_ROUNDS


def test_text_only_turn_is_not_a_silent_finish():
    """A prose answer without submit_files must not end the run empty."""

    def generate(messages, *, force_submit):
        if force_submit:
            return _response(
                [_tool_call("z", "submit_files", {"files": [{"path": "http.c"}]})]
            )
        return _response([], content="The relevant file is http.c.")

    result = asyncio.run(
        run_agent(_StubStores(), generate, instruction="q", store_identifiers=STORES)
    )
    assert result["retrieved_paths"] == ["http.c"]
