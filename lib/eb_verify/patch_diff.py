"""Comment-aware reading of an agent's uncommitted diff.

Why this exists: a patch checkpoint that greps a raw ``git diff`` for a token
cannot tell code from commentary. Appending one line — ``# could use .split(
someday`` — to the real fix site satisfied both "the diff touches the fix site"
and "the diff introduces a split-based parse" while the vulnerable regex sat
untouched, paying 60-65% of task weight for a comment (bead jn73.2.7, D2). Every
patch checkpoint on the enterprise-hard tasks shared the shape, so the fix
belongs here rather than in fourteen copies of the same bash.

The rule this module implements: **a line that is nothing but a comment or a
bare string literal is prose, not code.** So ``.split(`` inside ``# note: ...``,
inside ``/* ... */``, and inside a lone triple-quoted docstring all read as
absent, while ``key === '__proto__'`` — a string the real fix genuinely contains
— reads as present. String *contents* are deliberately kept: several real fixes
here are literals (``split('-')``, ``'__proto__'``), so stripping strings the way
we strip comments would fail the honest patch it exists to credit.

What it does not do: this is lexical, not semantic. It answers "did the agent
write code here", never "is the code correct" — that is the per-task regex's job
downstream, and a determined bypass (planting a token in a live string
assignment) still reads as code, because it *is* code. The checkpoint pays for
evidence of work at the fix site; it does not pretend to prove the fix.

The floor of that: **reachability is invisible here.** The genuine fix expression
wrapped in ``if (false)`` / ``if False:``, placed after an unconditional return,
or inside a never-called function reads as code and scores full credit while
shipping a no-op. This is not specific to any one task or to the marked/co-location
view — it is the same on the bare-token tasks — and closing it needs semantic
analysis a comment-stripper cannot do (dead-construct detection, return dominance,
call-graph liveness) or a shift to behavioral scoring. Tracked as
EnterpriseBench-jn73.2.7.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Sequence


class UnknownLanguage(KeyError):
    """No comment syntax is registered for the requested language.

    Raised rather than defaulted: silently falling back to "no comments" would
    restore the exact hole this module closes, and do it invisibly on whichever
    task added the language.
    """


@dataclass(frozen=True)
class _Syntax:
    """How one language spells the things that are not code."""

    line: Sequence[str] = ()
    block: Sequence[tuple[str, str]] = ()
    # Multi-character delimiters must precede their prefixes ('"""' before '"'),
    # since the scanner takes the first match.
    strings: Sequence[str] = ()
    multiline_strings: Sequence[str] = ()
    # Whole-line block markers (ruby's =begin/=end), matched only at column 0.
    line_block: Sequence[tuple[str, str]] = ()


_C_LIKE_STRINGS = ('"', "'")

SYNTAX: dict[str, _Syntax] = {
    "python": _Syntax(
        line=("#",),
        strings=('"', "'"),
        multiline_strings=('"""', "'''"),
    ),
    "ruby": _Syntax(
        line=("#",),
        strings=('"', "'"),
        line_block=(("=begin", "=end"),),
    ),
    # Rust omits "'" deliberately. It spells lifetimes with the same character
    # ("&'a str"), and treating that as an unterminated char literal would
    # swallow the rest of every generic signature. Char literals are the cost:
    # a "//" *inside* one would read as a comment. No real one contains it.
    "rust": _Syntax(line=("//",), block=(("/*", "*/"),), strings=('"',)),
    # Java's text block is a real multiline literal, so its interior is prose:
    # without the rule, `String NOTES = """ ... """` holding the fix's text reads
    # as bare code and scores full credit for a constant (bead jn73.2.7). Rust's
    # multi-line `"` strings and Ruby's heredocs are the same class and are still
    # open — EnterpriseBench-jn73.2.7.6.
    "java": _Syntax(
        line=("//",),
        block=(("/*", "*/"),),
        strings=_C_LIKE_STRINGS,
        multiline_strings=('"""',),
    ),
    "javascript": _Syntax(
        line=("//",),
        block=(("/*", "*/"),),
        strings=_C_LIKE_STRINGS,
        multiline_strings=("`",),
    ),
    "typescript": _Syntax(
        line=("//",),
        block=(("/*", "*/"),),
        strings=_C_LIKE_STRINGS,
        multiline_strings=("`",),
    ),
}


# Extension -> language, for the one question a caller cannot answer from a task
# definition: a task names the language of its *fix site*, but a patch-site check
# also has to ask "did the agent write real code anywhere else in this repo?" and
# the answer spans files it was never told about. Unmapped extensions resolve to
# None (see :func:`language_for`) rather than a guess.
_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".java": "java",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
}


def language_for(path: str) -> str | None:
    """The language of ``path``, or None when its extension names no code.

    None is a real answer, not a failure: a diff touching README.md or a
    .yml fixture is not a code change, and a caller asking "is there code here"
    wants that distinction rather than an exception.
    """
    dot = path.rfind(".")
    return _EXTENSIONS.get(path[dot:].lower()) if dot != -1 else None


@dataclass
class _State:
    """Scanner state that legitimately survives a line break."""

    block_end: str | None = None
    string_end: str | None = None
    line_block_end: str | None = None


# Marks a string literal's boundaries in the ``marked`` view so a caller can see
# where a string sat *without* confusing its contents for surrounding code. A
# control byte no real source line carries; :func:`_scan` strips any that arrive
# in the input so the only ones present are the ones it wrote.
STRING_MARK = "\x01"


@dataclass(frozen=True)
class CodeLine:
    """One added or removed line, in three views, with the non-code spelled out.

    ``code`` is the line minus its comments, string *contents kept*.
    ``bare_code`` is the same with string *interiors* dropped too, leaving only
    code outside any literal (delimiters retained as punctuation).
    ``marked`` keeps the string contents but replaces each string's *delimiters*
    with :data:`STRING_MARK`, so ``key === '__proto__'`` reads
    ``key === \\x01__proto__\\x01``. ``has_bare_code`` is whether any code
    appeared *outside* a string literal (``x = "note"`` is code; a lone
    triple-quoted docstring is prose).

    Three views because a semantic grep asks three different things:

    * ``bare_code`` — "did the agent write real code here?" A token that lives
      only inside a string is a dead assignment, not a fix, so it is absent here
      (``DUMMY = "range_spec.split(dash)"`` has no ``.split(`` in ``bare_code``;
      bead jn73.2.7, residual A). This is what the pure-bare tasks match.
    * ``code`` — the full text, for ``has_code_change`` (a string-only edit is
      still real code).
    * ``marked`` — for the few tasks whose fix genuinely *is* a string literal
      (``'__proto__'``, ``"x-gzip"``, ``%25``, ``[\\s\\S]``). Matching a
      structural token immediately before the mark (``=== \\x01__proto__``)
      proves the token *operates on* that literal, which independent presence
      checks could not: it refuses credit both to a dead ``obj.__proto__`` (the
      token is bare, not a marked string) and to a literal wearing a matching
      keyword elsewhere on the line, because only a real comparison puts the
      operator flush against the mark.
    """

    code: str
    bare_code: str
    marked: str
    has_bare_code: bool

    @property
    def is_substantive(self) -> bool:
        return bool(self.code.strip()) and self.has_bare_code


def _scan(line: str, syntax: _Syntax, state: _State) -> CodeLine:
    """Strip one line's comments, carrying multi-line state across the call."""
    # Any STRING_MARK in the input is stripped up front, so the only marks in the
    # ``marked`` view are the ones this function writes at string boundaries — an
    # agent cannot plant a byte that forges a string edge.
    line = line.replace(STRING_MARK, " ")

    # A ruby =begin block runs until =end, both anchored at column 0.
    if state.line_block_end is not None:
        if line.startswith(state.line_block_end):
            state.line_block_end = None
        return CodeLine("", "", "", False)
    for opener, closer in syntax.line_block:
        if line.startswith(opener):
            state.line_block_end = closer
            return CodeLine("", "", "", False)

    # ``out`` keeps string contents; ``bare`` drops them (delimiters go to both,
    # as punctuation); ``marked`` keeps contents but replaces the delimiters with
    # STRING_MARK, so a caller can tell "inside a string" from "flush against one".
    out: List[str] = []
    bare: List[str] = []
    marked: List[str] = []
    has_bare_code = False
    i = 0
    n = len(line)
    while i < n:
        rest = line[i:]

        if state.block_end is not None:
            if rest.startswith(state.block_end):
                i += len(state.block_end)
                state.block_end = None
            else:
                i += 1
            continue

        if state.string_end is not None:
            # Backslash escapes a delimiter, and the backslash itself. Consume
            # both so "\\" reads as a terminated string, not an escaped quote.
            # String interior: kept in ``out`` and ``marked``, withheld from ``bare``.
            if rest.startswith("\\") and len(rest) > 1:
                out.append(rest[:2])
                marked.append(rest[:2])
                i += 2
                continue
            if rest.startswith(state.string_end):
                out.append(state.string_end)
                bare.append(state.string_end)
                marked.append(STRING_MARK)
                i += len(state.string_end)
                state.string_end = None
                continue
            out.append(rest[0])
            marked.append(rest[0])
            i += 1
            continue

        block = next((b for b in syntax.block if rest.startswith(b[0])), None)
        if block is not None:
            opener, state.block_end = block
            i += len(opener)
            continue

        if any(rest.startswith(marker) for marker in syntax.line):
            break  # the rest of the line is a comment

        delim = next(
            (
                d
                for d in (*syntax.multiline_strings, *syntax.strings)
                if rest.startswith(d)
            ),
            None,
        )
        if delim is not None:
            state.string_end = delim
            out.append(delim)
            bare.append(delim)
            marked.append(STRING_MARK)
            i += len(delim)
            continue

        if not rest[0].isspace():
            has_bare_code = True
        out.append(rest[0])
        bare.append(rest[0])
        marked.append(rest[0])
        i += 1

    # A single-quoted string does not survive a line break; a triple-quoted one
    # does. Closing the former here keeps one stray quote from swallowing the
    # rest of the hunk.
    if state.string_end is not None and state.string_end not in syntax.multiline_strings:
        state.string_end = None

    return CodeLine("".join(out), "".join(bare), "".join(marked), has_bare_code)


# The only line inside a hunk that is not content. Everything else a unified diff
# spends on describing the file ("diff --git", "index", "+++ b/f", mode and
# rename lines, "Binary files ... differ") sits *before* the first "@@", so the
# hunk gate in :func:`_side_lines` already excludes it.
_NO_NEWLINE = "\\"  # "\ No newline at end of file"


def git_lines(text: str) -> List[str]:
    """``text``'s lines, split on ``\\n`` and nothing else.

    That is the only thing git ends a line with. ``str.splitlines`` also breaks
    on ``\\v \\f \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029`` and a lone ``\\r``, none
    of which git treats as a line ending — so it reads one line git emitted as
    several, and the agent picks what the extras say. A form feed inside a
    comment (``# notes\\x0c+CONTENT_DECODERS = [...]``) then mints an added line
    carrying a fix that was never written, in a file that still parses. The
    grammar in this module is only as sound as the line stream it runs on (bead
    jn73.2.7).

    Callers that count entries must drop the empty trailing field themselves:
    ``git_lines("")`` is ``[""]``, where ``"".splitlines()`` is ``[]``.
    """
    return text.split("\n")


def _file_sections(diff: str) -> List[List[str]]:
    """``diff``'s lines, grouped per file.

    Scanner state is per file: one ``git diff`` can span several (``--path`` is
    repeatable), and a delimiter left open at the end of one file's hunks says
    nothing about the next file. Letting it carry would swallow the next file's
    real code as "still inside a comment" — under-crediting an honest agent.
    """
    sections: List[List[str]] = []
    current: List[str] = []
    for line in git_lines(diff):
        if line.startswith("diff --git ") and current:
            sections.append(current)
            current = []
        current.append(line)
    if current:
        sections.append(current)
    return sections


def _side_lines(section: List[str], prefix: str) -> List[tuple[str, bool]]:
    """``(text, is_target)`` for one side of one file's diff, in file order.

    Context lines are yielded alongside the target ones — with ``is_target``
    False — because they are what tells the scanner it is inside a comment. A
    reader of only ``+``/``-`` lines never sees the delimiters of a block the
    agent edited *inside*, so it reads that block's interior as bare code, and
    a docstring the file already had becomes a place to hide a fix that was
    never written (bead jn73.2.7, D2).

    Content is recognised by the diff's own grammar — a line is content only
    *inside* a hunk — rather than by testing the raw line against the header
    spellings. Those overlap: an added ``++counter;`` renders as ``+++counter;``,
    which a ``startswith("+++")`` test reads as the ``+++ b/f`` header and drops,
    so a genuine pre-increment fix scored 0 (bead jn73.2.7, defect C). The hunk
    gate has no such ambiguity, because every header line precedes the first
    ``@@`` while content lines always carry a ``+``/``-``/space prefix.
    """
    other = "-" if prefix == "+" else "+"
    out: List[tuple[str, bool]] = []
    in_hunk = False
    for line in section:
        if line.startswith("@@"):
            # Unprefixed, so this is the hunk header itself; an "@@" in the file
            # arrives as "+@@"/" @@" and is content.
            in_hunk = True
            continue
        if not in_hunk or line.startswith(_NO_NEWLINE):
            continue
        if line.startswith(prefix):
            out.append((line[1:], True))
        elif line.startswith(other):
            continue  # the other side's line: not on this side of the file
        else:
            # Context. A hunk's unchanged lines carry a leading space; a fully
            # blank one may arrive with the space stripped.
            out.append((line[1:] if line.startswith(" ") else line, False))
    return out


def _substantive_lines(diff: str, lang: str, *, added: bool) -> List[CodeLine]:
    """The substantive added (or removed) lines, comment-stripped, in file order."""
    try:
        syntax = SYNTAX[lang]
    except KeyError:
        raise UnknownLanguage(
            f"no comment syntax registered for language {lang!r}; "
            f"known: {', '.join(sorted(SYNTAX))}"
        ) from None
    prefix = "+" if added else "-"
    out: List[CodeLine] = []
    for section in _file_sections(diff):
        state = _State()
        for text, is_target in _side_lines(section, prefix):
            scanned = _scan(text, syntax, state)
            if is_target and scanned.is_substantive:
                out.append(scanned)
    return out


# The per-view field selectors, named so :func:`_emit`'s callers read as their
# intent (bare vs. full) and mypy sees a typed function, not an untyped lambda.
def _BARE(cl: CodeLine) -> str:
    return cl.bare_code


def _CODE(cl: CodeLine) -> str:
    return cl.code


def _MARKED(cl: CodeLine) -> str:
    return cl.marked


def _emit(lines: List[CodeLine], view: Callable[[CodeLine], str]) -> str:
    return "\n".join(view(cl) for cl in lines)


def marked_code(diff: str, lang: str, *, added: bool) -> str:
    """The added (or removed) code with string delimiters replaced by :data:`STRING_MARK`.

    ``key === '__proto__'`` becomes ``key === \\x01__proto__\\x01``. A caller can
    then require a structural token flush against the mark (``=== \\x01__proto__``)
    to credit "the token operates on this literal", which the flat ``code`` view
    cannot express — there, a token inside an unrelated string reads the same as
    one applied to the target. Emitted as one blob (lines joined by ``\\n``) so an
    idiomatic fix wrapped across lines still reads as adjacent under a
    whitespace-spanning match.
    """
    return _emit(_substantive_lines(diff, lang, added=added), _MARKED)


def added_code(diff: str, lang: str, *, include_strings: bool = False) -> str:
    """The added lines that carry code, comments stripped, one per line.

    The text a per-task regex should match against. The default drops string
    *interiors* too (``bare_code``): most fixes are bare tokens, and a token
    that only appears inside a string literal is a dead assignment, not a fix
    (bead jn73.2.7, residual A). ``include_strings=True`` keeps the contents,
    for ``has_code_change`` — a string-only edit is still real code.
    """
    view = _CODE if include_strings else _BARE
    return _emit(_substantive_lines(diff, lang, added=True), view)


def removed_code(diff: str, lang: str, *, include_strings: bool = False) -> str:
    """The removed lines that carried code, comments stripped, one per line."""
    view = _CODE if include_strings else _BARE
    return _emit(_substantive_lines(diff, lang, added=False), view)


def has_code_change(diff: str, lang: str) -> bool:
    """Did this diff add or remove any actual code?

    False for the empty diff, and for one that only moves comments, blank lines
    or docstrings around — the distinction the patch-site checkpoint pays on.

    Reads the strings-included view on purpose: this answers "is there real code
    here", and a line whose only change is a string literal (``x = "x-gzip"``) is
    real code even when the token-grep that separates a fix from a dead
    assignment reads the bare view. Pinning it here keeps the *site* checkpoint —
    which pays for landing at the fix site, not for the fix itself — from
    under-crediting an honest string-only edit.
    """
    return bool(added_code(diff, lang, include_strings=True)) or bool(
        removed_code(diff, lang, include_strings=True)
    )
