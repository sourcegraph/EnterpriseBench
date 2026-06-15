# Soundness `--strict` warning triage (EnterpriseBench-y50)

**Date:** 2026-06-15
**Input:** the 105 `--strict` risk warnings surfaced by `scripts/validation/verifier_mutation_test.py benchmarks/ --json --strict` (the informational gate wired into CI by bt6). 71 `gold_zero` (false-negative risk) + 34 `garbage_credit` (false-positive risk).
**Method:** for each cluster, the verifier(s) were read, the *correct* artifact was hand-constructed from `ground_truth.json` / `expected_solution.json`, and the **real** checkpoint verifier was run against it (`WORKSPACE=$tmp TASK_DIR=$task bash checks/<check>.sh`). A correct artifact scoring >0 ⇒ the `gold_zero` is a **renderer artifact** (the generic `build_gold_answer` rendering cannot emit the task-specific structured shape; the verifier is fine). A correct artifact still scoring 0 ⇒ a **real defect** (malformed ground truth, wrong artifact path, or a copy-paste/wrong-task verifier) — a true false-negative an honest agent would also hit.

> **HALT at branch-ready.** This is the triage + verdict; fixes are a follow-up. The real-defect count is material → **defensibility event, escalated to PL** (see bottom).

---

## Headline

- **`gold_zero` is NOT uniformly a measurement artifact.** 51 of 71 (72%) are renderer artifacts (the bt6 hypothesis holds for those), but **20 of 71 (28%) are REAL DEFECTS** — checkpoints where a genuinely-correct agent answer scores 0 on the live task. That is a true-FN / headline-credibility issue, not a harness quirk.
- **`garbage_credit` is overwhelmingly real FP inflation.** ~30 of 34 award credit to off-topic filler via file-existence/length floors, default-pass on absent ground truth, unanchored-substring `grep`, or an exit-code that cannot tell a failing test from a crashing one. A pure-filler submission banks **0.08–0.48 of total task weight** on affected tasks (two checkpoints at a full 1.00).

---

## `gold_zero` verdict by cluster (71 findings)

| Cluster | Findings | Renderer-artifact | Real-defect | Root cause of real defects |
|---|---|---|---|---|
| refactor / topological_order | 12 | 12 | 0 | — (correct ordering / `refactor_steps[]` → 1.0, proven) |
| dead-code (single + cal) | 13 | 13 | 0 | — (correct `dead_code_report.json` → 1.0, proven on dead-code-001) |
| dead-code (dual) | 8 | 4 | 4 | react/k8s: degenerate GT (`{description,repos_involved}`, no file/symbol pairs) + hardcoded `react/` path for a k8s task; angular/django-wagtail/spring-hibernate are pure-structural → renderer artifact |
| config-drift (incl. cal-drift) | 17 | 13 | 4 | dual-argocd-001 + dual-prometheus-001 `drift_points`/`expected_values` verifiers are byte-identical **consul** copies (hardcoded `serfLAN/serfWAN`, `charts/bitnami/consul`) |
| schema-evolution-dual | 9 | 1 | 8 | django-001 + sentry-001 verifiers are verbatim **Zulip** copies — grep Zulip tokens (`NamedUserGroup`, `realm_audit_log`) in a `zulip/SCHEMA_IMPACT.md` path that exists in neither repo set |
| event-replay-click-ci-001 | 4 | 0 | 4 | verifier reads `WORKSPACE="${1:-.}"` (positional `$1`), but `CheckpointRunner` passes WORKSPACE in the **env only** → collapses to `.` → `actions.jsonl` never found. **Caveat:** task also carries an `[events]` block routing to `action_scorer.py`; the `.sh` checkpoints may be vestigial in the live event-replay path |
| support-mapping / dep / api-contract / ccx-incident | 12 | 12 | 0 | — (instruction documents the exact `answer.json` keys the verifier reads; correct answer → 1.0) |
| **Total** | **71** | **51** | **20** | |

### Real defects, by failure mode (20 findings, 8 tasks)

1. **Copy-paste / wrong-task verifiers (14 findings)** — the verifier was cloned from another task and never re-pointed:
   - `schema-evolution-dual-django-001` (4), `schema-evolution-dual-sentry-001` (4): Zulip tokens + nonexistent `zulip/` path.
   - `config-drift-dual-argocd-001` (2), `config-drift-dual-prometheus-001` (2): consul `serfLAN/serfWAN` + `charts/bitnami/consul`.
   - `dead-code-dual-k8s-001` (2): hardcoded `react/dead_code_report.json` for a kubernetes/client-go task.
2. **Degenerate ground truth (overlaps k8s; react = 2 findings)** — `dead-code-dual-react-001` (and the GT half of k8s) ship a placeholder `dead_code: [{description, repos_involved}]` with no `{file,symbol}` pairs, so the precision/recall verifier's recall denominator is 0 → score 0 for any answer.
3. **Verifier↔runner interface bug (4 findings)** — `event-replay-click-ci-001` reads WORKSPACE positionally; the runner passes it via env. (See caveat above re: the `[events]` scorer.)

**Every one of these 20 is a true false-negative: an agent that produces the correct artifact, following the instruction, scores 0.**

---

## `garbage_credit` verdict (34 findings) — FP risk

| Pattern | Example findings | Verdict |
|---|---|---|
| Default-pass on absent ground truth → **1.00** | dead-code-dual `feature_flags` (k8s, react): GT has no `feature_flags` key → `1.0 "No feature flags in ground truth"`; config-drift `validate_corrected_config`/`config_valid` "corrected config absent → 1.0" (consul, scdf-rabbitmq-002, redis-password-003, argocd-redis-ha-004, dual-argocd) | **INFLATION** |
| Exit-code conflation → **1.00** | ansible `test_fails_on_buggy_code`: `pytest` crashing on an unknown arg / missing plugin exits non-zero, read as "test correctly fails on the bug" | **INFLATION** (most dangerous — rewards a non-test) |
| File-existence / file-length floors | chain-err `fix_applied` (0.6 for >50 bytes), `cycle_identified` (0.2 floor), ansible `regression_test_written` (0.2 floor), camel `architecture_analysis` (0.33 for ≥500 chars) | **INFLATION** |
| Unanchored-substring `grep` (filler word contains a keyword) | ceph `findings_identified` (`low`⊂elderf**low**er → 0.70), cortex `cortex_ring` (`ring`⊂cate**ring** → 0.50), tempo `remediation_proposal` (`in.*ring` → 0.50), rbac `bypass_and_remediation` (`sever`⊂**Sever**al → 0.25), chain-err `investigation_quality` (`file`⊂**file**d → 0.60), config-drift-dual-webpack `expected_values` (`pre`⊂**pre**ferred → 0.10) | **INFLATION** |
| Parallelism silent-pass (v1) → **1.00** | refactor-orch-001..008 `parallelism`: `has_section==false && n_gt_groups==0 → 1.0` default-passes the no-parallelism case (the tri-* v2 verifier caps the same case at 0.50) | **INFLATION (bounded)** — defensible on GT grounds but a silent-pass; harmonize v1→v2 |
| Structural floor (correct content required for full credit) | config-drift-dual-terraform / tri-kustomize `config_valid` (0.20 "drift_points missing" floor) | **DEFENSIBLE** |

**Magnitude.** Tasks use weighted-sum scoring with no explicit pass threshold. Garbage-floor (filler-only) task scores: chain-err **0.48**, ansible **0.38**, ceph **0.28**, dead-code feature_flags **0.25** each, cortex **0.15**, tempo **0.10**, camel **0.083**, rbac **0.075**. A no-op/filler answer banks an answer-independent baseline before any real work.

---

## Threats-to-validity language (drafts)

**gold_zero / false-negative.** *"A soundness sweep that renders the curated gold answer in the canonical artifact shape and runs every checkpoint verifier finds that 51/71 gold-zero warnings are artifacts of a generic renderer that cannot synthesize task-specific structured outputs (verifier is sound), but 20/71 (across 8 tasks) are real false-negatives: two schema-evolution-dual tasks and two config-drift-dual tasks ship verifier scripts copy-pasted from unrelated tasks (Zulip / consul tokens and paths), the dead-code-dual react/k8s ground truth is a placeholder lacking the file/symbol pairs its precision/recall verifier requires, and the event-replay checkpoints read the workspace path from a positional argument the runner never supplies. On these tasks a correct agent answer scores 0, so any per-arm score on them is uninterpretable; they are excluded from headline results pending verifier/GT repair."*

**garbage_credit / false-positive.** *"Many deterministic checkpoints grade free-text reports with unanchored grep substring matches, hardcoded file-existence/length floors, default-pass on missing ground truth, and an exit-code that cannot distinguish a failing regression test from a crashing one; an off-topic filler submission therefore earns 0.08–0.48 of total task weight (two checkpoints at a full 1.0). Reported scores carry a measurable answer-independent baseline, and small inter-arm deltas near that floor are not trustworthy."*

---

## Recommended follow-up (fixes — not done here, HALT honored)

1. **P1 / blocks headline use** — repair or exclude the 8 real-defect tasks:
   - Rewrite `schema-evolution-dual-{django,sentry}-001` verifiers against their actual repos (drop the Zulip tokens + `zulip/` path).
   - Rewrite `config-drift-dual-{argocd,prometheus}-001` verifiers (drop consul `serfLAN/serfWAN` + `charts/bitnami/consul`).
   - Fix `dead-code-dual-{react,k8s}-001` ground truth to carry `{file,symbol}` dead_code pairs, and the k8s artifact path.
   - Resolve `event-replay-click-ci-001` verifier↔runner WORKSPACE contract (or confirm the `[events]` scorer is the live path and retire the `.sh` checkpoints).
2. **P2 / FP inflation** — replace default-pass-on-absent-GT with a neutral/partial score; gate `validate_corrected_config` on a real corrected config not just report existence; fix `test_fails_on_buggy_code` to distinguish test-failure from pytest-crash; word-boundary-anchor the free-text `grep` checks; harmonize the v1 parallelism verifier to the v2 (0.50-cap) behavior.
3. Once 1+2 land, re-run the `--strict` sweep; the remaining warnings should be the ~51 renderer artifacts (harness limitation, not benchmark unsoundness), at which point the gold-rendering harness itself could be taught the structured shapes to drive the count toward zero.

---

## Escalation (per bead acceptance — defensibility event)

20/71 gold-zero warnings are confirmed **real false-negatives** (not renderer artifacts), concentrated in copy-paste verifiers and degenerate dual-task ground truth; `garbage_credit` is systemic FP inflation worth 0.08–0.48/task. This exceeds "a material fraction" → **reported to PL.** The hard soundness property from bt6 still holds (0 false positives with *no* agent output at all); these are FN/FP *risk* defects in specific tasks and verifiers, surfaced precisely because the `--strict` gate was wired. Recommend the affected 8 tasks be quarantined from any headline score until repaired.
