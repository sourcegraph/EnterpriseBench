# Blast-radius audit: cross-task verifier copy-paste contamination (EnterpriseBench-awa)

**Date:** 2026-06-15
**Follow-up to:** y50 (`results/analysis/soundness_strict_triage_2026_06.md`, branch-ready @ bd79d0a)
**Scope:** EVERY multi-repo task verifier (99 active dual/tri tasks) — not just the 71 `gold_zero` checkpoints y50 inspected.
**Method:** (1) mechanical scan of all ~400 `checks/*.sh` for foreign workspace-dir paths and byte-identical bodies across differing repo sets; (2) parallel semantic audit (6 agents) of every token-bearing verifier family, each verifier's hardcoded paths / grep tokens / symbols cross-checked against the task's own declared `[[repos]]`; (3) direct re-verification of every flagged hit at `file:line`; (4) overlap check against the locked N=105 run set and the MCP-vs-baseline contrast arm.

> **HALT at branch-ready.** This is the audit + verdict table; fixes are a separate follow-up.

---

## Headline

- **99 multi-repo tasks audited (84 dual + 15 tri). 7 are CONTAMINATED** (verifier copy-pasted from a different task/repo and never re-targeted), **1 has a related degenerate-GT defect**, **91 are CLEAN** (tokens match own repos, or verifier is generic/GT-driven).
- **2 of the 7 are NEW — invisible to y50.** `support-mapping-dual-flask-001` and `support-mapping-dual-grafana-001` hardcode **Envoy** circuit-breaker tokens (`max_connections`, `max_pending_requests`, `ConnPoolBase`, `circuit_breaking`) in `check_severity.sh`, copied from the single-repo Envoy task `support-mapping-001`. These award **0.6×severity partial credit** (gold>0-capable) while the mechanism gate is **permanently unreachable** for a correct Flask/Grafana answer — so they could never surface as `gold_zero`. **This is exactly the blast-radius class the bead predicted: a copy-paste verifier that scores >0 by luck and hides from the gold_zero sweep.**
- **CRITICAL CONTAINMENT RESULT: none of the 7 contaminated tasks (nor the degenerate-GT task) appear in the locked N=105 headline set or the MCP-vs-baseline contrast arm.** Every contaminated task is a `*-dual-*` task that was **never executed** in the MCP campaign (`results/runs/` contains zero of them). The only multi-repo tasks that were scored are 14 **tri-repo** tasks — **all audited CLEAN**. The contamination does **not** compound the defensibility event into the headline.

---

## Contaminated tasks (7) — per-task verdict

| # | Task | Declared repos | Contaminant | Foreign source | Evidence (file:line + token) | In y50? | In N=105 / MCP arm? |
|---|---|---|---|---|---|---|---|
| 1 | `schema-evolution-dual-django-001` | django, wagtail | Zulip | `schema-evolution` Zulip task | all 4 checks read `$WORKSPACE/zulip/SCHEMA_IMPACT.md`; `check_schema_change.sh:13-14` grep `0578.*deactivated`/`NamedUserGroup`/`models/groups.py`; `check_indirect_refs.sh:18` `realm_audit_log` | yes | **no** |
| 2 | `schema-evolution-dual-sentry-001` | sentry, relay | Zulip | byte-identical to #1 | all 4 checks byte-identical to django-001's Zulip verifiers (`zulip/SCHEMA_IMPACT.md` + Zulip tokens) | yes | **no** |
| 3 | `config-drift-dual-argocd-001` | argo-cd, argo-helm | consul | `config-drift-001` (single-repo consul) | `check_config_valid.sh:10` `CHART_DIR=$WORKSPACE/charts/bitnami/consul`; `check_drift_points.sh:14-39` grep `serfLAN`/`serfWAN`; `check_expected_values.sh:33-36` serf ports | yes | **no** |
| 4 | `config-drift-dual-prometheus-001` | prometheus, thanos | consul | byte-identical to #3 | same consul `charts/bitnami/consul` + `serfLAN`/`serfWAN`, byte-identical to argocd | yes | **no** |
| 5 | `dead-code-dual-k8s-001` | kubernetes, client-go | react | `dead-code-dual-react-001` template | all 3 checks hardcode `REPORT=$WORKSPACE/react/dead_code_report.json` (`check_dead_code.sh:10`, `check_evidence.sh:7`, `check_feature_flags.sh:7`) vs instructed `/workspace/dead_code_report.json`; report never found → score 0 (also GT-degenerate) | yes | **no** |
| 6 | `support-mapping-dual-flask-001` | flask, werkzeug | **Envoy** | **`support-mapping-001` (single-repo Envoy)** | **`check_severity.sh:29-35` hardcodes Envoy `MECHANISM_KEYWORDS` (`max_connections`,`max_pending_requests`,`ConnPoolBase`,`circuit_breaking`); `check_ownership.sh:4` stale "Envoy codebase" comment** | **NO (new)** | **no** |
| 7 | `support-mapping-dual-grafana-001` | grafana, prometheus | **Envoy** | byte-identical severity to #6 | **`check_severity.sh:29-35` same Envoy `MECHANISM_KEYWORDS`; GT `ownership_keywords` are grafana/prometheus — Envoy mechanism gate unreachable** | **NO (new)** | **no** |

### Scoring impact of the new (#6, #7) contamination
`check_severity.sh` scores `0.6·severity_score + 0.4·mechanism_cited`, and `passed` requires `severity≥0.6 AND mechanism_cited`. For a Flask/Grafana task a correct answer can **never** cite Envoy circuit-breaker params ⇒ `mechanism_cited=0` always ⇒ the checkpoint **can never pass** and is capped at `0.6·severity`. This is simultaneously a true **false-negative** (the 0.4 mechanism component is unwinnable) and **gold>0-capable** (partial 0.6·severity credit), which is why a `gold_zero`-only sweep (y50) was blind to it.

---

## Related defect (not foreign-token contamination)

| Task | Declared repos | Defect | Note |
|---|---|---|---|
| `dead-code-dual-react-001` | react, create-react-app | **degenerate ground truth** | `ground_truth.json .dead_code = [{description, repos_involved}]` — placeholder with no `{file,symbol}` pair, dropped by the verifier's NORM filter ⇒ TP=FN=0 always. `check_dead_code.sh:10` also reads `$WORKSPACE/react/...` mismatching the instruction, but `react` **is** this task's own repo, so the path is not *provably foreign* → classified SUSPECT/degenerate-GT, not copy-paste. Shares the `react/` template with #5 (k8s), evidencing a common react origin. |

---

## Clean families (91 tasks) — summary

All tokens match the task's own declared repos, OR the verifier is generic (keywords built dynamically from `ground_truth.json`, reads `$WORKSPACE/agent_output/answer.json`). Generic-shared byte-identical bodies across many tasks are **not** contamination.

| Family | Count | Verdict | Notes |
|---|---:|---|---|
| api-contract (dual+tri) | 9 | CLEAN | tokens on-domain. `api-contract-tri-protobuf-001/check_contract_source.sh:29` has a **stale comment** "repo == fastapi" but jq logic correctly selects `repo=="protobuf"` (cosmetic only). |
| dep-graph + dep-traversal | 13 | CLEAN | all on-domain; `dep-graph-dual-nextjs-webpack-001` is GT-driven. |
| incident-investigation (dual+tri) | 18 | CLEAN | all on-domain; `incident-investigation-dual-django-celery-001` fully GT-driven. |
| config-drift (non-consul dual+tri) | 7 | CLEAN | jaeger-otel, setuptools-pip, terraform, tokio-hyper, webpack-babel, javax-jakarta-spring, tri-kustomize — keys/values match own repos. |
| monorepo-boundary | 10 | CLEAN | babel/pnpm/next.js/rust packages all match own repo. |
| dead-code-dual (clean) | 3 | CLEAN | angular, django-wagtail, spring-hibernate. |
| refactor-dual + refactor-orchestration-tri | 5 | CLEAN | axum-tower, flask-werkzeug; **refactor-orchestration-tri-{babel,spring,tokio}-001** `check_repo_set.sh` tokens match own repos exactly. |
| schema-evolution (clean) | 2 | CLEAN | spring-flyway, tri-supabase (GT-driven). |
| error-prov-dual | 4 | CLEAN | netty-spring, otel-jaeger, tokio-tower, urllib3-requests — tokens on-domain. |
| err-provenance-dual | 5 | GENERIC-FINE | byte-identical generic 3-file set; keywords from GT `.required_files`/`.error_chain`. |
| support-mapping-dual (generic) | 21 | GENERIC-FINE | generic 3-file set (`check_error_chain`/`check_error_source`/`check_trigger_conditions`); fully GT-driven, zero hardcoded domain tokens (a few whitespace-only hash variants). |
| **Clean total** | **91** | | |

---

## Checkpoint 3 — overlap with locked N=105 and MCP-vs-baseline contrast

**Locked N=105 headline set** (`mcp_vs_baseline_clean_summary.md`, bead p6ux.28) is built from `results/runs/` (138 run dirs; 126 task dirs; N=105 scored with valid baseline+mcp_only, baseline>0).

- **Exact-name overlap of the 7 contaminated + 1 degenerate-GT tasks with the run set: ZERO.** `ls results/runs | grep -iE 'dual'` returns **no** `*-dual-*` task; every contaminated task is a dual-repo task that was never run.
- The only multi-repo tasks in the scored set are **14 tri-repo** tasks (`api-contract-tri-*`, `config-drift-tri-kustomize-001`, `dep-graph-tri-*`, `dep-traversal-tri-*`, `incident-investigation-tri-containerd-001`, `refactor-orchestration-tri-{babel,spring,tokio}-001`, `schema-evolution-tri-supabase-001`) — **all 14 audited CLEAN.**
- **MCP-lift contrast top-10 EB** (`mcp_lift_task_selection.md`): contains **no** contaminated task. Its support/grafana picks are `support-mapping-003` (single-repo) and `dead-code-003`, `support-map-grafana-import-003` — distinct from the contaminated `support-mapping-dual-{flask,grafana}-001`.

**Conclusion: the contamination does NOT touch the N=105 locked headline set and does NOT touch the MCP-vs-baseline contrast arm.** The defensibility event is **contained** to the unscored dual-repo task population. It does **not** compound the headline numbers or tool-ranking. → No emergency PL escalation required on overlap grounds; the 2 new contaminations are nonetheless a material benchmark-soundness finding for the repair backlog.

---

## Recommended follow-up (fixes — NOT done here, HALT honored)

1. **New (P1 for the dual-repo arm):** re-target `support-mapping-dual-flask-001` and `support-mapping-dual-grafana-001` `check_severity.sh` — replace the hardcoded Envoy `MECHANISM_KEYWORDS` with task-appropriate mechanism tokens (or drive them from `ground_truth.json`); remove the stale "Envoy codebase" comment in their `check_ownership.sh`.
2. **Carry forward y50's repairs** for the 5 previously-known contaminated tasks (#1–#5) + the degenerate-GT `dead-code-dual-react-001`.
3. **Quarantine** all 7 contaminated + 1 degenerate-GT tasks from any future headline/contrast use until repaired. (Already satisfied for the *current* N=105 set, which excludes them by construction.)
4. **Cosmetic:** fix the stale `repo == fastapi` comment in `api-contract-tri-protobuf-001/check_contract_source.sh:29` (logic is correct).

---

## Escalation

- **Net-new defects found by this blast-radius audit: 2** (`support-mapping-dual-flask-001`, `support-mapping-dual-grafana-001`), both Envoy-token copy-paste, both gold>0-capable and therefore missed by the prior `gold_zero` sweep — confirming the bead's hypothesis that the true blast radius exceeded the 8 gold-zero-confirmed.
- **Containment is the key result for PL:** the contamination is **entirely outside** the locked N=105 set and the MCP-vs-baseline arm; the headline parity result and tool-ranking are **unaffected**. The defensibility event does **not** compound.
- HALT at branch-ready; fixes are a separate follow-up.
