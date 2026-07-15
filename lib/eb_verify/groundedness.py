"""
Evidence-span groundedness gate — a deterministic verifier primitive.

Checks that each citation's evidence_span appears verbatim (whitespace-
normalized, case-insensitive) in the cited file inside the task workspace.
Adapted from the autoresearch harness groundedness() metric, hardened with
workspace containment (via plugins.safe_read) and a minimum-span guard so
trivially matchable spans ("import os") do not count as evidence.

Reasons per citation:
  ok             — span found in the cited file
  span_not_found — file read fine, span not present (fabricated/paraphrased)
  file_missing   — cited file does not exist (or is unreadable) in workspace
  path_escape    — cited path resolves outside the workspace (.. or symlink)
  too_short      — normalized span shorter than MIN_SPAN_CHARS
  too_large      — cited file exceeds MAX_EVIDENCE_FILE_BYTES (never read)
  not_a_regular_file — cited path is a pipe/socket/device, not evidence
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from eb_verify.plugins import FileTooLargeError, NotRegularFileError, safe_read

# Spans shorter than this after whitespace normalization are not evidence.
MIN_SPAN_CHARS = 20

# Evidence files larger than this are rejected unread (reason: too_large) —
# a citation into a multi-gigabyte log is not verifiable evidence, and the
# verifier must not buffer it.
MAX_EVIDENCE_FILE_BYTES = 10 * 1024 * 1024

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to single spaces, lowercase, strip."""
    return _WS.sub(" ", text).lower().strip()


@dataclass(frozen=True)
class Citation:
    """A single evidence citation. repo may be '' for single-repo tasks."""

    repo: str
    file: str
    evidence_span: str

    def __post_init__(self) -> None:
        for name, value in (
            ("repo", self.repo),
            ("file", self.file),
            ("evidence_span", self.evidence_span),
        ):
            if not isinstance(value, str):
                raise ValueError(
                    f"Citation.{name} must be a string, got {type(value).__name__}"
                )
        if not self.file:
            raise ValueError("Citation.file must be non-empty")


@dataclass(frozen=True)
class CitationResult:
    citation: Citation
    grounded: bool
    reason: str  # ok | span_not_found | file_missing | path_escape | too_short | too_large


@dataclass(frozen=True)
class GroundednessResult:
    results: tuple[CitationResult, ...]
    score: float  # grounded / total; 1.0 for an empty citation list

    @property
    def all_grounded(self) -> bool:
        return all(r.grounded for r in self.results)

    def summary(self) -> str:
        if not self.results:
            return "no citations (score=1.00)"
        parts = [
            f"{r.citation.repo + '/' if r.citation.repo else ''}{r.citation.file}: {r.reason}"
            for r in self.results
        ]
        return f"score={self.score:.2f} [" + "; ".join(parts) + "]"


def _check_one(
    citation: Citation, workspace: Path, cache: dict[tuple[str, str], str]
) -> CitationResult:
    span = _normalize(citation.evidence_span)
    if len(span) < MIN_SPAN_CHARS:
        return CitationResult(citation, grounded=False, reason="too_short")

    key = (citation.repo, citation.file)
    if key in cache:
        normalized_content = cache[key]
    else:
        target = (
            workspace / citation.repo / citation.file
            if citation.repo
            else workspace / citation.file
        )
        try:
            content = safe_read(target, workspace, max_bytes=MAX_EVIDENCE_FILE_BYTES)
        except FileTooLargeError:
            # Size cap fired (after containment passed): file never read.
            return CitationResult(citation, grounded=False, reason="too_large")
        except NotRegularFileError:
            # A pipe/socket/device the agent planted inside its own workspace.
            # Listed before ValueError: it subclasses ValueError, and this is
            # not a containment failure.
            return CitationResult(citation, grounded=False, reason="not_a_regular_file")
        except ValueError:
            # safe_read's containment check fired: .. traversal or symlink escape.
            return CitationResult(citation, grounded=False, reason="path_escape")
        except (FileNotFoundError, IsADirectoryError):
            return CitationResult(citation, grounded=False, reason="file_missing")
        except (OSError, UnicodeDecodeError):
            # Unreadable (permissions, binary content): the file cannot serve
            # as textual evidence, so the citation is not grounded.
            return CitationResult(citation, grounded=False, reason="file_missing")
        normalized_content = _normalize(content)
        cache[key] = normalized_content

    if span in normalized_content:
        return CitationResult(citation, grounded=True, reason="ok")
    return CitationResult(citation, grounded=False, reason="span_not_found")


def check_groundedness(
    citations: Sequence[Citation], workspace: Path
) -> GroundednessResult:
    """Check every citation's evidence_span against the workspace.

    Score = grounded / total, and 1.0 for an empty list (matching the
    reference harness: no claims means nothing is ungrounded).
    """
    if not isinstance(workspace, Path):
        raise ValueError(f"workspace must be a Path, got {type(workspace).__name__}")
    # Per-call cache of successfully read + normalized file contents, so N
    # citations against the same file normalize it once. Read failures are
    # deliberately not cached (each citation reports its own failure).
    cache: dict[tuple[str, str], str] = {}
    results = tuple(_check_one(c, workspace, cache) for c in citations)
    if not results:
        return GroundednessResult(results=(), score=1.0)
    score = sum(1 for r in results if r.grounded) / len(results)
    return GroundednessResult(results=results, score=score)


def parse_citations(raw: Any) -> list[Citation]:
    """Parse a JSON-shaped citations list into Citation objects.

    Raises ValueError with a precise message on any malformed entry —
    malformed citations must surface as verification issues, never pass
    silently.
    """
    if not isinstance(raw, list):
        raise ValueError(f"citations must be a list, got {type(raw).__name__}")
    citations: list[Citation] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"citations[{i}] must be an object, got {type(entry).__name__}")
        if "file" not in entry:
            raise ValueError(f"citations[{i}] missing required key 'file'")
        if "evidence_span" not in entry:
            raise ValueError(f"citations[{i}] missing required key 'evidence_span'")
        try:
            citations.append(
                Citation(
                    repo=entry.get("repo", ""),
                    file=entry["file"],
                    evidence_span=entry["evidence_span"],
                )
            )
        except ValueError as e:
            raise ValueError(f"citations[{i}]: {e}") from e
    return citations


class CitationParseError(ValueError):
    """The citations field is missing or malformed; carries per-entry issues."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("; ".join(issues))
        self.issues = issues


def ground_citations(data: dict[str, Any], workspace: Path) -> GroundednessResult:
    """Plugin seam: extract data['citations'] and check groundedness.

    Raises CitationParseError (carrying the individual issues) when the
    citations field is missing or malformed. A structurally valid citations
    list always yields a GroundednessResult (whose per-citation reasons carry
    any grounding failures).
    """
    if "citations" not in data:
        raise CitationParseError(
            ["citations field is required when grounded citations are enforced"]
        )
    try:
        citations = parse_citations(data["citations"])
    except ValueError as e:
        raise CitationParseError([str(e)]) from e
    return check_groundedness(citations, workspace)
