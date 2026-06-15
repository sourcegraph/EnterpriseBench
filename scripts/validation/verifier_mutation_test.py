#!/usr/bin/env python3
"""Mutation-test checkpoint verifiers against synthetic agent outputs.

For each task, every checkpoint verifier runs in three synthetic workspaces:

  empty   — no agent output at all. Any score > 0 is a FALSE POSITIVE: the
            verifier awards credit for work that was never done.
  garbage — deterministic irrelevant prose written to every output file the
            verifier reads (paths extracted mechanically from the script).
            A score > 0 is a false-positive RISK: the verifier may be
            rewarding file existence rather than content.
  gold    — the curated expected_solution text (plus ground-truth file paths)
            written to the same files. A score of 0 on a checkpoint that has
            a curated expectation is a false-negative RISK: a correct answer
            in a reasonable surface form earns nothing.

This is the mechanical half of the CONVERGENCE_REPORT verifier-robustness
requirement (mutation testing on verifiers). It makes no semantic judgment —
it only checks that verifier scores move in the right direction between
no-answer, wrong-answer, and curated-answer inputs.

Exit codes: 0 clean, 1 any FALSE POSITIVE (or any warning with --strict),
2 usage/infrastructure error.

Usage:
    python3 scripts/validation/verifier_mutation_test.py benchmarks/
    python3 scripts/validation/verifier_mutation_test.py benchmarks/customer_escalation/err-provenance-01
    python3 scripts/validation/verifier_mutation_test.py benchmarks/ --json --strict
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.errors import VerifierInfraError  # noqa: E402
from eb_verify.runner import CheckpointRunner  # noqa: E402
from eb_verify.task_parser import TaskDefinition, parse_task  # noqa: E402

logger = logging.getLogger("verifier_mutation_test")

# Deterministic filler with no code symbols, file paths, or domain terms —
# any verifier that scores this above zero is matching on something other
# than answer content.
GARBAGE_TEXT = (
    "The committee reconvened on a rainy afternoon to discuss the seasonal "
    "catering rota. Several members preferred elderflower cordial, while "
    "others argued at length about the merits of woollen upholstery. No "
    "conclusions were reached and the minutes were filed under miscellaneous "
    "correspondence for future generations to ignore.\n"
) * 4

# Mechanical extraction of workspace-relative output paths a verifier reads.
# Matches $WORKSPACE/foo/bar.md and ${WORKSPACE:-/workspace}/foo/bar.md forms.
_WS_PATH_RES = (
    re.compile(r"\$\{WORKSPACE(?::-[^}]*)?\}(/[\w@%+=:,.\-/]+)"),
    re.compile(r"\$WORKSPACE(/[\w@%+=:,.\-/]+)"),
)


@dataclass
class Finding:
    task_id: str
    checkpoint: str
    variant: str  # empty | garbage | gold
    severity: str  # error | warning
    kind: str  # false_positive | garbage_credit | gold_zero
    score: float
    detail: str


@dataclass
class TaskReport:
    task_id: str
    task_dir: str
    checkpoints: int = 0
    gold_assessed: int = 0
    findings: list[Finding] = field(default_factory=list)
    skipped_reason: str | None = None


def extract_output_paths(script_path: Path) -> set[str]:
    """Return workspace-relative file paths referenced by a verifier script."""
    try:
        text = script_path.read_text(errors="replace")
    except OSError:
        return set()
    paths: set[str] = set()
    for pattern in _WS_PATH_RES:
        for match in pattern.finditer(text):
            rel = match.group(1).lstrip("/")
            # Skip directory-ish references and the verifier/runner internals
            if not rel or rel.startswith((".verifiers", ".task", ".eb_verify")):
                continue
            paths.add(rel)
    return paths


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _gt_paths(gt: dict, key: str) -> list[str]:
    """Pull paths out of ``gt[key]``, accepting either ``[{"path": ...}]`` or
    ``["a", "b"]`` shapes (both occur in ground_truth.json across the suite).
    Mirrors ``eb_verify.plugins.file_extraction._extract_gt_files``."""
    raw = gt.get(key, [])
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            p = item.get("path", "")
        elif isinstance(item, str):
            p = item
        else:
            p = ""
        if p:
            out.append(p)
    return out


def build_gold_answer(task_dir: Path) -> tuple[dict, str]:
    """Build the most favourable mechanical rendering of the curated answer.

    Returns (answer_obj, answer_text). The object follows the documented
    answer.json conventions (source_files / code_paths from ground truth,
    one list field per curated checkpoint named after the checkpoint); the
    text is the same data flattened for markdown report files. A verifier
    that scores 0 against this cannot recognise its own gold data in the
    canonical output shape.
    """
    expected = _load_json(task_dir / "expected_solution.json")
    gt = _load_json(task_dir / "ground_truth.json")
    # Some ground_truth.json files nest the payload under a "ground_truth" key
    # (the verifiers accept both shapes; so does this harness).
    if isinstance(gt.get("ground_truth"), dict):
        gt = gt["ground_truth"]

    required = _gt_paths(gt, "required_files")
    sufficient = _gt_paths(gt, "sufficient_files")

    answer: dict = {}
    text_parts: list[str] = []
    if required:
        answer["source_files"] = [{"path": p} for p in required]
        text_parts.append("\n".join(f"- {p}" for p in required))
    if required or sufficient:
        answer["code_paths"] = [{"path": p} for p in required + sufficient]

    for name, cp in (expected.get("checkpoints") or {}).items():
        sol = cp.get("expected_solution", "")
        if not sol:
            continue
        lines = [ln.strip() for ln in sol.splitlines() if ln.strip()]
        # Never clobber the canonical structured fields (source_files /
        # code_paths) with prose — verifiers match those against ground-truth
        # paths, and a checkpoint may share their name.
        if name not in answer:
            answer[name] = lines or [sol]
        text_parts.append(f"## {name}\n{sol}")

    return answer, "\n\n".join(text_parts)


def gold_checkpoint_names(task_dir: Path) -> set[str]:
    """Checkpoint names that have a curated expected_solution entry."""
    expected_path = task_dir / "expected_solution.json"
    if not expected_path.exists():
        return set()
    try:
        expected = json.loads(expected_path.read_text())
    except json.JSONDecodeError:
        return set()
    return set((expected.get("checkpoints") or {}).keys())


def _write_outputs(
    workspace: Path,
    rel_paths: set[str],
    text: str,
    answer_obj: dict | None = None,
) -> None:
    """Materialise the synthetic answer at every output path the verifiers read."""
    answer = workspace / "agent_output" / "answer.json"
    answer.parent.mkdir(parents=True, exist_ok=True)
    payload = answer_obj if answer_obj is not None else {"answer": text}
    answer.write_text(json.dumps(payload, indent=2))
    # Deepest paths first so a shallower reference that is really a directory
    # (e.g. "$WORKSPACE/flask" used with cd) collides as a dir and is skipped,
    # not created as a file that blocks its children.
    for rel in sorted(rel_paths, key=lambda p: p.count("/"), reverse=True):
        target = workspace / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                continue
            if rel.endswith(".json"):
                target.write_text(json.dumps(payload, indent=2))
            else:
                target.write_text(text)
        except OSError as exc:
            # Directory-style references extracted as paths are expected;
            # they simply cannot hold answer content.
            logger.debug("Skipping unmaterialisable path %s: %s", rel, exc)


def run_variant(
    task: TaskDefinition,
    task_dir: Path,
    rel_paths: set[str],
    text: str | None,
    answer_obj: dict | None = None,
) -> dict[str, float]:
    """Run all checkpoints in a synthetic workspace; return {name: score}.

    *text* is the content to materialise (None = empty workspace);
    *answer_obj* optionally overrides the answer.json payload.
    """
    # Deterministic tier only — the LLM judge can only lower scores, and
    # mutation testing targets the grep layer's own discrimination.
    det_task = dataclasses.replace(task, verification_modes=["deterministic"])
    scores: dict[str, float] = {}
    with tempfile.TemporaryDirectory(prefix="eb-mutate-") as tmp:
        workspace = Path(tmp)
        (workspace / "agent_output").mkdir(parents=True, exist_ok=True)
        # Empty repo dirs so directory-style $WORKSPACE/<repo> references
        # (cd, test -d) behave as directories, not missing files.
        for repo in det_task.repos:
            if repo.path:
                (workspace / repo.path).mkdir(parents=True, exist_ok=True)
        if text is not None:
            _write_outputs(workspace, rel_paths, text, answer_obj)
        runner = CheckpointRunner(det_task, task_dir=task_dir, workspace=workspace)
        for cp in det_task.checkpoints:
            try:
                scores[cp.name] = runner.run_checkpoint(cp).score
            except VerifierInfraError:
                # A verifier that cannot run in the synthetic sweep workspace
                # yields no trustworthy score; record 0.0 (conservative — never
                # a false positive) rather than aborting the whole sweep.
                scores[cp.name] = 0.0
    return scores


def mutate_task(task_toml: Path) -> TaskReport:
    """Run the empty/garbage/gold sweep for one task."""
    task_dir = task_toml.parent
    report = TaskReport(task_id=task_dir.name, task_dir=str(task_dir))

    try:
        task = parse_task(task_toml)
    except Exception as exc:
        report.skipped_reason = f"task.toml parse failed: {exc}"
        return report
    report.task_id = task.id or task_dir.name

    if not task.checkpoints:
        report.skipped_reason = "no checkpoints defined"
        return report
    report.checkpoints = len(task.checkpoints)

    # Per-checkpoint extracted output paths; union is materialised per variant.
    cp_paths: dict[str, set[str]] = {}
    for cp in task.checkpoints:
        script = (task_dir / cp.verifier).resolve()
        cp_paths[cp.name] = extract_output_paths(script) if script.exists() else set()
    all_paths = set().union(*cp_paths.values()) if cp_paths else set()

    empty_scores = run_variant(task, task_dir, all_paths, None)
    garbage_scores = run_variant(task, task_dir, all_paths, GARBAGE_TEXT)

    gold_names = gold_checkpoint_names(task_dir)
    gold_scores: dict[str, float] = {}
    gold_text = ""
    if gold_names:
        gold_answer, gold_text = build_gold_answer(task_dir)
        if gold_text or gold_answer:
            gold_scores = run_variant(
                task, task_dir, all_paths, gold_text, answer_obj=gold_answer
            )

    for cp in task.checkpoints:
        name = cp.name
        if empty_scores.get(name, 0.0) > 0.0:
            report.findings.append(
                Finding(
                    task_id=report.task_id,
                    checkpoint=name,
                    variant="empty",
                    severity="error",
                    kind="false_positive",
                    score=empty_scores[name],
                    detail="scores > 0 with no agent output at all",
                )
            )
        if garbage_scores.get(name, 0.0) > 0.0:
            report.findings.append(
                Finding(
                    task_id=report.task_id,
                    checkpoint=name,
                    variant="garbage",
                    severity="warning",
                    kind="garbage_credit",
                    score=garbage_scores[name],
                    detail="awards credit for irrelevant filler text",
                )
            )
        # Gold false-negative check is only meaningful when the checkpoint
        # has a curated expectation AND its verifier reads at least one
        # workspace output file the harness can materialise (answer.json
        # counts via the agent_output convention).
        reads_outputs = bool(cp_paths[name]) or "agent_output" in (
            (task_dir / cp.verifier).read_text(errors="replace")
            if (task_dir / cp.verifier).exists()
            else ""
        )
        if gold_text and name in gold_names and reads_outputs:
            report.gold_assessed += 1
            if gold_scores.get(name, 0.0) == 0.0:
                report.findings.append(
                    Finding(
                        task_id=report.task_id,
                        checkpoint=name,
                        variant="gold",
                        severity="warning",
                        kind="gold_zero",
                        score=0.0,
                        detail=(
                            "scores 0 against the curated expected_solution "
                            "text (false-negative risk)"
                        ),
                    )
                )
    return report


def discover_task_tomls(paths: list[Path]) -> list[Path]:
    tomls: list[Path] = []
    for p in paths:
        if p.is_file() and p.name == "task.toml":
            tomls.append(p)
        elif p.is_dir():
            if (p / "task.toml").exists():
                tomls.append(p / "task.toml")
            else:
                tomls.extend(sorted(p.rglob("task.toml")))
    # Archived tasks are out of scope
    return [t for t in tomls if "_archived" not in t.parts]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mutation-test checkpoint verifiers (FP/FN sweep)."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[REPO_ROOT / "benchmarks"],
        help="Task dirs, task.toml files, or trees to scan (default: benchmarks/)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Max number of tasks to test"
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON report")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero on warnings (garbage_credit / gold_zero) too",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    # Verifier scripts that shell out to python need eb_verify importable,
    # mirroring the container's PYTHONPATH=/workspace/.eb_verify setup.
    lib_path = str(REPO_ROOT / "lib")
    existing = os.environ.get("PYTHONPATH", "")
    if lib_path not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            f"{lib_path}{os.pathsep}{existing}" if existing else lib_path
        )

    tomls = discover_task_tomls([p.resolve() for p in args.paths])
    if not tomls:
        logger.error("No task.toml files found under %s", args.paths)
        return 2
    if args.limit:
        tomls = tomls[: args.limit]

    reports = [mutate_task(t) for t in tomls]

    errors = [f for r in reports for f in r.findings if f.severity == "error"]
    warnings = [f for r in reports for f in r.findings if f.severity == "warning"]
    skipped = [r for r in reports if r.skipped_reason]

    if args.json:
        payload = {
            "tasks_tested": len(reports) - len(skipped),
            "tasks_skipped": len(skipped),
            "false_positives": len(errors),
            "warnings": len(warnings),
            "reports": [
                {
                    "task_id": r.task_id,
                    "task_dir": r.task_dir,
                    "checkpoints": r.checkpoints,
                    "gold_assessed": r.gold_assessed,
                    "skipped_reason": r.skipped_reason,
                    "findings": [dataclasses.asdict(f) for f in r.findings],
                }
                for r in reports
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        for r in reports:
            if r.skipped_reason:
                print(f"SKIP  {r.task_id}: {r.skipped_reason}")
                continue
            for f in r.findings:
                tag = "FP   " if f.severity == "error" else "WARN "
                print(
                    f"{tag} {f.task_id} :: {f.checkpoint} [{f.variant}] "
                    f"score={f.score:.2f} — {f.detail}"
                )
        print(
            f"\n{len(reports) - len(skipped)} task(s) tested, "
            f"{len(skipped)} skipped — "
            f"{len(errors)} false positive(s), {len(warnings)} warning(s)"
        )

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
