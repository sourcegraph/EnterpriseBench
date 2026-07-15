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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence


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
    "java": _Syntax(line=("//",), block=(("/*", "*/"),), strings=_C_LIKE_STRINGS),
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


@dataclass(frozen=True)
class CodeLine:
    """One added or removed line, with the non-code spelled out.

    ``code`` is the line minus its comments. ``has_bare_code`` is whether any
    code appeared *outside* a string literal, which is what separates
    ``x = "note"`` (code) from a lone triple-quoted docstring (prose).
    """

    code: str
    has_bare_code: bool

    @property
    def is_substantive(self) -> bool:
        return bool(self.code.strip()) and self.has_bare_code


def _scan(line: str, syntax: _Syntax, state: _State) -> CodeLine:
    """Strip one line's comments, carrying multi-line state across the call."""
    # A ruby =begin block runs until =end, both anchored at column 0.
    if state.line_block_end is not None:
        if line.startswith(state.line_block_end):
            state.line_block_end = None
        return CodeLine("", False)
    for opener, closer in syntax.line_block:
        if line.startswith(opener):
            state.line_block_end = closer
            return CodeLine("", False)

    out: List[str] = []
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
            if rest.startswith("\\") and len(rest) > 1:
                out.append(rest[:2])
                i += 2
                continue
            if rest.startswith(state.string_end):
                out.append(state.string_end)
                i += len(state.string_end)
                state.string_end = None
                continue
            out.append(rest[0])
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
            i += len(delim)
            continue

        if not rest[0].isspace():
            has_bare_code = True
        out.append(rest[0])
        i += 1

    # A single-quoted string does not survive a line break; a triple-quoted one
    # does. Closing the former here keeps one stray quote from swallowing the
    # rest of the hunk.
    if state.string_end is not None and state.string_end not in syntax.multiline_strings:
        state.string_end = None

    return CodeLine("".join(out), has_bare_code)


# Lines a unified diff spends on saying which file it is describing. None of
# them is content, and every one of them would scan as code.
_METADATA = (
    "diff --git ",
    "index ",
    "@@",
    "old mode ",
    "new mode ",
    "new file mode ",
    "deleted file mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "Binary files ",
    "GIT binary patch",
    "\\",  # "\ No newline at end of file"
)


def _file_sections(diff: str) -> List[List[str]]:
    """``diff``'s lines, grouped per file.

    Scanner state is per file: one ``git diff`` can span several (``--path`` is
    repeatable), and a delimiter left open at the end of one file's hunks says
    nothing about the next file. Letting it carry would swallow the next file's
    real code as "still inside a comment" — under-crediting an honest agent.
    """
    sections: List[List[str]] = []
    current: List[str] = []
    for line in diff.splitlines():
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
    """
    header = prefix * 3
    other = "-" if prefix == "+" else "+"
    out: List[tuple[str, bool]] = []
    for line in section:
        if line.startswith(header) or line.startswith(other * 3):
            continue  # '+++ b/f' / '--- a/f'
        if any(line.startswith(m) for m in _METADATA):
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


def _code_lines(diff: str, lang: str, prefix: str) -> List[CodeLine]:
    try:
        syntax = SYNTAX[lang]
    except KeyError:
        raise UnknownLanguage(
            f"no comment syntax registered for language {lang!r}; "
            f"known: {', '.join(sorted(SYNTAX))}"
        ) from None
    out: List[CodeLine] = []
    for section in _file_sections(diff):
        state = _State()
        for text, is_target in _side_lines(section, prefix):
            scanned = _scan(text, syntax, state)
            if is_target:
                out.append(scanned)
    return out


def added_code(diff: str, lang: str) -> str:
    """The added lines that carry code, comments stripped, one per line.

    The text a per-task regex should match against: everything the agent added
    that is neither commentary nor a bare string.
    """
    return "\n".join(
        cl.code for cl in _code_lines(diff, lang, "+") if cl.is_substantive
    )


def removed_code(diff: str, lang: str) -> str:
    """The removed lines that carried code, comments stripped, one per line."""
    return "\n".join(
        cl.code for cl in _code_lines(diff, lang, "-") if cl.is_substantive
    )


def has_code_change(diff: str, lang: str) -> bool:
    """Did this diff add or remove any actual code?

    False for the empty diff, and for one that only moves comments, blank lines
    or docstrings around — the distinction the patch-site checkpoint pays on.
    """
    return bool(added_code(diff, lang)) or bool(removed_code(diff, lang))
