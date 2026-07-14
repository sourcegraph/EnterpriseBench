"""The README of a merge blocker must not claim coverage the gate does not have.

This directory's README is a table of corruption vectors and the test that closes
each one, and CI names the directory as a merge blocker on the strength of it. A
table of test names is a restatement of the corpus, and restatements drift: the
commit that replaced four `file_extraction` vectors with a stronger per-key matrix
deleted three of the tests the table advertised by name and left the table standing,
so the gate went on advertising three vectors it no longer ran. Nothing failed,
because nothing was reading the table.

Same lesson this corpus already applied to the scorer's ``--keys`` — read the
artifact that ships, never a copy of it — turned on the corpus's own documentation.
A false coverage claim inside a merge blocker is worse than no claim: it is the
reason nobody goes looking for the missing vector.

The table is read STRUCTURALLY — every data row's last cell, which is the "Test"
column of both tables — rather than by scanning the document for backtick spans. The
difference is the whole guard. A backtick scan sees only citations that are still
formatted as citations, so the one edit shape most likely to accompany a deletion —
a maintainer tidying the row they are touching — makes the claim invisible to the
checker rather than flagged by it, and the gate goes green on a table that still
advertises a test nobody runs. Reading the cell finds the name with or without its
backticks, and an emptied cell is a row claiming a vector with no coverage at all,
which fails here too.

Only the one direction is enforced. A test the README does not mention is fine (the
table is a map of vectors, not an index of every helper); a test the README *cites*
and the corpus does not contain is a lie about what the gate checks.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent
README = CORPUS_DIR / "README.md"

# A test name wherever it appears in a table cell — backticked or bare. Requiring the
# backticks is what let a de-backticked citation slip past an earlier version of this
# guard while still reading, to a human, as a coverage claim.
_TEST_NAME_RE = re.compile(r"\btest_\w+")

# The last cell of both README tables is the "Test" column; this is its header text.
_TEST_COLUMN_HEADER = "Test"


def cited_rows() -> list[tuple[str, list[str]]]:
    """Every data row of every README table: (row label, test names in its last cell).

    Header and `|---|` separator rows are dropped; everything else that starts a line
    with a pipe is a claim about coverage and is held to it.
    """
    rows: list[tuple[str, list[str]]] = []
    for line in README.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if all(not c.strip("-: ") for c in cells):  # the |---|---| separator
            continue
        if cells[-1] == _TEST_COLUMN_HEADER:  # the header row itself
            continue
        rows.append((cells[0], _TEST_NAME_RE.findall(cells[-1])))
    return rows


def corpus_test_names() -> set[str]:
    """Every test function in this directory, parsed rather than grepped.

    ``ast.walk`` reaches the ones nested in ``TestFoo`` classes, which a line-anchored
    ``^def test_`` grep silently misses — and a guard that misses a test it should have
    found would report the drift it exists to catch as a passing table.

    Non-recursive: the corpus is flat today, and a nested module would be missed here.
    """
    names: set[str] = set()
    for path in CORPUS_DIR.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            is_def = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_def and node.name.startswith("test_"):
                names.add(node.name)
    return names


def test_every_test_this_readme_cites_exists():
    """Rename or delete a test without touching the table and this goes red."""
    corpus = corpus_test_names()
    missing = sorted(
        {name for _, names in cited_rows() for name in names} - corpus
    )
    assert not missing, (
        "tests/integrity/README.md advertises vectors whose tests are not in the "
        f"corpus: {', '.join(missing)}. The merge blocker is claiming coverage it does "
        "not have — fix the table, or restore the tests."
    )


def test_every_readme_row_names_a_test():
    """A row whose Test cell names nothing claims a vector the gate does not cover.

    This is the other half of the de-backticking hole: emptying the cell, rather than
    unformatting it, would otherwise leave a row asserting a corruption vector is closed
    with no test behind it, and the check above would have nothing to look up.
    """
    empty = [label for label, names in cited_rows() if not names]
    assert not empty, (
        "tests/integrity/README.md has table rows whose Test cell names no test: "
        f"{empty}. Every row is a claim that a vector is covered — give it a test or "
        "drop the row."
    )


def test_the_readme_tables_were_actually_parsed():
    """Non-vacuity guard. Reformat the tables into a shape ``cited_rows`` cannot read
    and both checks above pass over an empty list, guarding nothing."""
    rows = cited_rows()
    assert len(rows) >= 15, (
        f"only {len(rows)} table rows parsed out of the README; the checks above would "
        "pass vacuously"
    )
