"""Parser tests for the MCP policy runner.

Every case here is a real citation shape observed in a sweep response. The
parser silently scored 19 of 20 tasks as zero twice before these existed, so
new formats belong here as they turn up rather than in a comment.
"""

from run_mcp_policy import _parse_citations


def test_json_files_array_is_the_answer_when_present():
    text = """Findings:

```json
{
  "files": [
    {"repo": "github.com/sg-evals/gcc--14", "path": "gcc/passes.def"},
    {"repo": "github.com/sg-evals/gcc--14", "path": "gcc/passes.cc"}
  ],
  "symbols": [
    {"repo": "github.com/sg-evals/gcc--14", "path": "gcc/tree-pass.h", "symbol": "opt_pass"}
  ]
}
```
"""
    answer, everything = _parse_citations(text)
    assert answer == ["gcc/passes.def", "gcc/passes.cc"]
    # symbols annotate paths the files array did not claim; kept out of the
    # answer but visible in the union.
    assert "gcc/tree-pass.h" in everything


def test_markdown_blob_links_are_read_when_there_is_no_json():
    text = (
        "- [fastapi/routing.py](/r/github.com/sg-evals/fastapi--0.109.0/-/blob/"
        "fastapi/routing.py?L196-L249) reads the body\n"
        "- [x](/r/github.com/sg-evals/httpx--0.26.0/-/blob/httpx/_content.py?L176-L216) y\n"
    )
    answer, _ = _parse_citations(text)
    assert answer == ["fastapi/routing.py", "httpx/_content.py"]


def test_inline_backticked_paths_are_read_when_there_are_no_links():
    text = (
        "Findings so far:\n"
        "- FastAPI body parsing: `fastapi/routing.py:227-264`\n"
        "- Dependency resolution: `fastapi/dependencies/utils.py:522-647`, `685-764`\n"
        "- Protobuf well-known types: `src/google/protobuf/timestamp.proto`\n"
        "- Multiple ranges: `api/envoy/config/cluster/v3/cluster.proto:1-50, 197-209`\n"
    )
    answer, _ = _parse_citations(text)
    assert answer == [
        "fastapi/routing.py",
        "fastapi/dependencies/utils.py",
        "src/google/protobuf/timestamp.proto",
        "api/envoy/config/cluster/v3/cluster.proto",
    ]


def test_backticked_symbols_and_bare_names_are_not_paths():
    text = (
        "The contract is `serde::de::Deserializer<'de>`, see `deserialize_any` and "
        "`envoy.config.cluster.v3.Cluster`, plus `FAIL_ON_UNKNOWN_PROPERTIES`.\n"
        "The only file is `serde/src/de/mod.rs`.\n"
    )
    answer, _ = _parse_citations(text)
    assert answer == ["serde/src/de/mod.rs"]


def test_a_path_is_reported_once_however_many_times_it_is_cited():
    text = (
        "- `fastapi/dependencies/utils.py:522-647`\n"
        "- `fastapi/dependencies/utils.py:685-764`\n"
        "- [a](/r/github.com/sg-evals/fastapi--0.109.0/-/blob/fastapi/dependencies/utils.py?L1)\n"
    )
    answer, everything = _parse_citations(text)
    assert answer == ["fastapi/dependencies/utils.py"]
    assert everything == ["fastapi/dependencies/utils.py"]


def test_prose_with_no_citation_yields_nothing():
    text = "I could not write /workspace/analysis/IMPACT_REPORT.md because no tool writes files."
    answer, everything = _parse_citations(text)
    assert answer == []
    assert everything == []
