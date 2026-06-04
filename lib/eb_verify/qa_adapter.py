"""EB adapter for the vendored ``benchmark_qa_core`` QA library.

This module is the *rig adapter* contract. It parses EB's three task-meta
artifacts (``task.toml``, ``ground_truth.json``, ``expected_solution.json``)
into the lib's flat input shapes and returns the lib's flat ``list[Finding]``.

Design notes:

* The lib is imported from ``eb_verify._vendor.benchmark_qa_core`` (a
  first-party copy; see ``_vendor/benchmark_qa_core/VENDOR.md``).
* Oracle file/symbol existence checks (``A1``/``B1``/``B2``) need access to the
  cloned repo. EB clones repos at runtime under ``$WORKSPACE/<repo.path>``;
  when no workspace is supplied or repos are missing, those checks are
  skipped (with an ``info`` finding so the operator knows).
* EB tasks do not carry a ``scoring_method`` field. The adapter synthesises
  one from ``verification_modes`` (joined with ``+``) so the lib's
  ``check_scoring_honesty`` can run unchanged.
* Aux-file leakage checks flag oracle file paths that appear verbatim in
  ``instruction.md`` (the agent-visible prompt mirror). Severity is
  ``warning`` by default since some task families legitimately mention the
  file under investigation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:  # pragma: no cover — covered by both branches in CI
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

from eb_verify._vendor.benchmark_qa_core import (
    Finding,
    OracleConstraints,
    check_aux_file_leakage,
    check_oracle_coherence,
    check_scoring_honesty,
)


# Sanctioned scoring-method tier table for EB. Synthesised values come from
# the task's ``verification_modes`` array, joined with ``+`` in sorted order.
EB_SCORING_METHOD_TIERS: dict[str, str] = {
    "deterministic": "calibrated",
    "llm_curator": "calibrated",
    "solve_verified": "calibrated",
    "structural_match": "calibrated",
    "deterministic+llm_curator": "calibrated",
    "deterministic+solve_verified": "calibrated",
    "deterministic+structural_match": "calibrated",
    "llm_curator+solve_verified": "calibrated",
    "llm_curator+structural_match": "calibrated",
    "deterministic+llm_curator+solve_verified": "calibrated",
    "deterministic+llm_curator+structural_match": "calibrated",
    "deterministic+solve_verified+structural_match": "calibrated",
    "llm_curator+solve_verified+structural_match": "calibrated",
    "deterministic+llm_curator+solve_verified+structural_match": "calibrated",
}


@dataclass(frozen=True)
class TaskInputs:
    """Already-parsed task-meta artifacts the adapter feeds into the lib."""

    task_toml: dict[str, Any]
    ground_truth: dict[str, Any]
    expected_solution: dict[str, Any]
    task_dir: Path
    instruction_path: Path | None
    workspace_root: Path | None  # None when not running locally


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


def load_task_inputs(
    task_toml_path: Path,
    *,
    workspace_root: Path | None = None,
) -> TaskInputs:
    """Read the three EB artifacts from ``task_toml_path.parent``.

    Missing ``ground_truth.json`` or ``expected_solution.json`` files are
    represented as empty dicts — that is itself a (downstream) finding from
    the schema validator, not a fatal error here.
    """
    if tomllib is None:  # pragma: no cover
        raise ImportError(
            "TOML parsing requires tomllib (Python 3.11+) or tomli."
        )
    task_dir = task_toml_path.parent
    with open(task_toml_path, "rb") as fh:
        task_toml = tomllib.load(fh)

    # Ground truth has two shapes in EB:
    #   - [ground_truth] table inside task.toml carries the canonical
    #     schema-validated required_files / sufficient_files.
    #   - ground_truth.json next to task.toml carries task-type-specific
    #     keys (producer_changed_files, breakage_type, etc.) and may also
    #     mirror required_files / sufficient_files for newer tasks.
    # Merge both: task.toml fields win, then JSON fills any gaps.
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
    es_path = task_dir / "expected_solution.json"
    expected_solution = (
        json.loads(es_path.read_text(encoding="utf-8")) if es_path.exists() else {}
    )
    instruction_path = task_dir / "instruction.md"
    instruction = instruction_path if instruction_path.exists() else None

    return TaskInputs(
        task_toml=task_toml,
        ground_truth=ground_truth,
        expected_solution=expected_solution,
        task_dir=task_dir,
        instruction_path=instruction,
        workspace_root=workspace_root,
    )


def _oracle_files(ground_truth: dict[str, Any]) -> list[tuple[str | None, str]]:
    """Flatten ``required_files`` + ``sufficient_files`` from GT.

    Returns tuples of ``(repo, path)`` where ``repo`` is the GT entry's
    ``repo`` field (or ``None`` if the entry doesn't declare one) and
    ``path`` is the file path relative to that repo's root.
    """
    out: list[tuple[str | None, str]] = []
    for section in ("required_files", "sufficient_files"):
        for entry in ground_truth.get(section, []) or []:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            repo = entry.get("repo")
            if isinstance(path, str) and path:
                out.append(
                    (repo if isinstance(repo, str) and repo else None, path)
                )
    return out


def _oracle_languages(task_toml: dict[str, Any]) -> frozenset[str]:
    """Extract expected languages from ``[metadata].languages`` if present."""
    meta = task_toml.get("metadata") or {}
    langs = meta.get("languages") or []
    cleaned: list[str] = [
        str(lang).lower() for lang in langs if isinstance(lang, str) and lang
    ]
    return frozenset(cleaned)


def _oracle_tokens(
    ground_truth: dict[str, Any],
    expected_solution: dict[str, Any],
) -> list[str]:
    """Collect strings the agent should not see verbatim in aux files.

    Only takes oracle file paths from ground_truth — these are the most
    common leak-into-prompt failure mode in EB. Symbol-level tokens from
    expected_solution are omitted: they are domain identifiers that
    legitimately appear in instruction.md (e.g. CVE IDs, public function
    names that the prompt tells the agent to investigate).
    """
    del expected_solution  # reserved for future symbol-token extraction
    paths: list[str] = []
    for entry in (
        list(ground_truth.get("required_files", []) or [])
        + list(ground_truth.get("sufficient_files", []) or [])
    ):
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        # Only flag the basename-bearing tail of the path, since the leading
        # directories (e.g. "src/") are too generic to count as a leak.
        # The full path is a fair leak signal — keep it.
        paths.append(path)
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _synthesised_scoring_method(task_toml: dict[str, Any]) -> str | None:
    """Synthesise EB's scoring-method string from ``verification_modes``.

    Returns ``None`` when ``verification_modes`` is missing or empty so the
    lib can emit ``E1``/``E2`` cleanly.
    """
    modes = task_toml.get("verification_modes")
    if not isinstance(modes, list) or not modes:
        return None
    cleaned = sorted({str(m) for m in modes if isinstance(m, str) and m})
    if not cleaned:
        return None
    return "+".join(cleaned)


def _resolve_repo_root(
    inputs: TaskInputs,
    primary_repo_path: str,
) -> Path | None:
    """Return the absolute repo root, or ``None`` if it can't be resolved.

    Order of preference:
      1. ``$WORKSPACE/<repo.path>`` if the workspace exists.
      2. ``inputs.task_dir / repo.path`` for archived/in-tree fixtures.
    """
    if inputs.workspace_root is not None:
        candidate = (inputs.workspace_root / primary_repo_path).resolve()
        if candidate.is_dir():
            return candidate
    candidate = (inputs.task_dir / primary_repo_path).resolve()
    if candidate.is_dir():
        return candidate
    return None


def run_qa_checks(
    inputs: TaskInputs,
    *,
    require_symbols_resolve: bool = True,
) -> QaReport:
    """Run all three QA checks for one EB task and aggregate findings.

    Args:
        inputs: Parsed task-meta artifacts. See :func:`load_task_inputs`.
        require_symbols_resolve: Forwarded to the lib's
            :class:`OracleConstraints`. ``True`` makes B1/B2 errors;
            ``False`` downgrades them to warnings.

    Returns:
        :class:`QaReport` aggregating all findings. The list is in the
        order: oracle coherence, scoring honesty, aux-file leakage.
    """
    findings: list[Finding] = []

    task_section = inputs.task_toml.get("task") or {}
    task_id = task_section.get("id") or inputs.task_dir.name
    instruction_text = (
        inputs.instruction_path.read_text(encoding="utf-8", errors="replace")
        if inputs.instruction_path is not None
        else ""
    )

    # --- Oracle coherence ------------------------------------------------
    repos = inputs.task_toml.get("repos") or []
    repo_paths_by_role: dict[str, str] = {}
    for r in repos:
        if not isinstance(r, dict):
            continue
        path = r.get("path")
        if isinstance(path, str) and path:
            role = r.get("role") or "primary"
            # First wins for "primary" so we have a deterministic root.
            repo_paths_by_role.setdefault(role, path)

    oracle_files_all = _oracle_files(inputs.ground_truth)
    declared_repo_paths = {
        r["path"] for r in repos if isinstance(r, dict) and isinstance(r.get("path"), str)
    }

    # Bucket oracle files by their declared repo. Entries that name a repo
    # not in repos[] are reported separately so we don't pretend they
    # resolved to anything.
    by_repo: dict[str, list[str]] = {}
    unmapped: list[tuple[str | None, str]] = []
    for repo, path in oracle_files_all:
        if repo is None or repo not in declared_repo_paths:
            unmapped.append((repo, path))
        else:
            by_repo.setdefault(repo, []).append(path)

    constraints = OracleConstraints(
        expected_languages=_oracle_languages(inputs.task_toml),
        require_symbols_resolve=require_symbols_resolve,
    )

    for repo_path, rel_files in by_repo.items():
        repo_root = _resolve_repo_root(inputs, repo_path)
        if repo_root is None:
            findings.append(
                Finding(
                    severity="info",
                    code="EB_A0",
                    message=(
                        f"Repo {repo_path!r} not cloned locally; "
                        f"skipping oracle file existence checks."
                    ),
                    location=repo_path,
                )
            )
            continue
        findings.extend(
            check_oracle_coherence(
                instruction_text=instruction_text,
                oracle_files=rel_files,
                oracle_symbols=[],  # EB does not declare (file, symbol) pairs
                repo_root=repo_root,
                constraints=constraints,
            )
        )

    for repo, path in unmapped:
        if repo is None:
            findings.append(
                Finding(
                    severity="warning",
                    code="EB_A2",
                    message=(
                        f"Oracle file {path!r} has no 'repo' field; cannot "
                        f"resolve against any of {sorted(declared_repo_paths)}"
                    ),
                    location=path,
                    suggested_fix=(
                        "Add a 'repo' key to the ground_truth entry naming "
                        "one of the declared repos."
                    ),
                )
            )
        else:
            findings.append(
                Finding(
                    severity="error",
                    code="EB_A3",
                    message=(
                        f"Oracle file {path!r} declares repo {repo!r} which "
                        f"is not in repos[]: {sorted(declared_repo_paths)}"
                    ),
                    location=f"{repo}::{path}",
                    suggested_fix=(
                        "Either fix the repo name on the ground_truth entry "
                        "or add the missing repo to repos[]."
                    ),
                )
            )

    # --- Scoring honesty -------------------------------------------------
    scoring_method = _synthesised_scoring_method(inputs.task_toml)
    task_meta_for_lib: dict[str, Any] = {}
    if scoring_method is not None:
        task_meta_for_lib["scoring_method"] = scoring_method
    findings.extend(
        check_scoring_honesty(task_meta_for_lib, EB_SCORING_METHOD_TIERS)
    )

    # --- Aux-file leakage ------------------------------------------------
    tokens = _oracle_tokens(inputs.ground_truth, inputs.expected_solution)
    aux_files: list[Path] = []
    if inputs.instruction_path is not None:
        aux_files.append(inputs.instruction_path)
    if tokens and aux_files:
        # Downgrade the lib's F2 errors to warnings: many EB task families
        # legitimately name the file under investigation in the prompt.
        # Surface the finding so authors can decide, but don't fail strict
        # mode on this alone. Tokens an author has explicitly triaged via a
        # [[qa.waivers]] block are downgraded further, to info, with the
        # recorded rationale attached — the finding stays visible as an audit
        # trail but no longer counts as a warning.
        waivers = _f2_waivers(inputs.task_toml)
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


def _strip_prefix(path: str, prefix: str) -> str:
    """Return ``path`` with a leading ``prefix/`` chopped off, if present."""
    p = prefix.rstrip("/") + "/"
    return path[len(p):] if path.startswith(p) else path


def iter_finding_lines(findings: Iterable[Finding]) -> Iterable[str]:
    """Pretty-print findings for CLI output."""
    for f in findings:
        loc = f" [{f.location}]" if f.location else ""
        fix = f"\n      fix: {f.suggested_fix}" if f.suggested_fix else ""
        yield f"  {f.severity.upper():<7} {f.code}{loc} {f.message}{fix}"
