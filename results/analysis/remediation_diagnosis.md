# Remediation Diagnosis — Checkpoint-Masking & Baseline-Missing

**Bead:** EnterpriseBench-mda · **Campaign:** p6ux · **For:** EnterpriseBench-tev (Stephanie's suite-lock decision) · **Date:** 2026-06-04 · **Mode:** read-only (no re-runs, no edits, no containers)

**Source:** `results/analysis/mcp_vs_baseline_clean_summary.md` (EnterpriseBench-klp)
**Inputs scanned:** `results/runs/<task>/{baseline,mcp_only,hybrid}/results.json`, `results/runs/_mcp_validity_audit_p6ux.tsv`, `benchmarks/**/{task.toml,ground_truth.json,instruction.md,checks/*.sh}`, `lib/eb_verify/plugins/*.py`, `scripts/sandbox/templates/*.Dockerfile`.

---

## Headline (for project-lead / tev)

**Both gaps are remediation artifacts, not benchmark-design failures. Almost nothing here is "genuinely hard."**

- **(A) Checkpoint-masking — 37 tasks:** **29 are UNSATISFIABLE due to fixable verifier/gold/env bugs** (agent answers are frequently *substantively correct* and rejected by exact-key / exact-string / repo-naming / no-python3 brittleness); **3 are infra no-op** (container EACCES — recover on re-run, verifier+gold already correct); **2 are genuinely HARD/format**; **3 are unverified singletons** to spot-check. **Zero** are "legit hard checkpoint no agent could pass on merit." → **FIX the verifiers, don't exclude the tasks.** Most fixes are one-liners.
- **(B) Baseline-missing — 52 tasks:** **41 are cheaply backfillable** (4 already ran baseline and only need result aggregation; 37 just need a one-mode baseline re-dispatch — defs, repos, and valid mcp/hybrid runs all present); **11 are archived tasks** where baseline backfill is low-value. The dominant root cause is mundane: the p6ux campaign dispatched only `mcp_only` + `hybrid` and **never ran the baseline mode** for these tasks. No task or infra defect is implicated.

---

## (A) Checkpoint-Masking — per-checkpoint-type verdicts

A checkpoint is "masked" when it scores **0.0 uniformly across every real mode** (baseline/mcp_only/hybrid present), so it cannot separate tool-access conditions and silently flattens the task's signal. 40 (task, masked-checkpoint) groups were found across 18 checkpoint types; excluding 3 deleted tasks (ansible-abc, aspnetcore, beam) → **37 real masked tasks**. Verdicts below are evidence-backed (verifier read + gold read + actual agent output replayed where possible).

| Checkpoint type | #tasks | Verdict | Root-cause evidence | Recommended fix |
|---|---:|---|---|---|
| **topo_order** | 11 | **UNSATISFIABLE** | Gold `dependency_graph` keys are org/repo slugs (`"spf13/cobra"`, `ground_truth.json`), but `instruction.md` names repos by workspace basename (`/workspace/cobra/`, "cobra"). `check_topo_order.sh` extracts only the **first whitespace token** after each list number, and `topological_order.py` filters `known_proposed = [r for r in proposed if r in graph_nodes]` by **exact string** → 0 recognized repos for every agent, every mode. | **Fix verifier**: basename-normalize repo names before matching (`cobra` ≡ `spf13/cobra`). One change unblocks all 11 (= every refactor_orchestration task). |
| **related_issues** | 10 | **UNSATISFIABLE** | `check_related_issues.sh:39` reads refs **only** from top-level keys `related_issues`/`references`/`related`/`docs`; no `instruction.md` ever names that key. Agent answer.json puts the correct gold path (e.g. `DashboardModel.ts`) nested under `root_cause` → `refs_raw=[]` → 0.0. The *same* answer.json scored 0.6 on `severity` (file existed + parsed). All 14 `check_related_issues.sh` are byte-identical (md5) → systemic. | **Fix verifier**: flatten the whole answer.json and fuzzy-match gold basenames, OR mandate an explicit `related_issues` key in every instruction. |
| **ownership** | 4 | **UNSATISFIABLE** | `check_ownership.sh:62` matches hyphenated gold compounds (`alert-scheduling`, `tick-loop`, `rule-group`) by requiring the literal de-hyphenated **bigram** (`"alert scheduling"`/`"alertscheduling"`) as a substring of free prose; natural prose never emits these. Proof: grafana-alerts-004 wrote a correct `"Unified Alerting scheduler subsystem (pkg/util/ticker)"`, still scored 0.0 while `code_paths=0.9`, `severity=1.0` same run. Threshold `>=0.3` on exact bigrams is unreachable. | **Fix verifier**: per-token stem overlap (split compounds on `-`, match any token) or route to LLM judge. |
| **dead_code** | 3 | **UNSATISFIABLE** | Agent answer entries use MCP key `path` (e.g. `{"path":"src/click/utils.py","symbol":"safecall"}`), but `check_dead_code.sh:42` normalizes via `e.get('file','')` → tuple `('', symbol)` never matches GT's `(file, symbol)`. `dead-code-004`'s verifier is worse: hard `e["file"]` → `KeyError` on `path`. Agent found 3/4 correct dead symbols, scored 0. | **Fix verifier** (one-liner): `e.get('file') or e.get('path')`. |
| **source_files** | 2 | **UNSATISFIABLE (env)** | Java template `scripts/sandbox/templates/java_multi_repo.Dockerfile:10` installs only `git curl ca-certificates jq` — **no python3**. Every `python3 -c` check exits **127** (`camel-routing-arch-001`, `ccx-compliance-053` results.json). Proof: camel's pure-grep `check_architecture.sh` passed 1.0 same run. camel *also* mis-points output (instruction says `/logs/agent/solution.md`, checker reads `/workspace/agent_output/answer.json`). | **Fix env**: add `python3` to Java (and any non-Python) Dockerfile template. Separately align camel's instruction/checker path. |
| **symbols** | 1 | **UNSATISFIABLE (env)** | Same Java no-python3 exit-127 (`ccx-compliance-053`). | Same Dockerfile fix. |
| **expected_values** | 5 | **MIXED** | **3 charts tasks** (consul/redis/scdf): verifier returns **1.00 on the agent's actual report** — the recorded 0.0 is the container-EACCES/teardown no-op (`check_expected_values.sh:23,38` never saw the file). **2 calibration tasks** (click, flask-config): `check_expected_values.sh:38` requires gold sentences (e.g. `"Default value should be returned when envvar is unset"`) to appear **verbatim** in agent text → unpassable. | charts: **fix harness** (s58f, in progress) + re-run. calibration: **fix gold/verifier**. |
| **drift_points** | 4 | **MIXED** | **3 charts** (consul/redis/scdf): same EACCES no-op; consul verifier scores **1.00/1.00** on the real `DRIFT_REPORT.json`. **1 calibration** (click): `check_drift_points.sh:33` does `if key in agent_text` with **invented** gold keys `envvar_resolution`/`type_cast_value`; agent used real Click identifiers (`consume_value`, `resolve_envvar_value`) → 0.0. | charts: fix harness + re-run. calibration: fix gold (use real identifiers). |
| **severity** | 3 | **HARD / not-unsatisfiable** | Scorer is sound (enum distance low/medium/high/critical, `check_severity.sh:60-72`; scores 1.0 when field present). 0.0 only when agent omits a top-level `severity` key (cal-sup-flask-errhandler-006) or answer.json is absent (kafka exit-127, firefox exit-1). | Enforce `severity` field in instruction/harness, or have judge extract from `findings`. |
| **code_paths** | 2 | **HARD / infra** | Verifier is correct (suffix-match, scores 0.8–0.9 on grafana-004, 0.35 on qutebrowser-008). The two 0.0s are not code_paths-specific: kafka exit_code **127** (container/setup) and firefox exit_code **1** (`No answer.json found`) across **all** that task's checkpoints. | No verifier change; fix kafka container exec + firefox missing-answer.json runs. |
| **error_source** | 2 | **MIXED** | `cal-err-requests-proxy-003`: **gold is wrong** — GT `required_files` = `utils.py`/`models.py`, but the `Proxy-Authorization` logic is in `sessions.py:302-329` (`rebuild_proxies`); agent correctly named `sessions.py` and was rejected. `error-trace-k8s-sidecar-restart-001`: GT correct, agent didn't surface a top-level `files` array (format/HARD). | cal-err-003: **fix gold** (set required_files to sessions.py). k8s: relax extractor to scan nested `error_chain[].file`. |
| singletons | 3 | **UNVERIFIED** | `monorepo-boundary-rustc-attr-009` (impact_classification), `dead-code-001` (feature_flags), `schema-evolution-007` (schema_change, archived) — one masked checkpoint each, not yet replayed. Pattern strongly resembles the key/format brittleness above. | Spot-check during the verifier-fix pass. |

### (A) roll-up

| Bucket | #tasks | Action |
|---|---:|---|
| UNSATISFIABLE — fixable verifier bug (key/format/naming) | 25 | **Fix verifier** (topo_order 11, related_issues 10, ownership 4) |
| UNSATISFIABLE — fixable env bug (no python3) | 2 | **Fix Dockerfile** (source_files/symbols) |
| UNSATISFIABLE — fixable gold bug | 2 | **Fix gold** (dead_code dead-code-004 KeyError variant + cal-err-003 wrong required_files; dead_code calibration pair share the path-key fix above) |
| INFRA no-op (verifier+gold correct) | 3 | **Re-run after s58f** (config-drift charts: consul/redis/scdf) |
| Genuinely HARD / format | 2 | Minor extractor relax / agent-compliance (k8s-sidecar error_source, cal-sup severity) |
| Unverified singletons | 3 | Spot-check |
| **Total real masked** | **37** | |

> Note: dead_code's 3 tasks are counted once under "verifier key" (the `file`/`path` fix covers cal-dead pair; dead-code-004 needs the same fix plus KeyError guard). The exact bucket boundaries blur because several support-map tasks carry *multiple* masked checkpoints (firefox/kafka appear under related_issues **and** ownership/severity/code_paths); they are counted once, under their dominant `related_issues` verdict.

**Bottom line for tev:** the "37 checkpoint-masked" cohort does **not** evidence genuine task difficulty. It is a small set of distinct, mechanical defects — exact-key matching (`file` vs `path`, top-level `related_issues`), repo-name normalization (slug vs basename), compound-bigram keyword matching, and a missing `python3` in the Java image — plus 3 EACCES no-ops the s58f fix already addresses. **Recommend FIX, not EXCLUDE**, for ~32 of 37.

---

## (B) Baseline-Missing — 52 tasks, reason buckets

"Baseline-missing" = a run dir that has `mcp_only` and/or `hybrid` `results.json` but **no** `baseline/results.json`. 54 such dirs exist; 2 are deleted/out-of-scope (`ansible-abc-imports-fix-001`, `beam-pipeline-builder-refac-001`) → **canonical 52**.

| Reason bucket | # | Backfillability | Tasks |
|---|---:|---|---|
| **Already ran, un-aggregated** | 4 | **TRIVIAL** — `baseline/rep1..rep3/results.json` all exist with valid scores; only the top-level `baseline/results.json` aggregate is missing | config-drift-argocd-redis-ha-004 (reps 0.0/1.25/2.25), error-trace-k8s-nftables-sync-001 (3.0/3.0/2.75), incident-inv-docker-shutdown-004 (4.0/3.5/3.5), support-map-grafana-alerts-004 (2.94/2.94/2.88) |
| **Active, never-dispatched baseline** | 37 | **CHEAP** — active task.toml + repos + valid mcp/hybrid runs all present; campaign simply ran 2 of 3 modes. Re-dispatch `baseline` only | api-contract-{envoy-xds-v2-006, gocp-module-007, grpc-balancer-002, protobuf-v2-005}; cal-dead-flask-legacy-002; cal-drift-{click-envvar-002, flask-config-001}; cal-err-{click-flagval-002, flask-blueprint-001, flask-endpoint-004, requests-proxy-003}; cal-sup-{click-clirunner-008, click-sentinel-005, flask-errhandler-006, requests-cabundle-007}; camel-routing-arch-001; ceph-rgw-auth-secure-001; config-drift-{consul-serf-port-001, redis-password-003, scdf-rabbitmq-002}; dep-traversal-{003,005,010}; error-trace-k8s-{job-starttime-001, poststarthook-001}; incident-inv-{prom-parse-003, watch-cache-001, watch-delete-002}; monorepo-boundary-nextjs-og-008; rbac-audit-001; refactor-orch-{001,003}; schema-evolution-{001,003}; support-map-{envoy-connpool-001, grafana-import-003}; ansible-galaxy-tar-regression-prove-001 |
| **Archived task** | 11 | **LOW-VALUE / BLOCKED** — task lives in `benchmarks/_archived/`; baseline backfill is wasted unless the task is un-archived into the active suite first | ccx-compliance-052, ccx-compliance-053, error-trace-grafana-jaeger-panic-001, error-trace-k8s-sessionaffinity-001, error-trace-k8s-sidecar-restart-001, rbac-audit-002, rbac-audit-003, schema-evolution-007, schema-evolution-010, support-map-firefox-css-crash-006, support-map-k8s-eviction-005 |
| *(excluded: deleted/out-of-scope)* | *2* | *N/A — drop* | *ansible-abc-imports-fix-001, beam-pipeline-builder-refac-001* |

### (B) roll-up

- **41 of 52 cheaply backfillable** (4 aggregate-only + 37 one-mode re-dispatch). No infra/gold blockers — every one has a valid mcp_only + hybrid run, so the repos build and the verifier runs.
- **11 archived** — exclude from the active baseline-coverage target, or un-archive first if they're wanted back.
- The whole gap is a **dispatch-coverage** gap (baseline mode skipped), not a capability or infra failure.

---

## Prioritized remediation plan

**P0 — trivial, do first (recovers signal for free):**
1. Aggregate the 4 already-run baselines (`config-drift-argocd-redis-ha-004`, `error-trace-k8s-nftables-sync-001`, `incident-inv-docker-shutdown-004`, `support-map-grafana-alerts-004`) from their existing `rep1..3` into `baseline/results.json`. No container, no agent.
2. Land the s58f EACCES fix (in progress on this branch) and re-run the 3 config-drift charts tasks (consul/redis/scdf) — their verifiers already score 1.00 on real output; this recovers both their masked checkpoints **and** their baseline rows.

**P1 — cheap verifier/env fixes (unmask ~27 tasks, mostly one-liners):**
3. `topological_order.py` / `check_topo_order.sh`: basename-normalize repo names → unmasks all 11 refactor tasks.
4. `check_related_issues.sh` (×14, identical): flatten answer.json + fuzzy basename match → unmasks 10 support-map tasks.
5. `check_dead_code.sh`: accept `e.get('file') or e.get('path')` (+ KeyError guard for dead-code-004) → unmasks 3.
6. `check_ownership.sh`: token/stem overlap instead of exact de-hyphenated bigram → unmasks 4.
7. `java_multi_repo.Dockerfile`: add `python3` → unmasks source_files/symbols (camel, ccx-053); fix camel's instruction↔checker output-path mismatch.

**P2 — gold corrections + backfill dispatch:**
8. Fix gold for `cal-err-requests-proxy-003` (required_files → `sessions.py`) and the calibration drift/expected_values invented-key golds (click, flask-config).
9. Re-dispatch `baseline` mode for the 37 active never-dispatched tasks (parallel, accounts 1–5). Restores the binding coverage constraint flagged in the klp summary.

**Exclude / defer:**
10. Drop the 2 deleted tasks from all accounting. Leave the 11 archived tasks out of the active baseline-coverage target unless intentionally un-archived. Spot-check the 3 unverified singletons during the P1 verifier pass.

**Net:** with P0+P1 (all low-risk, mostly mechanical), the benchmark recovers mode-separating signal on ~30 of 37 masked tasks and baseline rows on 7 tasks for ~zero agent cost; P2's 37-task baseline re-dispatch closes the coverage gap that currently caps the MCP-vs-baseline comparison at N=66.
