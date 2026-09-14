"""Tests for the evidence-span groundedness gate (lib/eb_verify/groundedness.py)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from eb_verify.groundedness import (  # noqa: E402
    Citation,
    CitationParseError,
    GroundednessResult,
    MAX_EVIDENCE_FILE_BYTES,
    MIN_SPAN_CHARS,
    check_groundedness,
    ground_citations,
)

# A span comfortably over the 20-normalized-char minimum.
SPAN = "def compute_score(results): return weighted_sum(results)"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Single-repo-style workspace: files directly under workspace/."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "scoring.py").write_text(
        "# scoring module\n"
        "def compute_score(results):    return weighted_sum(results)\n"
        "MAGIC = 42\n"
    )
    return workspace


@pytest.fixture
def multi_ws(tmp_path: Path) -> Path:
    """Multi-repo layout: workspace/{repo}/{file}."""
    workspace = tmp_path / "workspace"
    (workspace / "billing-svc" / "internal").mkdir(parents=True)
    (workspace / "billing-svc" / "internal" / "ledger.go").write_text(
        "func ApplyCredit(acct *Account, amount int64) error {\n"
        "\treturn acct.ledger.Append(amount)\n"
        "}\n"
    )
    return workspace


def one(result: GroundednessResult):
    assert len(result.results) == 1
    return result.results[0]


# --- matching semantics -----------------------------------------------------

def test_exact_match(ws: Path):
    c = Citation(repo="", file="src/scoring.py", evidence_span=SPAN)
    res = check_groundedness([c], ws)
    assert one(res).grounded is True
    assert one(res).reason == "ok"
    assert res.score == 1.0


def test_whitespace_variant_match(ws: Path):
    # File has multiple spaces + newline; span uses tabs and newlines.
    span = "def compute_score(results):\n\treturn\t weighted_sum(results)\nMAGIC = 42"
    c = Citation(repo="", file="src/scoring.py", evidence_span=span)
    res = check_groundedness([c], ws)
    assert one(res).grounded is True


def test_case_variant_match(ws: Path):
    c = Citation(repo="", file="src/scoring.py", evidence_span=SPAN.upper())
    res = check_groundedness([c], ws)
    assert one(res).grounded is True


def test_fabricated_span(ws: Path):
    c = Citation(
        repo="",
        file="src/scoring.py",
        evidence_span="def compute_score(results): return fabricated_nonsense(results)",
    )
    res = check_groundedness([c], ws)
    assert one(res).grounded is False
    assert one(res).reason == "span_not_found"
    assert res.score == 0.0


# --- file / path failure modes ----------------------------------------------

def test_missing_file(ws: Path):
    c = Citation(repo="", file="src/nonexistent.py", evidence_span=SPAN)
    res = check_groundedness([c], ws)
    assert one(res).grounded is False
    assert one(res).reason == "file_missing"


def test_path_escape_dotdot(ws: Path, tmp_path: Path):
    secret = tmp_path / "secret.txt"
    secret.write_text("this is a secret credential value outside workspace")
    c = Citation(
        repo="",
        file="../secret.txt",
        evidence_span="this is a secret credential value outside workspace",
    )
    res = check_groundedness([c], ws)
    assert one(res).grounded is False
    assert one(res).reason == "path_escape"


def test_path_escape_symlink(ws: Path, tmp_path: Path):
    secret = tmp_path / "secret.txt"
    secret.write_text("this is a secret credential value outside workspace")
    (ws / "link.txt").symlink_to(secret)
    c = Citation(
        repo="",
        file="link.txt",
        evidence_span="this is a secret credential value outside workspace",
    )
    res = check_groundedness([c], ws)
    assert one(res).grounded is False
    assert one(res).reason == "path_escape"


# --- span-length guard --------------------------------------------------------

def test_span_too_short(ws: Path):
    c = Citation(repo="", file="src/scoring.py", evidence_span="MAGIC = 42")
    assert len("MAGIC = 42") < MIN_SPAN_CHARS
    res = check_groundedness([c], ws)
    assert one(res).grounded is False
    assert one(res).reason == "too_short"


def test_span_too_short_after_normalization(ws: Path):
    # Padded with whitespace so raw length exceeds the minimum but the
    # normalized length does not.
    padded = "   MAGIC   =    42   \n\n\t "
    assert len(padded) >= MIN_SPAN_CHARS
    c = Citation(repo="", file="src/scoring.py", evidence_span=padded)
    res = check_groundedness([c], ws)
    assert one(res).reason == "too_short"


# --- layouts & aggregates ------------------------------------------------------

def test_multi_repo_layout(multi_ws: Path):
    c = Citation(
        repo="billing-svc",
        file="internal/ledger.go",
        evidence_span="func ApplyCredit(acct *Account, amount int64) error",
    )
    res = check_groundedness([c], multi_ws)
    assert one(res).grounded is True


def test_empty_citations_scores_one(ws: Path):
    res = check_groundedness([], ws)
    assert res.score == 1.0
    assert res.results == ()


def test_results_are_an_immutable_tuple(ws: Path):
    c = Citation(repo="", file="src/scoring.py", evidence_span=SPAN)
    res = check_groundedness([c], ws)
    assert isinstance(res.results, tuple)


def test_aggregate_score_mixed(ws: Path):
    good = Citation(repo="", file="src/scoring.py", evidence_span=SPAN)
    bad = Citation(
        repo="", file="src/scoring.py", evidence_span="totally fabricated span content here"
    )
    res = check_groundedness([good, bad], ws)
    assert res.score == 0.5


def test_citation_rejects_non_string_fields():
    with pytest.raises(ValueError):
        Citation(repo="", file=None, evidence_span=SPAN)  # type: ignore[arg-type]


# --- ground_citations helper (plugin seam) -------------------------------------

def test_ground_citations_parses_dicts(ws: Path):
    data = {
        "citations": [
            {"file": "src/scoring.py", "evidence_span": SPAN},
        ]
    }
    result = ground_citations(data, ws)
    assert isinstance(result, GroundednessResult)
    assert result.score == 1.0


def test_ground_citations_missing_field_raises(ws: Path):
    with pytest.raises(CitationParseError) as exc_info:
        ground_citations({}, ws)
    assert exc_info.value.issues  # explicit issues reported, not silently OK


def test_ground_citations_malformed_entry_raises(ws: Path):
    data = {"citations": [{"file": "src/scoring.py"}]}  # no evidence_span
    with pytest.raises(CitationParseError) as exc_info:
        ground_citations(data, ws)
    assert any("evidence_span" in i for i in exc_info.value.issues)


def test_citation_parse_error_is_a_value_error():
    # Callers that catch ValueError from the old sentinel-tuple era keep working.
    assert issubclass(CitationParseError, ValueError)


# --- evidence-file size guard ----------------------------------------------

def _write_oversized(path: Path, lead: str = "") -> None:
    """Sparse file one byte over the cap; optional readable prefix."""
    with open(path, "wb") as f:
        if lead:
            f.write(lead.encode())
        f.seek(MAX_EVIDENCE_FILE_BYTES)
        f.write(b"\0")


def test_oversized_file_in_workspace_is_too_large_not_read(ws: Path):
    # The span IS present at the start of the file: if the implementation
    # read the content anyway, the citation would ground as "ok". too_large
    # proves the size gate fired before any content matching.
    big = ws / "big.log"
    _write_oversized(big, lead=SPAN)
    c = Citation(repo="", file="big.log", evidence_span=SPAN)
    res = check_groundedness([c], ws)
    assert one(res).grounded is False
    assert one(res).reason == "too_large"


def test_cited_fifo_is_rejected_and_never_blocks(ws: Path):
    """A citation naming a FIFO must resolve to a reason, not hang the verifier.

    mkfifo needs no privilege and the pipe sits inside the workspace, so
    containment does not stop it. Reading it blocks until a writer appears —
    none ever does — which would stall scoring until its timeout and convert a
    certain 0.0 into a re-runnable infra error. The span deliberately never has
    to match: the open is what hangs.
    """
    os.mkfifo(ws / "evidence.pipe")
    c = Citation(repo="", file="evidence.pipe", evidence_span=SPAN)
    res = check_groundedness([c], ws)
    assert one(res).grounded is False
    assert one(res).reason == "not_a_regular_file"


def test_oversized_file_outside_workspace_is_path_escape(ws: Path, tmp_path: Path):
    # Containment is checked FIRST: an escaping path must report path_escape,
    # never be stat'd for a size verdict.
    big = tmp_path / "big_outside.log"
    _write_oversized(big, lead=SPAN)
    c = Citation(repo="", file="../big_outside.log", evidence_span=SPAN)
    res = check_groundedness([c], ws)
    assert one(res).grounded is False
    assert one(res).reason == "path_escape"


# --- incident_report integration ------------------------------------------------

def _write_report(ws: Path, citations) -> None:
    report = {
        "timeline": [{"time": "2026-01-01T00:00:00Z", "event": "alert fired"}],
        "root_cause": "scoring bug",
        "remediation": "patch scoring.py",
        "affected_services": ["scoring"],
        "citations": citations,
    }
    (ws / "incident_report.json").write_text(json.dumps(report))


def test_incident_report_grounded_citations_pass(ws: Path):
    from eb_verify.plugins.incident_report import IncidentReportValidator

    _write_report(ws, [{"file": "src/scoring.py", "evidence_span": SPAN}])
    result = IncidentReportValidator().validate(ws, require_grounded_citations=True)
    assert result.valid is True


def test_incident_report_fabricated_citation_fails(ws: Path):
    from eb_verify.plugins.incident_report import IncidentReportValidator

    _write_report(
        ws, [{"file": "src/scoring.py", "evidence_span": "fabricated span that is long enough"}]
    )
    result = IncidentReportValidator().validate(ws, require_grounded_citations=True)
    assert result.valid is False
    assert "span_not_found" in result.detail


def test_incident_report_missing_citations_fails_when_required(ws: Path):
    from eb_verify.plugins.incident_report import IncidentReportValidator

    report = {
        "timeline": [],
        "root_cause": "scoring bug",
        "remediation": "patch",
        "affected_services": [],
    }
    (ws / "incident_report.json").write_text(json.dumps(report))
    result = IncidentReportValidator().validate(ws, require_grounded_citations=True)
    assert result.valid is False


def test_incident_report_default_ignores_citations(ws: Path):
    """Without the flag, behavior is unchanged (least-invasive integration)."""
    from eb_verify.plugins.incident_report import IncidentReportValidator

    _write_report(ws, [{"file": "src/scoring.py", "evidence_span": "fabricated but ignored span"}])
    result = IncidentReportValidator().validate(ws)
    assert result.valid is True
