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


# ---------------------------------------------------------------------------
# Multi-line Bash — a newline separates sub-commands just like `;` does.
# Before EnterpriseBench-2hum a multi-line command collapsed into ONE
# sub-command named after the first word of the FIRST line, so every read on
# every later line was silently dropped — understating the Bash-mediated
# (baseline/hybrid) arms only.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # The three shapes from the bead. argv[0] of line 1 is `#`, `cd`, `echo`
        # — none a read command — so each of these previously yielded nothing.
        pytest.param(
            '# Look at the lockfile\nsed -n "1970,1990p" /workspace/jest/yarn.lock',
            ["/workspace/jest/yarn.lock"],
            id="comment-led-block",
        ),
        pytest.param(
            "cd /workspace/spring-boot\ncat build.gradle",
            ["build.gradle"],
            id="cd-then-read",
        ),
        pytest.param(
            'echo "=== deps ==="\ngrep -n require src/handler.go',
            ["src/handler.go"],
            id="echo-then-read",
        ),
        # Reads inside a loop body land on their own lines and now count.
        pytest.param(
            "for d in one two; do\n  cat config/base.yaml\ndone",
            ["config/base.yaml"],
            id="for-loop-body",
        ),
        # An apostrophe in a comment ("Let's") must not open a quote — if it did,
        # the newline would look quoted, the lines would glue back together, and
        # this bug would silently return. This is why comment-stripping and
        # newline-splitting have to be one scan.
        pytest.param(
            "# Let's find the config\ncat config/app.yaml",
            ["config/app.yaml"],
            id="apostrophe-in-comment",
        ),
        # A trailing comment's words were argv tokens of the read sub-command, so
        # any path in the prose counted as a read — a false POSITIVE, opposite in
        # direction to the dropped-lines false negative above.
        pytest.param(
            "grep pattern file.py # see also other/note.py",
            ["file.py"],
            id="trailing-comment-not-a-read",
        ),
        # `#` opens a comment only at a word boundary, as in a real shell. shlex's
        # own `commenters` rule would treat this `#` as a comment start and take
        # the file with it.
        pytest.param(
            "grep foo#bar src/handler.go",
            ["src/handler.go"],
            id="hash-inside-word",
        ),
        # A quoted `#` is data, not a comment.
        pytest.param("grep '#' src/handler.go", ["src/handler.go"], id="quoted-hash"),
        pytest.param(
            'grep "#define MAX" src/handler.go',
            ["src/handler.go"],
            id="quoted-hash-in-pattern",
        ),
        # A newline inside quotes is data: one command, file kept.
        pytest.param(
            "grep -n 'foo\nbar' src/handler.go",
            ["src/handler.go"],
            id="quoted-newline-is-data",
        ),
        # A backslash-escaped newline is a line continuation: still ONE command,
        # so `grep` stays argv[0] and its file survives.
        pytest.param(
            "grep -n foo \\\n  src/handler.go",
            ["src/handler.go"],
            id="backslash-continuation",
        ),
        # An unterminated quote swallows the remaining newlines and the line fails
        # to lex — degrading to today's behaviour (nothing), never to a mangled path.
        pytest.param(
            "cat 'a/first.py\ncat b/second.py",
            [],
            id="unbalanced-quote-multiline",
        ),
    ],
)
def test_bash_read_files_splits_on_newlines(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    assert _bash_files(tmp_path, command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A here-doc body is DATA the agent wrote, not commands it ran. EB's own
        # artifacts (runbooks, incident reports) are full of shell commands, so
        # without a here-doc model an agent writing a runbook earns retrieval
        # credit for every file it merely wrote *about*. The redirect target
        # itself is still counted (the qefr false positive, preserved).
        pytest.param(
            "cat > /workspace/agent_output/runbook.md <<'EOF'\n"
            "grep -rn timeout /workspace/repo/src/never_opened.py\n"
            "EOF",
            ["/workspace/agent_output/runbook.md"],
            id="heredoc-body-led-by-read-cmd",
        ),
        # `tee`/`python3`-led here-docs are the live corpus shape. Splitting the
        # body into sub-commands would credit a file the agent never opened —
        # a false positive that inflates the Bash-mediated arms specifically.
        pytest.param(
            "tee /workspace/agent_output/report.md <<'EOF'\n"
            "grep -rn panic /workspace/kafka/KafkaApis.scala\n"
            "EOF",
            [],
            id="heredoc-tee-led",
        ),
        pytest.param(
            "python3 <<'EOF'\n"
            "head = open('/workspace/webpack/yarn.lock').read()\n"
            "EOF",
            [],
            id="heredoc-python-led",
        ),
        # An apostrophe in a here-doc body must not open a quote and swallow the
        # real read that follows the body.
        pytest.param(
            "cat > notes.md <<'EOF'\n"
            "It's a race in the retry loop\n"
            "EOF\n"
            "grep -n retry /workspace/repo/src/real_read.py",
            ["notes.md", "/workspace/repo/src/real_read.py"],
            id="heredoc-body-apostrophe",
        ),
        # `<<-` strips leading tabs before the delimiter.
        pytest.param(
            "cat > out.md <<-EOF\n\tsee src/never.py\n\tEOF\ncat src/real.py",
            ["out.md", "src/real.py"],
            id="heredoc-dash-tab-delimiter",
        ),
        # A here-STRING is not a here-doc: nothing to skip.
        pytest.param(
            'grep -n foo src/handler.go <<< "$payload"',
            ["src/handler.go"],
            id="here-string-not-heredoc",
        ),
        # One line can open SEVERAL bodies, and they close in order — which is why
        # the pending delimiters are a queue and not just the last one seen. Here
        # A's body contains a line reading `B`, so skipping straight to B's
        # delimiter would stop early and spill the rest of A's body back into the
        # command stream, crediting a `grep` the agent never ran against a file it
        # never opened. Only the read that runs AFTER both bodies is real.
        pytest.param(
            "cat <<A <<B\n"
            "B\n"
            "grep -rn x /workspace/repo/never_opened.py\n"
            "A\n"
            "second body\n"
            "B\n"
            "cat /workspace/repo/real.py",
            ["/workspace/repo/real.py"],
            id="stacked-heredocs-close-in-order",
        ),
        # An escaped space keeps the `#` mid-word, so it is NOT a comment and the
        # file argument survives. Deriving the word boundary from the previous
        # character alone would see a blank here and eat the rest of the line.
        pytest.param(
            "grep -rn TODO\\ #123 src/handler.go",
            ["src/handler.go"],
            id="escaped-space-before-hash",
        ),
    ],
)
def test_bash_read_files_heredocs_and_word_boundaries(
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
