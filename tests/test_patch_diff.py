"""patch_diff must tell code from commentary.

The bug these pin (bead jn73.2.7, D2): a patch checkpoint grepping a raw
``git diff`` for a token credited a comment carrying that token as if it were
the fix. So the load-bearing cases are the negative ones — a token present in
the diff text and absent from the code.
"""

from __future__ import annotations

import pytest

from eb_verify.patch_diff import (
    UnknownLanguage,
    added_code,
    has_code_change,
    removed_code,
)


def diff(*added: str, removed: tuple[str, ...] = ()) -> str:
    """A minimal but realistically-shaped unified diff."""
    body = "".join(f"-{line}\n" for line in removed)
    body += "".join(f"+{line}\n" for line in added)
    return (
        "diff --git a/f b/f\n"
        "index 1111111..2222222 100644\n"
        "--- a/f\n"
        "+++ b/f\n"
        "@@ -1,3 +1,4 @@\n"
        " context line\n" + body
    )


class TestFileHeadersAreNotContent:
    """'+++ b/f' and '--- a/f' are headers, not added and removed lines."""

    def test_headers_excluded(self) -> None:
        assert added_code(diff("x = 1"), "python") == "x = 1"
        assert removed_code(diff("x = 1", removed=("y = 2",)), "python") == "y = 2"

    def test_context_lines_are_not_added(self) -> None:
        assert "context line" not in added_code(diff("x = 1"), "python")


class TestCommentOnlyIsNotCode:
    """The D2 repro, per language: a comment carrying the token scores nothing."""

    def test_ruby_comment_only(self) -> None:
        # Verbatim from the bead's reproduction against rack.
        d = diff("      # note: could use .split( someday")
        assert added_code(d, "ruby") == ""
        assert has_code_change(d, "ruby") is False

    def test_python_comment_only(self) -> None:
        d = diff("    # raise SerializerNotInstalled here")
        assert added_code(d, "python") == ""
        assert has_code_change(d, "python") is False

    @pytest.mark.parametrize("lang", ["rust", "java", "javascript"])
    def test_line_comment_only(self, lang: str) -> None:
        d = diff("    // use checked_mul to avoid overflow")
        assert added_code(d, lang) == ""
        assert has_code_change(d, lang) is False

    def test_trailing_comment_is_stripped_but_code_kept(self) -> None:
        d = diff("x = 1  # could use .split( someday")
        assert ".split(" not in added_code(d, "python")
        assert "x = 1" in added_code(d, "python")
        assert has_code_change(d, "python") is True


class TestBlockCommentsSpanLines:
    """A multi-line /* */ is prose on every one of its lines, not just the first."""

    @pytest.mark.parametrize("lang", ["rust", "java", "javascript"])
    def test_multiline_block_comment(self, lang: str) -> None:
        d = diff("/*", " * could use checked_mul here", " */")
        assert added_code(d, lang) == ""
        assert has_code_change(d, lang) is False

    def test_code_after_block_close_is_code(self) -> None:
        d = diff("/* note */ let x = checked_mul(a, b);")
        code = added_code(d, "rust")
        assert "note" not in code
        assert "checked_mul" in code

    def test_ruby_begin_end_block(self) -> None:
        d = diff("=begin", "could use .split( someday", "=end")
        assert added_code(d, "ruby") == ""


class TestBareStringIsNotCode:
    """A lone string literal is prose wearing quotes — the docstring variant."""

    def test_python_docstring_only(self) -> None:
        d = diff('    """could use .split( someday"""')
        assert added_code(d, "python") == ""
        assert has_code_change(d, "python") is False

    def test_multiline_docstring(self) -> None:
        d = diff('    """', "    could use .split( someday", '    """')
        assert added_code(d, "python") == ""

    def test_string_assigned_to_a_name_is_code(self) -> None:
        # Code outside the quotes ("note =") makes this a real statement.
        d = diff('note = "could use .split( someday"')
        assert has_code_change(d, "python") is True


class TestStringContentsSurvive:
    """Real upstream fixes here are string literals; stripping them would break them."""

    def test_split_literal_kept(self) -> None:
        d = diff("        ranges = range_spec.split('-')")
        assert ".split('-')" in added_code(d, "ruby")

    def test_proto_literal_kept(self) -> None:
        d = diff("      if (key === '__proto__') { continue; }")
        assert "__proto__" in added_code(d, "javascript")

    def test_comment_marker_inside_string_is_not_a_comment(self) -> None:
        d = diff('url = "http://example.com/path"')
        assert "example.com" in added_code(d, "javascript")

    def test_hash_inside_string_is_not_a_comment(self) -> None:
        d = diff('anchor = "section#heading"')
        assert "heading" in added_code(d, "ruby")


class TestRustLifetimesAreNotStrings:
    """Rust spells lifetimes with a quote; reading them as strings ate the line."""

    def test_lifetime_does_not_swallow_the_line(self) -> None:
        d = diff("fn read<'a>(&'a self) -> Result<usize> { self.checked_mul(2) }")
        assert "checked_mul" in added_code(d, "rust")

    def test_lifetime_then_comment_still_strips_the_comment(self) -> None:
        d = diff("fn read<'a>(&'a self) {} // could use checked_mul")
        code = added_code(d, "rust")
        assert "checked_mul" not in code
        assert "fn read" in code


class TestEscapes:
    def test_escaped_quote_does_not_end_the_string(self) -> None:
        d = diff('s = "he said \\"hi\\""  # .split(')
        assert ".split(" not in added_code(d, "python")

    def test_stray_quote_does_not_swallow_later_lines(self) -> None:
        # An apostrophe in a single-line string context must not leak state into
        # the next added line and hide real code behind it.
        d = diff("x = 'it", "y = checked_mul(a, b)")
        assert "checked_mul" in added_code(d, "rust")


class TestRemovedCode:
    def test_removed_comment_is_not_removed_code(self) -> None:
        d = diff(removed=("# range_spec =~ /(\\d*)-(\\d*)/",))
        assert removed_code(d, "ruby") == ""

    def test_removed_real_code(self) -> None:
        d = diff(removed=("return nil unless range_spec =~ /(\\d*)-(\\d*)/",))
        assert "range_spec =~" in removed_code(d, "ruby")

    def test_removal_alone_is_a_code_change(self) -> None:
        d = diff(removed=("x = 1",))
        assert has_code_change(d, "python") is True


class TestEmptyAndBlank:
    def test_empty_diff(self) -> None:
        assert has_code_change("", "python") is False
        assert added_code("", "python") == ""

    def test_blank_added_lines_are_not_code(self) -> None:
        assert has_code_change(diff("", "   "), "python") is False


class TestContextEstablishesCommentState:
    """A comment the agent did not open is still a comment.

    The delimiters of a block the agent edits *inside* live on unchanged context
    lines, so a scanner reading only ``+``/``-`` lines never sees them and reads
    the interior as bare code. That reopens D2 without a trailing comment:
    plant the token inside any docstring or license header the file already has.
    Real OSS files almost always have one, so this is the common case, not an
    edge case.
    """

    def test_line_added_inside_a_pre_existing_docstring_is_not_code(self) -> None:
        d = (
            "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,4 +1,5 @@\n"
            '     """A class docstring that already existed.\n'
            "\n"
            "     Wraps the object and marks it safe.\n"
            "+    Uses DOTALL internally for matching, see docs.\n"
            '     """\n'
        )
        assert added_code(d, "python") == ""
        assert has_code_change(d, "python") is False

    def test_line_added_inside_a_pre_existing_block_comment_is_not_code(self) -> None:
        d = (
            "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,4 @@\n"
            " /* Copyright 2011 the authors.\n"
            "+ * uses checkedMul(a, b) for the bounds\n"
            "  */\n"
        )
        assert added_code(d, "javascript") == ""
        assert has_code_change(d, "javascript") is False

    def test_code_added_after_a_pre_existing_comment_closes_is_code(self) -> None:
        """The positive control: context must not make real code disappear."""
        d = (
            "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,4 @@\n"
            " /* license header\n"
            "  */\n"
            "+var x = checkedMul(1, 2);\n"
        )
        assert "checkedMul" in added_code(d, "javascript")
        assert has_code_change(d, "javascript") is True

    def test_removed_line_inside_a_pre_existing_docstring_is_not_code(self) -> None:
        """The removed side reads its own context: deleting prose is not a fix."""
        d = (
            "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,4 +1,3 @@\n"
            '     """Existing docstring.\n'
            "-    mentions range_spec =~ here\n"
            '     """\n'
        )
        assert removed_code(d, "python") == ""


class TestStateDoesNotCrossFiles:
    """One `git diff` can span files; a dangling delimiter must not leak.

    ``--path`` is repeatable (incident-httpclient5-poolleak-timeout-001 passes
    two Java files), so one invocation scans one concatenated diff. A comment
    left open at the end of the first file's hunks would swallow the second
    file's genuine fix — under-crediting an honest agent, which is the mirror of
    the false-credit bug and just as wrong.
    """

    def test_dangling_comment_in_first_file_does_not_swallow_second(self) -> None:
        d = (
            "diff --git a/a.js b/a.js\n--- a/a.js\n+++ b/a.js\n@@ -1 +1,2 @@\n"
            "+/* an opener whose closer is on an unchanged line\n"
            "diff --git a/b.js b/b.js\n--- a/b.js\n+++ b/b.js\n@@ -1 +1,2 @@\n"
            "+var real_code = checkedMul(1, 2);\n"
        )
        assert "real_code" in added_code(d, "javascript")

    def test_unterminated_string_in_first_file_does_not_swallow_second(self) -> None:
        d = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n"
            '+x = """an opener whose closer is on an unchanged line\n'
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1,2 @@\n"
            "+parts = spec.split('-')\n"
        )
        assert "split(" in added_code(d, "python")


class TestUnknownLanguageFailsLoudly:
    """Defaulting to 'no comments' would restore the hole, invisibly."""

    def test_unknown_language_raises(self) -> None:
        with pytest.raises(UnknownLanguage):
            added_code(diff("x = 1"), "cobol")
