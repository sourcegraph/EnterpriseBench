# Multi-repo verifier contamination — blast-radius audit (EnterpriseBench-awa)

**Date:** 2026-06-15
**Follow-up to:** y50 (`results/analysis/soundness_strict_triage_2026_06.md`), which confirmed 8 dual-repo checkpoints on 5 tasks have verifiers copy-pasted from unrelated tasks. y50 only inspected the 71 `gold_zero` checkpoints — but a verifier copy-pasted from the wrong task can still score gold>0 *by luck* and never appear as `gold_zero`. This audit sizes the true blast radius.
**Scope:** every multi-repo task verifier — 129 tasks (dual / tri / multi / monorepo strata), all `checks/*.sh`.
**Method:** reproducible scan, `scripts/validation/verifier_contamination_scan.py`, three independent detectors:
1. **Foreign repo-path reference** — a verifier reads `$WORKSPACE/<dir>/…` where `<dir>` is not one of the task's declared `[[repos]]` paths and not a generic output dir.
2. **Byte-identical verifier body shared across tasks with *different* repo sets** — the copy-paste signature.
3. **Foreign repo-name token** — a distinctive repo name appearing as a whole word in a verifier that is neither the task's own repo nor in its prompt/ground-truth context.

Every hit was then **read and classified by hand** (a shared body / token is only a defect if the *active scoring code* — not a comment — is bound to the wrong task).

> **HALT at branch-ready.** Audit + verdicts only; fixes are tracked in `EnterpriseBench-ose`.

---

## Headline

- **No new scoring-broken tasks beyond y50's set.** The confirmed wrong-task contamination is the **same 5 tasks** y50 found (schema-evolution-dual django/sentry, config-drift-dual argocd/prometheus, dead-code-dual k8s). The scan found no additional task whose active scoring is bound to the wrong repo.
- **But the per-checkpoint blast radius is ~21% larger than `gold_zero` showed.** Within those 5 tasks, **17 checkpoints are contaminated, not the 14** `gold_zero` flagged: `config-drift-dual-argocd-001 config_valid`, `config-drift-dual-prometheus-001 config_valid`, and `dead-code-dual-k8s-001 feature_flags` are all bound to the wrong task **yet scored >0 by luck** (default-pass branches), so they surfaced only as `garbage_credit`, never as `gold_zero`. This is exactly the blind spot the bead predicted.
- **The contamination does NOT touch the locked N=105 headline set or the MCP-vs-baseline contrast arm.** All 5 contaminated tasks (and the 4 related dead-code/schema duals) are **un-run** — 0 run dirs each, all in the 102-task un-run backlog. They never entered `aggregate_mcp_clean.py` / the p6ux.28 locked numbers, and none appear in the 20-task MCP-lift selection (whose multi-repo entries use only sound/generic verifiers). **The contamination does not compound the y50 defensibility event into any published result.**
- **Forward risk only:** the 5 tasks sit in `configs/sweep_manifest_p6ux.json` and the un-run backlog. If scored as-is they *would* corrupt those results — they must be repaired before being run (covered by `ose`).

---

## Tier 1 — Confirmed wrong-task contamination (active scoring broken) — 5 tasks, 17 checkpoints

| Task | Declared repos | Contaminated checkpoints | Wrong-task source | Signature | In N=105 / MCP-arm? |
|---|---|---|---|---|---|
| schema-evolution-dual-django-001 | django, wagtail | direct_refs, indirect_refs, schema_change, test_impact (4) | a **Zulip** db-schema task | reads `$WORKSPACE/zulip/SCHEMA_IMPACT.md`; greps Zulip tokens | no — un-run |
| schema-evolution-dual-sentry-001 | sentry, relay | direct_refs, indirect_refs, schema_change, test_impact (4) | same Zulip task (byte-identical to django) | `zulip/` path + tokens | no — un-run |
| config-drift-dual-argocd-001 | argo-cd, argo-helm | drift_points, expected_values, **config_valid** (3) | a **consul** drift task | reads `$WORKSPACE/charts/…`; greps `serfLAN/serfWAN`, `consul` | no — un-run |
| config-drift-dual-prometheus-001 | prometheus, thanos | drift_points, expected_values, **config_valid** (3) | same consul task | `charts/` + `serfLAN/serfWAN` | no — un-run |
| dead-code-dual-k8s-001 | kubernetes, client-go | dead_code, evidence, **feature_flags** (3) | the **react** dead-code task | reads `$WORKSPACE/react/dead_code_report.json` | no — un-run |

**Bold checkpoints** (`config_valid` ×2, `feature_flags`) are the **3 y50 missed**: they are bound to the wrong task but their verifiers have a default-pass branch ("corrected config absent → 1.0" / "no feature_flags in GT → 1.0") that returns >0 on any input, so they registered as `garbage_credit` (FP inflation) rather than `gold_zero` (FN). They are doubly defective: wrong-task **and** default-pass.

### Related defect (not foreign-repo contamination) — carried from y50

- **dead-code-dual-react-001** — its verifier reads its *own* `react/` path (it is the *source* of the k8s copy, not foreign), but `ground_truth.dead_code` is a degenerate placeholder (`{description, repos_involved}`, no `{file,symbol}` pairs) → unscorable. Un-run. Same fix bead (`ose`).

---

## Tier 2 — Cosmetic copy-paste residue (stale comment, active scoring CORRECT) — 2 tasks

The token scan surfaced two verifiers whose **comments** name a foreign task, but whose **active code is correctly re-targeted** to the task's own ground truth:

| Task | Verifier | Stale comment | Active scoring (correct) | Verdict |
|---|---|---|---|---|
| support-mapping-dual-flask-001 | check_ownership.sh, check_severity.sh | "ownership terms from the **Envoy** codebase" | reads `.ownership_keywords` from *this task's* `ground_truth.json` | LEGIT — comment cleanup only |
| api-contract-tri-protobuf-001 | check_contract_source.sh | "gt_files: … repo == **fastapi**" | active filter is `select(.repo == "protobuf")` (a declared repo; GT has those entries) | LEGIT — comment cleanup only |

These are **not** scoring defects — low-severity comment hygiene. (api-contract-tri-protobuf-001 has 1 run dir; its score is valid because the active code is correct.)

---

## Tier 3 — Legitimately shared generic verifiers (deliberate reuse, NOT contamination)

The byte-identical scan flagged many groups that are **intentionally shared, fully GT-parameterized** verifiers — sharing a body across repo sets is correct here, because the body reads each task's own `ground_truth.json` / `answer.json`:

| Shared verifier | Tasks | Why it is fine |
|---|---|---|
| `file_extraction` port (check_context_mapping / drift_points / transform_mapping / root_cause / fix) | 6 | matches agent `source_files` against `ground_truth.required_files` (suffix policy); no hardcoded repo |
| check_error_chain / check_error_source | err-provenance-dual ×6 (+ error-trace-k8s) | extracts keywords from `ground_truth.error_chain` dynamically |
| check_topo_order | refactor-orch ×6 + tri ×3 = 9 | reads `REFACTOR_PLAN.md` + `ground_truth.dependency_graph`; the `cobra`/`grpc-go` token hits are **comment examples** (false positives), verified against tri-tokio which has neither yet scores correctly |
| check_parallelism (v1 + v2) | refactor-orch ×6 + tri ×3 | reads `ground_truth.parallelizable_steps` dynamically (v1's default-pass is a separate FP-inflation note from y50, not contamination) |
| dead-code structural checks (check_dependency_trace / removal_impact) | dead-code-dual django-wagtail, spring-hibernate | pure-structural `answer.json` field checks; no foreign repo reference |

Token-scan false positives also include `incident-investigation-quad-containerd-001` ("components" — an English word in a printf) and `dep-traversal-009` ("awscli" — a domain-legitimate AWS-SDK-family keyword alongside boto3/botocore).

---

## Answer to the bead's critical question (#3): does this touch the headline / tool ranking?

**No.**

- **Locked N=105 headline set:** the N=105 numbers are generated by `aggregate_mcp_clean.py` from `results/runs`. All 5 contaminated tasks have **0 run dirs** (verified) and sit in the 102-task un-run backlog → they are structurally absent from the locked aggregate.
- **MCP-vs-baseline contrast arm:** the 20-task MCP-lift selection (`results/analysis/mcp_lift_task_selection.md`) contains **none** of the 5. Its multi-repo entries — refactor-orch-001, refactor-orchestration-tri-tokio-001, config-drift-tri-kustomize-001, dead-code-003, api-contract-grpc-* — all use sound or generic (GT-parameterized) verifiers per Tier 3 / y50. Tool ranking is unaffected.

**Conclusion:** the contamination is a **latent defect in un-scored tasks**, not a corruption of any published result. It does **not** compound the y50 defensibility event into the headline or the MCP contrast. The only live consequence is forward: these tasks must be repaired before they are ever scored.

---

## Recommended follow-up (fixes — not done here; HALT honored)

1. **Folds into `EnterpriseBench-ose`** (the y50 repair bead). Add the **3 newly-identified contaminated checkpoints** to its scope so the fix is complete, not gold_zero-only:
   - `config-drift-dual-argocd-001 :: config_valid`, `config-drift-dual-prometheus-001 :: config_valid` (consul copy + default-pass).
   - `dead-code-dual-k8s-001 :: feature_flags` (react copy + default-pass).
2. **P3 comment hygiene** (no scoring impact): drop the stale "Envoy codebase" / "repo == fastapi" comments in `support-mapping-dual-flask-001` and `api-contract-tri-protobuf-001`.
3. **Prevention:** wire `scripts/validation/verifier_contamination_scan.py` into CI as an informational check (sibling to the bt6 soundness gate) so a verifier that references a repo dir/token outside its task's declared set is caught at authoring time. Gate on the **foreign-path** detector (high-precision, zero false positives in this run); keep the token detector informational (it has benign comment/domain hits).
