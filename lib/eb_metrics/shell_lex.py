"""A quoting-aware shell lexer, just sufficient to find file reads in a command.

:func:`lex` splits a shell command into :class:`Token` s tagged ``word``,
``op`` (a control operator or redirect), or ``subst`` (the *inner text* of a
``$(...)``/backtick command substitution, for the caller to lex recursively).

The one invariant everything else rests on: **a metacharacter is an operator
only when it is unquoted.** ``shlex`` in posix mode cannot express that — it
strips quotes as it goes, so ``grep '|' f.py`` and ``grep | f.py`` reduce to
the same token stream, and a consumer that splits sub-commands on ``|`` loses
``f.py`` from the first. Keeping the distinction is what lets the caller treat
``(``/``)`` and newline as real separators (recovering subshells and multi-line
commands) without re-opening that hole.

Scope is deliberately narrow. This exists to decide *which tokens are file
arguments to a read command*, so it recognizes quoting, escapes, comments,
control operators, redirects, here-docs, here-strings, and command
substitution — and nothing else. Variable expansion, globbing, aliases, and
arithmetic are not evaluated: a lexer cannot know a filesystem or an
environment, and inventing values it cannot know is how a retrieval metric
starts counting reads that never happened.

Malformed input is *read*, not validated: an unterminated quote or
substitution yields the tokens that completed before it and drops the partial
word. The partial word is dropped rather than emitted because a half-parsed
path is not a path the agent opened.

``bashlex`` was considered and rejected: a third-party runtime dependency in
the scoring path that raises on real-world commands, and a parse failure
falling back to "no reads" would reintroduce exactly the systematic undercount
this module was written to remove (EnterpriseBench-be50).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "OP",
    "SUBST",
    "SUBST_MARK",
    "WORD",
    "SEPARATORS",
    "HEREDOC_OPS",
    "Token",
    "lex",
]

WORD = "word"
OP = "op"
SUBST = "subst"

# Stands in for a substitution's *result* at the position it occupied inside a
# word. A substitution glued to literal text makes one word — `$(pwd)/x.py` is a
# single argument — whose value the command text does not contain. Dropping the
# substitution and keeping the leftover literal would hand `/x.py` to the caller
# as a path the agent opened, which it never did; the mark is what lets the
# caller tell that fabrication apart from a genuine literal argument and drop it
# (the same floor as an unexpanded glob). NUL cannot occur in a real shell word,
# so it can never collide with one.
SUBST_MARK = "\x00"


@dataclass(frozen=True)
class Token:
    """A lexed shell token. ``text`` is unquoted; ``kind`` is the tag."""

    text: str
    kind: str


# Operators that end one sub-command and begin the next. A token is only ever
# matched against these as a whole *operator* token, never as raw text, so a
# quoted `|` cannot reach them.
SEPARATORS = frozenset({"|", "||", "|&", "&", "&&", ";", ";;", "(", ")", "\n"})

# Redirects whose operand is *content*, not a path: the here-doc delimiter and
# the here-string body. The caller drops the word that follows one of these.
HEREDOC_OPS = frozenset({"<<", "<<-", "<<<"})

# Every operator the lexer emits, longest-first: `<<<` must win over `<<`, `&&`
# over `&`, and so on. The redirects here are operators but deliberately NOT in
# SEPARATORS: `cat a.py > out.txt` stays one command, so `out.txt` stays an
# argument of it. That keeps the redirect-target false positive
# (EnterpriseBench-qefr) exactly as it was rather than silently fixing it here,
# on a different axis than this module's change.
_OPERATORS = (
    "<<<", "<<-", "<<", "&&", "||", "|&", ";;", ">>", ">&", "&>",
    ";", "&", "|", "(", ")", "<", ">",
)
_OP_START = frozenset(op[0] for op in _OPERATORS)
_DQUOTE_ESCAPABLE = '"\\$`\n'
_BACKTICK_ESCAPABLE = "$`\\"
# A substitution inside a quoted run inside a substitution recurses
# (_match_paren -> _scan_double -> _scan_dollar_paren -> _match_paren). Commands
# come from model-authored traces, so the nesting is not ours to trust: past this
# depth the command reads as malformed, which this module already answers by
# dropping the partial word rather than guessing. Unbounded, it would instead
# raise RecursionError and abort the scoring run. Real commands nest one or two
# deep; 64 is far past anything a corpus has shown.
_MAX_NEST = 64
_ANSI_C_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f",
    "v": "\v", "e": "\x1b", "E": "\x1b", "0": "\0", "\\": "\\", "'": "'",
    '"': '"', "?": "?",
}


def lex(command: str) -> list[Token]:
    """Split ``command`` into tokens, preserving the quoted/unquoted distinction.

    Here-doc *bodies* are consumed and never tokenized — they are content the
    agent wrote, not files it read. Command substitutions become ``subst``
    tokens carrying their inner text, in the position they appeared, so the
    caller can recurse and keep retrieval order faithful for rank metrics.
    """
    tokens: list[Token] = []
    buf: list[str] = []
    in_word = False
    heredocs: list[tuple[str, bool]] = []  # (delimiter, strip_leading_tabs)
    pending_delim: bool | None = None  # strip_leading_tabs of an unfilled `<<`
    i, n = 0, len(command)

    def flush() -> None:
        nonlocal in_word, pending_delim
        if not in_word:
            return
        text = "".join(buf)
        buf.clear()
        in_word = False
        tokens.append(Token(text, WORD))
        if pending_delim is not None:
            heredocs.append((text, pending_delim))
            pending_delim = None

    while i < n:
        c = command[i]

        if c in " \t\r":
            flush()
            i += 1
        elif c == "\n":
            flush()
            tokens.append(Token("\n", OP))
            i += 1
            for delim, strip_tabs in heredocs:
                i = _skip_heredoc_body(command, i, delim, strip_tabs)
            heredocs.clear()
        elif c == "#" and not in_word:
            i = command.find("\n", i)
            if i < 0:
                break
        elif c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                return tokens  # unterminated
            buf.append(command[i + 1 : j])
            in_word = True
            i = j + 1
        elif c == "$" and command.startswith("$'", i):
            j = _scan_ansi_c(command, i, buf)
            if j is None:
                return tokens  # unterminated
            in_word = True
            i = j
        elif c == '"':
            j = _scan_double(command, i, buf, tokens)
            if j is None:
                return tokens  # unterminated
            in_word = True
            i = j
        elif c == "\\":
            if i + 1 >= n:
                break  # trailing backslash: nothing to escape
            if command[i + 1] == "\n":
                i += 2  # line continuation — not a separator
            else:
                buf.append(command[i + 1])
                in_word = True
                i += 2
        elif c == "`":
            j = _scan_backtick(command, i, tokens)
            if j is None:
                return tokens  # unterminated
            buf.append(SUBST_MARK)
            in_word = True
            i = j
        elif c == "$" and command.startswith("$(", i):
            j = _scan_dollar_paren(command, i, tokens)
            if j is None:
                return tokens  # unterminated
            buf.append(SUBST_MARK)
            in_word = True
            i = j
        elif c in _OP_START and (op := _match_operator(command, i)):
            flush()
            tokens.append(Token(op, OP))
            if op in ("<<", "<<-"):
                pending_delim = op == "<<-"
            i += len(op)
        else:
            buf.append(c)
            in_word = True
            i += 1

    flush()
    return tokens


def _match_operator(command: str, i: int) -> str | None:
    return next((op for op in _OPERATORS if command.startswith(op, i)), None)


def _scan_double(
    command: str, i: int, buf: list[str], tokens: list[Token], depth: int = 0
) -> int | None:
    """Consume a double-quoted run at ``i``; return the index after its close.

    Metacharacters inside are data, but substitutions are still live — so this
    emits ``subst`` tokens for any ``$(...)``/backtick it passes over.
    """
    if depth > _MAX_NEST:
        return None
    n = len(command)
    j = i + 1
    while j < n:
        c = command[j]
        if c == '"':
            return j + 1
        if c == "\\" and j + 1 < n and command[j + 1] in _DQUOTE_ESCAPABLE:
            if command[j + 1] != "\n":  # a `\<newline>` continuation adds nothing
                buf.append(command[j + 1])
            j += 2
        elif c == "`":
            k = _scan_backtick(command, j, tokens)
            if k is None:
                return None
            buf.append(SUBST_MARK)
            j = k
        elif c == "$" and command.startswith("$(", j):
            k = _scan_dollar_paren(command, j, tokens, depth + 1)
            if k is None:
                return None
            buf.append(SUBST_MARK)
            j = k
        else:
            buf.append(c)
            j += 1
    return None  # unterminated


def _scan_ansi_c(command: str, i: int, buf: list[str]) -> int | None:
    """Consume ``$'…'`` (ANSI-C quoting) at ``i``; return the index after its close.

    The ``$`` belongs to the quote syntax, not to the word. Letting it fall
    through as a literal turns ``$'src/a.py'`` into the path ``$src/a.py`` —
    a file that does not exist, and a real read lost.
    """
    n = len(command)
    j = i + 2  # past the `$'`
    while j < n:
        c = command[j]
        if c == "'":
            return j + 1
        if c == "\\" and j + 1 < n:
            buf.append(_ANSI_C_ESCAPES.get(command[j + 1], command[j + 1]))
            j += 2
        else:
            buf.append(c)
            j += 1
    return None  # unterminated


def _scan_backtick(command: str, i: int, tokens: list[Token]) -> int | None:
    """Consume a backtick substitution at ``i``, emitting its inner text.

    Inside backquotes a backslash keeps its literal value except before ``$``,
    ``` ` ``` or ``\\`` (POSIX). Unescaping everything would strip the backslash
    from ``cat foo.txt\\ bar.py`` before the inner text is re-lexed, splitting one
    escaped-space filename into two files the agent never opened.
    """
    n = len(command)
    inner: list[str] = []
    j = i + 1
    while j < n:
        if command[j] == "`":
            tokens.append(Token("".join(inner), SUBST))
            return j + 1
        if command[j] == "\\" and j + 1 < n:
            if command[j + 1] in _BACKTICK_ESCAPABLE:
                inner.append(command[j + 1])  # the backslash was the escape
            else:
                inner.extend(command[j : j + 2])  # literal backslash, kept
            j += 2
        else:
            inner.append(command[j])
            j += 1
    return None  # unterminated


def _scan_dollar_paren(
    command: str, i: int, tokens: list[Token], depth: int = 0
) -> int | None:
    """Consume ``$(...)`` at ``i``, emitting its inner text as a ``subst``.

    ``$((...))`` is arithmetic, not a command: it is skipped and emits nothing.
    """
    if depth > _MAX_NEST:
        return None
    arithmetic = command.startswith("$((", i)
    close = _match_paren(command, i + 1, depth)
    if close is None:
        return None
    if not arithmetic:
        tokens.append(Token(command[i + 2 : close], SUBST))
    return close + 1


def _match_paren(command: str, i: int, depth: int = 0) -> int | None:
    """Index of the ``)`` closing the ``(`` at ``i``, or ``None`` if unbalanced.

    Parens inside quotes are data — ``$(grep ')' a.py)`` closes at the last one.
    """
    if depth > _MAX_NEST:
        return None
    n = len(command)
    nesting = 0
    j = i
    while j < n:
        c = command[j]
        if c == "\\" and j + 1 < n:
            j += 2
        elif c == "'":
            k = command.find("'", j + 1)
            if k < 0:
                return None
            j = k + 1
        elif c == '"':
            # Scan the quoted run only to step over it — its content and any
            # substitution inside it are re-lexed when the caller recurses into
            # this substitution's text, so both outputs are discarded here.
            k = _scan_double(command, j, [], [], depth + 1)
            if k is None:
                return None
            j = k
        else:
            if c == "(":
                nesting += 1
            elif c == ")":
                nesting -= 1
                if nesting == 0:
                    return j
            j += 1
    return None


def _skip_heredoc_body(command: str, i: int, delim: str, strip_tabs: bool) -> int:
    """Index just past the here-doc terminated by ``delim``, starting at line ``i``.

    An unterminated body runs to the end of the command — consuming too much of
    a malformed here-doc only costs reads the agent almost certainly did not
    make, while consuming too little would credit its body as file paths.
    """
    n = len(command)
    while i < n:
        end = command.find("\n", i)
        line = command[i:] if end < 0 else command[i:end]
        candidate = line.lstrip("\t") if strip_tabs else line
        if candidate.rstrip("\r") == delim:
            return n if end < 0 else end + 1
        if end < 0:
            return n
        i = end + 1
    return n
