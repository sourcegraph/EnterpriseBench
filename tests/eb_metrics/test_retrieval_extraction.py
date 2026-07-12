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
    _mcp_repo_name,
    compute_run_ir_scores,
    required_files_from_ground_truth,
    retrieved_files_from_trace,
)

WORKER = "/workspace/kubernetes/pkg/kubelet/prober/worker.go"
WORKER_TEST = "pkg/kubelet/prober/worker_test.go"  # MCP read_file, repo-relative
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


def _mcp_read(tid: str, repo: str, path: str) -> str:
    return _assistant_tool_use(tid, "mcp__sourcegraph__read_file", {"repo": repo, "path": path})


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
            _mcp_read("t3", "kubernetes", WORKER_TEST),
        ],
    )
    got = retrieved_files_from_trace(trace)
    # First-seen order preserved; Bash contributes nothing. The MCP read carries
    # its repo, so it lands workspace-relative like the local Read above it.
    assert got == [WORKER, f"/workspace/kubernetes/{WORKER_TEST}"]


# ---------------------------------------------------------------------------
# MCP read_file carries its repo — GT is workspace-relative, MCP `path` is not
# (EnterpriseBench-0nru)
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
        # Trailing slash must not silently yield "" and drop the qualifier —
        # that would quietly restore the very bug this carries the repo to fix.
        ("github.com/sg-evals/requests--v2.31.0/", "requests"),
        # _repo_dirs feeds task.toml urls through here, and derive_mirror_name
        # (which mints the MCP identifier from that same url) drops `.git`. If
        # this kept it, the url would key "next.js.git" while the identifier said
        # "next.js" — the repo would silently score 0 on every read.
        ("https://github.com/vercel/next.js.git", "next.js"),
        ("https://github.com/pallets/flask.git/", "flask"),
        # Absent → no prefix to add.
        ("", ""),
        # A name-less identifier is indistinguishable from a bare "org/repo", so
        # its last segment is used. Malformed input, no real caller produces it;
        # pinned so the trailing-slash handling above stays honest about it.
        ("github.com/sg-evals/", "sg-evals"),
    ],
)
def test_mcp_repo_name_shapes(repo: str, expected: str) -> None:
    assert _mcp_repo_name(repo) == expected


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
        [_mcp_read("t1", "github.com/sg-evals/tonic--v0.10.0", "tonic/Cargo.toml")],
    )
    assert retrieved_files_from_trace(trace) == ["/workspace/tonic/tonic/Cargo.toml"]


def test_mcp_reads_of_same_filename_in_different_repos_stay_distinct(
    tmp_path: Path,
) -> None:
    # Dedup keys on the normalized form, so dropping the repo collapsed two
    # genuinely different reads into one bare "setup.cfg" — undercounting
    # n_retrieved and inviting a cross-repo false match.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _mcp_read("t1", "github.com/sg-evals/requests--v2.31.0", "setup.cfg"),
            _mcp_read("t2", "github.com/sg-evals/botocore--1.31.0", "setup.cfg"),
        ],
    )
    assert retrieved_files_from_trace(trace) == [
        "/workspace/requests/setup.cfg",
        "/workspace/botocore/setup.cfg",
    ]


def test_compute_run_ir_scores_tri_repo_mcp_only_regression(tmp_path: Path) -> None:
    # Regression trial for the arm-distorting misscore, mirroring the real
    # dep-graph-tri-boto3-urllib3-requests-001 mcp_only run: the agent read all
    # three ground-truth files over MCP, each at its repo root, and scored 0.0.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _mcp_read("t1", "github.com/sg-evals/requests--v2.31.0", "setup.cfg"),
            _mcp_read("t2", "github.com/sg-evals/botocore--1.31.0", "setup.py"),
            _mcp_read("t3", "github.com/sg-evals/urllib3--2.0.0", "CHANGES.rst"),
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
            _mcp_read(
                "t1",
                "github.com/sg-evals/grafana--v10.0.0",
                "pkg/services/sqlstore/migrator/migrator.go",
            ),
            _mcp_read("t2", "github.com/sg-evals/tonic--v0.10.0", "tonic/Cargo.toml"),
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


# ---------------------------------------------------------------------------
# Precision: carrying the repo is only worth anything if the join keeps it.
# Each of these is a file the agent did NOT open; none may score a hit.
# ---------------------------------------------------------------------------


def test_root_manifest_read_is_not_credited_for_nested_manifest(tmp_path: Path) -> None:
    # dep-graph-dual-tokio-hyper-001: GT wants tokio's CRATE manifest. An agent
    # that reads only the two workspace-ROOT manifests never opened it. Scoring
    # this 1.0 is credit for a file that was never read — it happened because
    # the repo qualifier was stripped back off before the join, making
    # "tokio/Cargo.toml" and "tokio/tokio/Cargo.toml" the same key.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _mcp_read("t1", "github.com/sg-evals/tokio--tokio-1.0.0", "Cargo.toml"),
            _mcp_read("t2", "github.com/sg-evals/hyper--v1.0.0", "Cargo.toml"),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "tokio/tokio/Cargo.toml", "repo": "tokio"},
            {"path": "hyper/Cargo.toml", "repo": "hyper"},
        ],
    )
    scores = compute_run_ir_scores(trace, gt, "dep-graph-dual-tokio-hyper-001", "mcp_only")
    assert scores is not None
    # hyper's GT *is* the root manifest, so that one is a genuine hit; tokio's
    # crate manifest was never opened and must not be.
    assert scores.file_recall == 0.5
    assert scores.n_overlap == 1


def test_root_and_nested_manifest_reads_stay_two_retrievals(tmp_path: Path) -> None:
    # Reading the workspace root manifest and then the crate manifest is the
    # natural exploration order in a Rust workspace. They are DIFFERENT files;
    # collapsing them undercounts n_retrieved, inflates context_efficiency, and
    # ranks the hit at the wrong position for MRR/nDCG.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _mcp_read("t1", "github.com/sg-evals/tokio--tokio-1.0.0", "Cargo.toml"),
            _mcp_read("t2", "github.com/sg-evals/tokio--tokio-1.0.0", "tokio/Cargo.toml"),
        ],
    )
    assert retrieved_files_from_trace(trace) == [
        "/workspace/tokio/Cargo.toml",
        "/workspace/tokio/tokio/Cargo.toml",
    ]


def test_read_from_wrong_repo_is_not_a_hit(tmp_path: Path) -> None:
    # f5wh: same repo-relative path, different repo. Without the repo on both
    # sides of the join these normalize to the same key and the agent is
    # credited for a file it opened in the wrong repository.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_assistant_tool_use("t1", "Read", {"file_path": "/workspace/prometheus/pkg/svc/migrator.go"})],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "pkg/svc/migrator.go", "repo": "grafana"}],
    )
    scores = compute_run_ir_scores(trace, gt, "t", "baseline")
    assert scores is not None
    assert scores.file_recall == 0.0
    assert scores.n_overlap == 0


# ---------------------------------------------------------------------------
# Aliased repos: the repo a task CLONES is not always the repo name upstream.
# ---------------------------------------------------------------------------


def test_mcp_read_of_aliased_repo_matches_ground_truth(tmp_path: Path) -> None:
    # refactor-orchestration-tri-babel-001 clones github.com/vercel/next.js into
    # /workspace/nextjs. The MCP repo identifier is minted from the URL, so it
    # carries "next.js" while ground truth says "nextjs". Resolving both through
    # task.toml is what makes them the same key — without it the MCP arm silently
    # scores 0.0 on every aliased repo while the baseline arm (which reads
    # /workspace/nextjs/... directly) scores 1.0, re-introducing exactly the arm
    # distortion this join exists to remove.
    (tmp_path / "task.toml").write_text(
        '[[repos]]\nurl = "https://github.com/vercel/next.js"\npath = "nextjs"\n'
    )
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _mcp_read(
                "t1",
                "github.com/sg-evals/next.js--v14.0.0",
                "packages/next/package.json",
            )
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "packages/next/package.json", "repo": "nextjs"}],
    )
    scores = compute_run_ir_scores(trace, gt, "refactor-orchestration-tri-babel-001", "mcp_only")
    assert scores is not None
    assert scores.file_recall == 1.0


def test_aliased_repo_with_dot_git_url_still_joins(tmp_path: Path) -> None:
    # The alias map keys on the task.toml url, and the MCP identifier is minted
    # from that same url by derive_mirror_name — which drops `.git`. So the alias
    # map must drop it too, or a `.git` url keys "next.js.git", never matches the
    # identifier's "next.js", and the repo silently scores 0. No corpus url ends
    # in `.git` today; the schema does not forbid one, and the failure would be
    # silent, so pin the agreement rather than the current corpus.
    (tmp_path / "task.toml").write_text(
        '[[repos]]\nurl = "https://github.com/vercel/next.js.git"\npath = "nextjs"\n'
    )
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [_mcp_read("t1", "github.com/sg-evals/next.js--v14.0.0", "packages/next/package.json")],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "packages/next/package.json", "repo": "nextjs"}],
    )
    scores = compute_run_ir_scores(trace, gt, "t", "mcp_only")
    assert scores is not None
    assert scores.file_recall == 1.0


def test_ground_truth_naming_repo_upstream_matches_workspace_read(tmp_path: Path) -> None:
    # err-provenance-dual-docker-001 does the reverse: ground truth names the
    # repo "cli" (its upstream name) though the task clones it as "docker-cli".
    # Both sides must still land on the clone dir.
    (tmp_path / "task.toml").write_text(
        '[[repos]]\nurl = "https://github.com/docker/cli"\npath = "docker-cli"\n'
    )
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use(
                "t1",
                "Read",
                {"file_path": "/workspace/docker-cli/cli/command/container/create.go"},
            )
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "cli/command/container/create.go", "repo": "cli"}],
    )
    scores = compute_run_ir_scores(trace, gt, "err-provenance-dual-docker-001", "baseline")
    assert scores is not None
    assert scores.file_recall == 1.0


def test_repo_relative_ground_truth_is_qualified_with_its_repo(tmp_path: Path) -> None:
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "pkg/kubelet/worker.go", "repo": "kubernetes"},  # repo-relative
            {"path": "hyper/Cargo.toml", "repo": "hyper"},  # already qualified
            {"path": "no/repo/field.go"},  # no repo → unchanged
        ],
    )
    assert required_files_from_ground_truth(gt) == [
        "kubernetes/pkg/kubelet/worker.go",
        "hyper/Cargo.toml",
        "no/repo/field.go",
    ]


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
    # Re-reading the same file (bare "/" and case differences aside) is one
    # retrieval, not two.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("t1", "Read", {"file_path": WORKER}),
            _assistant_tool_use("t2", "Read", {"file_path": WORKER + "  "}),
        ],
    )
    assert retrieved_files_from_trace(trace) == [WORKER]


def test_workspace_read_and_repo_relative_twin_collapse_at_align(tmp_path: Path) -> None:
    # A /workspace/<repo>/ read and a bare repo-relative read of the same file
    # are no longer conflated by normalize_path (it cannot know the bare path's
    # repo). They still collapse where it matters — the align step rewrites both
    # onto the required key — so a relevant file is not double-counted and
    # context_efficiency is not deflated.
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("t1", "Read", {"file_path": WORKER}),
            _assistant_tool_use(
                "t2", "Read", {"file_path": "pkg/kubelet/prober/worker.go"}
            ),
        ],
    )
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "pkg/kubelet/prober/worker.go", "repo": "kubernetes"}],
    )
    scores = compute_run_ir_scores(trace, gt, "t", "baseline")
    assert scores is not None
    assert scores.n_retrieved == 1
    assert scores.file_recall == 1.0


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


def test_required_files_carries_repo(tmp_path: Path) -> None:
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [
            {"path": "pkg/kubelet/prober/worker.go", "repo": "kubernetes"},
            {"path": "pkg/kubelet/prober/worker_test.go", "repo": "kubernetes"},
        ],
    )
    assert required_files_from_ground_truth(gt) == [
        "kubernetes/pkg/kubelet/prober/worker.go",
        "kubernetes/pkg/kubelet/prober/worker_test.go",
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
    assert required_files_from_ground_truth(path) == ["r/a.go"]
    assert required_files_from_ground_truth(path, include_sufficient=True) == [
        "r/a.go",
        "r/b.go",
    ]


# ---------------------------------------------------------------------------
# compute_run_ir_scores — the false-zero guard
# ---------------------------------------------------------------------------


def test_compute_run_ir_scores_full_recall(tmp_path: Path) -> None:
    trace = _write_trace(
        tmp_path / "agent_trace.jsonl",
        [
            _assistant_tool_use("t1", "Read", {"file_path": WORKER}),
            _assistant_tool_use(
                "t2",
                "mcp__sourcegraph__read_file",
                {"repo": "github.com/sg-evals/kubernetes--v1.28.0", "path": WORKER_TEST},
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


def test_ambiguous_repo_lead_credits_both_manifests(tmp_path: Path) -> None:
    # Pins the KNOWN GAP in _workspace_path (EnterpriseBench-y64cp) rather than
    # letting it look closed. The tokio repo really has BOTH a root Cargo.toml and
    # a tokio/ crate dir, so GT {repo: tokio, path: tokio/Cargo.toml} cannot be
    # resolved by the scorer, and credit flows in both directions. This is
    # symmetric across arms — it does not bias the arm comparison — but it is a
    # precision leak, and it is the corpus that has to fix it.
    gt = _write_ground_truth(
        tmp_path / "ground_truth.json",
        [{"path": "tokio/Cargo.toml", "repo": "tokio"}],
    )
    for read in ("/workspace/tokio/Cargo.toml", "/workspace/tokio/tokio/Cargo.toml"):
        trace = _write_trace(
            tmp_path / "agent_trace.jsonl",
            [_assistant_tool_use("t1", "Read", {"file_path": read})],
        )
        scores = compute_run_ir_scores(trace, gt, "refactor-orchestration-tri-tokio-001", "baseline")
        assert scores is not None
        assert scores.file_recall == 1.0, read
