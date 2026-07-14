"""Tests for eb_metrics.shell_lex — the quoting-aware shell lexer.

The invariant the read-extractor depends on: a shell metacharacter is an
operator *only* when it is unquoted. Every test here is ultimately about that
one distinction, because losing it is what made a quoted ``|`` truncate a
command and take its file argument with it.
"""

from __future__ import annotations

import pytest

from eb_metrics.shell_lex import OP, SUBST, WORD, Token, lex


def kinds(command: str) -> list[tuple[str, str]]:
    return [(t.text, t.kind) for t in lex(command)]


def words(command: str) -> list[str]:
    return [t.text for t in lex(command) if t.kind == WORD]


def ops(command: str) -> list[str]:
    return [t.text for t in lex(command) if t.kind == OP]


def substs(command: str) -> list[str]:
    return [t.text for t in lex(command) if t.kind == SUBST]


# ---------------------------------------------------------------------------
# Quoting decides word-vs-operator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        pytest.param("cat a.py | grep b", ["|"], id="unquoted-pipe-is-op"),
        pytest.param("cat a.py|grep b", ["|"], id="unspaced-pipe-is-op"),
        pytest.param("grep '|' a.py", [], id="single-quoted-pipe-is-word"),
        pytest.param('grep "|" a.py', [], id="double-quoted-pipe-is-word"),
        pytest.param(r"grep \| a.py", [], id="escaped-pipe-is-word"),
        pytest.param("grep 'a|b' a.py", [], id="pipe-inside-quoted-pattern"),
        pytest.param("(cat a.py)", ["(", ")"], id="unquoted-parens-are-ops"),
        pytest.param("grep '(' a.py", [], id="quoted-paren-is-word"),
        pytest.param("cat a.py; cat b.py", [";"], id="semicolon"),
        pytest.param("cat a.py && cat b.py", ["&&"], id="andand"),
        pytest.param("cat a.py || cat b.py", ["||"], id="oror"),
        pytest.param("cat a.py\ncat b.py", ["\n"], id="newline-is-op"),
    ],
)
def test_operators_only_when_unquoted(command: str, expected: list[str]) -> None:
    assert ops(command) == expected


def test_quoted_operator_stays_in_its_word() -> None:
    assert words("grep '|' src/handler.go") == ["grep", "|", "src/handler.go"]


def test_adjacent_quotes_concatenate_into_one_word() -> None:
    # Shell joins quoted and unquoted runs with no intervening whitespace.
    assert words("cat 'src/'\"handler\".go") == ["cat", "src/handler.go"]


# ---------------------------------------------------------------------------
# Newlines, comments, continuations
# ---------------------------------------------------------------------------


def test_newline_separates_commands() -> None:
    assert kinds("cd /w\ncat a.py") == [
        ("cd", WORD),
        ("/w", WORD),
        ("\n", OP),
        ("cat", WORD),
        ("a.py", WORD),
    ]


def test_backslash_newline_is_a_continuation_not_a_separator() -> None:
    assert ops("cat \\\n  a.py") == []
    assert words("cat \\\n  a.py") == ["cat", "a.py"]


def test_unquoted_hash_starts_a_comment() -> None:
    assert words("cat a.py  # see b.py") == ["cat", "a.py"]


def test_comment_ends_at_newline() -> None:
    assert words("cat a.py # note\ncat b.py") == ["cat", "a.py", "cat", "b.py"]


def test_hash_inside_a_word_is_literal() -> None:
    # `#` only opens a comment at the start of a word (shell rule): a fragment
    # identifier or a quoted `#define` is data.
    assert words("grep '#define' a.h") == ["grep", "#define", "a.h"]
    assert words("cat docs/api.md#anchor") == ["cat", "docs/api.md#anchor"]


# ---------------------------------------------------------------------------
# Command substitution
# ---------------------------------------------------------------------------


def test_dollar_paren_becomes_a_subst_token() -> None:
    assert substs("echo $(cat a.py)") == ["cat a.py"]


def test_backticks_become_a_subst_token() -> None:
    assert substs("echo `cat a.py`") == ["cat a.py"]


def test_subst_inside_double_quotes_is_still_a_subst() -> None:
    assert substs('echo "$(cat a.py)"') == ["cat a.py"]


def test_nested_dollar_paren_is_captured_whole() -> None:
    assert substs("echo $(cat $(ls b.txt))") == ["cat $(ls b.txt)"]


def test_paren_inside_quotes_does_not_close_a_subst() -> None:
    assert substs("""echo $(grep ')' a.py)""") == ["grep ')' a.py"]


def test_arithmetic_expansion_is_not_a_command() -> None:
    assert substs("echo $((1 + 2))") == []


# ---------------------------------------------------------------------------
# Here-docs and here-strings
# ---------------------------------------------------------------------------


def test_heredoc_body_is_not_lexed() -> None:
    assert words("cat <<'EOF'\nsrc/fake.py\nEOF") == ["cat", "EOF"]


def test_heredoc_dash_strips_leading_tabs_from_the_delimiter() -> None:
    assert words("cat <<-EOF\n\tsrc/fake.py\n\tEOF") == ["cat", "EOF"]


def test_lexing_resumes_after_a_heredoc_body() -> None:
    assert words("cat <<EOF\nbody.py\nEOF\ncat real.py") == ["cat", "EOF", "cat", "real.py"]


def test_unterminated_heredoc_consumes_the_rest() -> None:
    assert words("cat <<EOF\nsrc/fake.py\n") == ["cat", "EOF"]


def test_here_string_operator_is_distinct_from_heredoc() -> None:
    assert kinds("cat <<< src/fake.py") == [
        ("cat", WORD),
        ("<<<", OP),
        ("src/fake.py", WORD),
    ]


# ---------------------------------------------------------------------------
# Redirects — emitted as operators, but NOT command separators. The consumer
# relies on this to keep counting redirect targets (EnterpriseBench-qefr).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected_ops"),
    [
        pytest.param("cat a.py > out.txt", [">"], id="truncate"),
        pytest.param("cat a.py >> out.txt", [">>"], id="append"),
        pytest.param("cat < a.py", ["<"], id="stdin"),
        pytest.param("cat a.py 2>/dev/null", [">"], id="fd-redirect"),
        pytest.param("cat a.py &> out.txt", ["&>"], id="both-streams"),
    ],
)
def test_redirect_operators(command: str, expected_ops: list[str]) -> None:
    assert ops(command) == expected_ops


# ---------------------------------------------------------------------------
# Malformed input: read someone else's shell, do not validate it.
# ---------------------------------------------------------------------------


def test_unterminated_quote_drops_the_partial_word() -> None:
    # The half-quoted path must not surface as a token — it was never a
    # complete argument, and emitting it would invent a retrieval.
    assert lex("cat 'src/handler.go") == [Token("cat", WORD)]


def test_unterminated_double_quote_drops_the_partial_word() -> None:
    assert lex('cat "src/handler.go') == [Token("cat", WORD)]


def test_unterminated_subst_drops_the_partial_word() -> None:
    assert lex("echo $(cat a.py") == [Token("echo", WORD)]


def test_empty_command_lexes_to_nothing() -> None:
    assert lex("") == []
    assert lex("   \t  ") == []


def test_pathological_nesting_does_not_blow_the_stack() -> None:
    # A substitution inside a quoted run inside a substitution is mutual
    # recursion (_match_paren -> _scan_double -> _scan_dollar_paren). Trace
    # commands are model-authored and this parser is not allowed to raise on
    # them: a RecursionError here would abort a whole scoring run, and the run
    # that dies is the one that dies *silently* in a batch rescore.
    # Deeper than _MAX_NEST reads as malformed, which the module already
    # defines as "drop the partial word" rather than "guess".
    deep = '$("' * 5000 + "cat a.py" + '")' * 5000
    assert lex(deep) == []


def test_real_nesting_within_the_bound_still_lexes() -> None:
    # The bound must not cost a command anyone would actually write: a
    # substitution inside a quoted run is the shape the bound guards, and the
    # one-deep case is ordinary.
    assert Token("cat a.py", SUBST) in lex('echo "$(cat a.py)"')
