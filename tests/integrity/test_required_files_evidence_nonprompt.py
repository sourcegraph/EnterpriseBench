"""A graded file path must not be one the prompt already spelled in full.

The sibling static invariant test_scoring_evidence_is_nonprompt.py covers tasks
that grade `scoring_evidence` / `drift_points.evidence_tokens`. It does NOT reach
the file-path cohort: err-provenance, support-mapping, api-contract, dep-graph
and peers grade the source files an agent names against
`ground_truth.required_files` / `sufficient_files`. Those checks were "resistant
by construction" but UNENFORCED (EnterpriseBench-jn73.2.7.3.1.4) — nothing stopped
a re-anchor or a new task from putting a graded path into the prompt, where a
fabricated answer could echo it for free.

This is the STATIC complement the md-echo vector (test_report_prompt_echo.py)
cannot supply for these tasks: their deliverable is JSON, so `cp instruction.md`
is not valid JSON, json.load throws, the check scores 0.0, and the task is
certified clean without the vector ever running. Comparing the graded path to the
prompt needs no deliverable at all, so it reaches them.

Faithful to how these tasks actually credit a file. Two matchers are in play and
both credit a VERBATIM FULL-PATH echo:

* The inline shell/jq/python cohort scores a ground-truth path `gt` when
  `any(gt in af or af.endswith(gt) for af in agent_files)`. `gt` is the whole
  path, so only the full path echoed into the answer scores; a basename the
  prompt happens to mention (`values.yaml`, `Cargo.toml`) cannot satisfy
  `gt in af` and is NOT a leak.
* The file_extraction scorer (eb_verify.scorers.file_extraction) matches on path
  components; a verbatim full-path echo reproduces every component and scores an
  exact match there too.

So the invariant is: no graded full path may appear verbatim in the prompt. It is
a NECESSARY condition (a subset both matchers agree is creditable), deliberately
narrower than "echo-proof": for the handful of component-matcher tasks a bare
BASENAME echo can also score, which this test does not model — empirically no
graded task has a basename-only echo today, and that surface is tracked
separately. A full-path hit here is always a real leak; a pass does not certify
resistance to every echo shape.

Graded, not merely present. A task's required_files/sufficient_files is only the
graded surface when a check actually reads it by path. Several tasks carry these
fields as author documentation while grading elsewhere — schema-evolution-004's
nine sufficient_files and incident-investigation-dual-nats-001's `nats.go` are
scored via scoring_evidence (or not by path at all), so a path of theirs sitting
in the prompt credits nothing and must NOT be flagged. `_graded_path_fields`
reads each task's own check scripts and counts a field only when a non-comment
line consumes it (or invokes the file_extraction scorer, which reads
required_files). This is what keeps the sweep sound: every path it asserts on is
one a fabricated answer could actually cash in.

Judged against instruction.md (plus instruction_mcp.md where present, shown in
mcp/cli modes) — the entire text the agent receives. task.toml's [task].prompt is
stale CSB-migration metadata never shown to the agent, so grading against it would
both miss real leaks and invent phantom ones (see the sibling test's note).

Comparison is case-sensitive fixed-string containment, matching the checks' own
`in`/`endswith`: an echo preserves the prompt's casing, so a case-variant in the
prompt cannot be cashed in and is not a leak.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH = REPO_ROOT / "benchmarks"

PATH_FIELDS = ("required_files", "sufficient_files")


def _noncomment_lines(text: str):
    """Lines that execute — a check's comment may name a field it no longer grades
    (e.g. "# re-anchored away from required_files"), and counting that would
    resurrect a vestigial field as if it were the graded surface."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield line


def _graded_path_fields(task_dir: Path) -> set[str]:
    """Which of required_files/sufficient_files this task actually grades by path.

    A field counts only when a check consumes it on a non-comment line, or when a
    check invokes the file_extraction scorer (which reads required_files from
    ground_truth). Fields carried in ground_truth but graded via scoring_evidence,
    or not scored at all, are excluded — echoing them credits nothing.
    """
    checks_dir = task_dir / "checks"
    if not checks_dir.is_dir():
        return set()
    fields: set[str] = set()
    invokes_file_extraction = False
    for check in sorted(checks_dir.glob("*.sh")):
        for line in _noncomment_lines(check.read_text(errors="replace")):
            if "file_extraction" in line:
                invokes_file_extraction = True
            for field in PATH_FIELDS:
                if field in line:
                    fields.add(field)
    if invokes_file_extraction:
        fields.add("required_files")
    return fields


def _gt_body(gt: dict) -> dict:
    """The dict a check actually reads the graded fields from.

    Every path check resolves the field with `gt.get('ground_truth', gt)`:
    support-mapping-001..004 nest required_files/sufficient_files under a
    `ground_truth` key with nothing at the top level, and the dual-* tasks carry
    both copies (the nested one is what the scorer consumes). Reading the top
    level only would silently drop the wholly-nested cohort from the sweep — a real
    grader treated as absent — so mirror the checks' unwrap exactly.
    """
    inner = gt.get("ground_truth")
    return inner if isinstance(inner, dict) else gt


def _paths_in_field(gt: dict, field: str) -> list[str]:
    """Ground-truth paths in a field, accepting both entry shapes the checks do:
    bare strings and {"path": ...} dicts."""
    body = _gt_body(gt)
    out: list[str] = []
    for entry in body.get(field) or []:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
        elif isinstance(entry, dict):
            path = entry.get("path")
            if isinstance(path, str) and path.strip():
                out.append(path.strip())
    return out


def _graded_paths(task_dir: Path, gt: dict) -> list[tuple[str, str]]:
    """Every (field, path) this task grades by path matching, deduped."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for field in sorted(_graded_path_fields(task_dir)):
        for path in _paths_in_field(gt, field):
            key = (field, path)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _prompt_text(task_dir: Path) -> str:
    """Everything the agent is shown: instruction.md, plus instruction_mcp.md in
    mcp/cli modes when the task carries one."""
    text = (task_dir / "instruction.md").read_text(errors="replace")
    mcp = task_dir / "instruction_mcp.md"
    if mcp.exists():
        text += "\n" + mcp.read_text(errors="replace")
    return text


def _candidate_task_dirs() -> list[Path]:
    """Every non-archived task dir the sweep could consider: one carrying both a
    ground_truth.json and the instruction.md the comparison reads.

    Split out from the graded set so the coverage guard can report a denominator.
    Coverage falls for two unrelated reasons and only one of them is this file's
    bug; the counts separate them (see test_the_invariant_is_actually_covering_tasks).
    """
    out: list[Path] = []
    for gt_path in sorted(BENCH.rglob("ground_truth.json")):
        if {"_archived", "mined"} & set(gt_path.relative_to(BENCH).parts):
            continue
        if (gt_path.parent / "instruction.md").exists():
            out.append(gt_path.parent)
    return out


def _tasks_grading_file_paths(candidates: list[Path]) -> list[Path]:
    found: list[Path] = []
    for task_dir in candidates:
        try:
            gt = json.loads((task_dir / "ground_truth.json").read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue  # schema tests own malformed ground_truth
        if isinstance(gt, dict) and _graded_paths(task_dir, gt):
            found.append(task_dir)
    return found


CANDIDATE_DIRS = _candidate_task_dirs()
TASK_DIRS = _tasks_grading_file_paths(CANDIDATE_DIRS)
TASK_IDS = [str(d.relative_to(BENCH)) for d in TASK_DIRS]


@pytest.mark.parametrize("task_dir", TASK_DIRS, ids=TASK_IDS)
def test_graded_file_path_absent_from_prompt(task_dir: Path) -> None:
    """No path this task grades may be spelled in full in its own prompt."""
    gt = json.loads((task_dir / "ground_truth.json").read_text())
    prompt = _prompt_text(task_dir)

    leaked = [
        (field, path)
        for field, path in _graded_paths(task_dir, gt)
        if path in prompt  # case-sensitive, faithful to the checks' `in`/`endswith`
    ]
    assert not leaked, (
        f"{task_dir.relative_to(BENCH)} grades file paths its own prompt spells in "
        f"full, so a fabricated answer earns credit by echoing them: {leaked}. "
        f"Re-anchor to files only a reader of the repo can name, or drop the path "
        f"from the prompt."
    )


def test_the_invariant_is_actually_covering_tasks() -> None:
    """Guard against a silently empty sweep: if _graded_path_fields stops matching
    the check idioms (a scorer rename, a jq-to-python rewrite), every test above
    vanishes and the suite still goes green.

    Reported as a fraction of the candidate corpus, because coverage falls for two
    unrelated reasons whose fixes are opposite. Naming only the discovery half sent
    two independent investigations (EnterpriseBench-8o9xs, EnterpriseBench-edsyz) at
    a sound `_graded_path_fields` before either measured the denominator.
    """
    assert len(TASK_DIRS) >= 60, (
        f"only {len(TASK_DIRS)} of {len(CANDIDATE_DIRS)} candidate tasks grade file "
        f"paths; 63 did when this was written, and a tree carrying the whole corpus "
        f"still discovers exactly those 63 out of ~180 candidates today. Read BOTH "
        f"numbers before changing anything here — they separate two different bugs:\n"
        f"  * Candidates ALSO down: the corpus in this checkout is incomplete and the "
        f"sweep is sound. 31 of those 63 task dirs (the *-dual-* cohort) are untracked "
        f"in git (EnterpriseBench-hllf4), so a clean checkout discovers 32 while the "
        f"tree that authored them discovers all 63. Discovery is not the bug: do not "
        f"widen it, and do not lower this bound — 60 is calibrated to a complete "
        f"corpus and lowering it would retire the guard. Confirm with:\n"
        f"      git status --porcelain benchmarks | grep '^??'\n"
        f"  * Candidates HOLD but graded falls: this file's own bug. _graded_path_fields "
        f"no longer reads how the checks consume required_files/sufficient_files, or "
        f"_gt_body stopped unwrapping the nested ground_truth key — the sweep is passing "
        f"vacuously and every assertion above has silently vanished."
    )
