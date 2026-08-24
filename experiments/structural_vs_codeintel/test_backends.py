"""Offline tests for the condition backends and their instrumentation.

A temp git repo stands in for a clone. ast-grep runs for real (it is local and
fast); Sourcegraph's code-intel tools are stubbed, since their live behaviour is
a property of the index, not of this code.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toast_retrieval"))

import backends
import conditions
import run_local_policy
from run_claude_policy import _FileRequest, _GrepRequest, _ListRequest, _dispatch


def _run(coro):
    return asyncio.run(coro)


STORE = "github.com/sg-evals/demo--1.0"


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    directory = tmp_path / "demo--1.0"
    directory.mkdir()
    (directory / "app.py").write_text(
        "import os\n\n\ndef handle(request):\n    return request\n\n\n"
        "def render(template, context):\n    return template\n"
    )
    (directory / "notes.md").write_text("def handle(request): not code\n")
    subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
    subprocess.run(["git", "-C", str(directory), "add", "-A"], check=True)
    # Both modules resolve clones through their own reference to _repo_dir;
    # patching one and not the other would leave the lexical seam pointed at
    # the real repos/ tree.
    monkeypatch.setattr(backends, "_repo_dir", lambda store_id: directory)
    monkeypatch.setattr(run_local_policy, "_repo_dir", lambda store_id: directory)
    backends.repo_languages.cache_clear()
    return directory


# --- structural ------------------------------------------------------------


def test_ast_search_matches_by_syntax_not_text(repo):
    """The prose in notes.md contains the same characters; only app.py parses."""
    seam = backends.StructuralSeam(backends.ast_grep_binary())
    result = _run(seam.ast_search((STORE,), "def $F($$$ARGS)", "python", 10))
    assert result.get("error") is None
    paths = {hit["filename"] for hit in result["data"]}
    assert paths == {"app.py"}
    assert len(result["data"]) == 2


def test_ast_search_infers_language_when_omitted(repo):
    seam = backends.StructuralSeam(backends.ast_grep_binary())
    result = _run(seam.ast_search((STORE,), "def $F($$$ARGS)", None, 10))
    assert {hit["filename"] for hit in result["data"]} == {"app.py"}


def test_ast_search_caps_at_top_k(repo):
    seam = backends.StructuralSeam(backends.ast_grep_binary())
    result = _run(seam.ast_search((STORE,), "def $F($$$ARGS)", "python", 1))
    assert len(result["data"]) == 1


def test_ast_search_returns_pattern_errors_to_the_model(repo):
    """An unusable pattern is something the model can fix, so it is data."""
    seam = backends.StructuralSeam(backends.ast_grep_binary())
    result = _run(seam.ast_search((STORE,), "def $F(", "no_such_language", 10))
    assert result["data"] == []
    assert result["error"]


def test_ast_search_reports_a_missing_clone(monkeypatch, tmp_path):
    monkeypatch.setattr(backends, "_repo_dir", lambda store_id: tmp_path / "absent")
    backends.repo_languages.cache_clear()
    seam = backends.StructuralSeam(backends.ast_grep_binary())
    result = _run(seam.ast_search((STORE,), "def $F($$$A)", "python", 10))
    assert result["data"] == []
    assert "no local clone" in result["error"]


def test_repo_languages_counts_tracked_files_only(repo):
    (repo / "ignored.go").write_text("package main\n")  # unstaged
    backends.repo_languages.cache_clear()
    assert backends.repo_languages(str(repo)) == ("python",)


# --- code intel ------------------------------------------------------------


def test_parse_code_intel_reads_repo_path_and_lines():
    hits = backends.parse_code_intel(
        "# github.com/sg-evals/click--8.2.1 – src/click/core.py\n"
        "168: class Context:\n"
        "169:     \"\"\"docstring\"\"\"\n"
    )
    assert [h["filename"] for h in hits] == ["src/click/core.py"] * 2
    assert hits[0]["store_id"] == "github.com/sg-evals/click--8.2.1"
    assert hits[0]["chunk_index"] == 2  # line 168 -> third 60-line window


def test_code_intel_surfaces_backend_errors_without_aborting(monkeypatch):
    def boom(tool, arguments):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(backends.sg_cli, "call_tool", boom)
    result = _run(
        backends.CodeIntelSeam().navigate("sg_go_to_definition", STORE, "a.py", "X", 10)
    )
    assert result["data"] == []
    assert "index unavailable" in result["error"]


def test_code_intel_empty_result_is_reported_as_no_results(monkeypatch):
    monkeypatch.setattr(backends.sg_cli, "call_tool", lambda t, a: ("", False))
    result = _run(
        backends.CodeIntelSeam().navigate("sg_find_references", STORE, "a.py", "X", 10)
    )
    assert result["data"] == []
    assert "no results" in result["error"]


# --- condition isolation ---------------------------------------------------


@pytest.mark.parametrize("name", sorted(conditions.CONDITIONS))
def test_disabled_primitives_are_refused_in_every_condition(name, repo, monkeypatch):
    """A condition must not answer a tool it does not offer.

    The tool surface withholds the schema, but the backend is the enforcement:
    if a model emits a call anyway, the answer has to be a refusal, or the
    condition has silently leaked a primitive it is defined by not having.
    """
    monkeypatch.setattr(backends.sg_cli, "call_tool", lambda t, a: ("# r – p\n1: x\n", False))
    condition = conditions.CONDITIONS[name]
    backend = condition.backend()
    offered = set(condition.tool_names)

    grep = _run(backend.grep(_GrepRequest((STORE,), "def", False, 10)))
    assert bool(grep["data"]) == ("grep" in offered)

    ast = _run(backend.dispatch_extra("ast_search", (STORE,), {"pattern": "def $F($$$A)"}))
    assert bool(ast.get("data")) == ("ast_search" in offered)

    for tool in ("definition", "references"):
        result = _run(
            backend.dispatch_extra(tool, (STORE,), {"repo": STORE, "path": "a.py", "symbol": "X"})
        )
        assert bool(result.get("data")) == (tool in offered)


def test_unknown_tool_still_errors_through_the_shared_router(repo):
    backend = conditions.CONDITIONS["combined"].backend()
    answer = _run(_dispatch(backend, (STORE,), "teleport", {}))
    assert "unknown tool" in answer


def test_every_condition_offers_read_list_and_submit():
    for condition in conditions.CONDITIONS.values():
        names = [t["function"]["name"] for t in condition.tools]
        assert {"read_file", "list_files", "submit_files"} <= set(names)
        assert len(names) == len(set(names))


def test_tool_descriptions_pass_the_parity_gate():
    assert conditions.check_description_parity() == []


# --- instrumentation -------------------------------------------------------


def test_instrumented_records_latency_size_and_hits(repo):
    recorder = backends.Instrumented(conditions.CONDITIONS["structural"].backend())
    _run(recorder.dispatch_extra("ast_search", (STORE,), {"pattern": "def $F($$$A)"}))
    _run(recorder.files.retrieve(_FileRequest(STORE, "app.py")))
    _run(recorder.files.list(_ListRequest(STORE, 100)))

    log = recorder.to_log()
    assert [entry["tool"] for entry in log] == ["ast_search", "read_file", "list_files"]
    assert log[0]["hit_paths"] == ["app.py", "app.py"]
    # read_file's size is the file the model actually received, not a stand-in.
    assert log[1]["result_chars"] == len((repo / "app.py").read_text())
    assert all(entry["seconds"] >= 0 for entry in log)


def test_instrumented_read_of_a_missing_file_records_no_hit(repo):
    recorder = backends.Instrumented(conditions.CONDITIONS["lexical"].backend())
    _run(recorder.files.retrieve(_FileRequest(STORE, "absent.py")))
    assert recorder.to_log()[0]["hit_paths"] == []


def test_instrumented_passes_through_unknown_tools(repo):
    recorder = backends.Instrumented(conditions.CONDITIONS["lexical"].backend())
    assert _run(recorder.dispatch_extra("teleport", (STORE,), {})) is None
    assert recorder.to_log() == []


def test_cpp_headers_are_parsed_as_cpp_not_c():
    """A .h in this corpus is a C++ header; parsing it as C loses most of Envoy."""
    assert backends._LANGUAGE_BY_SUFFIX[".h"] == "cpp"
    assert backends._LANGUAGE_BY_SUFFIX[".hpp"] == "cpp"
