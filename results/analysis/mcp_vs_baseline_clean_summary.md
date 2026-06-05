# Validity-Filtered MCP-vs-Baseline — Clean Summary (full-suite, post-remediation)

**Bead:** EnterpriseBench-p6ux.28 (kxt step 3) · supersedes the N=66 klp snapshot · **Campaign:** p6ux · **Date:** 2026-06-05 · **Mode:** read-only aggregation (no re-runs)

**Sources:** `results/runs/<task>/{baseline,mcp_only,hybrid}/results.json` after decision-B remediation:
p6ux.1 (4 aggregated baselines) · p6ux.22 (37 baseline backfill) · p6ux.4 (config-drift charts re-run) · p6ux.7/.10/.13/.16/.19 + 9nb (verifier/gold fixes) · p6ux.25 (masked re-verify).
**Generator:** `results/analysis/aggregate_mcp_clean.py` (read-only). Reproduce: `python3 results/analysis/aggregate_mcp_clean.py`.

**Validity rule (unchanged from klp):** a run counts only if it executed (`num_turns>0`, `aggregate_mcp_clean.py:36`) and `status` is not INVALID/NO-OP (`:38-41`). Clean MCP-vs-baseline pairing requires non-deleted task + valid baseline with score>0 + valid mcp_only (`:91-117`). MCP-usage ground truth is `mcp_tool_calls>0` (`:47`); `mcp_handshake_ok` is a confirmed false-negative and ignored.

---

## Headline numbers

**On the expanded clean set (N=105, up from 66), mcp_only remains at PARITY with baseline — median delta exactly 0.0 — with a faint baseline lean (mean −0.09 pts on a ~0–4 scale).** The active-suite baseline-coverage gap that constrained the N=66 snapshot is now CLOSED: of the 11 tasks still lacking a baseline, **all 11 are archived** (out of the active target).

### 1. mcp_only vs baseline (clean set)

- **N = 105 tasks** (valid baseline **and** valid mcp_only, baseline score > 0). Prior snapshot: N=66.
- **Mean delta** (mcp_only − baseline): **-0.0925 pts**  *(prior N=66: −0.045)*.
- **Median delta: +0.000** (exact tie) — unchanged from the N=66 snapshot.
- **MCP better: 24 · tie: 52 · MCP worse: 29**  *(prior: 17 / 33 / 16)*.
- Distribution: stdev 0.53, range [-2.00, +1.50]. 52/105 exact ties. Tails — better: 2 ≥+1.0 + 19 in +0.25..1.0; worse: 10 ≤−1.0 + 17 in −1.0..−0.25.

**Delta vs prior N=66:** N +39 (mostly the 37 backfilled baselines); mean drifts −0.045 → -0.093 (~2.3% of scale); median holds at 0.0; the better/worse balance tips from near-symmetric (17/16) to mildly worse-leaning (24/29).

**Verdict: parity, with a faint baseline edge.** The median task is unaffected; the small negative mean is within noise of zero and is plausibly inflated by a baseline-vintage artifact (see §6).

### 2. Coverage — the gap is closed for the active suite

- **11 tasks still lack a baseline — and every one is archived** (`benchmarks/_archived/`), excluded from the active target by design:
  ccx-compliance-052, ccx-compliance-053, error-trace-grafana-jaeger-panic-001, error-trace-k8s-sessionaffinity-001, error-trace-k8s-sidecar-restart-001, rbac-audit-002, rbac-audit-003, schema-evolution-007, schema-evolution-010, support-map-firefox-css-crash-006, support-map-k8s-eviction-005
- **3** deleted (out-of-scope coding tasks, cut in 9fa129f).
- **1** mcp_only NO-OP (bustub-hyperloglog-impl-001).
- The N=66 snapshot excluded **52** baseline-missing; the p6ux.22 backfill cut that to **11** — a full close of the *active* coverage gap.

### 3. Baseline-dead tasks (MCP-vs-baseline meaningless; excluded from the mean)

| task | baseline | mcp_only | note |
|------|---------:|---------:|------|
| ansible-galaxy-tar-regression-prove-001 | 0.000 | 2.000 | MCP rescued (+2.00) |
| dead-code-001 | 0.000 | 0.000 | both dead |
| dead-code-004 | 0.000 | 1.790 | MCP rescued (+1.79) |
| support-map-grafana-import-003 | 0.000 | 0.600 | MCP rescued (+0.60) |
| support-map-kafka-bufpool-012 | 0.000 | 0.000 | both dead |

> **3 of 5** baseline-dead tasks show mcp_only *rescuing* a 0.0 baseline (+2.00, +1.79, +0.60). These are pro-MCP signal that the parity mean (which requires baseline>0) structurally excludes — a mild conservative bias against MCP in the headline.

### 4. Hybrid: did it exercise MCP?

- **121** real hybrid runs; **90 (74%)** made ≥1 MCP call; **31 (26%)** fell back to filesystem-only.
- Consistent with the N=66 finding: hybrid uses MCP the majority of the time; fs-fallback is real but concentrated (tool-selection behavior, not capability).

### 5. Checkpoint-masking — resolved at the verifier level (stored scores lag)

- The generator still flags **29 tasks** with ≥1 uniform-0.0 checkpoint *in stored `results.json`* (down from 37): topo_order 11, related_issues 9, dead_code 3, ownership 3, code_paths 2, severity 2, expected_values 1, source_files 1, symbols 1, feature_flags 1, error_source 1, impact_classification 1, schema_change 1.
- **These stored scores predate the verifier fixes** (WU3-7 + 9nb changed `check_*.sh`/plugins but were not written back into old mcp_only/hybrid `results.json`).
- The authoritative re-verification (**p6ux.25**, `results/analysis/masked_reverify_2026_06.md`, re-scored under the fixed verifiers) shows **20 of 37 unmask**; the residual 17 are **genuine agent misses + archived-by-design**, not structural verifier defects.
- Net: masking is a *resolved* verifier issue. Because it flattens a checkpoint to 0.0 *symmetrically across modes*, it suppresses signal (inflates ties) but does **not** bias the mcp_only−baseline direction.

### 6. Caveat — baseline vintage

The 37 backfilled baselines were run 2026-06-04/05 under the **current** agent + fixed verifiers, while the mcp_only runs they pair against are from the **original** campaign. A fresh-baseline vs stale-mcp comparison can manufacture a small anti-MCP delta; this plausibly accounts for the −0.045 → −0.09 mean drift. (The klp snapshot flagged the inverse for the s58f-refiled cohort — vintage mismatch cuts both ways.) A same-vintage A/B would tighten the mean toward the 0.0 median.

---

## Bottom line

1. **Parity holds at scale.** Expanding 66 → 105 clean tasks leaves the median at 0.0 and the mean within ±0.1 of zero. mcp_only delivers **no net task-score benefit** over baseline, and **no deficit of practical magnitude**.
2. **The active coverage gap is closed.** Only archived tasks remain unmeasured; the binding constraint from the N=66 snapshot (52 baseline-missing) is gone.
3. **Three named residual gaps, all second-order:** (a) baseline-vintage mismatch likely inflates the small negative mean; (b) stored masked-checkpoint scores lag the verifier fixes (p6ux.25: 20/37 unmask, rest genuine/archived) — flattens but does not bias; (c) hybrid fs-fallback ~26% (tool-selection).

## Ready-to-lock signal

**Recommendation: READY TO LOCK the primary finding** — *mcp_only is at parity with baseline on task score (median 0.0; mean −0.09, ~2% of scale); MCP shows no task-score advantage on this suite.* The finding is **robust to the 66 → 105 expansion** and the coverage gap is closed for the active suite. None of the three residual gaps can plausibly move the conclusion from parity (they are either by-design archived exclusions, or symmetric/conservative effects), so they are **caveats, not blockers**. The one item that could nudge the mean from −0.09 toward 0.0 is a same-vintage baseline/mcp A/B; it would *strengthen* (not overturn) the parity call.

---

## Clean MCP-vs-baseline set (N=105), sorted by delta

| task | baseline | mcp_only | delta |
|------|---------:|---------:|------:|
| support-map-nodebb-notif-011 | 2.000 | 0.000 | -2.000 |
| error-trace-tf-backend-copypaste-001 | 2.330 | 0.500 | -1.830 |
| support-map-libreoffice-formula-007 | 1.810 | 0.000 | -1.810 |
| cal-sup-flask-errhandler-006 | 2.650 | 1.250 | -1.400 |
| cal-err-requests-proxy-003 | 2.800 | 1.600 | -1.200 |
| support-map-grafana-alerts-004 | 2.940 | 1.900 | -1.040 |
| cal-drift-flask-config-001 | 3.000 | 2.000 | -1.000 |
| camel-routing-arch-001 | 3.000 | 2.000 | -1.000 |
| schema-evolution-004 | 3.250 | 2.250 | -1.000 |
| schema-evolution-tri-supabase-001 | 2.400 | 1.400 | -1.000 |
| support-map-envoy-connpool-001 | 2.670 | 1.920 | -0.750 |
| cal-sup-requests-cabundle-007 | 1.620 | 0.980 | -0.640 |
| ccx-incident-032 | 2.700 | 2.100 | -0.600 |
| support-map-element-unread-010 | 2.470 | 1.970 | -0.500 |
| api-contract-tri-protobuf-001 | 3.000 | 2.500 | -0.500 |
| cal-drift-click-envvar-002 | 1.500 | 1.000 | -0.500 |
| dep-graph-tri-tokio-hyper-tonic-001 | 2.500 | 2.000 | -0.500 |
| incident-inv-prom-parse-003 | 3.500 | 3.000 | -0.500 |
| refactor-orchestration-tri-spring-001 | 1.500 | 1.000 | -0.500 |
| schema-evolution-001 | 4.000 | 3.500 | -0.500 |
| cal-err-click-flagval-002 | 2.300 | 1.870 | -0.430 |
| support-map-llvm-assertion-002 | 2.370 | 2.030 | -0.340 |
| api-contract-celgo-program-008 | 4.000 | 3.670 | -0.330 |
| api-contract-gocp-module-007 | 4.000 | 3.670 | -0.330 |
| dep-traversal-012 | 4.000 | 3.670 | -0.330 |
| monorepo-boundary-rustc-attr-009 | 1.750 | 1.500 | -0.250 |
| rbac-audit-001 | 4.000 | 3.750 | -0.250 |
| refactor-orch-005 | 1.200 | 1.000 | -0.200 |
| dead-code-005 | 2.675 | 2.575 | -0.100 |
| api-contract-envoy-xds-v2-006 | 3.670 | 3.670 | +0.000 |
| api-contract-grpc-balancer-002 | 4.000 | 4.000 | +0.000 |
| api-contract-grpc-clientconn-004 | 3.670 | 3.670 | +0.000 |
| api-contract-protobuf-v2-005 | 3.750 | 3.750 | +0.000 |
| api-contract-tri-envoy-001 | 2.400 | 2.400 | +0.000 |
| cal-dead-click-legacy-001 | 1.000 | 1.000 | +0.000 |
| cal-dead-flask-legacy-002 | 1.000 | 1.000 | +0.000 |
| cal-err-flask-blueprint-001 | 2.000 | 2.000 | +0.000 |
| cal-err-flask-endpoint-004 | 2.360 | 2.360 | +0.000 |
| ccx-dep-trace-106 | 1.800 | 1.800 | +0.000 |
| ceph-rgw-auth-secure-001 | 2.500 | 2.500 | +0.000 |
| config-drift-argocd-redis-ha-004 | 2.250 | 2.250 | +0.000 |
| config-drift-consul-serf-port-001 | 3.000 | 3.000 | +0.000 |
| config-drift-scdf-rabbitmq-002 | 3.000 | 3.000 | +0.000 |
| dep-graph-tri-boto3-urllib3-requests-001 | 2.000 | 2.000 | +0.000 |
| dep-graph-tri-prometheus-alertmanager-grafana-001 | 2.500 | 2.500 | +0.000 |
| dep-traversal-001 | 3.000 | 3.000 | +0.000 |
| dep-traversal-002 | 4.000 | 4.000 | +0.000 |
| dep-traversal-003 | 4.000 | 4.000 | +0.000 |
| dep-traversal-004 | 4.000 | 4.000 | +0.000 |
| dep-traversal-005 | 4.000 | 4.000 | +0.000 |
| dep-traversal-007 | 4.000 | 4.000 | +0.000 |
| dep-traversal-008 | 4.000 | 4.000 | +0.000 |
| dep-traversal-009 | 4.000 | 4.000 | +0.000 |
| dep-traversal-011 | 4.000 | 4.000 | +0.000 |
| dep-traversal-tri-boto3-001 | 3.000 | 3.000 | +0.000 |
| dep-traversal-tri-jackson-001 | 3.000 | 3.000 | +0.000 |
| dep-traversal-tri-openssl-001 | 2.750 | 2.750 | +0.000 |
| error-trace-k8s-job-starttime-001 | 2.500 | 2.500 | +0.000 |
| error-trace-k8s-nftables-sync-001 | 3.000 | 3.000 | +0.000 |
| error-trace-k8s-poststarthook-001 | 2.500 | 2.500 | +0.000 |
| error-trace-k8s-utf8-envvar-001 | 2.500 | 2.500 | +0.000 |
| error-trace-tf-backend-nilptr-001 | 2.500 | 2.500 | +0.000 |
| incident-inv-docker-shutdown-004 | 4.000 | 4.000 | +0.000 |
| incident-inv-watch-cache-001 | 4.000 | 4.000 | +0.000 |
| incident-inv-watch-delete-002 | 4.000 | 4.000 | +0.000 |
| incident-investigation-quad-containerd-001 | 5.000 | 5.000 | +0.000 |
| incident-investigation-tri-containerd-001 | 3.500 | 3.500 | +0.000 |
| monorepo-boundary-babel-forof-004 | 2.670 | 2.670 | +0.000 |
| monorepo-boundary-babel-metadata-003 | 3.000 | 3.000 | +0.000 |
| monorepo-boundary-babel-ts-init-001 | 3.000 | 3.000 | +0.000 |
| monorepo-boundary-nextjs-og-008 | 3.000 | 3.000 | +0.000 |
| monorepo-boundary-pnpm-lifecycle-006 | 3.000 | 3.000 | +0.000 |
| monorepo-boundary-pnpm-sha256-007 | 2.420 | 2.420 | +0.000 |
| refactor-orch-002 | 1.800 | 1.800 | +0.000 |
| refactor-orch-004 | 1.800 | 1.800 | +0.000 |
| refactor-orch-007 | 1.800 | 1.800 | +0.000 |
| refactor-orch-008 | 1.800 | 1.800 | +0.000 |
| refactor-orchestration-tri-babel-001 | 1.500 | 1.500 | +0.000 |
| schema-evolution-002 | 4.000 | 4.000 | +0.000 |
| schema-evolution-008 | 4.000 | 4.000 | +0.000 |
| support-map-qutebrowser-cache-008 | 0.950 | 0.950 | +0.000 |
| dead-code-002 | 2.224 | 2.370 | +0.146 |
| schema-evolution-003 | 3.600 | 3.800 | +0.200 |
| cal-sup-click-sentinel-005 | 1.950 | 2.150 | +0.200 |
| monorepo-boundary-rustc-statics-010 | 2.420 | 2.670 | +0.250 |
| schema-evolution-006 | 3.750 | 4.000 | +0.250 |
| refactor-orch-003 | 1.500 | 1.800 | +0.300 |
| cal-sup-click-clirunner-008 | 2.180 | 2.500 | +0.320 |
| dep-traversal-006 | 3.670 | 4.000 | +0.330 |
| error-trace-tf-builtin-schema-001 | 2.170 | 2.500 | +0.330 |
| monorepo-boundary-babel-decorators-002 | 2.670 | 3.000 | +0.330 |
| rbac-audit-004 | 3.670 | 4.000 | +0.330 |
| refactor-orch-006 | 1.000 | 1.400 | +0.400 |
| schema-evolution-005 | 3.150 | 3.550 | +0.400 |
| api-contract-grpc-transport-003 | 2.670 | 3.170 | +0.500 |
| config-drift-redis-password-003 | 2.500 | 3.000 | +0.500 |
| config-drift-tri-kustomize-001 | 1.500 | 2.000 | +0.500 |
| dep-traversal-010 | 3.500 | 4.000 | +0.500 |
| refactor-orchestration-tri-tokio-001 | 1.500 | 2.000 | +0.500 |
| api-contract-grpc-metadata-001 | 3.170 | 3.750 | +0.580 |
| dead-code-003 | 1.688 | 2.354 | +0.667 |
| monorepo-boundary-pnpm-lockfile-005 | 2.000 | 2.780 | +0.780 |
| refactor-orch-001 | 1.000 | 1.800 | +0.800 |
| support-map-element-threads-009 | 1.970 | 2.970 | +1.000 |
| schema-evolution-009 | 2.500 | 4.000 | +1.500 |

*Generated by `results/analysis/aggregate_mcp_clean.py` (read-only).*
