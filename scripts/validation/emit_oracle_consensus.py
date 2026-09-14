"""Emit ``oracle_backends_consensus`` provenance into EnterpriseBench ground truth.

Bead codeprobe-zf3k cross-rig provenance shim. Walks ``benchmarks/`` and
adds a top-level ``oracle_backends_consensus`` list to each
``ground_truth.json`` based on the existing per-file ``source`` field
(``deterministic`` | ``curator`` | ``both``). The list is the canonical
shape codeprobe ``core/bias_detection.py`` reads; populating it lets
codeprobe ingest EnterpriseBench artifacts uniformly with codeprobe-mined
and CSB-mined tasks.

See ``docs/oracle_curator_decision.md`` for the architectural decision
behind this shim, and codeprobe ``docs/oracle_curator.md`` for the field's
upstream contract.

The script is idempotent. Tasks without typed per-file sources (raw
breaking-change extracts that don't carry a ``source`` field) are skipped
with a logged reason rather than guessed at — codeprobe falls back to
other signals when the field is missing, so an honest skip is safer than
a wrong default.

Usage::

    python3 scripts/validation/emit_oracle_consensus.py benchmarks/
    python3 scripts/validation/emit_oracle_consensus.py benchmarks/ --dry-run
    python3 scripts/validation/emit_oracle_consensus.py benchmarks/ --json

Exit codes:
- 0: ran cleanly
- 1: at least one task could not be processed
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


EXCLUDED_DIR_NAMES = frozenset({"_archived", "mined"})

# Per-file ``source`` value → backend tokens that contributed it. ``both``
# means the file was confirmed by both pipelines, so its consensus is the
# union of the two.
_SOURCE_TO_BACKENDS = {
    "deterministic": ("deterministic",),
    "curator": ("curator",),
    "both": ("curator", "deterministic"),
}


@dataclass(frozen=True)
class TaskOutcome:
    """Result of processing one ``ground_truth.json``."""

    task_dir: Path
    action: str  # "updated" | "unchanged" | "skipped" | "error"
    consensus: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "task_dir": str(self.task_dir),
            "action": self.action,
            "consensus": list(self.consensus),
            "reason": self.reason,
        }


@dataclass
class RunReport:
    outcomes: list[TaskOutcome] = field(default_factory=list)

    def add(self, outcome: TaskOutcome) -> None:
        self.outcomes.append(outcome)

    def by_action(self, action: str) -> list[TaskOutcome]:
        return [o for o in self.outcomes if o.action == action]

    def has_errors(self) -> bool:
        return any(o.action == "error" for o in self.outcomes)


def iter_task_dirs(root: Path) -> Iterator[Path]:
    """Yield directories containing a ``ground_truth.json`` under *root*.

    Skips ``_archived`` and ``mined`` subtrees so retired or candidate
    tasks are not mutated.
    """
    for gt_path in root.rglob("ground_truth.json"):
        if any(part in EXCLUDED_DIR_NAMES for part in gt_path.parts):
            continue
        yield gt_path.parent


def consensus_from_sources(sources: list[str]) -> tuple[str, ...]:
    """Return sorted ``oracle_backends_consensus`` value for a list of sources.

    Accepts the per-file ``source`` strings as they appear in
    ``ground_truth.json`` (``"deterministic"`` | ``"curator"`` |
    ``"both"``) and returns a sorted tuple of canonical backend tokens.
    Unknown values are ignored — the field is structural, not a place to
    smuggle freeform labels.
    """
    seen: set[str] = set()
    for src in sources:
        for token in _SOURCE_TO_BACKENDS.get(src, ()):
            seen.add(token)
    return tuple(sorted(seen))


def _collect_sources(gt: dict[str, object]) -> list[str]:
    """Return all ``source`` values found on per-file entries in *gt*."""
    out: list[str] = []
    for key in ("required_files", "sufficient_files"):
        items = gt.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                src = item.get("source")
                if isinstance(src, str) and src.strip():
                    out.append(src.strip().lower())
    nested = gt.get("ground_truth")
    if isinstance(nested, dict):
        for key in ("required_files", "sufficient_files"):
            items = nested.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    src = item.get("source")
                    if isinstance(src, str) and src.strip():
                        out.append(src.strip().lower())
    return out


def process_task(task_dir: Path, *, dry_run: bool = False) -> TaskOutcome:
    """Process one task's ``ground_truth.json``.

    Reads existing content, derives the consensus list from per-file
    ``source`` values, and writes the merged result back unless dry-run
    is set. Returns a :class:`TaskOutcome` describing the action taken.
    """
    gt_path = task_dir / "ground_truth.json"
    if not gt_path.is_file():
        return TaskOutcome(task_dir, "error", reason="ground_truth.json missing")

    try:
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return TaskOutcome(
            task_dir, "error", reason=f"unreadable ground_truth.json: {exc}"
        )
    if not isinstance(gt, dict):
        return TaskOutcome(
            task_dir, "error", reason="ground_truth.json is not a JSON object"
        )

    sources = _collect_sources(gt)
    if not sources:
        return TaskOutcome(
            task_dir,
            "skipped",
            reason="no per-file 'source' field — cannot derive consensus",
        )

    consensus = consensus_from_sources(sources)
    if not consensus:
        return TaskOutcome(
            task_dir,
            "skipped",
            reason="all 'source' values were unrecognised",
        )

    existing = gt.get("oracle_backends_consensus")
    if (
        isinstance(existing, list)
        and tuple(str(b) for b in existing) == consensus
    ):
        return TaskOutcome(task_dir, "unchanged", consensus=consensus)

    gt["oracle_backends_consensus"] = list(consensus)
    if not dry_run:
        gt_path.write_text(
            json.dumps(gt, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return TaskOutcome(task_dir, "updated", consensus=consensus)


def run(root: Path, *, dry_run: bool = False) -> RunReport:
    report = RunReport()
    for task_dir in sorted(iter_task_dirs(root)):
        report.add(process_task(task_dir, dry_run=dry_run))
    return report


def _format_text_report(report: RunReport, *, dry_run: bool) -> str:
    lines: list[str] = []
    updated = report.by_action("updated")
    unchanged = report.by_action("unchanged")
    skipped = report.by_action("skipped")
    errors = report.by_action("error")

    if updated:
        prefix = "WOULD UPDATE" if dry_run else "UPDATED"
        lines.append(f"\n{prefix} ({len(updated)}):")
        for o in updated[:20]:
            lines.append(f"  {o.task_dir} -> {list(o.consensus)}")
        if len(updated) > 20:
            lines.append(f"  ... and {len(updated) - 20} more")

    if unchanged:
        lines.append(f"\nUNCHANGED ({len(unchanged)}):")
        # Don't list every unchanged task — too noisy on repeated runs.
        lines.append(f"  (already at desired value)")

    if skipped:
        by_reason: dict[str, list[TaskOutcome]] = {}
        for o in skipped:
            by_reason.setdefault(o.reason, []).append(o)
        lines.append(f"\nSKIPPED ({len(skipped)}):")
        for reason, items in by_reason.items():
            lines.append(f"  reason: {reason} ({len(items)})")
            for o in items[:5]:
                lines.append(f"    {o.task_dir}")
            if len(items) > 5:
                lines.append(f"    ... and {len(items) - 5} more")

    if errors:
        lines.append(f"\nERRORS ({len(errors)}):")
        for o in errors:
            lines.append(f"  {o.task_dir}: {o.reason}")

    summary = (
        f"\nSummary: updated={len(updated)} unchanged={len(unchanged)} "
        f"skipped={len(skipped)} errors={len(errors)} "
        f"total={len(report.outcomes)}"
    )
    lines.append(summary)
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    p.add_argument(
        "path",
        type=Path,
        help="Root directory to walk (e.g. benchmarks/).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files.",
    )
    p.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human report.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.path.exists():
        print(f"path does not exist: {args.path}", file=sys.stderr)
        return 1
    report = run(args.path, dry_run=args.dry_run)
    if args.emit_json:
        print(
            json.dumps(
                {
                    "dry_run": args.dry_run,
                    "outcomes": [o.to_dict() for o in report.outcomes],
                },
                indent=2,
            )
        )
    else:
        print(_format_text_report(report, dry_run=args.dry_run))
    return 1 if report.has_errors() else 0


if __name__ == "__main__":
    raise SystemExit(main())
