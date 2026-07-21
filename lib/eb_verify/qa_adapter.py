"""EB adapter for the vendored ``benchmark_qa_core`` QA library (F2 subset).

This module is the *rig adapter* contract for the aux-file-leakage check. It
parses EB's task-meta artifacts (``task.toml``, ``ground_truth.json``) into
the lib's flat input shapes and returns the lib's flat ``list[Finding]``.

Design notes:

* The lib is imported from ``eb_verify._vendor.benchmark_qa_core`` (a
  first-party copy; see ``_vendor/benchmark_qa_core/VENDOR.md``).
* The F2 check flags oracle file paths (``[ground_truth].required_files`` /
  ``sufficient_files``) that appear word-boundary-verbatim in
  ``instruction.md`` (the agent-visible prompt mirror). The lib emits F2 at
  ``error``; this adapter downgrades it to ``warning`` because several EB
  task families legitimately name the file under investigation.
* Tokens an author has explicitly triaged via a ``[[qa.waivers]]`` block in
  ``task.toml`` are downgraded further, to ``info``, with the recorded
  rationale attached — the finding stays visible as an audit trail (not
  swallowed) but no longer counts as a warning. Token matching is exact (by
  ``repr``), so a waiver for one path cannot suppress a different path's F2.
* This is QA tooling over task *definitions*; it never touches the scoring
  path (``scorer_guard`` channels do not apply here).

Precedent for waive-vs-leak decisions: ``docs/qa/eb_f2_triage_2026_06_04.md``
— a named path is a leak only when it reveals a discriminative answer the
task scores the agent for *discovering*, not when it is the given
investigation entry point.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from eb_verify._vendor.benchmark_qa_core import (
    Finding,
    check_aux_file_leakage,
)


@dataclass(frozen=True)
class TaskInputs:
    """Already-parsed task-meta artifacts the adapter feeds into the lib."""

    task_toml: dict[str, Any]
    ground_truth: dict[str, Any]
    task_dir: Path
    instruction_path: Path | None


@dataclass(frozen=True)
class QaReport:
    """Aggregated QA findings for one EB task."""

    task_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]


def load_task_inputs(task_toml_path: Path) -> TaskInputs:
    """Read the EB task-meta artifacts from ``task_toml_path.parent``.

    Ground truth has two shapes in EB:

    * the ``[ground_truth]`` table inside ``task.toml`` carries the canonical
      schema-validated ``required_files`` / ``sufficient_files``;
    * ``ground_truth.json`` next to ``task.toml`` carries task-type-specific
      keys and may also mirror ``required_files`` / ``sufficient_files``.

    Both are merged: ``task.toml`` fields win, then JSON fills any gaps. A
    missing or unparseable ``ground_truth.json`` contributes nothing — that
    is a schema-validator finding, not a fatal error here.
    """
    task_dir = task_toml_path.parent
    with open(task_toml_path, "rb") as fh:
        task_toml = tomllib.load(fh)

    gt_embedded = task_toml.get("ground_truth")
    ground_truth: dict[str, Any] = (
        dict(gt_embedded) if isinstance(gt_embedded, dict) else {}
    )
    gt_path = task_dir / "ground_truth.json"
    if gt_path.exists():
        try:
            gt_json = json.loads(gt_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            gt_json = {}
        if isinstance(gt_json, dict):
            for key, value in gt_json.items():
                ground_truth.setdefault(key, value)

    instruction_path = task_dir / "instruction.md"

    return TaskInputs(
        task_toml=task_toml,
        ground_truth=ground_truth,
        task_dir=task_dir,
        instruction_path=instruction_path if instruction_path.exists() else None,
    )


def _oracle_tokens(ground_truth: dict[str, Any]) -> list[str]:
    """Collect oracle file paths the agent should not see verbatim.

    Only takes oracle file paths from ground_truth — these are the most
    common leak-into-prompt failure mode in EB. Symbol-level tokens are
    omitted: they are domain identifiers that legitimately appear in
    ``instruction.md`` (e.g. CVE IDs, public function names the prompt tells
    the agent to investigate).
    """
    paths: list[str] = []
    for section in ("required_files", "sufficient_files"):
        for entry in ground_truth.get(section, []) or []:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def run_qa_checks(inputs: TaskInputs) -> QaReport:
    """Run the F2 aux-file-leakage check for one EB task.

    Returns:
        :class:`QaReport` with F2 findings at ``warning`` (unwaived) or
        ``info`` (waived via ``[[qa.waivers]]``); F1/F3 findings pass
        through at the lib's severity.
    """
    task_section = inputs.task_toml.get("task") or {}
    task_id = task_section.get("id") or inputs.task_dir.name

    tokens = _oracle_tokens(inputs.ground_truth)
    aux_files: list[Path] = (
        [inputs.instruction_path] if inputs.instruction_path is not None else []
    )
    if not tokens or not aux_files:
        return QaReport(task_id=task_id, findings=[])

    waivers = _f2_waivers(inputs.task_toml)
    findings: list[Finding] = []
    for raw_finding in check_aux_file_leakage(tokens, aux_files):
        if raw_finding.code != "F2":
            findings.append(raw_finding)
            continue
        waiver = _match_waiver(raw_finding.message, waivers)
        if waiver is None:
            severity, message = "warning", raw_finding.message
        else:
            severity = "info"
            message = (
                f"{raw_finding.message} "
                f"[{waiver['decision']}d: {waiver['rationale']}]"
            )
        findings.append(
            Finding(
                severity=severity,
                code=raw_finding.code,
                message=message,
                location=raw_finding.location,
                suggested_fix=raw_finding.suggested_fix,
            )
        )

    return QaReport(task_id=task_id, findings=findings)


def _f2_waivers(task_toml: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Map waived F2 token -> waiver record from ``[[qa.waivers]]``.

    Only ``code == "F2"`` waivers with an ``accept``/``waive`` decision are
    returned, keyed by the oracle ``token`` they suppress.
    """
    qa = task_toml.get("qa") or {}
    out: dict[str, dict[str, str]] = {}
    for entry in qa.get("waivers", []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("code") != "F2":
            continue
        token = entry.get("token")
        decision = entry.get("decision")
        if not isinstance(token, str) or decision not in ("accept", "waive"):
            continue
        out[token] = {
            "decision": str(decision),
            "rationale": str(entry.get("rationale", "")),
        }
    return out


def _match_waiver(
    message: str, waivers: dict[str, dict[str, str]]
) -> dict[str, str] | None:
    """Return the waiver whose token the F2 ``message`` reports, if any.

    The lib emits ``Oracle token <token!r> appears in aux file: ...``; the
    token is matched by its ``repr`` so partial-path collisions can't match
    the wrong waiver.
    """
    for token, waiver in waivers.items():
        if f"token {token!r} " in message:
            return waiver
    return None


def iter_finding_lines(findings: Iterable[Finding]) -> Iterable[str]:
    """Pretty-print findings for CLI output."""
    for f in findings:
        loc = f" [{f.location}]" if f.location else ""
        fix = f"\n      fix: {f.suggested_fix}" if f.suggested_fix else ""
        yield f"  {f.severity.upper():<7} {f.code}{loc} {f.message}{fix}"
