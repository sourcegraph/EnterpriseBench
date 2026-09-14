"""Offline tests for the local-baseline backend.

A temp git repo stands in for a clone; git grep only sees tracked files, so
the fixture stages everything it writes. No network, no model."""

import asyncio
import subprocess

import pytest

import run_local_policy
from run_claude_policy import _FileRequest, _GrepRequest, _ListRequest, _SearchRequest


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(run_local_policy, "REPOS_DIR", tmp_path)
    directory = tmp_path / "git--v2.41.0"
    directory.mkdir()
    (directory / "http.c").write_text(
        "\n".join(f"// filler {i}" for i in range(70)) + "\ncurl_setopt(sslCAInfo);\n"
    )
    (directory / "remote.c").write_text("int fetch_remote(void) { return 0; }\n")
    subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
    subprocess.run(["git", "-C", str(directory), "add", "-A"], check=True)
    return "github.com/sg-evals/git--v2.41.0"


def _run(coro):
    return asyncio.run(coro)


def test_local_tools_drop_search_corpus_only():
    names = {t["function"]["name"] for t in run_local_policy.LOCAL_TOOLS}
    assert names == {"grep", "read_file", "list_files", "submit_files"}


def test_grep_is_case_insensitive_by_default(repo):
    result = _run(
        run_local_policy.LocalStores().grep(
            _GrepRequest((repo,), pattern="sslcainfo", case_sensitive=False, top_k=10)
        )
    )
    (hit,) = result["data"]
    assert hit["filename"] == "http.c"
    assert hit["store_id"] == repo
    assert hit["chunk_index"] == 1  # line 71 -> second 60-line window
    assert "sslCAInfo" in hit["text"]


def test_grep_case_sensitive_excludes_wrong_case(repo):
    result = _run(
        run_local_policy.LocalStores().grep(
            _GrepRequest((repo,), pattern="sslcainfo", case_sensitive=True, top_k=10)
        )
    )
    assert result["data"] == []


def test_grep_caps_at_top_k(repo):
    result = _run(
        run_local_policy.LocalStores().grep(
            _GrepRequest((repo,), pattern="filler", case_sensitive=False, top_k=5)
        )
    )
    assert len(result["data"]) == 5


def test_grep_missing_clone_surfaces_error(repo):
    result = _run(
        run_local_policy.LocalStores().grep(
            _GrepRequest(
                (repo, "github.com/sg-evals/absent--1.0"),
                pattern="fetch_remote",
                case_sensitive=False,
                top_k=10,
            )
        )
    )
    assert len(result["data"]) == 1
    assert "absent--1.0" in result["error"]


def test_retrieve_reads_file_content(repo):
    result = _run(
        run_local_policy.LocalStores().files.retrieve(
            _FileRequest(store_identifier=repo, file_identifier="remote.c")
        )
    )
    assert result["chunks"][0]["text"].startswith("int fetch_remote")


def test_retrieve_missing_file_is_not_found(repo):
    result = _run(
        run_local_policy.LocalStores().files.retrieve(
            _FileRequest(store_identifier=repo, file_identifier="nope.c")
        )
    )
    assert result == {"status": "not_found"}


def test_list_returns_tracked_files_up_to_limit(repo):
    stores = run_local_policy.LocalStores()
    full = _run(stores.files.list(_ListRequest(store_identifier=repo, limit=10)))
    assert {f["filename"] for f in full["data"]} == {"http.c", "remote.c"}
    capped = _run(stores.files.list(_ListRequest(store_identifier=repo, limit=1)))
    assert len(capped["data"]) == 1


def test_search_declares_itself_unavailable(repo):
    result = _run(
        run_local_policy.LocalStores().search(
            _SearchRequest((repo,), query="ssl", top_k=10)
        )
    )
    assert result["data"] == []
    assert "not available" in result["error"]
