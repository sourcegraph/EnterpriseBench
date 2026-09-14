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

from eb_metrics.bash_reads import bash_read_files
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


def test_retrieved_files_big_int_literal_line_skipped(tmp_path: Path) -> None:
    # A 4300+-digit integer literal makes json.loads raise a BARE ValueError
    # (CPython's int-str guard, CVE-2020-10735), not the JSONDecodeError
    # subclass. The malformed line must be skipped, not abort extraction.
    trace = tmp_path / "agent_trace.jsonl"
    trace.write_text(
        '{"input_tokens": ' + "9" * 5000 + "}\n"
        + _assistant_tool_use("t1", "Read", {"file_path": WORKER}) + "\n"
    )
    assert retrieved_files_from_trace(trace) == [WORKER]


def test_retrieved_files_deeply_nested_line_skipped(tmp_path: Path) -> None:
    # A ~10k-deep-nested line makes json.loads raise RecursionError (a
    # RuntimeError, NOT a ValueError subclass). It must be skipped too.
    trace = tmp_path / "agent_trace.jsonl"
    trace.write_text(
        "[" * 10_000 + "]" * 10_000 + "\n"
        + _assistant_tool_use("t1", "Read", {"file_path": WORKER}) + "\n"
    )
    assert retrieved_files_from_trace(trace) == [WORKER]


def test_retrieved_files_stringified_tool_input_recursionerror(tmp_path: Path) -> None:
    # A tool_use whose 'input' is a *stringified* deeply-nested payload makes the
    # inner json.loads raise RecursionError (a RuntimeError, NOT a ValueError
    # subclass). That parse runs OUTSIDE the widened per-line try/except, so
    # uncaught it aborts the whole trace scan, not one line. The valid Read that
    # follows must still be captured.
    nested = "[" * 10_000 + "]" * 10_000
    trace = tmp_path / "agent_trace.jsonl"
    trace.write_text(
        _assistant_tool_use("t1", "Read", nested) + "\n"
        + _assistant_tool_use("t2", "Read", {"file_path": WORKER}) + "\n"
    )
    assert retrieved_files_from_trace(trace) == [WORKER]


def test_retrieved_files_stringified_tool_input_bigint(tmp_path: Path) -> None:
    # A stringified tool_input carrying a 4300+-digit int literal makes json.loads
    # raise a *bare* ValueError (CPython int-str conversion guard), NOT a
    # JSONDecodeError. The site caught only JSONDecodeError, so this escaped the
    # scan entirely. It must be treated as malformed and skipped.
    bigint_input = '{"file_path": ' + "1" * 4400 + "}"
    trace = tmp_path / "agent_trace.jsonl"
    trace.write_text(
        _assistant_tool_use("t1", "Read", bigint_input) + "\n"
        + _assistant_tool_use("t2", "Read", {"file_path": WORKER}) + "\n"
    )
    assert retrieved_files_from_trace(trace) == [WORKER]


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
        # A quoted operator is data even when it is the *whole* argument: the
        # lexer keeps quoting, so it can never be mistaken for a real operator.
        pytest.param("grep '(' src/handler.go", ["src/handler.go"], id="bare-quoted-paren"),
        pytest.param("grep '|' src/handler.go", ["src/handler.go"], id="bare-quoted-pipe"),
        pytest.param("grep ';' src/handler.go", ["src/handler.go"], id="bare-quoted-semicolon"),
        # ...and an unquoted paren *is* an operator, so subshells contribute.
        pytest.param(
            "(cat a/first.py; cat b/second.py)",
            ["a/first.py", "b/second.py"],
            id="subshell",
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
# Reads hidden from a naive argv[0]-plus-literal-token scan (EnterpriseBench-be50).
# Every shape here was previously extracted as [] — an *undercount*, and Bash is
# used by the baseline/hybrid arms but not by mcp_only, so each one biased those
# arms' recall down relative to MCP. They are the reason this table exists.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        pytest.param("FOO=1 cat src/handler.go", ["src/handler.go"], id="env-prefix"),
        pytest.param(
            "FOO=1 BAR=2 cat src/handler.go", ["src/handler.go"], id="env-prefix-multi"
        ),
        pytest.param("sudo cat src/handler.go", ["src/handler.go"], id="sudo"),
        pytest.param("time cat src/handler.go", ["src/handler.go"], id="time-wrapper"),
        pytest.param("/bin/cat src/handler.go", ["src/handler.go"], id="absolute-path-cmd"),
        pytest.param("sh -c 'cat src/handler.go'", ["src/handler.go"], id="sh-c"),
        pytest.param(
            'bash -c "grep foo src/handler.go"', ["src/handler.go"], id="bash-c-dquoted"
        ),
        pytest.param("echo $(cat src/handler.go)", ["src/handler.go"], id="command-subst"),
        pytest.param(
            'echo "$(cat src/handler.go)"', ["src/handler.go"], id="command-subst-in-dquotes"
        ),
        pytest.param("echo `cat src/handler.go`", ["src/handler.go"], id="backticks"),
        pytest.param(
            "find . -name x -exec grep -l foo config/app.yaml \\;",
            ["config/app.yaml"],
            id="find-exec-literal-file",
        ),
        # Newline is a sub-command separator (EnterpriseBench-2hum). Previously a
        # leading non-read line swallowed every read after it.
        pytest.param(
            "cd /workspace/zulip\ncat zerver/models/realms.py",
            ["zerver/models/realms.py"],
            id="newline-after-non-read",
        ),
        pytest.param(
            "cat a/first.py\ncat b/second.py",
            ["a/first.py", "b/second.py"],
            id="newline-separated-reads",
        ),
        pytest.param(
            "cd /workspace/zulip && \\\n  cat zerver/models/realms.py",
            ["zerver/models/realms.py"],
            id="line-continuation",
        ),
        pytest.param(
            "cat a/first.py\r\ncat b/second.py",
            ["a/first.py", "b/second.py"],
            id="crlf-line-endings",
        ),
        # A shell keyword occupies argv[0] and hides the command behind it.
        pytest.param(
            "if grep -q x a/first.py; then cat b/second.py; fi",
            ["a/first.py", "b/second.py"],
            id="if-then-fi",
        ),
        pytest.param(
            "for f in 1 2; do cat a/first.py; done",
            ["a/first.py"],
            id="for-do-done",
        ),
        pytest.param("! cat a/first.py", ["a/first.py"], id="negation"),
        pytest.param("{ cat a/first.py; }", ["a/first.py"], id="brace-group"),
        # ANSI-C quoting: `$'…'` is a quoted string, not a `$` glued to one.
        pytest.param("cat $'a/first.py'", ["a/first.py"], id="ansi-c-quoting"),
        pytest.param(
            "grep -q $'\\t' a/first.py", ["a/first.py"], id="ansi-c-tab-pattern"
        ),
        # `--` ends the options. Without it, a dash-shaped pattern reads as one
        # more flag, `pattern_seen` never flips, and the *file* is swallowed as
        # the pattern — the file vanishes entirely.
        pytest.param(
            "grep -- '--verbose' src/cli.py", ["src/cli.py"], id="end-of-options-dash-pattern"
        ),
        pytest.param(
            "grep -rn -- -foo.py src/handler.go",
            ["src/handler.go"],
            id="end-of-options-filename-pattern",
        ),
        pytest.param("grep -- pat src/x.py", ["src/x.py"], id="end-of-options-plain-pattern"),
        pytest.param("cat -- src/normal.py", ["src/normal.py"], id="end-of-options-cat"),
    ],
)
def test_bash_read_files_recovers_hidden_reads(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    assert _bash_files(tmp_path, command) == expected


# ---------------------------------------------------------------------------
# False positives: text that is *not* a read but looks like a path. Latent on
# today's corpus (none of them collide with a required file), but each would
# inflate a Bash arm the moment a trial searched for a filename that happens to
# be a required file — so they are removed rather than tracked.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A here-doc body is content the agent WROTE, not a file it read.
        pytest.param("cat <<'EOF'\nsrc/fake.py\nEOF", [], id="heredoc-body"),
        pytest.param("cat <<-EOF\n\tsrc/fake.py\n\tEOF", [], id="heredoc-dash-body"),
        pytest.param("cat <<< src/fake.py", [], id="here-string"),
        # A search pattern is not a read, whether it arrives via -e/-f or as
        # grep's first positional argument.
        pytest.param("grep -e foo.py src/target.py", ["src/target.py"], id="grep-e-pattern"),
        pytest.param(
            "grep -f patterns.txt src/target.py", ["src/target.py"], id="grep-f-patternfile"
        ),
        pytest.param("grep utils.py src/target.py", ["src/target.py"], id="grep-positional-pattern"),
        pytest.param("rg config.py src/target.py", ["src/target.py"], id="rg-positional-pattern"),
        pytest.param("awk prog.awk src/target.py", ["src/target.py"], id="awk-positional-program"),
        # A context/count flag takes a *separate* value. Miss that and the value
        # is mistaken for the pattern, which promotes the real pattern into the
        # file slot — inventing a read. Found by the corpus A/B, not by reading.
        pytest.param(
            "grep -A 3 utils.py src/target.py", ["src/target.py"], id="grep-context-flag-value"
        ),
        pytest.param(
            "grep -m 5 config.py src/target.py", ["src/target.py"], id="grep-maxcount-flag-value"
        ),
        pytest.param(
            "grep -n -A 5 '^lodash@' /workspace/lodash/yarn.lock",
            ["/workspace/lodash/yarn.lock"],
            id="grep-context-flag-real-corpus-shape",
        ),
        pytest.param(
            "awk -F , prog.awk src/target.py", ["src/target.py"], id="awk-field-separator-value"
        ),
        # ...but with -e supplying the pattern, the first positional IS a file.
        pytest.param(
            "grep -e foo src/target.py", ["src/target.py"], id="grep-e-then-positional-is-file"
        ),
        # An unquoted `#` starts a comment; paths inside it were never read.
        pytest.param(
            "cat src/handler.go  # see also src/other.go",
            ["src/handler.go"],
            id="trailing-comment",
        ),
    ],
)
def test_bash_read_files_rejects_non_reads(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    assert _bash_files(tmp_path, command) == expected


# ---------------------------------------------------------------------------
# The documented floor: shapes whose file set is decided by the filesystem or by
# stdin, not by the command text. They are NOT statically recoverable, so they
# extract to [] — a known, deliberate undercount, not a silent one. Recovering
# them means reading the command's *output* from the trace (EnterpriseBench-jqyhg).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        pytest.param(
            "find /workspace -name package.json | xargs grep -l lodash",
            [],
            id="xargs-files-from-stdin",
        ),
        pytest.param(
            "grep -l lodash /workspace/jest/packages/*/package.json",
            [],
            id="unexpanded-glob",
        ),
        pytest.param(
            "find src -name '*.py' -exec cat {} \\;",
            [],
            id="find-exec-placeholder",
        ),
        pytest.param(
            "xargs -I{} grep -l foo {}",
            [],
            id="xargs-replace-placeholder",
        ),
    ],
)
def test_bash_read_files_known_floor(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    # Asserting [] pins the floor: a future output-recovery pass should turn
    # these into real files, and this test is where that change announces itself.
    assert _bash_files(tmp_path, command) == expected


def test_bash_redirect_target_still_counted(tmp_path: Path) -> None:
    # Pre-existing false positive, preserved deliberately: an output-redirect
    # target is counted as a read. Fixing it changes scoring on a different
    # axis than this bug, so it is tracked separately (EnterpriseBench-qefr).
    assert _bash_files(tmp_path, "cat src/handler.go > /tmp/out.txt") == [
        "src/handler.go",
        "/tmp/out.txt",
    ]


def test_bash_heredoc_body_excluded_but_redirect_target_kept(tmp_path: Path) -> None:
    # The two rules meet: the here-doc body is dropped (it was written, not
    # read) while the redirect target stays counted (EnterpriseBench-qefr).
    assert _bash_files(tmp_path, "cat <<'EOF' > out.txt\nsrc/fake.py\nEOF") == ["out.txt"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A substitution glued to literal text makes ONE word whose value the
        # command text does not contain. Erasing the substitution and keeping
        # the leftover literal invents a path the agent never opened — the same
        # class of fabrication as crediting an unexpanded glob, and the exact
        # thing the module's "no guessing" contract forbids. These are mainstream
        # idioms, not corner cases.
        pytest.param('cat "$(pwd)/config.py"', [], id="subst-prefix-quoted"),
        pytest.param("cat $(pwd)/x.py", [], id="subst-prefix-bare"),
        pytest.param("cat src/$(basename x).py", [], id="subst-infix"),
        pytest.param("cat pre$(echo mid)post.py", [], id="subst-middle"),
        pytest.param("cat `pwd`/x.py", [], id="subst-prefix-backtick"),
        # ...but a read INSIDE the substitution is still real and still counted.
        pytest.param("cat $(cat inner.py)/x.py", ["inner.py"], id="subst-inner-read-kept"),
        # A substitution standing alone is a separate argument, not glued to
        # anything: the words around it keep their own meaning.
        pytest.param("echo $(cat x.py)", ["x.py"], id="subst-standalone"),
        pytest.param("cat $(cmd) file.py", ["file.py"], id="subst-then-real-file"),
        # Regression the fix buys back: the substitution occupies the pattern
        # slot, so the file after it is a FILE. Erasing the substitution made
        # grep read `f.py` as its pattern and the real read vanished.
        pytest.param("grep $(get_pattern) f.py", ["f.py"], id="subst-as-grep-pattern"),
    ],
)
def test_bash_substitution_adjacent_to_text_is_not_a_file(
    command: str, expected: list[str]
) -> None:
    assert bash_read_files(command) == expected


def test_bash_backtick_keeps_non_special_escapes() -> None:
    # POSIX: inside backquotes a backslash keeps its literal value except before
    # $, ` or \. Unescaping everything splits one escaped-space filename into
    # two fabricated reads.
    assert bash_read_files(r"echo `cat foo.txt\ bar.py`") == ["foo.txt bar.py"]


def test_bash_fd_redirect_digits_are_not_files() -> None:
    # `2>` is a redirect on fd 2, not the file "2"; `>&1` dups fd 1, not "1".
    # Inert today only because a filter in another module drops extension-less
    # candidates — bash_read_files promises paths, so it should not emit these.
    assert bash_read_files("cat a.py 2>/dev/null") == ["a.py", "/dev/null"]
    assert bash_read_files("cat a.py 2>&1") == ["a.py"]


def test_bash_nested_find_exec_is_depth_bounded() -> None:
    # `find -exec find -exec …` recurses through _program_read_files, which is
    # the one path that does NOT re-enter bash_read_files — so before the guard
    # moved there it climbed past _MAX_SUBST_DEPTH to Python's own recursion
    # limit and raised. Extraction must be *total*: a scoring run reads whatever
    # commands a model happened to emit, and one RecursionError aborts the whole
    # batch rescore in iter_run_retrievals, not just the offending trial.
    command = "find ." + " -exec find ." * 500 + " -exec cat x.py \\;" + " \\;" * 500
    assert bash_read_files(command) == []  # bounded, and must not raise


def test_bash_shallow_find_exec_still_reads() -> None:
    # The bound must not cost the real shape it guards.
    assert bash_read_files("find . -name '*.yaml' -exec grep -l db cfg.yaml \\;") == [
        "cfg.yaml"
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
