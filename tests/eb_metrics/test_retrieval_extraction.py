"""Tests for eb_metrics.retrieval_extraction (trace parser + GT loader).

Covers the Claude-Code ``agent_trace.jsonl`` parsing paths (local reads,
MCP read_file, MCP search-result scraping), the ground-truth loader, and
the ``compute_run_ir_scores`` false-zero guard that returns ``None`` for
unobserved runs rather than a misleading ``0.0``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eb_metrics.retrieval_extraction import (
    _mcp_repo_dir,
    compute_run_ir_scores,
    required_files_from_ground_truth,
    retrieved_files_from_trace,
)

WORKER = "/workspace/kubernetes/pkg/kubelet/prober/worker.go"
WORKER_TEST = "pkg/kubelet/prober/worker_test.go"  # MCP read_file, repo-relative
# What the MCP read of WORKER_TEST must extract to: repo-qualified, i.e. the
# same workspace-relative shape a local Read of the same file would yield.
WORKER_TEST_QUALIFIED = "kubernetes/pkg/kubelet/prober/worker_test.go"
MANAGER = "pkg/kubelet/prober/results/manager.go"  # surfaced via search result


def _assistant_tool_use(tid: str, name: str, inp: dict) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]
            },
        }
    )


def _user_tool_result(tid: str, content: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": content}
                ]
            },
        }
    )


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
                "t3",
                "mcp__sourcegraph__read_file",
                {"repo": "kubernetes", "path": WORKER_TEST},
            ),
        ],
    )
    got = retrieved_files_from_trace(trace)
    # First-seen order preserved; Bash contributes nothing. The MCP read carries
    # its repo, so it lands workspace-relative like the local Read above it.
    assert got == [WORKER, WORKER_TEST_QUALIFIED]


# ---------------------------------------------------------------------------
# MCP read_file carries its repo (EnterpriseBench-0nru)
#
# The MCP read input is {repo, path} with `path` *repo*-relative, while ground
# truth is *workspace*-relative ("requests/setup.cfg"). Dropping the repo left a
# root-level MCP read one component short — "setup.cfg" — which can never align
# to a 2-component GT key, so the mcp_only arm silently scored 0.0 on files the
# agent had actually opened. Carrying the repo restores parity with the local
# Read path, whose /workspace/<repo>/ prefix already made it workspace-relative.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        # Sourcegraph sg-evals: host/org prefix + `--<rev>` version pin.
        ("github.com/sg-evals/requests--v2.31.0", "requests"),
        ("github.com/sg-evals/botocore--1.31.0", "botocore"),
        # No version pin.
        ("github.com/sg-evals/grafana", "grafana"),
        # GitHub MCP passes a bare repo name.
        ("requests", "requests"),
        # Pin on a bare name; repo names may contain dots and dashes.
        ("urllib3--2.0.0", "urllib3"),
        ("my-repo.js--abc123", "my-repo.js"),
        # Absent/degenerate → no prefix to add.
        ("", ""),
        ("github.com/sg-evals/", ""),
    ],
)
def test_mcp_repo_dir_shapes(repo: str, expected: str) -> None:
    assert _mcp_repo_dir(repo) == expected


def test_mcp_read_without_repo_falls_back_to_bare_path(tmp_path: Path) -> None:
    # No repo field → nothing to prefix; the bare path is still a retrieval.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("t1", "mcp__sourcegraph__read_file", {"path": "setup.cfg"})],
    )
    assert retrieved_files_from_trace(trace) == ["setup.cfg"]


def test_mcp_read_prefixes_repo_even_when_path_repeats_it(tmp_path: Path) -> None:
    # tonic-style: the repo dir legitimately repeats as the inner crate dir, so
    # the prefix must be unconditional. Guarding on `startswith(repo_dir)` would
    # yield "tonic/Cargo.toml" — the wrong workspace-relative path for a file
    # that really lives at /workspace/tonic/tonic/Cargo.toml.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1",
                "mcp__sourcegraph__read_file",
                {"repo": "github.com/sg-evals/tonic--v0.10.0", "path": "tonic/Cargo.toml"},
            )
        ],
    )
    assert retrieved_files_from_trace(trace) == ["tonic/tonic/Cargo.toml"]


def test_mcp_reads_of_same_filename_in_different_repos_stay_distinct(
    tmp_path: Path,
) -> None:
    # Dedup keys on the normalized form, so dropping the repo collapsed two
    # genuinely different reads into one bare "setup.cfg" — undercounting
    # n_retrieved and inviting a cross-repo false match.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1",
                "mcp__sourcegraph__read_file",
                {"repo": "github.com/sg-evals/requests--v2.31.0", "path": "setup.cfg"},
            ),
            _assistant_tool_use(
                "t2",
                "mcp__sourcegraph__read_file",
                {"repo": "github.com/sg-evals/botocore--1.31.0", "path": "setup.cfg"},
            ),
        ],
    )
    assert retrieved_files_from_trace(trace) == [
        "requests/setup.cfg",
        "botocore/setup.cfg",
    ]


def test_compute_run_ir_scores_tri_repo_mcp_only_regression(tmp_path: Path) -> None:
    # Regression trial for the arm-distorting misscore, mirroring the real
    # dep-graph-tri-boto3-urllib3-requests-001 mcp_only run: the agent read all
    # three ground-truth files over MCP, each at its repo root, and scored 0.0.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1",
                "mcp__sourcegraph__read_file",
                {"repo": "github.com/sg-evals/requests--v2.31.0", "path": "setup.cfg"},
            ),
            _assistant_tool_use(
                "t2",
                "mcp__sourcegraph__read_file",
                {"repo": "github.com/sg-evals/botocore--1.31.0", "path": "setup.py"},
            ),
            _assistant_tool_use(
                "t3",
                "mcp__sourcegraph__read_file",
                {"repo": "github.com/sg-evals/urllib3--2.0.0", "path": "CHANGES.rst"},
            ),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "urllib3/CHANGES.rst", "repo": "urllib3"},
            {"path": "requests/setup.cfg", "repo": "requests"},
            {"path": "botocore/setup.py", "repo": "botocore"},
        ],
    )
    scores = compute_run_ir_scores(
        trace, gt, "dep-graph-tri-boto3-urllib3-requests-001", "mcp_only"
    )
    assert scores is not None
    assert scores.file_recall == 1.0
    assert scores.n_overlap == 3


def test_compute_run_ir_scores_mcp_deep_path_still_matches(tmp_path: Path) -> None:
    # C6 no-regression. These deep-path hits currently match only by accident:
    # the repo drop left them repo-relative, and normalize_path's lead-strip
    # happened to bring GT to the same string. Post-fix both sides carry the
    # repo lead and normalize_path strips it symmetrically, so they must still
    # match — now via the primary suffix-align path rather than by coincidence.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1",
                "mcp__sourcegraph__read_file",
                {
                    "repo": "github.com/sg-evals/grafana--v10.0.0",
                    "path": "pkg/services/sqlstore/migrator/migrator.go",
                },
            ),
            _assistant_tool_use(
                "t2",
                "mcp__sourcegraph__read_file",
                {"repo": "github.com/sg-evals/tonic--v0.10.0", "path": "tonic/Cargo.toml"},
            ),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {
                "path": "grafana/pkg/services/sqlstore/migrator/migrator.go",
                "repo": "grafana",
            },
            {"path": "tonic/tonic/Cargo.toml", "repo": "tonic"},
        ],
    )
    scores = compute_run_ir_scores(trace, gt, "dep-graph-tri-tokio-hyper-tonic-001", "mcp_only")
    assert scores is not None
    assert scores.file_recall == 1.0
    assert scores.n_overlap == 2


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
# Bash tokenization — file args must survive shell metacharacters that appear
# *inside* quoted search patterns (regression: splitting on `|` before
# tokenizing dropped the file arg of any `grep 'a|b' path` command).
# ---------------------------------------------------------------------------


def _bash_files(tmp_path: Path, command: str) -> list[str]:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("b1", "Bash", {"command": command})],
    )
    return retrieved_files_from_trace(trace)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A quoted `|` is part of the pattern, not an operator. Each of these
        # lost its file argument when the raw command was split before lexing.
        pytest.param(
            "grep -n 'realm|export' zerver/models/realms.py",
            ["zerver/models/realms.py"],
            id="bre-alternation",
        ),
        pytest.param(
            r"grep 'realm\|export' zerver/models/realms.py",
            ["zerver/models/realms.py"],
            id="escaped-alternation",
        ),
        pytest.param(
            "grep -E 'foo|bar' src/handler.go | head -20",
            ["src/handler.go"],
            id="quoted-alternation-then-real-pipe",
        ),
        pytest.param(
            "awk -F'|' '{print $1}' data/report.csv",
            ["data/report.csv"],
            id="pipe-as-field-separator",
        ),
        # An unquoted operator still separates, spaced or not.
        pytest.param(
            "cat zerver/views/realm_export.py|grep export",
            ["zerver/views/realm_export.py"],
            id="unspaced-pipe",
        ),
        pytest.param(
            "cat a/first.py; cat b/second.py",
            ["a/first.py", "b/second.py"],
            id="semicolon-chain",
        ),
        pytest.param(
            "cat a/first.py && head -5 b/second.py",
            ["a/first.py", "b/second.py"],
            id="andand-chain",
        ),
        # Guard: `(` must NOT be a shell operator. Adding it would make the
        # subshell case below work, at the cost of losing this file — the same
        # quoted-operator hole this whole function exists to close.
        pytest.param("grep '(' src/handler.go", ["src/handler.go"], id="bare-quoted-paren"),
        # Known residual holes (EnterpriseBench-fhtm): when a quoted operator is
        # the *whole* argument, posix lexing strips the quotes and it becomes
        # indistinguishable from a real operator, so the file arg is lost. Same
        # root cause costs us subshells. Latent — zero impact on the scored corpus.
        pytest.param(
            "grep '|' src/handler.go",
            ["src/handler.go"],
            id="bare-quoted-pipe",
            marks=pytest.mark.xfail(strict=True, reason="EnterpriseBench-fhtm"),
        ),
        pytest.param(
            "(cat a/first.py; cat b/second.py)",
            ["a/first.py", "b/second.py"],
            id="subshell",
            marks=pytest.mark.xfail(strict=True, reason="EnterpriseBench-fhtm"),
        ),
        # Unterminated quote: keep the prefix that lexed, emit no mangled path.
        pytest.param("cat 'zerver/models/realms.py", [], id="unbalanced-quote"),
    ],
)
def test_bash_read_files_survives_shell_metacharacters(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    assert _bash_files(tmp_path, command) == expected


def test_bash_redirect_target_still_counted(tmp_path: Path) -> None:
    # Pre-existing false positive, preserved deliberately: an output-redirect
    # target is counted as a read. Fixing it changes scoring on a different
    # axis than this bug, so it is tracked separately (EnterpriseBench-qefr).
    assert _bash_files(tmp_path, "cat src/handler.go > /tmp/out.txt") == [
        "src/handler.go",
        "/tmp/out.txt",
    ]


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
