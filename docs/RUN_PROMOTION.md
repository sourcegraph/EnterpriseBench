# Run Promotion — Atomic Guarantee

EnterpriseBench promotes a raw benchmark run from
`results/runs/<run_id>/` into the durable, official-run state at
`results/official_runs/<run_id>/` through a single coordinator:
`scripts/orchestration/run_promotion_orchestrator.py`.

The promotion is **atomic** in the strict sense: when the orchestrator
returns `succeeded=True`, every promoted artefact is in place and the
registry is consistent; when it returns `succeeded=False`, every
already-completed step that *can* be reversed has been rolled back, and a
forensic snapshot is left under `results/official_runs/_failures/` for
investigation.

This document defines the guarantee, lists every step in the pipeline,
and identifies which are reversible vs. forward-only-with-resume.

## Why this exists

Historical context: in CodeScaleBench, the analogous pipeline was a
chain of independent scripts (`consolidate_staging.py → promote_run.py
→ generate_manifest.py → extract_metrics.py → export_results.py`).
A failure half-way left the official-runs tree in a partially-promoted
state with no rollback (CSB-BUG-011 in `docs/csb_bugs.md`). That defect
was inherited as a latent risk in EnterpriseBench because the promotion
artefacts (`results/analysis/score_analysis.json`, charts, reports,
registries) are produced by separate Make targets that share no
transactional context.

The orchestrator wraps the sequence in a stage → publish split: every
write goes to a sibling staging directory (`_staging/<run_id>/`) and
the final move is a single `os.rename` onto the same filesystem.

## Atomic-promotion guarantee

The orchestrator gives three guarantees for a single invocation:

1. **No partial publication.** The official-run directory
   `results/official_runs/<run_id>/` either does not exist at all
   (failure rolled back, registry untouched) or contains every artefact
   the pipeline produced (registry updated to match). There is no state
   in between.

2. **No silent corruption.** The registry
   (`results/official_runs/_registry.json`) is rewritten via
   tmp-file + rename. On failure of any later step, the registry is
   restored from the `.bak` snapshot taken immediately before the
   rewrite. A reader observing the registry mid-flight sees either the
   old or new contents — never a truncated file.

3. **Forensics on every failure.** Any failure writes a snapshot to
   `results/official_runs/_failures/<run_id>_<UTC_TIMESTAMP>/`
   containing `context.json`, `error.json`, and `completed_steps.json`.
   The snapshot is independent of the staging directory (which the
   rollback pass deletes) so root-cause investigation does not race
   the cleanup.

## Pipeline

The default pipeline has 9 steps. The `--resume-from-step N` flag uses
1-based indices that line up with this list.

| # | Step | Reversibility | Effect | Rollback action |
|---|------|---------------|--------|-----------------|
| 1 | `validate_inputs` | reversible (no-op) | Read-only — confirms the raw run dir exists and is non-empty. | None needed; nothing written. |
| 2 | `validate_tasks_preflight` | reversible (no-op) | Runs `scripts/validate_tasks_preflight.py --json` for the touched task suite. | None needed. |
| 3 | `validate_crnt` | reversible (no-op) | Runs `scripts/validation/crnt_validator.py --all` over multi-repo tasks. | None needed. |
| 4 | `validate_expected_solutions` | reversible (no-op) | Runs `scripts/validation/validate_expected_solutions.py benchmarks/`. | None needed. |
| 5 | `stage_metrics` | **reversible** | Runs `scripts/analyze_scores.py --output _staging/<run_id>/score_analysis.json`. | `shutil.rmtree(staging_dir)`. |
| 6 | `stage_charts` | **reversible** | Runs `scripts/generate_charts.py` writing PNGs into `_staging/<run_id>/charts/`. | Same: removes the entire staging dir. |
| 7 | `stage_report` | **reversible** | Runs `scripts/generate_report.py` writing `_staging/<run_id>/report.md`. | Same. |
| 8 | `atomic_publish` | **reversible** | Single `os.rename(_staging/<run_id>, official_runs/<run_id>)`. | Inverse rename `official_runs/<run_id> → _staging/<run_id>`. |
| 9 | `update_registry` | **reversible** | Tmp + rename rewrite of `results/official_runs/_registry.json`. | Restore from `<registry>.bak` snapshot. |

There are currently **no forward-only steps**. If a future step touches
an external system that cannot be reversed in software (e.g. publishing
to a remote artefact store), it must be appended *after* step 9 and
declared with `Step(..., reversible=False)`. In that case the
orchestrator will not attempt rollback on failure of a later step and
operators must use `--resume-from-step` to retry. The `Step` dataclass
already carries the `reversible` field for this purpose.

## CLI

```bash
# Full promotion, official state.
python3 scripts/orchestration/run_promotion_orchestrator.py \
    --run-id 20260429_001 --target-state official

# Read-only validation; never writes to staging or final.
python3 scripts/orchestration/run_promotion_orchestrator.py \
    --run-id 20260429_001 --validate-only

# Pick up after a transient failure at step 5 (stage_metrics).
python3 scripts/orchestration/run_promotion_orchestrator.py \
    --run-id 20260429_001 --resume-from-step 5

# Print the plan without writing anything.
python3 scripts/orchestration/run_promotion_orchestrator.py \
    --run-id 20260429_001 --dry-run

# Machine-readable report.
python3 scripts/orchestration/run_promotion_orchestrator.py \
    --run-id 20260429_001 --json
```

## Programmatic API

```python
from scripts.orchestration.run_promotion_orchestrator import (
    RunPromotionOrchestrator, build_context,
)

ctx = build_context(run_id="20260429_001", target_state="official")
report = RunPromotionOrchestrator(ctx).run()

if not report.succeeded:
    # report.forensics_path points to the failure snapshot.
    raise SystemExit(report.error)
```

## Failure-mode catalog

| Failure | Detected by | Operator action |
|---------|-------------|-----------------|
| Raw run dir missing | step 1 | Fix the run id and re-run. |
| Schema/CRNT/expected_solutions invalid | steps 2–4 | Fix the task definition; the official-runs tree is untouched. |
| `analyze_scores.py` crashes | step 5 | Fix the underlying analysis bug; staging dir auto-removed. Re-run from scratch. |
| Chart or report generation fails | steps 6–7 | Same as above. The previous official run (if any) is unaffected. |
| Atomic publish fails (e.g. final dir already exists) | step 8 | Resolve the collision (typically a previous successful promotion of the same run id), then re-run. |
| Registry rewrite fails | step 9 | The publish (step 8) is rolled back; the previous registry is restored from `.bak`. |
| Power loss between steps | — | Re-invoke with `--resume-from-step N` where N is the next step after the last entry in `_progress.json` inside the staging dir. |

## Design notes

* **No semantic decisions.** The orchestrator is pure mechanism. It
  shells out to existing validators and generators (`analyze_scores.py`,
  `generate_charts.py`, …) which already encapsulate the policy. Swapping
  any of those out — including replacing them with model calls — does
  not require changes to the orchestrator.
* **Same filesystem invariant.** The staging directory is a sibling of
  the final directory specifically so that `os.rename` is atomic on
  POSIX. Don't move either path onto a different mount.
* **Idempotent rollback.** Every rollback hook tolerates being called
  when its step never ran or was already rolled back. This keeps the
  orchestrator's LIFO rollback loop simple and safe.
* **Single entry per run id.** `update_registry` replaces any existing
  entry for `run_id` rather than appending a duplicate. Re-promoting
  the same run is therefore safe (after a manual cleanup of the existing
  final directory, which step 8 refuses to overwrite).
