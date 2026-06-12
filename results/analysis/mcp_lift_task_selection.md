# MCP-Lift Study — Phase 1: Task Selection (top-10 EB + top-10 CSB)

**Bead:** EnterpriseBench-323 · **Date:** 2026-06-12 · **Phase:** 1 of 2 (selection only; no runs)
**Question:** which tasks are most likely to show high lift from Sourcegraph MCP, to feed a head-to-head of **Sonnet 4.6 + SG-MCP** (condition A) vs **Fable 5 without MCP** (condition B)?

## Selection method

Three signals, blended in priority order:

1. **Observed per-task MCP-vs-baseline delta** — EB: the locked p6ux.28 set (sonnet-4-6, `results/analysis/mcp_vs_baseline_clean_summary.md`) plus the baseline-dead rescue group its mean excludes; CSB: the `csb-v1-mixed371--haiku45--030326` snapshot (`runs/snapshots/.../export/summary/rewards.json`, haiku-4.5, baseline-local-direct vs mcp-remote-direct).
2. **Task-type MCP-signal rating** from `docs/TASK_TYPE_PRD.md` (api_contract ★★★★★, refactor_orchestration ★★★★★, dependency_graph ★★★★½, monorepo_boundary ★★★★½, dead_code/db_schema/error_provenance ★★★★, …).
3. **Discovery-heavy traits**: repo count (CRNT multi-repo), repo scale (kubernetes/grafana/react/linux-class), and the bead's named discovery types (error_provenance, dep_traversal, dead_code_necropsy).

**Eligibility:** EB picks must be **active** tasks (not `benchmarks/_archived/`); CSB picks must be in the **canonical 275** (`benchmarks/CANONICAL.json`).

### Caveats that bound expectations (from the dph audit, `docs/qa/locked_runset_audit_2026_06.md`)

- **Winner's curse:** every delta below comes from 1 run per condition. Selecting top deltas selects partly on noise; phase-2 lift will regress toward the mean. Phase 2 must run ≥3 reps per condition.
- **Baseline-0.0 rescues** are the strongest empirical signal but baseline 0.0 can be max-turns censoring rather than genuine inability (2 of the locked set's worst mcp_only scores were exactly that). Keep `--max-turns` identical across conditions.
- CSB deltas were measured with **haiku-4.5**, not sonnet — MCP lift tends to be larger for weaker drivers, so CSB expectations carry extra uncertainty under sonnet.

---

## Top-10 EB tasks

| # | task id (run id) | benchmark dir | type (MCP rating) | repos | observed delta | rationale |
|---|---|---|---|---|---:|---|
| 1 | ansible-galaxy-tar-regression-prove-001 | incident_response/ansible-galaxy-tar-regression-prove-001 | error_provenance (★★★★) | ansible | **+2.00** (0.0→2.0) | Strongest rescue in the suite; discovery-heavy provenance tracing in a large Python codebase. Baseline-dead (excluded from locked mean) — exactly the signal this study targets. |
| 2 | refactor-orch-001 | technical_debt/refactor-orchestration-001 | refactor_orchestration (★★★★★) | etcd + kubernetes | +0.80 (1.0→1.8) | Top-rated type; dual-repo with k8s-scale navigation; biggest non-rescue delta among ★★★★★ types. |
| 3 | monorepo-boundary-pnpm-lockfile-005 | feature_delivery/monorepo-boundary-005 | monorepo_boundary (★★★★½) | pnpm | +0.78 (2.0→2.78) | Monorepo cross-package boundary tracing — symbol-level navigation across workspace packages. |
| 4 | dead-code-003 | technical_debt/dead-code-003 | dead_code_necropsy (★★★★) | react | +0.67 (1.69→2.35) | Named discovery-heavy type; reference-finding across a large repo is the core MCP mechanism. |
| 5 | support-map-grafana-import-003 | customer_escalation/support-mapping-003 | support_code_mapping (★★★½) | grafana | +0.60 (0.0→0.6) | Baseline-dead rescue on a very large Go/TS repo; ticket-to-code mapping is search-bound. |
| 6 | api-contract-grpc-metadata-001 | dependency_management/api-contract-001 | api_contract (★★★★★) | grpc-go + etcd | +0.58 (3.17→3.75) | Top-rated type, dual-repo contract tracing between producer and consumer. |
| 7 | api-contract-grpc-transport-003 | dependency_management/api-contract-003 | api_contract (★★★★★) | grpc-go + etcd + kubernetes | +0.50 (2.67→3.17) | Tri-repo contract chain ending in kubernetes — highest repo-scale pick of the api_contract group. |
| 8 | dep-traversal-010 | dependency_management/dep-traversal-010 | dependency_graph (★★★★½) | jackson-databind + spring-boot + dropwizard | +0.50 (3.5→4.0) | Named discovery-heavy type; tri-repo dependency chain (CRNT). |
| 9 | refactor-orchestration-tri-tokio-001 | technical_debt/refactor-orchestration-tri-tokio-001 | refactor_orchestration (★★★★★) | tokio + hyper + axum | +0.50 (1.5→2.0) | Top-rated type at tri-repo scale; Rust async stack requires cross-repo symbol resolution. |
| 10 | config-drift-tri-kustomize-001 | platform_engineering/config-drift-tri-kustomize-001 | config_drift (★★★) | kustomize + argo-cd + flux2 | +0.50 (1.5→2.0) | Lowest type rating in the picks, but tri-repo CRNT + solid observed delta; diversifies the suite-coverage of the study. |

Composition: 6/10 multi-repo (2 dual, 4 tri), 8 task types covered, 2 baseline-dead rescues, repos up to kubernetes/grafana/react scale. All 10 are active and were SG-indexed for the original mcp_only campaign (deltas exist ⇒ MCP runs completed).

**Excluded despite top deltas (archived):** dead-code-004 (+1.79 rescue), schema-evolution-009 (+1.50), support-map-element-threads-009 (+1.00) — all in `benchmarks/_archived/`, out of the active target. **First alternates (active):** refactor-orch-006 (+0.40), schema-evolution-003 (+0.20, only active schema-evolution with positive delta).

---

## Top-10 CSB tasks (canonical 275; deltas from haiku-4.5 snapshot)

| # | task id | repo (scale) | category / difficulty | observed delta | rationale |
|---|---|---|---|---:|---|
| 1 | terraform-eval-context-refac-001 | hashicorp/terraform (L, Go) | cross_file_refactoring / hard | **+1.00** (0→1) | Full rescue; cross-file refactor in a large Go codebase is reference-tracing-bound. |
| 2 | django-rate-limit-middleware-feat-001 | django/django (L, Python) | feature_implementation / hard | **+1.00** (0→1) | Full rescue; feature placement requires finding the right extension points across a mature framework. |
| 3 | python-http-class-naming-refac-001 | csb_sdlc_refactor suite | refactor | +0.96 (0→0.96) | Near-full rescue, naming refactor = exhaustive reference discovery. |
| 4 | envoy-request-routing-qa-001 | envoyproxy/envoy (XL, C++) | dataflow_qa / hard | +0.87 (0→0.87) | Dataflow question over one of the largest C++ OSS codebases; precise symbol search is the differentiator. |
| 5 | pytorch-release-210-fix-001 | pytorch (XL, C++) | cross_module_bug_fix / hard | +0.79 (0→0.79) | Cross-module bug localization at XL scale. |
| 6 | etcd-grpc-api-upgrade-001 | etcd (sdlc_design) | understand | +0.71 (0→0.71) | API-upgrade comprehension task; contract tracing analogous to EB api_contract (★★★★★ type family). |
| 7 | strata-fx-european-refac-001 | OpenGamma/Strata (M, Java) | cross_file_refactoring / hard | +0.70 (0→0.70) | Java cross-file refactor; type-hierarchy navigation. |
| 8 | ccx-migration-274 | sg-evals/kafka mirror (L, Java) | migration-inventory / hard | +0.60 (0.4→1.0) | Org-suite discovery task on an sg-evals mirror; non-zero baseline makes the delta less rescue-artifact-prone. |
| 9 | ccx-vuln-remed-014 | sg-evals/grafana mirror (L, Go) | vuln-remediation / hard | +0.50 (0→0.5) | Vulnerability usage-site discovery across a large mirror — dep_traversal-like. |
| 10 | linux-acpi-backlight-fault-001 | linux (XXL, C) | fault_localization / expert | +0.40 (0.3→0.7) | Largest repo in the pick set; expert fault localization with a non-zero baseline — high headroom, low rescue-artifact risk. |

**Excluded despite top deltas (not in canonical 275):** ccx-vuln-remed-011 (+1.00), ccx-incident-031 (+1.00), ccx-dep-trace-004 (+0.88), openlibrary-solr-boolean-fix-001 (+0.67), ansible-module-respawn-fix-001 (+0.53), ccx-compliance-051 (+0.49). **First alternate (canonical):** terraform-compact-diff-fmt-feat-001 (+1.00 — held out only to avoid a second terraform task; promote it if any pick proves unrunnable).

---

## Phase-2 readiness notes

- **Conditions:** A = `claude-sonnet-4-6` + SG MCP (EB `mcp_only` mode; CSB `Dockerfile.sg_only`). B = `claude-fable-5`, baseline tool access. Model is set by appending `--model <id>` to the runner's agent command (`run_task.py` `DEFAULT_OAUTH_AGENT_COMMAND`).
- **Replication:** ≥3 reps per task × condition (120 runs total) — required by the single-run-noise finding above. Aggregate by mean (not best-of-N; see audit §1.4).
- **Budget parity:** keep `--max-turns 50` identical in both conditions and triage max-turns exhaustion explicitly (audit §1.3) before counting any run; apply the s58f `status` classifier to every run.
- **Parallelism:** accounts 1–5, `&` + `wait`, mode-suffixed image tags (CLAUDE.md conventions). CSB runs go through its Daytona runner (`daytona_runner.py`) with deterministic pairing via `control_plane.py`.
- **Metrics per run:** task_score + checkpoint sub_scores (eb_verify ScoreResult), wall-clock (timing block), input/output tokens (ScoreResult diagnostics, commit 0db2e7b) → dollar cost at per-model pricing **including auxiliary haiku usage** (audit §3: haiku was 28% of locked-run cost), IR metrics (files-found vs ground_truth.json, tool-call counts, MCP-call counts).
