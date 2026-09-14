# Audit: docker-cp silent-zero blast radius beyond the rescore_aq8e/uu17 campaign scope

Bead: `EnterpriseBench-s4a2` · Extends: `results/audit_docker_cp_silent_zeros/AUDIT.md`
(branch `audit/hktt-docker-cp-silent-zeros` @ `78cfcd2`, unmerged) · Fix commit: `16280cf`

## Why this exists

hktt's original audit scoped its `topo_order` sweep to `results/rescore_aq8e/`
and `results/rescore_uu17/` (12 result files, 6 tasks x 1 arm each) and found
5 confirmed docker-cp silent zeros. Those two directories are re-adjudications
of a fixed 105-task set and were never the primary data source — they are
downstream of `results/runs/`, the directory `results/analysis/aggregate_mcp_clean.py`
reads directly to build the "locked N=105" headline. hktt's audit never
scanned `results/runs/` itself, nor the separate `EnterpriseBench-9awn`
baseline re-run tree that feeds the accepted `uu17_mixed` headline.

This audit reapplies hktt's exact fingerprint outside those two directories.

## Fingerprint (unchanged from hktt's audit)

A `topo_order` checkpoint is a docker-cp silent zero iff `score == 0.0` **and**
`exit_code == 1` (the check imported `eb_verify` through the broken
`PYTHONPATH=/workspace/.eb_verify` layout and hit `ModuleNotFoundError`).
`exit_code == 0` with `score == 0.0` is a genuine agent miss (guard fired
before the import — no `REFACTOR_PLAN.md` or no numbered ordering).

## Method

Scanned every `results.json` under `results/` (main checkout) and
`/home/ds/projects/EnterpriseBench-9awn/results/rerun_9awn/` containing a
`topo_order` checkpoint (62 checkpoint instances across 5 locations),
classified each by the fingerprint above. Full raw output in
`broader_scope_findings_raw.json`; curated summary in
`broader_scope_findings.json`.

## Headline result

**27 total docker-cp silent-zero `topo_order` instances** across all scanned
locations — 5.4x hktt's original count of 5:

| location | bug count | consumer |
|---|---:|---|
| `results/rescore_aq8e/` + `results/rescore_uu17/` | 5 | unchanged from hktt's audit |
| `results/runs/` (live, mode-subdir form) | 16 | `aggregate_mcp_clean.py` → `recompute_headline_aq8e.py`'s `old_locked` |
| `results/runs/` (dead flat legacy file, but live for a different consumer) | 1 | `scripts/analyze_scores.py` → `results/score_analysis.json` (paper headline numbers) |
| `EnterpriseBench-9awn/results/rerun_9awn/` (baseline arm) | 5 | `recompute_headline_aq8e.py`'s `uu17_mixed` (the accepted/closed recompute) |

`results/mcp_lift_study/` and `results/rerun_pt0n/` were also scanned: both
clean of the bug (the latter because it is the post-fix rerun — see below).

## Answer to (1): does `results/runs/` and the full topo_order-checkpoint set have further exit_code=1 instances hktt's audit missed?

**Yes, extensively.** `refactor-orch-006/mcp_only` (the spot-check that
motivated this bead) is confirmed affected. But it is one of **16** live
docker-cp silent zeros in `results/runs/`, spanning **8 of the 11**
refactor-orchestration-family tasks:

```
refactor-orch-001  mcp_only
refactor-orch-002  hybrid
refactor-orch-003  hybrid
refactor-orch-004  baseline
refactor-orch-006  mcp_only
refactor-orch-007  baseline, hybrid, mcp_only   (all 3 modes)
refactor-orch-008  hybrid, mcp_only
refactor-orchestration-tri-babel-001   baseline, hybrid, mcp_only   (all 3 modes)
refactor-orchestration-tri-tokio-001   baseline, hybrid, mcp_only   (all 3 modes)
```

Only `refactor-orch-005` is fully clean (all 3 modes genuine guard zeros — the
agent never produced a valid ordering, in any mode). `refactor-orchestration-tri-spring-001`
has a *different*, unrelated exit_code=127 failure in hybrid/mcp_only (command-not-found,
not `ModuleNotFoundError`) — not this bug, flagged separately for its own
investigation.

`aggregate_mcp_clean.py`'s own existing `masked` heuristic (uniform-zero
checkpoint detector) already flags `topo_order` as suspect for **all 11**
refactor-orchestration tasks, because the checkpoint scores exactly 0.0 in
every mode for every task in the live data — it just cannot distinguish the
16 infra-bug zeros from the other 17 genuine-miss zeros. This audit provides
that missing exit_code-level disambiguation.

### A second, independent defect this scan surfaced

`refactor-orch-003` has two result files in `results/runs/`: a March flat
`results.json` (pre-mode-split convention, docker-cp affected, task_score
0.6) and a June `baseline/results.json` (post-mode-split rerun, genuine guard
zero, task_score 0.5). `aggregate_mcp_clean.py` only reads the mode-subdir
form, so it never sees the stale March file. But `scripts/analyze_scores.py`
(the pipeline `paper.md` cites for headline numbers) walks `results/runs/`
with `rglob("results.json")` and dedups same-`(task_id, mode)` collisions by
**keeping the higher `normalized_score`**, not the newer file. Verified
directly against the live artifact: `results/score_analysis.json`
`per_task[92]` (refactor-orch-003) currently reports `baseline: 0.6` — the
stale, docker-cp-contaminated March value, silently overriding the corrected
June rerun. This is a distinct bug in `analyze_scores.py`'s dedup rule
(should prefer the newer file, or refuse to guess and require unique
`(task_id, mode)` keys) — filed as a follow-up, not fixed here.

## `EnterpriseBench-9awn/results/rerun_9awn/` — wider than described

The bead described only `refactor-orch-{001,007}` as affected. Direct rescan
of the 6 present baseline-arm result files finds **5 affected**
(001, 002, 003, 004, 007) and only 1 clean (008). This baseline arm is read
by `recompute_headline_aq8e.py` and trusted unconditionally whenever
`phase == "complete"` and `success is True` — it does **not** check
checkpoint-level `exit_code`, so all 5 buggy task_scores currently flow,
uncorrected, into the `uu17_mixed` headline: the recompute that was already
**accepted and closed** as the parity verdict (`docs/qa/uu17_mcp_rescore_path_verdict.md`).

## `results/rerun_pt0n/` — validates the fix, but isn't wired to anything yet

pt0n's rerun currently covers 3 cells (refactor-orch-001/004/007, all
baseline mode), all now `genuine_guard_zero` (exit_code=0) — confirming the
2026-07-04 fix resolves the `ModuleNotFoundError`. Three gaps:

1. It doesn't cover hktt's own original 5 (aq8e's 004/007 *baseline* are
   covered, but uu17's 001/007/008 *mcp_only* are not touched at all).
2. It covers none of the 16 newly-found `results/runs/` cells or the 5
   newly-found `rerun_9awn` cells.
3. **No script currently reads `results/rerun_pt0n/`** — grepped for it
   across every `.py` file in the repo, zero hits. `recompute_headline_aq8e.py`
   hardcodes `RERUN9` to `EnterpriseBench-9awn/results/rerun_9awn` and its
   median files to `results/rescore_aq8e/`; it has no path into pt0n's
   output at all. So today pt0n is purely a **validation exercise** (proof
   the fix works) — even its 3 correct reruns are not propagating into any
   headline number, and won't until something wires them in.

Separately: post-fix, `topo_order` is *still* 0.0 for all 3 rerun cells —
the fix stops the infra crash but doesn't retroactively recover a score;
each cell still needs the agent's `REFACTOR_PLAN.md` to actually exist and
be valid for `topo_order` to score above 0.

## Answer to (2): does `results/runs/` warrant its own correction pass, or stay frozen?

**It needs a correction pass — it is not an inert historical anchor.**
"Locked" in this codebase means a frozen *task-membership list* for
apples-to-apples comparison (`results/analysis/blast_radius_verifier_contamination_2026_06.md:68`),
not a claim that the underlying `results/runs/` files are archival and
therefore safe to leave wrong. Concretely, `results/runs/` is read **live**
by two separate pipelines every time they are invoked:

- `aggregate_mcp_clean.py` → `recompute_headline_aq8e.py`'s `old_locked`
  (verified by literally re-running it: current output is
  `delta_mean=-0.0925, delta_median=0.0, n_clean=105`).
- `scripts/analyze_scores.py` → `results/score_analysis.json`, which
  `paper.md` cites directly for published headline numbers.

Both currently incorporate the 16 (+1 via the analyze_scores.py dedup path)
docker-cp-contaminated `topo_order` scores in `results/runs/`, exactly as
hktt's audit already established for the two rescore directories — just at
3x the footprint, and reaching all the way to the paper-facing artifact.

**Recommendation, matching established precedent (aq8e/uu17/9awn all
corrected via new sibling directories, never by editing `results/runs/` in
place):**

1. Do **not** edit `results/runs/` files in place — keep it as the immutable
   raw-run record.
2. pt0n's 3 baseline-mode reruns (001/004/007) do not by themselves close
   any of the gaps below — none of these 21 distinct `(task, mode)` cells
   are covered by pt0n's current output, and pt0n isn't wired to any
   consumer yet regardless:
   - `results/runs/` (feeds `old_locked` live): refactor-orch-001/mcp_only,
     -002/hybrid, -003/hybrid, -004/baseline, -006/mcp_only,
     -007/{baseline,hybrid,mcp_only}, -008/{hybrid,mcp_only},
     tri-babel-001/{baseline,hybrid,mcp_only},
     tri-tokio-001/{baseline,hybrid,mcp_only} — 16 cells.
   - `rerun_9awn/` (feeds `uu17_mixed` live): refactor-orch-{001,002,003,004,007}/baseline
     — 5 cells.
   pt0n's own 3 cells (001/004/007 baseline) overlap task-wise with 3 of the
   5 `rerun_9awn` cells but live in a separate, unwired directory — they
   would need to be re-run *again* in whichever campaign actually gets
   wired into the headline scripts, or that wiring needs to point at pt0n's
   existing output instead of duplicating the work.
3. Land the full 21-cell rerun in a new sibling directory (extending
   `results/rerun_pt0n/` or a fresh dir), then layer the correction into
   `recompute_headline_aq8e.py` and `aggregate_mcp_clean.py` the same way
   `recompute_headline_aq8e.py` already overlays the aq8e/uu17/9awn
   corrections onto the baseline/mcp arms — add a `topo_order`-specific
   override keyed by `(task, mode)` rather than trusting the raw
   `results/runs/` checkpoint score wherever a rerun exists.
4. File the `analyze_scores.py` dedup-rule defect (keeps highest score
   instead of newest file) as its own bead — it's an independent bug that
   happens to compound with this one for `refactor-orch-003`, and could
   affect any other task that has both a legacy flat and a mode-subdir
   result file.

## Artifacts

- `broader_scope_findings.json` — curated classification of every affected
  cell, grouped by location and consumer.
- `broader_scope_findings_raw.json` — full machine-readable dump of all 62
  scanned `topo_order` checkpoint instances (file, task, mode, score,
  exit_code, classification) across every location this audit touched.
