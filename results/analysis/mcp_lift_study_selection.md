# MCP-Lift Study — Phase 1: Task Selection

**Bead:** EnterpriseBench-323 · **Date:** 2026-06-12 · Separate experiment from the locked N=105 headline (does not reopen it).

**Question:** which 10 EB tasks and 10 CSB tasks are *most likely* to show high lift from Sourcegraph MCP, for a head-to-head where Condition A = `claude-sonnet-4-6` + SG-MCP (`mcp_only`) and Condition B = `claude-fable-5` without MCP (`baseline`)?

## Selection signals

1. **Observed per-task mcp_only−baseline delta** from the locked clean set
   (`results/analysis/mcp_vs_baseline_clean_summary.md`, N=105, same-model runs) — primary signal for EB.
   Baseline-dead "MCP rescue" tasks (§3 of that doc) count as strong positive signal even though the
   parity mean excludes them.
2. **Task-type MCP signal rating** from `docs/TASK_TYPE_PRD.md` (★★★–★★★★★).
3. **Task traits:** repo count (multi-repo = cross-repo necessity; CRNT is enforced structurally at
   authoring time for multi-repo tasks — no stored per-task CRNT scores exist), discovery-heavy task
   types (error_provenance, dependency_graph, dead_code_necropsy, refactor_orchestration).
4. **CSB:** per-task MCP−baseline reward deltas recomputed from
   `~/projects/CodeScaleBench/runs/analysis/<suite>/cc_sonnet46/{baseline,mcp}/*/verifier/reward.txt`
   (166 paired tasks, claude-sonnet-4.6, 1 trial per variant), plus suite-level lift
   (security/incident/crossrepo_tracing/migration are the top-lift, discovery-heavy suites).

**Constraints:** EB picks are restricted to **active** (non-archived) tasks — several of the largest
positive deltas (schema-evolution-009 +1.50, dead-code-004 rescue +1.79, support-map-element-threads-009
+1.00) sit in `benchmarks/_archived/` and are excluded by design. CSB picks exclude pure
implementation/test-generation tasks (bustub-hyperloglog-impl-001 +0.83, test-unitgen-py-001 +0.40)
whose lift is not retrieval-driven — the study targets context-retrieval lift, and bustub's EB twin
was archived as an out-of-scope coding task (its locked mcp_only run was a NO-OP).

**Noise caveat:** locked EB deltas are 1 run per mode; CSB deltas are 1 trial per variant. Per-task
deltas are noisy point estimates; the portfolio (10+10) is the unit of inference, not any single task.

## Top-10 EB tasks

| # | task | locked Δ (mcp−base) | type (MCP signal) | repos | difficulty | rationale |
|---|------|--------------------:|-------------------|------:|------------|-----------|
| 1 | ansible-galaxy-tar-regression-prove-001 | **+2.00 (rescue: base 0.00)** | error_provenance (★★★★) | 1 | hard | Largest MCP rescue in the locked set; discovery-heavy provenance tracing |
| 2 | refactor-orch-001 | +0.80 | refactor_orchestration (★★★★★) | 2 | medium | Highest active non-rescue delta; top-rated type; dual-repo |
| 3 | monorepo-boundary-pnpm-lockfile-005 | +0.78 | monorepo_boundary (★★★★½) | 1 | expert | Large delta; cross-package navigation in a monorepo |
| 4 | dead-code-003 | +0.67 | dead_code_necropsy (★★★★) | 1 | hard | Reference-finding workload = core MCP strength |
| 5 | support-map-grafana-import-003 | **+0.60 (rescue: base 0.00)** | support_code_mapping (★★★½) | 1 | hard | MCP rescued a dead baseline |
| 6 | api-contract-grpc-metadata-001 | +0.58 | api_contract (★★★★★) | 2 | hard | Top-rated type; cross-repo contract boundary |
| 7 | api-contract-grpc-transport-003 | +0.50 | api_contract (★★★★★) | 3 | expert | Tri-repo + top-rated type |
| 8 | refactor-orchestration-tri-tokio-001 | +0.50 | refactor_orchestration (★★★★★) | 3 | hard | Tri-repo + top-rated type |
| 9 | dep-traversal-010 | +0.50 | dependency_graph (★★★★½) | 3 | hard | Tri-repo dependency traversal (discovery-heavy) |
| 10 | config-drift-tri-kustomize-001 | +0.50 | config_drift (★★★) | 3 | expert | Tri-repo; chosen over single-repo config-drift-redis-password-003 at equal delta |

Portfolio: 5 multi-repo (2× dual is counted once — items 2,6 dual; 7,8,9,10 tri), 7 task types,
all deltas ≥ +0.50 or baseline-dead rescue, all active.

## Top-10 CSB tasks

Deltas: mean MCP reward − mean baseline reward, cc_sonnet46, reward scale 0–1.

| # | task | Δ (mcp−base) | suite | rationale |
|---|------|-------------:|-------|-----------|
| 1 | ccx-vuln-remed-014 | **+1.00 (0.00→1.00)** | org_security | Full rescue; security = top suite-level lift (+0.106) |
| 2 | ccx-crossorg-217 | +0.62 | org_crossorg | Cross-org tracing — maximal discovery surface |
| 3 | ccx-migration-274 | +0.55 | org_migration | Migration impact mapping across repos |
| 4 | ccx-vuln-remed-135 | +0.49 | org_security | Security remediation; discovery-heavy |
| 5 | ccx-config-trace-010 | +0.43 | org_crossrepo_tracing | Cross-repo config tracing; suite lift +0.05 |
| 6 | ccx-migration-289 | +0.36 | org_migration | Second migration pick; consistent suite lift |
| 7 | ccx-agentic-223 | +0.35 | org_org | Org-wide discovery task |
| 8 | ccx-vuln-remed-126 | +0.26 | org_security | Third security pick (suite depth, 0.66→0.92) |
| 9 | ccx-incident-145 | +0.22 (0.00→0.22) | org_incident | Incident = highest suite-level lift (+0.113); baseline-dead rescue |
| 10 | ccx-crossorg-288 | +0.21 | org_crossorg | Second cross-org pick |

Portfolio: all org-suite, discovery-heavy tasks from the four top-lift suites
(security ×3, crossorg ×2, migration ×2, tracing ×1, incident ×1, org ×1).
No overlap with the EB top-10 (the two active EB ccx mirrors — ccx-incident-032,
ccx-dep-trace-106 — are not selected on either side).

## Phase 2 design (recorded here for provenance)

- **Condition A:** `claude-sonnet-4-6`, Sourcegraph MCP (EB `--mode mcp_only`; CSB `mcp-remote-direct`).
- **Condition B:** `claude-fable-5`, no MCP (EB `--mode baseline`; CSB baseline variant).
- Model and tooling vary together **by design** — the question is whether a cheaper model with MCP
  competes with a frontier model without it.
- EB runs: `scripts/orchestration/run_task.py`, parallel across OAuth accounts 1–5, mode-suffixed
  image tags, outputs under `results/mcp_lift_study/` (separate from the locked `results/runs/` tree).
- CSB runs: local-Docker harbor via `configs/harnesses/run_selected_tasks.sh` with a custom
  selection file.
- Validity: EB infra-error triage (`status`/`failure_class`, num_turns>0) applied before counting any run.
- Metrics per task × condition: ScoreResult reward + checkpoint sub_scores, wall-clock, input/output
  tokens + $ cost (Claude-Code-reported `cost_usd` primary), IR metrics (ground-truth file recall,
  tool-call counts, search efficiency) from `agent_trace.jsonl` / trajectories.
