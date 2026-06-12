# Audit — Locked N=105 Run Set (invalid-run triage, run counts, model manifest)

**Bead:** EnterpriseBench-dph · **Date:** 2026-06-12 · **Mode:** read-only audit (no re-runs)
**Subject:** the locked MCP-vs-baseline aggregate from p6ux.28 (`results/analysis/mcp_vs_baseline_clean_summary.md`, commit 8529b4e): N=105, mean delta −0.093, median 0.0, better/tie/worse 24/52/29.
**Generator:** `results/analysis/audit_locked_runset.py` (reuses the exact clean-set selection from `aggregate_mcp_clean.py`, then triages every run record on disk).
**Machine-readable deliverables:** `docs/qa/locked_runset_dispositions.csv` (398 rows, one per physical run record) · `docs/qa/locked_runset_audit_summary.json`.
**Reproduce:** `python3 results/analysis/audit_locked_runset.py`

The audit reconstructs the locked numbers exactly (N=105, mean −0.0925, median 0.0, 24/52/29), so the dispositions below refer to precisely the runs behind the headline.

---

## Executive answers

1. **Triage audit:** A usable triage framework exists and the locked set is largely clean, but screening was **partial, not complete**. 140 of the 210 locked run records (67%) predate the s58f validity classifier (no `status` field) and were admitted by a weaker retroactive rule (`num_turns>0`). Within the locked set the audit finds **6 suspect runs in two groups**: (a) **2 budget-censored mcp_only runs** (hit the 50-turn ceiling, `failure_class=agent_error`, scored 0.0) that are **still counted** — they are the two single worst deltas in the locked table; (b) **4 baseline cells that are best-of-3 promotions** paired against single-run mcp_only — an aggregation asymmetry. **No infra-error, EACCES, OOM, ENOSPC, docker-daemon, or malformed-record artifact is counted in the locked set** — every such artifact found sits in already-excluded runs. Neither suspect group can flip the parity verdict; both currently bias *against* MCP (details §1.3–1.4).
2. **Run counts:** The locked headline is built on **one run per task × condition**. 108/125 tasks have exactly 3 run records (1 per mode); only **4/125 tasks have ≥3 runs per condition**; 112/125 have ≥3 total only because the three modes sum to 3. There is no replicate-based variance estimate behind the locked numbers.
3. **Model manifest:** Every run in the locked set was driven by **`claude-sonnet-4-6`** (all modes, confirmed by `agent_trace.jsonl` and the `modelUsage` block). However, **`claude-haiku-4-5-20251001`** appears as an auxiliary/subagent model with a strong mode skew: in locked **baseline** cells haiku produced **1.35M output tokens (46% of baseline output volume, 85/105 cells)** vs **28k tokens (3/105 cells)** in locked mcp_only cells. The two headline conditions are model-matched at the driver level but **not at the delegation level**.

---

## 1. Triage audit (per-run validity)

### 1.1 Does the triage framework exist?

Yes, in usable form, in two layers — but it arrived mid-campaign:

- **At-run-time classifier** (bead s58f): `scripts/orchestration/run_task.py` classifies agent exit codes (137→`infra_oom`, 124→`infra_timeout`, other≠0→`agent_error`), infra phases (`agent_infra_error`/`verifier_infra_error`), EACCES/MCP-config errors, and writes a `status` field (VALID / INVALID / FALLBACK) into `results.json`. Covered by `tests/test_infra_error_classification.py`.
- **At-aggregation-time rule** (`aggregate_mcp_clean.py:36-41`): excludes `num_turns==0` (NO-OP) and `status=INVALID`; runs lacking a `status` field are classed VALID if they have real turns.

The gap: **140/210 locked records are legacy-schema** (pre-s58f, no `status`) and were therefore never screened by the full run-time classifier — only by the retroactive `num_turns>0` rule. This audit closes that gap by re-screening all 398 run records against stderr signatures (EACCES/permission, OOM/SIGKILL, timeout, ENOSPC, docker-daemon), `failure_class`, infra phases, max-turn censoring, parse errors, missing checkpoints, and missing verifier artifacts.

### 1.2 Per-run disposition (locked set: 210 records → 105 pairs)

| Disposition | Count | Notes |
|---|---:|---|
| valid | 204 | no flags of any kind |
| suspect — budget-censored | 2 | §1.3, **counted in locked numbers** |
| suspect — best-of-3 promotion | 4 | §1.4, **counted in locked numbers** |

Checks that came back **clean across all 210 locked records**: 0 parse errors, 0 empty/malformed `results.json`, 0 missing verifier `output.json`, 0 missing checkpoint detail, 0 null scores, 0 infra phases, 0 EACCES/OOM/timeout/ENOSPC/docker stderr signatures. The EACCES hits (aspnetcore-code-review-001, all 3 modes) and harness-timeout hits (bustub-hyperloglog-impl-001) found on disk are all in runs **already excluded** from the locked set (deleted task / NO-OP). The full 398-row table including excluded and replicate runs is in `locked_runset_dispositions.csv`.

### 1.3 Finding A — 2 budget-censored runs are counted (headline-credibility flag)

| task | mode | score | evidence |
|---|---|---:|---|
| support-map-nodebb-notif-011 | mcp_only | 0.0 | 51 turns, 91 MCP calls, `errors:["Reached maximum number of turns (50)"]`, `failure_class=agent_error` |
| support-map-libreoffice-formula-007 | mcp_only | 0.0 | 51 turns, 95 MCP calls, same max-turns error, `failure_class=agent_error` |

Both agents ran fully (50+ turns, heavy MCP usage) and were cut off by the harness turn ceiling without emitting an answer, then scored 0.0. `run_task.py` records the non-zero agent exit as `failure_class=agent_error`, but the validity classifier still admits them (nodebb is even `status=VALID`). Whether max-turns exhaustion is "invalid" is a policy call — it is a *budget* limit, not an infra failure — but these two runs are precisely the **−2.000 and −1.810 rows that head the locked delta table**, so the call is material and must be explicit.

**Sensitivity (recomputed by the audit script):** dropping both → N=103, **mean −0.057** (from −0.093), median 0.0 unchanged, better/tie/worse 24/52/27. The parity verdict is robust either way; as counted, the censored runs *deepen* the anti-MCP mean. No baseline run in the locked set hit max turns (7 other max-turns runs exist on disk; all are in excluded/baseline-dead runs — see CSV).

### 1.4 Finding B — 4 baseline cells are best-of-3 promotions paired against single mcp_only runs

The p6ux.1 "aggregated baselines" were produced by copying the **best of 3 replicates** to the cell level (byte-identical `tool_usage`/`timing` confirm the copy). `aggregate_mcp_clean.py` reads only cell-level records, so for these 4 tasks the locked comparison is **best-of-3 baseline vs single-run mcp_only**:

| task | baseline cell (best-of-3) | baseline reps | mcp_only cell | mcp_only reps (unused) | locked delta |
|---|---:|---|---:|---|---:|
| config-drift-argocd-redis-ha-004 | 2.25 | 0.0 / 1.25 / **2.25** | 2.25 | 2.25 / 2.25 / 2.25 | 0.00 |
| error-trace-k8s-nftables-sync-001 | 3.00 | **3.0** / 3.0 / 2.75 | 3.00 | 3.0 / 2.88 / 3.0 | 0.00 |
| incident-inv-docker-shutdown-004 | 4.00 | **4.0** / 3.5 / 3.5 | 4.00 | 3.0 / 3.5 / 3.0 | 0.00 |
| support-map-grafana-alerts-004 | 2.94 | **2.94** / 2.94 / 2.88 | 1.90 | 2.57 / 2.26 / 2.57 | **−1.04** |

Material impact is confined to **support-map-grafana-alerts-004**: under symmetric best-of-3 the delta would be 2.57 − 2.94 = **−0.37**, not the −1.04 counted (the locked mcp_only cell is an older run that *underperforms all three of its own unused replicates*). The other three pairs happen to tie either way. Note the aggregation-choice sensitivity in argocd (baseline reps 0.0/1.25/2.25: mean-aggregation would give +1.08 *pro*-MCP) — best-of-N promotion is a consequential, currently asymmetric policy.

### 1.5 Verdict on Q1

No infra-invalid run is counted in the locked numbers. Two budget-censored runs and four asymmetrically-aggregated baselines **are** counted; both effects lean anti-MCP, so correcting them moves the mean from −0.093 toward −0.04 ± and cannot overturn the parity finding — they are caveats that belong next to the locked headline, plus one policy decision to make explicit (is max-turns exhaustion a valid 0.0?).

---

## 2. Run counts per task × mode

125 task dirs carry results (398 physical run records: 362 cell-level + 36 replicates). Distribution of **distinct runs per task** (promoted best-of-3 copies deduplicated):

| runs per task | tasks | composition |
|---:|---:|---|
| 2 | 13 | baseline missing (11 archived + 2 deleted tasks): 1× mcp_only + 1× hybrid |
| 3 | 108 | exactly 1 run per mode — the modal case |
| 11 | 4 | the replicated tasks (3 reps × 3 modes + independent mcp_only/hybrid cells) |

- **≥3 runs total:** 112/125 tasks — but only because three modes × 1 run each sums to 3.
- **≥3 runs per condition:** **4/125 tasks** (config-drift-argocd-redis-ha-004, error-trace-k8s-nftables-sync-001, incident-inv-docker-shutdown-004, support-map-grafana-alerts-004). The remaining 121 tasks have exactly 1 run (or 0) per condition.
- The locked aggregate reads **only cell-level records**: 1 baseline + 1 mcp_only per task. Replicates, where they exist, are not aggregated in (except as the §1.4 best-of-3 promotions). **The locked N=105 numbers therefore carry no within-task variance information**, and single-run noise at the task level is undamped — relevant when reading per-task deltas as task-level conclusions (cf. grafana-alerts: its mcp_only reps would tell a different story than its counted single run).

Full per-task table: `run_counts` in `locked_runset_audit_summary.json`.

---

## 3. Model manifest

**Driver model — `claude-sonnet-4-6` for every run in the locked set, all modes.** Confirmed two ways: `agent_trace.jsonl` model fields (107/121 baseline, 124/124 mcp_only, 124/124 hybrid cells; the only trace-less records are the deleted aspnetcore task and the 4 promoted baseline copies, whose replicates all confirm `claude-sonnet-4-6`), and the `modelUsage` block in every `agent_stdout.log`. No locked run was driven by any other model; modes do not differ at the driver level.

**Auxiliary model — `claude-haiku-4-5-20251001`, heavily mode-skewed.** The Claude Code harness delegates internal work (subagents/auxiliary calls) to haiku, and the volume is not negligible:

| locked cells | sonnet-4-6 output tokens | haiku-4-5 output tokens | cells with >1k haiku tokens |
|---|---:|---:|---:|
| baseline (105) | 1,588,675 | **1,354,832 (46% of mode volume)** | 85 |
| mcp_only (105) | 1,573,202 | 27,978 (1.7%) | 3 |

(Non-headline hybrid cells: 891k haiku tokens — same pattern as baseline.) Mechanically: baseline agents explore the workspace by fanning out filesystem subagents (billed to haiku), while mcp_only agents make Sourcegraph MCP calls in the main (sonnet) loop. Two consequences for how the locked headline is described:

1. The locked comparison is **sonnet+haiku-subagents vs sonnet-only**, not sonnet vs sonnet. The conditions are tool-different *and* delegation-different by construction.
2. Haiku accounts for ~28% of the measured dollar cost of locked cells, concentrated in baseline — any cost-per-quality framing of the locked runs must include it (haiku output tokens at haiku pricing, not sonnet pricing).

Per-run model breakdown: `models` + `model_output_tokens` columns in the dispositions CSV.

---

## Bottom line

The locked N=105 parity finding **survives this audit**: zero infra-invalid runs are counted, and the six suspect runs that are counted (2 budget-censored, 4 best-of-3-promoted) all bias the mean *against* MCP — removing or symmetrizing them moves the mean from −0.093 toward zero and leaves the median at 0.0. Three things should travel with the headline as caveats: (1) the max-turns-as-0.0 scoring policy for the two censored mcp_only runs; (2) single-run-per-condition design — no variance estimates, 4/125 tasks replicated; (3) the haiku delegation asymmetry, which makes "baseline" effectively a sonnet+haiku condition and matters for any cost framing.
