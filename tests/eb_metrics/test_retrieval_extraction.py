"""Tests for eb_metrics.retrieval_extraction (trace parser + GT loader).

Covers the Claude-Code ``agent_trace.jsonl`` parsing paths (local reads,
MCP read_file, MCP search-result scraping), the ground-truth loader, the
``compute_run_ir_scores`` false-zero guard that returns ``None`` for
unobserved runs rather than a misleading ``0.0``, and (jn73.1)
turns-/tokens-to-first-relevant (TTFR) extraction.
"""

from __future__ import annotations

import json
from pathlib import Path

from eb_metrics.retrieval_extraction import (
    compute_run_ir_scores,
    compute_ttfr,
    required_files_from_ground_truth,
    retrieved_files_from_trace,
)

WORKER = "/workspace/kubernetes/pkg/kubelet/prober/worker.go"
WORKER_TEST = "pkg/kubelet/prober/worker_test.go"  # MCP read_file, repo-relative
MANAGER = "pkg/kubelet/prober/results/manager.go"  # surfaced via search result


def _assistant_tool_use(
    tid: str,
    name: str,
    inp: dict,
    *,
    ts: str | None = None,
    usage: dict | None = None,
    request_id: str | None = None,
) -> str:
    message: dict = {
        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]
    }
    if usage is not None:
        message["usage"] = usage
    entry: dict = {"type": "assistant", "message": message}
    if ts is not None:
        entry["timestamp"] = ts
    if request_id is not None:
        entry["requestId"] = request_id
    return json.dumps(entry)


def _user_tool_result(tid: str, content: str, *, ts: str | None = None) -> str:
    entry: dict = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": content}
            ]
        },
    }
    if ts is not None:
        entry["timestamp"] = ts
    return json.dumps(entry)


def _write_trace(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


def _write_ground_truth(path: Path, required: list[dict]) -> Path:
    path.write_text(json.dumps({"ground_truth": {"required_files": required}}))
    return path


# ---------------------------------------------------------------------------
# Trace parsing
# ---------------------------------------------------------------------------


def test_retrieved_files_local_and_mcp_reads(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("t1", "Read", {"file_path": WORKER}),
            _assistant_tool_use("t2", "Bash", {"command": "ls"}),  # ignored
            _assistant_tool_use(
                "t3", "mcp__sourcegraph__read_file", {"repo": "k8s", "path": WORKER_TEST}
            ),
        ],
    )
    got = retrieved_files_from_trace(trace)
    # First-seen order preserved; Bash contributes nothing.
    assert got == [WORKER, WORKER_TEST]


def test_retrieved_files_scrapes_search_results(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("s1", "mcp__sourcegraph__keyword_search", {"query": "prober"}),
            _user_tool_result("s1", f'[{{"path":"{MANAGER}"}}]'),
        ],
    )
    assert retrieved_files_from_trace(trace) == [MANAGER]


def test_retrieved_files_dedup_on_normalized_form(tmp_path: Path) -> None:
    # A /workspace/ path and its repo-relative twin collapse to one entry.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("t1", "Read", {"file_path": WORKER}),
            _assistant_tool_use(
                "t2", "Read", {"file_path": "pkg/kubelet/prober/worker.go"}
            ),
        ],
    )
    assert retrieved_files_from_trace(trace) == [WORKER]


def test_retrieved_files_missing_trace_returns_empty(tmp_path: Path) -> None:
    assert retrieved_files_from_trace(tmp_path / "nope.jsonl") == []


def test_retrieved_files_parses_bash_read_commands(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("b1", "Bash", {"command": "cat /workspace/zulip/zerver/models/realms.py"}),
            # grep: pattern (no extension) filtered, flag filtered, file kept.
            _assistant_tool_use(
                "b2", "Bash", {"command": 'grep -rn "realm_export" zerver/actions/realm_export.py'}
            ),
            # pipeline: cat's file kept; wc is a read cmd but has no file arg.
            _assistant_tool_use("b3", "Bash", {"command": "cat zerver/views/realm_export.py | wc -l"}),
            # running a script is NOT a retrieval.
            _assistant_tool_use("b4", "Bash", {"command": "python /workspace/zulip/manage.py migrate"}),
            _assistant_tool_use("b5", "Bash", {"command": "ls -la zerver/"}),  # ls not a read cmd
        ],
    )
    got = retrieved_files_from_trace(trace)
    assert got == [
        "/workspace/zulip/zerver/models/realms.py",
        "zerver/actions/realm_export.py",
        "zerver/views/realm_export.py",
    ]
    # manage.py (python, not a read cmd) and the ls dir are absent.
    assert not any("manage.py" in f for f in got)


def test_retrieved_files_ignores_non_file_writes(tmp_path: Path) -> None:
    # answer.json Write is not a retrieval; Write is not a _LOCAL_FILE_TOOL.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("w1", "Write", {"file_path": "/workspace/agent_output/answer.json"})],
    )
    assert retrieved_files_from_trace(trace) == []


# ---------------------------------------------------------------------------
# Ground-truth loader
# ---------------------------------------------------------------------------


def test_required_files_reads_paths_dropping_repo(tmp_path: Path) -> None:
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "pkg/kubelet/prober/worker.go", "repo": "kubernetes"},
            {"path": "pkg/kubelet/prober/worker_test.go", "repo": "kubernetes"},
        ],
    )
    assert required_files_from_ground_truth(gt) == [
        "pkg/kubelet/prober/worker.go",
        "pkg/kubelet/prober/worker_test.go",
    ]


def test_required_files_missing_or_empty(tmp_path: Path) -> None:
    assert required_files_from_ground_truth(tmp_path / "nope.json") == []
    empty = _write_ground_truth(tmp_path / "ground_truth.json", [])
    assert required_files_from_ground_truth(empty) == []


def test_required_files_optional_sufficient(tmp_path: Path) -> None:
    path = tmp_path / "ground_truth.json"
    path.write_text(
        json.dumps(
            {
                "ground_truth": {
                    "required_files": [{"path": "a.go", "repo": "r"}],
                    "sufficient_files": [{"path": "b.go", "repo": "r"}],
                }
            }
        )
    )
    assert required_files_from_ground_truth(path) == ["a.go"]
    assert required_files_from_ground_truth(path, include_sufficient=True) == ["a.go", "b.go"]


# ---------------------------------------------------------------------------
# compute_run_ir_scores — the false-zero guard
# ---------------------------------------------------------------------------


def test_compute_run_ir_scores_full_recall(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("t1", "Read", {"file_path": WORKER}),
            _assistant_tool_use(
                "t2", "mcp__sourcegraph__read_file", {"repo": "k8s", "path": WORKER_TEST}
            ),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "pkg/kubelet/prober/worker.go", "repo": "kubernetes"},
            {"path": "pkg/kubelet/prober/worker_test.go", "repo": "kubernetes"},
        ],
    )
    scores = compute_run_ir_scores(trace, gt, "err-prov-04", "baseline")
    assert scores is not None
    assert scores.file_recall == 1.0
    assert scores.n_overlap == 2


def test_compute_run_ir_scores_genuine_miss_reports_zero(tmp_path: Path) -> None:
    # Agent opened a file, but not a relevant one → real 0.0, NOT None.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("t1", "Read", {"file_path": "/workspace/kubernetes/README.md"})],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "pkg/kubelet/prober/worker.go", "repo": "kubernetes"}],
    )
    scores = compute_run_ir_scores(trace, gt, "t", "baseline")
    assert scores is not None
    assert scores.file_recall == 0.0


def test_compute_run_ir_scores_none_when_no_trace(tmp_path: Path) -> None:
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "pkg/a.go", "repo": "r"}],
    )
    assert compute_run_ir_scores(tmp_path / "missing.jsonl", gt, "t", "c") is None


def test_compute_run_ir_scores_none_when_no_file_opens(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("b1", "Bash", {"command": "echo hi"})],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "pkg/a.go", "repo": "r"}],
    )
    assert compute_run_ir_scores(trace, gt, "t", "c") is None


def test_compute_run_ir_scores_suffix_match_workspace_repo_prefix(tmp_path: Path) -> None:
    # zulip-style: GT is repo-relative (zerver/...); retrieved carries the
    # /workspace/<repo>/ prefix. Component-suffix matching must still count it.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1", "Read", {"file_path": "/workspace/zulip/zerver/actions/realm_export.py"}
            ),
            _assistant_tool_use(
                "t2", "Read", {"file_path": "zulip/zerver/views/realm_export.py"}
            ),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "zerver/actions/realm_export.py", "repo": "zulip"},
            {"path": "zerver/views/realm_export.py", "repo": "zulip"},
        ],
    )
    scores = compute_run_ir_scores(trace, gt, "schema-evolution-002", "baseline")
    assert scores is not None
    assert scores.file_recall == 1.0
    assert scores.n_overlap == 2


def test_compute_run_ir_scores_repo_name_collides_with_inner_dir(tmp_path: Path) -> None:
    # gcc-style: repo name == inner source dir. Suffix matching must not
    # over-strip. Both a repo-relative and a /workspace/<repo>/ form match.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1", "mcp__sourcegraph__read_file", {"repo": "gcc", "path": "gcc/passes.def"}
            ),
            _assistant_tool_use(
                "t2", "Read", {"file_path": "/workspace/gcc/gcc/tree-pass.h"}
            ),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "gcc/passes.def", "repo": "gcc"},
            {"path": "gcc/tree-pass.h", "repo": "gcc"},
        ],
    )
    scores = compute_run_ir_scores(trace, gt, "ccx-dep-trace-106", "mcp_only")
    assert scores is not None
    assert scores.file_recall == 1.0
    assert scores.n_overlap == 2


def test_compute_run_ir_scores_none_when_no_required_files(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("t1", "Read", {"file_path": WORKER})],
    )
    gt = _write_ground_truth(tmp_path / "ground_truth.json", [])
    assert compute_run_ir_scores(trace, gt, "t", "c") is None


# ---------------------------------------------------------------------------
# compute_ttfr — turns-/tokens-/seconds-to-first-relevant (jn73.1)
# ---------------------------------------------------------------------------


def _usage(total_tokens: int) -> dict:
    # A single-field usage block is enough to exercise the 4-field sum;
    # other tests below use realistic multi-field breakdowns.
    return {"output_tokens": total_tokens}


def test_compute_ttfr_ok_counts_turns_tokens_and_seconds(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1", "Bash", {"command": "grep -rn foo src/"},
                ts="2026-01-01T00:00:00.000Z", usage=_usage(100),
            ),
            _assistant_tool_use(
                "t2", "Read", {"file_path": "/workspace/kubernetes/README.md"},
                ts="2026-01-01T00:00:10.000Z", usage=_usage(50),
            ),
            _assistant_tool_use(
                "t3", "Read", {"file_path": WORKER},
                ts="2026-01-01T00:00:25.500Z", usage=_usage(75),
            ),
        ],
    )
    required = ["pkg/kubelet/prober/worker.go"]
    result = compute_ttfr(trace, required)
    assert result.coverage == "ok"
    assert result.turns_to_first_relevant == 3
    assert result.tokens_to_first_relevant == 225  # 100 + 50 + 75, cumulative through step 3
    assert result.ttfr_seconds == 25.5


def test_compute_ttfr_counts_non_file_tool_calls_as_turns() -> None:
    # A dispatch-style tool (e.g. "Agent") contributes no file paths but
    # still counts as a turn -- mirrors the real trial pattern where an
    # agent's first move is a sub-agent dispatch, not a file read.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        trace = _write_trace(
            Path(d) / "agent_trace.jsonl",
            [
                _assistant_tool_use(
                    "t1", "Agent", {"description": "explore"},
                    ts="2026-01-01T00:00:00.000Z", usage=_usage(10),
                ),
                _assistant_tool_use(
                    "t2", "Read", {"file_path": WORKER},
                    ts="2026-01-01T00:00:05.000Z", usage=_usage(20),
                ),
            ],
        )
        result = compute_ttfr(trace, ["pkg/kubelet/prober/worker.go"])
        assert result.coverage == "ok"
        assert result.turns_to_first_relevant == 2  # Agent call was turn 1
        assert result.tokens_to_first_relevant == 30
        assert result.ttfr_seconds == 5.0


def test_compute_ttfr_matches_via_tool_result_search_scrape(tmp_path: Path) -> None:
    # The relevant file is surfaced by a search tool's *result* payload, not
    # its input -- the hit must attribute back to the originating step.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "s1", "mcp__sourcegraph__keyword_search", {"query": "prober"},
                ts="2026-01-01T00:00:00.000Z", usage=_usage(40),
            ),
            _user_tool_result("s1", f'[{{"path":"{MANAGER}"}}]', ts="2026-01-01T00:00:01.000Z"),
        ],
    )
    result = compute_ttfr(trace, ["pkg/kubelet/prober/results/manager.go"])
    assert result.coverage == "ok"
    assert result.turns_to_first_relevant == 1
    assert result.ttfr_seconds == 0.0


def test_compute_ttfr_not_found_is_a_genuine_miss(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1", "Read", {"file_path": "/workspace/kubernetes/README.md"},
                ts="2026-01-01T00:00:00.000Z", usage=_usage(10),
            ),
        ],
    )
    result = compute_ttfr(trace, ["pkg/kubelet/prober/worker.go"])
    assert result == compute_ttfr(trace, ["pkg/kubelet/prober/worker.go"])
    assert result.coverage == "not_found"
    assert result.turns_to_first_relevant is None
    assert result.tokens_to_first_relevant is None
    assert result.ttfr_seconds is None


def test_compute_ttfr_no_trace_reports_coverage_not_none(tmp_path: Path) -> None:
    result = compute_ttfr(tmp_path / "missing.jsonl", ["pkg/a.go"])
    assert result.coverage == "no_trace"


def test_compute_ttfr_no_ground_truth_reports_coverage(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("t1", "Read", {"file_path": WORKER})],
    )
    result = compute_ttfr(trace, [])
    assert result.coverage == "no_ground_truth"


def test_compute_ttfr_no_tool_calls_is_an_observability_gap(tmp_path: Path) -> None:
    # A trace with only non-tool-use entries (e.g. plain assistant text).
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})],
    )
    result = compute_ttfr(trace, ["pkg/a.go"])
    assert result.coverage == "no_tool_calls"


def test_compute_ttfr_tokens_none_without_usage_telemetry(tmp_path: Path) -> None:
    # Turns/seconds are still measurable even when the trace carries no
    # usage block at all -- tokens_to_first_relevant stays None (a coverage
    # gap on ONE field), not a fabricated 0.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("t1", "Read", {"file_path": WORKER}, ts="2026-01-01T00:00:00.000Z"),
        ],
    )
    result = compute_ttfr(trace, ["pkg/kubelet/prober/worker.go"])
    assert result.coverage == "ok"
    assert result.turns_to_first_relevant == 1
    assert result.tokens_to_first_relevant is None
    assert result.ttfr_seconds == 0.0


def test_compute_ttfr_dedupes_tokens_by_request_id(tmp_path: Path) -> None:
    """Regression guard: EB's Claude-Code trace logs one JSONL line per
    *content block* (thinking/text/tool_use — and a turn can emit several
    tool_use blocks), with every line of one API call sharing an identical
    ``usage`` snapshot and ``requestId``. Naively summing every assistant
    line's usage overcounts by however many lines share a call; found via
    hand-verification against a real EB trial (see the integration test
    below), where naive summation reported 199,622 cumulative tokens
    through the first-relevant step versus the correct 75,442 -- a ~2.6x
    inflation. Two tool_use blocks sharing one requestId must contribute
    that request's usage exactly once.
    """
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            # One turn, two tool_use blocks, same requestId + usage snapshot.
            _assistant_tool_use(
                "t1", "Bash", {"command": "grep -rn foo src/"},
                ts="2026-01-01T00:00:00.000Z", usage=_usage(1000), request_id="req_A",
            ),
            _assistant_tool_use(
                "t2", "Bash", {"command": "find . -name '*.go'"},
                ts="2026-01-01T00:00:01.000Z", usage=_usage(1000), request_id="req_A",
            ),
            # Second turn: distinct requestId, its own usage counted once more.
            _assistant_tool_use(
                "t3", "Read", {"file_path": WORKER},
                ts="2026-01-01T00:00:05.000Z", usage=_usage(500), request_id="req_B",
            ),
        ],
    )
    result = compute_ttfr(trace, ["pkg/kubelet/prober/worker.go"])
    assert result.coverage == "ok"
    assert result.turns_to_first_relevant == 3
    # 1000 (req_A, counted once despite 2 tool_use lines) + 500 (req_B) = 1500,
    # NOT 1000 + 1000 + 500 = 2500 (the naive per-line overcounting bug).
    assert result.tokens_to_first_relevant == 1500


def test_compute_ttfr_seconds_none_without_timestamps(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("t1", "Read", {"file_path": WORKER}, usage=_usage(5))],
    )
    result = compute_ttfr(trace, ["pkg/kubelet/prober/worker.go"])
    assert result.coverage == "ok"
    assert result.turns_to_first_relevant == 1
    assert result.tokens_to_first_relevant == 5
    assert result.ttfr_seconds is None


# ---------------------------------------------------------------------------
# compute_run_ir_scores — TTFR + localization wired end-to-end
# ---------------------------------------------------------------------------


def test_compute_run_ir_scores_wires_ttfr_and_localization(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1", "Bash", {"command": "grep -rn foo src/"},
                ts="2026-01-01T00:00:00.000Z", usage=_usage(100),
            ),
            _assistant_tool_use(
                "t2", "Read", {"file_path": WORKER},
                ts="2026-01-01T00:00:12.000Z", usage=_usage(50),
            ),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "pkg/kubelet/prober/worker.go", "repo": "kubernetes"}],
    )
    scores = compute_run_ir_scores(trace, gt, "t", "baseline")
    assert scores is not None
    assert scores.file_recall == 1.0
    assert scores.localization[1] == 1.0
    assert scores.turns_to_first_relevant == 2
    assert scores.tokens_to_first_relevant == 150
    assert scores.ttfr_seconds == 12.0
    assert scores.ttfr_coverage == "ok"


# ---------------------------------------------------------------------------
# Integration — reconstructed real EB trial (jn73.1 empirical validation)
#
# The tool-call sequence, every timestamp, every per-request token usage
# dict, and the requestId groupings below are copied verbatim (hand-
# extracted, not invented) from a real baseline run of
# ansible-galaxy-tar-regression-prove-001
# (results/runs/ansible-galaxy-tar-regression-prove-001/baseline/agent_trace.jsonl,
# a local run artifact, not committed — see the jn73.1 completion report for
# the full back-compute pass across further real trials, and for how the
# 199,622-vs-75,442 token discrepancy below was found). Reconstructed here
# as a small fixture, per this repo's tests/fixtures/ convention of compact
# hand-authored data rather than committing multi-hundred-KB trace blobs.
# Note steps 2+3 share one requestId (one turn issued two Bash calls back
# to back) and steps 4+5 share another (one turn issued two Read calls) --
# reproducing the exact grouping that made the requestId-dedup fix necessary.
# ---------------------------------------------------------------------------


def test_compute_run_ir_scores_matches_hand_verified_real_trial(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            # Session-start marker (real trace's t0 predates its first tool
            # call by ~7s of setup/thinking) -- omitting this line understates
            # ttfr_seconds by exactly that gap.
            json.dumps({"type": "queue-operation", "timestamp": "2026-06-05T04:54:35.188Z"}),
            # step 1: sub-agent dispatch, contributes no file paths.
            _assistant_tool_use(
                "r1", "Agent",
                {"description": "Find tar extraction code", "subagent_type": "Explore"},
                ts="2026-06-05T04:54:42.257Z", request_id="req_1",
                usage={"input_tokens": 2, "cache_creation_input_tokens": 6249,
                       "cache_read_input_tokens": 17103, "output_tokens": 324},
            ),
            # step 2: grep, no hit. Same requestId as step 3 (one turn, two Bash calls).
            _assistant_tool_use(
                "r2", "Bash", {"command": 'grep -n "_extract_tar_dir" -r lib/'},
                ts="2026-06-05T04:55:43.836Z", request_id="req_2",
                usage={"input_tokens": 1, "cache_creation_input_tokens": 1965,
                       "cache_read_input_tokens": 23352, "output_tokens": 290},
            ),
            # step 3: find, no hit.
            _assistant_tool_use(
                "r3", "Bash", {"command": 'find test -name "*extract*tar*"'},
                ts="2026-06-05T04:55:44.532Z", request_id="req_2",
                usage={"input_tokens": 1, "cache_creation_input_tokens": 1965,
                       "cache_read_input_tokens": 23352, "output_tokens": 290},
            ),
            # step 4: Read of a required file -- the real first-relevant hit.
            # Same requestId as step 5 (one turn, two Read calls).
            _assistant_tool_use(
                "r4", "Read",
                {"file_path": "/workspace/ansible/lib/ansible/galaxy/collection/__init__.py",
                 "offset": 1545, "limit": 100},
                ts="2026-06-05T04:55:48.092Z", request_id="req_3",
                usage={"input_tokens": 1, "cache_creation_input_tokens": 678,
                       "cache_read_input_tokens": 25317, "output_tokens": 160},
            ),
            # step 5: Read of a non-required test file (retrieved, but not relevant).
            _assistant_tool_use(
                "r5", "Read",
                {"file_path": "/workspace/ansible/test/units/cli/galaxy/test_collection_extract_tar.py"},
                ts="2026-06-05T04:55:48.367Z", request_id="req_3",
                usage={"input_tokens": 1, "cache_creation_input_tokens": 678,
                       "cache_read_input_tokens": 25317, "output_tokens": 160},
            ),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "lib/ansible/galaxy/collection/__init__.py", "repo": "ansible"},
            {"path": "lib/ansible/galaxy/collection/concrete_artifact_manager.py", "repo": "ansible"},
        ],
    )
    scores = compute_run_ir_scores(trace, gt, "ansible-galaxy-tar-regression-prove-001", "baseline")
    assert scores is not None
    # Hand-verified against the real run: 1 of 2 required files opened.
    assert scores.file_recall == 0.5
    assert scores.n_overlap == 1
    assert scores.n_retrieved == 2
    # First retrieved file overall is the relevant one.
    assert scores.localization[1] == 1.0
    # Hand-verified TTFR: the 4th tool call is the first-relevant hit;
    # 75,442 cumulative tokens through that call (req_1 + req_2, each
    # counted once despite req_2 covering 2 tool_use blocks) + req_3's own
    # 26,156 = 23,678 + 25,608 + 26,156 = 75,442; 72.904s of wall time.
    assert scores.turns_to_first_relevant == 4
    assert scores.tokens_to_first_relevant == 75442
    assert scores.ttfr_seconds == 72.904
    assert scores.ttfr_coverage == "ok"
