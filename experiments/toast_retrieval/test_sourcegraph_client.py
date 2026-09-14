"""Pure-function tests for the chunk/identity scheme -- no network.

Live retrieval correctness is covered by verify_adapter.py against a real
Sourcegraph instance (verify_retrieval_client needs no MXBAI_API_KEY, only
SOURCEGRAPH_ACCESS_TOKEN, so it isn't a fit for an offline unit suite).
"""

from sourcegraph_client import (
    CHUNK_LINES,
    _GREP_QUERY,
    _MATCHES_PER_FILE,
    _MIN_MATCH_BUDGET,
    _SEARCH_QUERY,
    _match_budget,
    _parse_nls_results,
    _chunk_id,
    _chunks_from_content,
    _file_id,
    _repo_scope,
    _search_hits_to_chunks,
)

STORE = "github.com/sg-evals/git--v2.41.0"


def test_file_id_and_chunk_id_are_stable_and_distinct():
    assert _file_id(STORE, "http.c") == f"{STORE}::http.c"
    assert _chunk_id(STORE, "http.c", 0) == f"{STORE}::http.c::0"
    assert _chunk_id(STORE, "http.c", 1) != _chunk_id(STORE, "http.c", 0)


def test_repo_scope_builds_alternation_for_multiple_stores():
    scope = _repo_scope([STORE, "github.com/sg-evals/curl--curl-8_1_2"])
    assert scope.startswith("repo:")
    assert f"^{STORE}$" in scope
    assert "^github.com/sg-evals/curl--curl-8_1_2$" in scope


def test_chunks_from_content_splits_on_fixed_line_window():
    content = "\n".join(f"line{i}" for i in range(CHUNK_LINES * 2 + 5))
    chunks = _chunks_from_content(STORE, "http.c", content)
    assert len(chunks) == 3
    assert [c["chunk_index"] for c in chunks] == [0, 1, 2]
    assert chunks[0]["metadata"]["start_line"] == 1
    assert chunks[0]["metadata"]["end_line"] == CHUNK_LINES
    assert chunks[1]["metadata"]["start_line"] == CHUNK_LINES + 1


def test_chunks_from_content_handles_empty_file():
    chunks = _chunks_from_content(STORE, "empty.c", "")
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["text"] == ""


def test_search_hits_carry_identity_consistent_with_files_retrieve():
    """A search hit's (store_id, file_id, chunk_index) must resolve to the
    same chunk get_chunks/files.retrieve would return for that file --
    otherwise Toast's dedup/handle-minting silently breaks."""
    raw = {
        "search": {
            "results": {
                "results": [
                    {
                        "__typename": "FileMatch",
                        "repository": {"name": STORE},
                        "file": {"path": "http.c"},
                        "lineMatches": [{"preview": "sslCAInfo", "lineNumber": 145}],
                    }
                ]
            }
        }
    }
    hits = _search_hits_to_chunks([STORE], raw)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["store_id"] == STORE
    assert hit["file_id"] == _file_id(STORE, "http.c")
    assert hit["chunk_index"] == 145 // CHUNK_LINES
    assert hit["id"] == _chunk_id(STORE, "http.c", 145 // CHUNK_LINES)


def test_search_hits_skip_non_file_match_results():
    raw = {"search": {"results": {"results": [{"__typename": "SearchAlert"}]}}}
    assert _search_hits_to_chunks([STORE], raw) == []


def test_search_uses_keyword_pattern_type():
    """`standard` treats a multi-word query as a phrase.

    search_corpus arrives as natural language, so under `standard` it matches
    only where that exact phrasing appears in source -- measured live as
    matchCount=0 for "optimization pass manager" against gcc and "client
    session configuration" against boto3, which scored two sweep tasks 0.0
    for a reason that had nothing to do with retrieval quality.
    """
    assert "patternType: keyword" in _SEARCH_QUERY
    assert "patternType: standard" not in _SEARCH_QUERY


def test_grep_keeps_regexp_pattern_type():
    """grep is a regex seam; keyword semantics would silently change it."""
    assert "patternType: regexp" in _GREP_QUERY


def test_match_budget_scales_past_the_default_so_top_k_files_are_reachable():
    # Sourcegraph counts matches, the harness counts documents: one file can
    # absorb the whole default budget, returning a single document for a
    # top_k=25 call.
    assert _match_budget(25) >= 25
    assert _match_budget(25) > _match_budget(5) or _match_budget(5) == _MIN_MATCH_BUDGET
    assert _match_budget(100) == 100 * _MATCHES_PER_FILE


def test_match_budget_falls_back_when_top_k_is_absent_or_nonsense():
    assert _match_budget(None) == _MIN_MATCH_BUDGET
    assert _match_budget(0) == _MIN_MATCH_BUDGET
    assert _match_budget(-3) == _MIN_MATCH_BUDGET


NLS_SAMPLE = (
    f"# {STORE} – zerver/lib/avatar.py\n"
    "115:     compute an avatar URL\n"
    "116:     avatar source\n"
    "\n"
    f"# {STORE} – zerver/models/realms.py\n"
    "721: \n"
    '723:     AVATAR_FROM_GRAVATAR = "G"\n'
)


def test_parse_nls_results_groups_lines_into_the_shared_chunk_window():
    chunks = _parse_nls_results(NLS_SAMPLE, [STORE])
    by_path = {c["metadata"]["path"]: c for c in chunks}
    assert set(by_path) == {"zerver/lib/avatar.py", "zerver/models/realms.py"}
    # Lines 115/116 share one CHUNK_LINES window and must merge, not duplicate.
    avatar = by_path["zerver/lib/avatar.py"]
    assert avatar["chunk_index"] == (115 - 1) // CHUNK_LINES
    assert "compute an avatar URL" in avatar["text"]
    assert "avatar source" in avatar["text"]


def test_parse_nls_results_identity_matches_the_files_retrieve_scheme():
    """A search hit and a later get_chunks for the same file must share an id."""
    chunk = _parse_nls_results(NLS_SAMPLE, [STORE])[0]
    assert chunk["id"] == _chunk_id(STORE, "zerver/lib/avatar.py", chunk["chunk_index"])
    assert chunk["file_id"] == _file_id(STORE, "zerver/lib/avatar.py")
    assert chunk["store_id"] == STORE


def test_parse_nls_results_splits_on_en_dash_so_hyphenated_paths_survive():
    text = f"# {STORE} – src/well-known/foo-bar.py\n10: x = 1\n"
    chunk = _parse_nls_results(text, [STORE])[0]
    assert chunk["metadata"]["path"] == "src/well-known/foo-bar.py"


def test_parse_nls_results_is_empty_for_output_with_no_headers():
    assert _parse_nls_results("no results found\n", [STORE]) == []
