# Ablation + LLM Judge Validation — Findings

**Bead:** EnterpriseBench-0rv.19
**Date:** 2026-04-28
**Tasks:** incident-inv-docker-shutdown-004, support-map-grafana-alerts-004
**Mode:** baseline (no MCP)
**Reps:** 3 per ablation variant

## Summary

12 ablation runs scored with both grep (Tier 1) and LLM judge (Tier 2). Final
score per checkpoint = min(grep_score, judge_score). Results below test the
PRD threshold: **removed-repo checkpoints should score <= 0.30**.

| task | variant | n | mean min(grep,judge) | max | <=0.30? |
| --- | --- | ---: | ---: | ---: | --- |
| incident-inv-004 | ablate-moby | 12 | 0.33 | 0.50 | borderline |
| incident-inv-004 | ablate-containerd | 12 | 0.88 | 1.00 | no — invalid CRNT |
| support-map-004 | ablate-grafana | 12 | 0.55 | 1.00 | no |
| support-map-004 | ablate-alertmanager | 12 | 0.48 | 1.00 | no |

(Rows = 4 checkpoints x 3 reps = 12 per variant; means above are over all rows.)

## Per-checkpoint results

| task | variant | checkpoint | mean | max |
| --- | --- | --- | ---: | ---: |
| incident-inv-004 | ablate-moby | affected_services | 0.33 | 0.50 |
| incident-inv-004 | ablate-moby | error_chain_trace | 0.33 | 0.50 |
| incident-inv-004 | ablate-moby | remediation_proposal | 0.33 | 0.50 |
| incident-inv-004 | ablate-moby | root_cause_identification | 0.33 | 0.50 |
| incident-inv-004 | ablate-containerd | affected_services | 1.00 | 1.00 |
| incident-inv-004 | ablate-containerd | error_chain_trace | 1.00 | 1.00 |
| incident-inv-004 | ablate-containerd | remediation_proposal | 0.50 | 0.50 |
| incident-inv-004 | ablate-containerd | root_cause_identification | 1.00 | 1.00 |
| support-map-004 | ablate-grafana | code_paths | 0.50 | 0.50 |
| support-map-004 | ablate-grafana | ownership | 0.58 | 1.00 |
| support-map-004 | ablate-grafana | related_issues | 0.11 | 0.33 |
| support-map-004 | ablate-grafana | severity | 1.00 | 1.00 |
| support-map-004 | ablate-alertmanager | code_paths | 0.31 | 0.35 |
| support-map-004 | ablate-alertmanager | ownership | 0.50 | 0.50 |
| support-map-004 | ablate-alertmanager | related_issues | 0.28 | 0.50 |
| support-map-004 | ablate-alertmanager | severity | 0.83 | 1.00 |

## Findings

### F1. ablate-containerd ablation is invalid due to vendored dependencies

Removing the upstream `containerd` repo does not deny the agent access to
containerd source code. The moby repo vendors the entire containerd source
under `vendor/github.com/containerd/containerd/...`. In rep3 ablate-containerd,
the agent navigated to `vendor/.../core/unpack/unpacker.go` and reproduced the
exact bug analysis verbatim. Mean min(grep,judge) = 0.88.

**Implication:** CRNT-by-removal is an invalid test for tasks where the
"removed" repo is vendored into the kept repo. For Go/Rust tasks especially,
ablation must remove the vendored copy too, or the task must rely on a
non-vendored boundary.

### F2. ablate-moby is at the borderline of the 30% threshold

Mean 0.33 across all checkpoints; max 0.50 in some reps. The agent (with only
containerd source) can produce partial answers — the judge gives 0.5 partial
credit when the answer references containerd internals correctly but lacks the
moby-side enabling code. Grep typically gives 0.0 because moby file paths are
absent. **Strong directional CRNT signal but does not meet the strict <=0.30
threshold across all reps.**

### F3. severity checkpoints provide no CRNT signal

`severity` scores 0.83-1.00 across all variants (both ablate-grafana and
ablate-alertmanager). Severity assessment is a high-level judgment that doesn't
require code-specific knowledge. **Recommendation:** drop or reweight severity
checkpoints for support-mapping tasks; they don't measure code navigation.

### F4. related_issues for ablate-grafana shows the cleanest CRNT signal

Mean 0.11, max 0.33 — passes the threshold across most reps. When grafana is
removed, the agent cannot identify grafana-side related issues, and the judge
correctly penalizes generic answers. This checkpoint design (exact-path match
on grafana files) is the right shape for CRNT validation.

### F5. LLM judge sometimes gives 0.5 when grep gives 1.0

In several support-map runs, judge scored 0.5 while grep scored 0.67-1.0. The
judge applies a stricter "must reference code-specific details from the
removed repo" criterion and downgrades answers that pattern-match correctly
but rely on generic domain knowledge. **min(grep, judge)** correctly takes
the lower score and acts as a useful ceiling on grep over-counting.

## Bug fixes made during this validation

1. **`_apply_llm_judge` name resolution** (`scripts/orchestration/run_task.py`)
   Verifier emits checkpoint names from `check_*.sh` filenames (short:
   `error_chain`), but `expected_solution.json` for incident-inv-004 uses
   canonical task.toml names (long: `error_chain_trace`). Without resolution,
   3 of 4 checkpoints silently skipped the judge. Fix: build a verifier-name
   to canonical-name map from `task.toml [[checkpoints]].verifier` paths.

2. **`run_pilot.py` ablation dispatch** (`scripts/orchestration/run_pilot.py`)
   Ablation entries dispatched through `run_crnt_ablation.sh` which rebuilt
   the image per rep and ignored manifest `account_id`. Fix: route ablate-*
   entries through `run_task.py --ablation-variant --no-build`, after
   pre-building images via `run_crnt_ablation.sh --reps 0`.

3. **incident-inv-004 Dockerfile** (`benchmarks/.../environment/Dockerfile`)
   Used non-existent `sg-evals/containerd--v1.7.24` mirror. Fix: clone upstream
   `containerd/containerd` at tag `v1.7.24`. (Note: Dockerfile is gitignored
   and regenerated by `dockerfile_generator.py` with `--source upstream`,
   which already does the right thing — fix only matters for stale local
   copies.)

## Acceptance criteria status

- [x] All 12 ablation runs scored with Tier 2 LLM judge (6 fresh + 6 rescored)
- [x] Results saved to `results/phase1_pilot/` with judge_score + grep_score
      per checkpoint
- [x] Summary CSV updated with min(grep, judge) composite scores
      (`ablation_judge_summary.csv`)
- [ ] **NOT MET:** removed-repo checkpoint scores <= 0.30 across all
      CRNT-passing tasks. ablate-moby at 0.33 (borderline); ablate-containerd
      invalid due to vendoring; support-map variants mixed (0.11-1.00).

## Recommended follow-ups

1. Redesign incident-inv-004 to avoid vendored containerd path (or add explicit
   vendor/ ablation to `run_crnt_ablation.sh`).
2. Drop `severity` checkpoint from support-mapping tasks or anchor it to
   code-specific severity drivers.
3. Tighten checkpoint design for ablate-moby — current judge gives 0.5 partial
   credit on generic containerd-only answers, pushing mean above 0.30.
4. Add a CRNT pre-check that flags any task whose primary repo vendors the
   dependency repo; mark such tasks ineligible for repo-removal ablation.
