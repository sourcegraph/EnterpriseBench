r"""Decide which files a shell command *read* — the policy layer over the lexer.

:func:`bash_read_files` turns one Bash command into the ordered list of file
paths it opened. It sits on :mod:`eb_metrics.shell_lex` (which decides shell
*syntax*) and answers the separate, judgement-laden question of shell
*semantics*: which programs count as a retrieval, which of their arguments are
files, and which merely look like files.

This exists because baseline agents open files with `cat`/`grep`, not with the
structured `Read` tool. Without it their retrieval is largely unobservable —
and since Bash is how the baseline and hybrid arms read but (largely) not how
mcp_only does, every shape this layer misses biases those arms' recall *down*
against MCP's. That asymmetry, not tidiness, is why the shapes below are
handled explicitly (EnterpriseBench-be50).

Conservative by construction. A sub-command not led by a known read program
contributes nothing, because running a script is not a retrieval; and an
argument that cannot be resolved to a concrete path from the command text
alone contributes nothing rather than a guess.

ZFC: this is policy enforcement over a mechanical parse — a fixed program/flag
table and deterministic argument classification, no semantic judgement. It has
to be code, not a model call: a scoring primitive re-judged per call would not
reproduce across rescores, and the back-computations depend on that.
"""

from __future__ import annotations

import re
from itertools import groupby
from typing import Mapping

from eb_metrics.shell_lex import (
    HEREDOC_OPS,
    OP,
    SEPARATORS,
    SUBST,
    SUBST_MARK,
    WORD,
    Token,
    lex,
)

__all__ = ["bash_read_files"]


# Shell programs that read file contents. Baseline agents open files via these
# (`cat foo.py`, `grep pat src/x.go`) rather than the structured Read tool, so
# without this the baseline arm's retrieval is largely unobservable. EB-specific
# extension beyond the CSB port, which does not parse Bash. Flags and search
# patterns are dropped by this module (`_is_flag`, `_read_command_args`) rather
# than left to the consumer's ``_looks_like_file`` shape filter — see
# :func:`_command_words` on why this layer keeps its own promises.
_BASH_READ_CMDS = frozenset(
    {
        "cat", "head", "tail", "less", "more", "bat", "nl", "view", "cut",
        "column", "xxd", "od", "strings", "wc", "grep", "egrep", "fgrep",
        "rg", "ag", "sed", "awk", "diff",
    }
)
# Programs that run *another* program: strip them (and their own flags) to reach
# the real argv[0], so `sudo cat x.py` and `xargs grep -l p x.py` are not lost.
_WRAPPER_CMDS = frozenset(
    {"sudo", "env", "command", "nohup", "stdbuf", "time", "nice", "xargs"}
)
# Shell keywords sit where argv[0] would be and hide the command behind them:
# in `if grep -q x a.py; then cat b.py; fi` the reads belong to grep and cat, but
# the sub-commands lead with `if` and `then`. Strip them for the same reason as
# wrappers. Stripping cannot invent a read — whatever is left still has to be a
# known read program to count for anything.
_SHELL_KEYWORDS = frozenset(
    {
        "if", "then", "elif", "else", "fi", "while", "until", "for", "do",
        "done", "case", "esac", "select", "function", "in", "coproc",
        "{", "}", "!", "[[", "]]",
    }
)
# Programs that take a shell string to execute: recurse into it.
_SHELL_CMDS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})

# Programs whose *first positional* argument is a pattern or a script, not a
# file — `grep utils.py src/x.py` searched for the text "utils.py", it did not
# read it. Only applies when no -e/-f already supplied the pattern.
_GREP_CMDS = frozenset({"grep", "egrep", "fgrep", "rg", "ag"})
_PATTERN_CMDS = _GREP_CMDS | {"sed", "awk"}

# Flags on a :data:`_PATTERN_CMDS` program that *supply* the pattern or script,
# so the first positional after one of them is a real file. Their value is never
# a retrieved file. Scoped to those programs because the same letters mean
# something else elsewhere: `-f` is a pattern file to grep but "follow" to tail,
# and consuming tail's operand would lose a genuine read.
_PATTERN_FLAGS = frozenset({"-e", "--regexp", "-f", "--file", "--expression"})

# Flags that take a *separate* value which is neither a file nor the pattern —
# context counts, globs, type filters, awk assignments. Modelling their arity is
# not cosmetic: miss it and `grep -A 3 utils.py src/x.py` reads "3" as the
# pattern, which promotes utils.py into the file slot and invents a read that
# never happened. Per-program because the letters collide (`-F`/`-v` take a
# value for awk but are bare switches for grep).
_GREP_VALUE_FLAGS = frozenset(
    {
        "-A", "-B", "-C", "-m", "-d", "-g", "-t",
        "--after-context", "--before-context", "--context", "--max-count",
        "--devices", "--binary-files", "--color", "--colour", "--label",
        "--include", "--exclude", "--exclude-dir", "--glob", "--type",
    }
)
_VALUE_FLAGS: Mapping[str, frozenset[str]] = {
    **{cmd: _GREP_VALUE_FLAGS for cmd in _GREP_CMDS},
    "awk": frozenset({"-F", "-v", "--field-separator", "--assign"}),
    "sed": frozenset({"-l", "--line-length"}),
}
_NO_FLAGS: frozenset[str] = frozenset()

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GLOB_CHARS = "*?"
# Redirects whose operand is a descriptor, not a path (`2>&1`), and the full set
# a leading fd number can attach to (`2>`, `2>>`, `2<`).
_FD_DUP_OPS = frozenset({">&", "<&"})
_REDIRECT_OPS = frozenset({">", ">>", "<", ">&", "<&", "&>"})
# `{}` is the placeholder `find -exec` and `xargs -I` substitute the matched file
# into. It names whatever the filesystem matched, which the command text does not
# contain — the same floor as an unexpanded glob (EnterpriseBench-jqyhg).
_PLACEHOLDER = "{}"
# `sh -c "sh -c ..."` nests in principle; in practice two levels is already
# exotic. The bound just stops a pathological trace from recursing without end.
_MAX_SUBST_DEPTH = 4


def bash_read_files(command: str, depth: int = 0) -> list[str]:
    """Extract the files a shell command reads, in the order it reads them.

    Lexes with :mod:`eb_metrics.shell_lex` (which keeps quoting), splits the
    token stream into sub-commands on the *operator* tokens, and asks each
    sub-command what it read. Conservative by construction: a sub-command not
    led by a known read program contributes nothing, because running a script is
    not a retrieval.

    Everything this recovers — env-prefixed and wrapped commands, ``sh -c``
    strings, command substitutions, ``find -exec``, newline-separated lines —
    was previously extracted as nothing. That mattered asymmetrically: Bash is
    how the baseline and hybrid arms read files and (largely) not how mcp_only
    does, so each missed shape pushed those arms' recall down against MCP's
    (EnterpriseBench-be50).
    """
    if depth > _MAX_SUBST_DEPTH:
        return []
    files: list[str] = []
    for is_separator, group in groupby(
        lex(command), lambda t: t.kind == OP and t.text in SEPARATORS
    ):
        if not is_separator:
            files.extend(_sub_command_read_files(list(group), depth))
    return files


def _sub_command_read_files(sub: list[Token], depth: int) -> list[str]:
    """Files read by one sub-command (a token run between separators)."""
    # Substitutions run regardless of what the enclosing program is: the read in
    # `echo $(cat x.py)` is real even though `echo` reads nothing.
    files = [
        f
        for token in sub
        if token.kind == SUBST
        for f in bash_read_files(token.text, depth + 1)
    ]
    files.extend(_program_read_files(_command_words(sub), depth))
    return files


def _program_read_files(words: list[str], depth: int) -> list[str]:
    """Files read by a single program invocation, given its words.

    There are two *disjoint* recursion cycles, so each needs its own bound and
    neither guard is redundant:

    * ``$(…)`` and ``sh -c`` — :func:`bash_read_files` →
      :func:`_sub_command_read_files` → :func:`bash_read_files`, which never
      passes through here on its recursive step.
    * ``find -exec find -exec …`` — here → :func:`_find_exec_read_files` → here,
      which never passes through :func:`bash_read_files`.

    Bounding only one of the two left the other free to climb to Python's own
    recursion limit and raise, which aborts the whole batch rescore rather than
    the one trial. Delete either guard and that shape comes back.
    """
    if depth > _MAX_SUBST_DEPTH:
        return []
    words = _strip_wrappers(words)
    if not words:
        return []
    cmd = words[0].rsplit("/", 1)[-1]

    if cmd in _SHELL_CMDS:
        return _shell_c_read_files(words, depth)
    if cmd == "find":
        return _find_exec_read_files(words, depth)
    if cmd in _BASH_READ_CMDS:
        return _read_command_args(cmd, words[1:])
    return []


def _command_words(sub: list[Token]) -> list[str]:
    """Word tokens of a sub-command, with redirect operands resolved.

    Operators drop out. The word after ``<<``/``<<-``/``<<<`` drops out with
    them: it is a here-doc delimiter or a here-string body — content, not a path
    (crediting it would count a file the agent *wrote* as one it read). The
    operand of every other redirect stays, which is what keeps ``> out.txt``
    counted (EnterpriseBench-qefr) and ``< in.py`` counted as the genuine read
    it is.

    File descriptors are not files. ``2>/dev/null`` redirects fd 2, so the ``2``
    in front of the operator is part of the redirect, not an argument; ``>&1``
    dups fd 1, so its operand is a descriptor rather than a path. Both are
    dropped — today a filter in another module happens to reject them for having
    no extension, but this function promises paths, and a promise kept by
    somebody else's filter is not kept.
    """
    words: list[str] = []
    skip_operand = False
    for token in sub:
        if token.kind == OP:
            if token.text in _REDIRECT_OPS and words and words[-1].isdigit():
                words.pop()  # a leading fd number, e.g. the `2` of `2>`
            skip_operand = token.text in HEREDOC_OPS or token.text in _FD_DUP_OPS
        elif token.kind == WORD:
            if skip_operand:
                skip_operand = False
            else:
                words.append(token.text)
    return words


def _strip_wrappers(words: list[str]) -> list[str]:
    """Drop everything before the real program: assignments, keywords, wrappers.

    ``FOO=1 sudo cat x.py`` has to reach ``cat`` to see the read at all, and
    ``if grep -q p x.py`` has to get past ``if``. A wrapper's own flags go too,
    so ``xargs -0 grep …`` resolves. A wrapper flag that takes a separate value
    (``sudo -u root cat``) still stops the scan — a documented floor, not worth
    guessing per-wrapper flag arities for.
    """
    i = 0
    while i < len(words):
        word = words[i]
        if _ENV_ASSIGN_RE.match(word) or word in _SHELL_KEYWORDS:
            i += 1
        elif word.rsplit("/", 1)[-1] in _WRAPPER_CMDS:
            i += 1
            while i < len(words) and _is_flag(words[i]):
                i += 1
        else:
            break
    return words[i:]


def _shell_c_read_files(words: list[str], depth: int) -> list[str]:
    """Recurse into the script string of ``sh -c '…'``."""
    for i, word in enumerate(words[1:-1], start=1):
        if word == "-c":
            return bash_read_files(words[i + 1], depth + 1)
    return []


def _find_exec_read_files(words: list[str], depth: int) -> list[str]:
    """Recurse into each ``find … -exec <cmd> … ;`` body.

    Recovers a literal file in the exec'd command (``-exec grep -l p cfg.yaml``).
    A ``{}`` placeholder names whatever find matched, which the command text
    cannot tell us — that read stays part of the documented floor.
    """
    files: list[str] = []
    i = 0
    while i < len(words):
        if words[i] in ("-exec", "-execdir"):
            i += 1
            body: list[str] = []
            while i < len(words) and words[i] not in (";", "+"):
                body.append(words[i])
                i += 1
            files.extend(_program_read_files(body, depth + 1))
        else:
            i += 1
    return files


def _read_command_args(cmd: str, args: list[str]) -> list[str]:
    """File arguments of a known read command, with patterns and globs removed.

    Things that look like paths but are not reads get dropped here: the value of
    a pattern flag (``grep -e foo.py``), the value of a flag that takes one
    (``grep -A 3``), the leading positional pattern or script of a search program
    (``grep utils.py src/x.py`` searched for that text), and an unexpanded glob
    (``packages/*/package.json`` names no single file, can never match a concrete
    ground-truth path, and would only occupy a rank slot). Which files a glob
    *did* expand to is decided by the filesystem, not the command — see the
    module docstring's floor.
    """
    takes_pattern = cmd in _PATTERN_CMDS
    value_flags = _VALUE_FLAGS.get(cmd, _NO_FLAGS)
    pattern_seen = not takes_pattern
    end_of_options = False
    files: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if not end_of_options and _is_flag(arg):
            if arg == "--":
                # Everything after `--` is an operand, however dash-shaped.
                # Reading it as one more flag would leave `pattern_seen` unset,
                # and the file after it would then be swallowed as the pattern
                # and never counted.
                end_of_options = True
            elif takes_pattern and arg in _PATTERN_FLAGS:
                pattern_seen = True  # the pattern came from here, not a positional
                i += 1
            elif arg in value_flags:
                i += 1  # a count/glob/assignment — neither file nor pattern
        elif not pattern_seen:
            pattern_seen = True  # the first positional is the pattern, not a file
        elif _names_a_concrete_path(arg):
            files.append(arg)
        i += 1
    return files


def _names_a_concrete_path(arg: str) -> bool:
    """Does ``arg`` name one file the *command text* determines?

    A glob, a ``{}`` placeholder, and a substitution's result are all decided by
    the filesystem or by another command's output, not by the text in front of
    us. Crediting them would either invent a path (``$(pwd)/x.py`` with the
    substitution erased is ``/x.py``, which nothing opened) or occupy a rank slot
    with something that can never match a ground-truth path. Contributing nothing
    is the documented floor (EnterpriseBench-jqyhg), and it is the honest answer.
    """
    return (
        SUBST_MARK not in arg
        and _PLACEHOLDER not in arg
        and not any(ch in arg for ch in _GLOB_CHARS)
    )


def _is_flag(word: str) -> bool:
    """``-l``/``--include=*.py`` are flags; a bare ``-`` is stdin, an operand."""
    return word.startswith("-") and len(word) > 1
