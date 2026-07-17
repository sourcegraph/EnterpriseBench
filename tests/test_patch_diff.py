"""patch_diff must tell code from commentary.

The bug these pin (bead jn73.2.7, D2): a patch checkpoint grepping a raw
``git diff`` for a token credited a comment carrying that token as if it were
the fix. So the load-bearing cases are the negative ones — a token present in
the diff text and absent from the code.
"""

from __future__ import annotations

import re

import pytest

from eb_verify.patch_diff import (
    STRING_MARK,
    UnknownLanguage,
    _side_lines,
    added_code,
    has_code_change,
    marked_code,
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


# Everything Python's str.splitlines() breaks on that git does not end a diff
# line with. Git terminates lines on "\n" alone, so each of these arrives as an
# ordinary character INSIDE a line the agent wrote.
_NON_GIT_SEPARATORS = [
    pytest.param("\v", id="vertical-tab"),
    pytest.param("\f", id="form-feed"),
    pytest.param("\x1c", id="file-separator"),
    pytest.param("\x1d", id="group-separator"),
    pytest.param("\x1e", id="record-separator"),
    pytest.param("\x85", id="next-line"),
    pytest.param(" ", id="line-separator"),
    pytest.param(" ", id="paragraph-separator"),
    pytest.param("\r", id="lone-carriage-return"),
]


class TestOnlyNewlineEndsADiffLine:
    """The grammar is only as sound as the line stream it runs on.

    ``str.splitlines`` breaks on nine separators git does not, so it reads one
    line git emitted as several — and the agent, who writes the file, chooses
    what the extras say. A form feed inside a comment mints an added line
    carrying a fix that was never written, in a file that still parses and still
    has the vulnerability. Every rule below this — the hunk gate, the comment
    stripper — is downstream of the split and cannot see past it (bead jn73.2.7).
    """

    @pytest.mark.parametrize("sep", _NON_GIT_SEPARATORS)
    def test_separator_in_a_comment_cannot_mint_an_added_line(self, sep: str) -> None:
        """One comment git emitted must not become a comment AND a fix."""
        forged = 'CONTENT_DECODERS = ["gzip", "x-gzip"]'
        d = diff(f"# perf notes{sep}+{forged}")

        assert added_code(d, "python") == "", (
            f"a comment carrying {sep!r} minted an added code line: "
            f"{added_code(d, 'python')!r}"
        )
        assert has_code_change(d, "python") is False

    @pytest.mark.parametrize("sep", _NON_GIT_SEPARATORS)
    def test_separator_in_a_context_line_cannot_mint_an_added_line(self, sep: str) -> None:
        """The same, from the baseline the agent committed rather than its edit.

        Whole-file context puts every line the agent ever committed into the
        diff, so a poisoned baseline line is a forgery site for any diff that
        touches the file — the agent's own edit can then be one honest comment
        a thousand lines away.
        """
        forged = 'CONTENT_DECODERS = ["gzip", "x-gzip"]'
        d = (
            "diff --git a/f.py b/f.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,3 +1,4 @@\n"
            f" # perf notes{sep}+{forged}\n"
            " import io\n"
            "+# done\n"
        )
        assert added_code(d, "python") == "", (
            f"a CONTEXT line carrying {sep!r} minted an added code line: "
            f"{added_code(d, 'python')!r}"
        )
        assert has_code_change(d, "python") is False

    def test_a_real_fix_still_reads_across_ordinary_newlines(self) -> None:
        """The control: splitting on "\\n" alone must not lose real added lines."""
        d = diff("x = 1", "y = 2")
        assert added_code(d, "python") == "x = 1\ny = 2"


class TestJavaTextBlockIsProse:
    """A Java text block is a string, so its interior is not code.

    Same rule as a lone Python docstring, and the same bypass without it: the
    agent adds one ``String`` constant whose text block spells the fix, touches
    nothing else, and the token reads as bare code (bead jn73.2.7). Rust's
    multi-line ``"`` strings and Ruby's heredocs are the same class and are NOT
    closed — see EnterpriseBench-jn73.2.7.6.
    """

    def test_text_block_interior_is_not_code(self) -> None:
        d = diff(
            '  static final String NOTES = """',
            "      xlen = xlen1 | xlen2 << 8;",
            '      """;',
        )
        assert "xlen1" not in added_code(d, "java"), added_code(d, "java")

    def test_code_after_the_text_block_closes_is_code(self) -> None:
        """The positive control: the rule must not swallow the rest of the file."""
        d = diff(
            '  static final String NOTES = """',
            "      notes",
            '      """;',
            "  xlen = xlen1 | xlen2 << 8;",
        )
        assert "xlen1" in added_code(d, "java")

    def test_ordinary_java_strings_still_read(self) -> None:
        """A lone quote still opens a one-line string; the new rule did not shadow it.

        Read on the strings-included view: the default bare one drops interiors
        on purpose, so it cannot tell "the string closed" from "the string ate
        the file".
        """
        d = diff('x = "gzip";')
        assert added_code(d, "java", include_strings=True) == 'x = "gzip";'
        assert added_code(d, "java") == 'x = "";'  # bare view: interior dropped

    def test_empty_string_does_not_open_a_text_block(self) -> None:
        """`""` is two delimiters, not the first two thirds of one."""
        assert added_code(diff('String s = "";', "int n = 1;"), "java") == (
            'String s = "";\nint n = 1;'
        )


class TestIncrementLinesAreContentNotHeaders:
    """``++counter;`` is a pre-increment, not the ``+++ b/f`` file header.

    The header was recognised by testing the RAW prefixed line against ``+++``,
    which an added ``++counter;`` matches exactly — it renders as ``+++counter;``.
    So a genuine JS/Java/TS pre-increment fix vanished from ``added_code`` and
    scored 0 on check_patch_site (bead jn73.2.7, defect C). Content lines are the
    ones inside a hunk; every header precedes the first ``@@``.
    """

    def test_added_pre_increment_is_code(self) -> None:
        assert added_code(diff("++counter;"), "javascript") == "++counter;"
        assert has_code_change(diff("++counter;"), "javascript") is True

    def test_removed_pre_decrement_is_code(self) -> None:
        d = diff("x = 1;", removed=("--counter;",))
        assert removed_code(d, "javascript") == "--counter;"

    def test_post_increment_control(self) -> None:
        """The spelling that never collided with the header, for contrast."""
        assert added_code(diff("counter++;"), "javascript") == "counter++;"

    def test_file_header_is_still_not_content(self) -> None:
        """The control: admitting ``++counter;`` must not admit ``+++ b/f``."""
        assert "b/f" not in added_code(diff("++counter;"), "javascript")

    def test_no_newline_marker_is_kept_out_of_the_line_stream(self) -> None:
        """git's own prose is not a line of the file, on either side.

        Asserted through ``_side_lines`` rather than ``added_code``: the marker
        carries no ``+``/``-``, so it lands on the CONTEXT branch, and context is
        never emitted as added code. A test at the public API therefore passes
        with the guard deleted. What the guard actually buys is that the marker
        is not fed to the scanner as if it were a line the file contains — inert
        for every language registered today, wrong in principle, and the sort of
        thing that stops being inert when a language is added.
        """
        section = (diff("x = 1") + "\\ No newline at end of file\n").split("\n")

        for prefix in ("+", "-"):
            texts = [text for text, _ in _side_lines(section, prefix)]
            assert not any("No newline" in t for t in texts), (
                f"git's no-newline marker reached the scanner as a {prefix!r}-side "
                f"line: {texts}"
            )

    def test_binary_section_yields_no_content(self) -> None:
        """No hunk, so nothing in it is content — including git's own prose."""
        d = (
            "diff --git a/i.png b/i.png\n"
            "index 1111111..2222222 100644\n"
            "Binary files a/i.png and b/i.png differ\n"
        )
        assert added_code(d, "python") == ""

    def test_rename_only_section_yields_no_content(self) -> None:
        d = (
            "diff --git a/a.py b/b.py\n"
            "similarity index 100%\n"
            "rename from a.py\n"
            "rename to b.py\n"
        )
        assert added_code(d, "python") == ""


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


class TestBareViewDropsStringInteriors:
    """The default view drops string *contents* — residual A.

    A token that appears only inside a string literal is a dead assignment, not
    a fix: ``DUMMY = "range_spec.split(dash)"`` left the vulnerable regex
    untouched yet scored full semantic credit because the grep read the string
    (bead jn73.2.7, residual A). The default view drops the interior so the
    token reads as absent; ``include_strings=True`` keeps it for the tasks whose
    genuine fix is a literal.
    """

    def test_split_method_call_survives_bare(self) -> None:
        # `.split(` is a method call outside the string — bare code, kept.
        d = diff("        ranges = range_spec.split('-')")
        assert ".split(" in added_code(d, "ruby")

    def test_dead_string_token_absent_from_bare(self) -> None:
        # THE residual-A negative: the token lives only inside the string.
        d = diff("        DUMMY = 'range_spec.split(dash)'")
        assert ".split(dash)" not in added_code(d, "ruby")
        assert ".split(dash)" in added_code(d, "ruby", include_strings=True)

    def test_proto_literal_only_under_include_strings(self) -> None:
        d = diff("      if (key === '__proto__') { continue; }")
        assert "__proto__" not in added_code(d, "javascript")
        assert "__proto__" in added_code(d, "javascript", include_strings=True)
        # The structural token IS bare, so a co-line check can still anchor it.
        assert "===" in added_code(d, "javascript")

    def test_comment_marker_inside_string_is_not_a_comment(self) -> None:
        # The '//' is inside the string, so it must not truncate the line: bare
        # code after the string ( ';' ) survives, and include-strings keeps all.
        d = diff('url = "http://example.com/path";')
        assert "example.com" not in added_code(d, "javascript")
        assert "example.com" in added_code(d, "javascript", include_strings=True)

    def test_hash_inside_string_is_not_a_comment(self) -> None:
        d = diff('anchor = "section#heading"')
        assert "heading" not in added_code(d, "ruby")
        assert "heading" in added_code(d, "ruby", include_strings=True)


class TestMarkedView:
    """``marked_code`` replaces string delimiters with STRING_MARK, keeping contents.

    This is the view the literal-fix tasks match against: a structural token flush
    against the mark proves it operates on the literal, which a flat text view
    cannot show — there a token inside an unrelated string reads identically.
    """

    def test_delimiters_become_marks_contents_kept(self) -> None:
        d = diff("if (key === '__proto__') { continue; }")
        m = marked_code(d, "javascript", added=True)
        assert f"=== {STRING_MARK}__proto__{STRING_MARK}" in m

    def test_a_string_token_is_flanked_by_marks_not_bare(self) -> None:
        # The whole dead line is one string: the guard tokens inside it sit
        # between the outer marks, never flush against a mark of their own.
        d = diff('const D = "if (key === \'__proto__\') continue";')
        m = marked_code(d, "javascript", added=True)
        assert f"=== {STRING_MARK}__proto__" not in m
        assert m.startswith("const D = " + STRING_MARK)

    def test_comment_stripped_before_marking(self) -> None:
        d = diff("x = 1  // note '__proto__'")
        assert STRING_MARK not in marked_code(d, "javascript", added=True)

    def test_input_mark_byte_is_sanitized(self) -> None:
        # An agent cannot plant a raw STRING_MARK to forge a string edge: any in
        # the input is stripped, so every mark in the view is one _scan wrote.
        d = diff(f"key === {STRING_MARK}__proto__{STRING_MARK}")  # bare, not a string
        m = marked_code(d, "javascript", added=True)
        assert STRING_MARK not in m

    def test_multiline_blob_keeps_a_wrapped_fix_adjacent(self) -> None:
        # Lines join with '\n', so a structural token and its literal wrapped
        # across lines are still separated only by whitespace.
        d = diff("if (key ===", "    '__proto__') continue;")
        m = marked_code(d, "javascript", added=True)
        assert re.search(rf"===\s*{re.escape(STRING_MARK)}__proto__", m)


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
