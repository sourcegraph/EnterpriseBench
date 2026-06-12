# MCP-Lift Study — Phase 2 Report: Sonnet 4.6 + SG-MCP vs Fable 5 (no MCP)

**Bead:** EnterpriseBench-323 · **Date:** 2026-06-12 · Separate from the locked N=105 headline (does not reopen it).
**Selection:** `results/analysis/mcp_lift_study_selection.md` (Phase 1) — top-10 EB + top-10 CSB tasks chosen for maximal expected MCP lift.
**Reproduce:** `python3 results/mcp_lift_study/analyze.py` (emits `study_results.json` + `study_aggregates.json`).

## Design

| | Condition A | Condition B |
|---|---|---|
| Model | `claude-sonnet-4-6` | `claude-fable-5` (frontier) |
| Tooling | Sourcegraph MCP only (no local source in workspace) | Baseline local tools (full checkout, no MCP) |
| EB harness | `run_task.py --mode mcp_only` | `run_task.py --mode baseline` |
| CSB harness | harbor `mcp-remote-direct` (Dockerfile.sg_only) | harbor `baseline-local-direct` |

Model and tooling vary **together by design**: the question is whether a cheaper model with code-intelligence retrieval competes with a frontier model without it. Quality is normalized reward 0–1 (EB: `task_score / Σ checkpoint weights`; CSB: harbor reward). Costs are Claude-Code-reported `cost_usd` per run. 1 trial per task × condition; the portfolio is the unit of inference.

## Headline

**Across all 20 pairs: quality is near-parity (A 0.548 vs B 0.626, Δ −0.08; wins 9/4/7 for A/tie/B) — but Condition A costs 46% less in dollars ($20.05 vs $36.98), giving a cost-per-quality-point of $1.83 vs $2.96 (38% cheaper per point).**

The two benches split sharply:

| bench | n | mean A | mean B | Δ(A−B) | A-win/tie/B-win | cost A | cost B | $/quality A | $/quality B | time A | time B |
|---|---|---|---|---|---|---|---|---|---|---|---|
| EB | 10 | 0.468 | 0.748 | **−0.280** | 1/3/6 | $13.34 | $26.53 | $2.85 | $3.55 | 4625s | 3378s |
| CSB | 10 | 0.629 | 0.503 | **+0.125** | 8/1/1 | $6.71 | $10.46 | $1.07 | $2.08 | 1402s | 1238s |
| ALL | 20 | 0.548 | 0.626 | −0.077 | 9/4/7 | $20.05 | $36.98 | $1.83 | $2.96 | 6027s | 4616s |

- **On CSB org tasks (discovery-heavy, cross-repo), Sonnet+MCP beats Fable-baseline 8/1/1** at half the cost-per-quality-point — the cheaper-model-with-retrieval thesis holds on the task profile MCP was built for.
- **On EB tasks, Fable-baseline wins 6/1** (3 ties). EB's `mcp_only` removes local source entirely; the deficit concentrates in tasks needing deep multi-file reading or artifact production within turn budget (ansible-galaxy −1.0: Sonnet+MCP exhausted its 50-turn budget; support-map-grafana −0.84; refactor-tri-tokio −0.67). Fable's raw capability with a full local checkout dominates there.

## Information-retrieval metrics

Ground-truth file recall computed from full transcripts vs each task's `ground_truth.json`/`expected_solution.json` (file-level GT exists for 18/20 tasks; the two repo-level-GT tasks — refactor-orch-001, dep-traversal-010 — are excluded). Baseline agents search/read through `Bash` (grep/rg/cat); the classifier parses Bash commands so condB counts are comparable to condA's named tools.

| metric (mean over pairs) | Cond A (sonnet+MCP) | Cond B (fable, no MCP) |
|---|---|---|
| GT files *seen* in any tool result (lenient) | 0.859 | 0.816 |
| GT files explicitly *read/fetched* | 0.480 | 0.397 |
| tool calls per task | 37.6 | 13.7 |
| search calls per task | 17.9 | 4.2 |

Both conditions converge on the relevant files at broadly similar rates; the difference is *how*. The MCP condition spends 2.7× the tool calls and 4× the search calls to get there — deliberate, query-attributable retrieval — while Fable reaches comparable ground-truth coverage with a third of the tool traffic by reading the local checkout directly (on CSB its explicit gt-read recall, 0.50, actually edges out A's 0.43). Retrieval volume does not convert into reward: config-drift-tri-kustomize burned 52 searches in condition A and scored 0.0 in both conditions. Per-bench split: on EB, A's gt-read lead is real (0.54 vs 0.27) yet B still wins on reward — finding files was not the bottleneck; *using* a full local context was.

## Per-task results

| bench | task | A reward | B reward | Δ(A−B) | A time s | B time s | A $ | B $ | A tools | B tools | A gt-read | B gt-read |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EB | ansible-galaxy-tar-regression-prove-001 | 0 | 1 | -1 | 1331 | 334 | 3.03 | 2.3 | 71 | 17 | 0.667 | 0.333 |
| EB | refactor-orch-001 | 0.333 | 0.333 | +0 | 230 | 197 | 0.71 | 1.33 | 33 | 14 | — | — |
| EB | monorepo-boundary-pnpm-lockfile-005 | 0.89 | 1 | -0.11 | 323 | 341 | 0.96 | 2.78 | 49 | 18 | 0.182 | 0.227 |
| EB | dead-code-003 | 0.785 | 0.74 | +0.045 | 285 | 483 | 1.07 | 4.98 | 42 | 39 | 1 | 1 |
| EB | support-map-grafana-import-003 | 0 | 0.843 | -0.843 | 908 | 691 | 2.79 | 6.4 | 70 | 8 | 0.833 | 0 |
| EB | api-contract-grpc-metadata-001 | 0.875 | 0.938 | -0.063 | 238 | 226 | 0.72 | 1.68 | 27 | 17 | 0.273 | 0.273 |
| EB | api-contract-grpc-transport-003 | 0.71 | 0.875 | -0.165 | 309 | 276 | 0.79 | 1.8 | 32 | 20 | 0.182 | 0.182 |
| EB | refactor-orchestration-tri-tokio-001 | 0.333 | 1 | -0.667 | 247 | 231 | 0.76 | 1.54 | 35 | 8 | 1 | 0 |
| EB | dep-traversal-010 | 0.75 | 0.75 | +0 | 233 | 189 | 0.7 | 0.86 | 35 | 23 | — | — |
| EB | config-drift-tri-kustomize-001 | 0 | 0 | +0 | 521 | 409 | 1.83 | 2.86 | 78 | 10 | 0.167 | 0.167 |
| CSB | ccx-vuln-remed-014 | 0.667 | 0 | +0.667 | 212 | 178 | 0.94 | 1.31 | 28 | 15 | 0.667 | 0 |
| CSB | ccx-vuln-remed-135 | 0.591 | 0.196 | +0.395 | 67 | 126 | 0.27 | 0.95 | 8 | 12 | 0 | 0.545 |
| CSB | ccx-vuln-remed-126 | 0.764 | 0.722 | +0.042 | 364 | 118 | 1.54 | 0.98 | 76 | 13 | 0.889 | 0.889 |
| CSB | ccx-crossorg-217 | 0.643 | 0.1 | +0.543 | 80 | 96 | 0.37 | 1.12 | 10 | 9 | 0.333 | 0.278 |
| CSB | ccx-crossorg-288 | 0 | 0.862 | -0.862 | 164 | 178 | 1.04 | 1.49 | 43 | 9 | 0.5 | 0.1 |
| CSB | ccx-migration-274 | 1 | 0.891 | +0.109 | 140 | 139 | 0.69 | 1.11 | 34 | 11 | 1 | 1 |
| CSB | ccx-migration-289 | 0.661 | 0.594 | +0.067 | 161 | 153 | 0.74 | 1.12 | 34 | 8 | 0.5 | 0.429 |
| CSB | ccx-config-trace-010 | 1 | 1 | +0 | 44 | 28 | 0.2 | 0.31 | 8 | 3 | 0 | 0.5 |
| CSB | ccx-agentic-223 | 0.778 | 0.5 | +0.278 | 88 | 106 | 0.41 | 0.91 | 23 | 7 | 0 | 0.778 |
| CSB | ccx-incident-145 | 0.182 | 0.167 | +0.015 | 81 | 117 | 0.52 | 1.16 | 15 | 12 | 0.444 | 0.444 |

## Run validity (infra-error triage applied)

All 20×2 final runs are VALID with `num_turns > 0`. Quarantined and re-run before counting (artifacts in `results/mcp_lift_study/quarantine/`):

- **5 runs hit OAuth 429 five-hour session limits** (resets 13:10 EDT): EB refactor-orch-001-B, api-contract-grpc-transport-003-A/B, ansible-galaxy-A (compounded with timeout), CSB ccx-agentic-223-A. All re-run post-reset on healthy accounts.
- **ansible-galaxy condA timed out twice at the standard 1800s** (`infra_timeout`, agent actively working: 58 tool calls). Final attempt used **2700s** (the only deviation from standard limits); the agent then *completed* in 1331s but exhausted its 50-turn budget and scored 0.0 — a genuine capability/budget outcome, recorded as such. Sensitivity: under a strict-1800s rule this pair drops and EB becomes n=9, Δ −0.20 (1/3/5) — direction unchanged.
- CSB condA's first three launch attempts failed before any agent ran (environment drift, see below) — no agent results discarded.

## Caveats

1. **n=1 per cell.** Single trials; per-task deltas are noisy. Aggregate direction (CSB +, EB −, cost A ≪ B) is the takeaway, not individual task deltas.
2. **Model+tooling confounded by design** — this is a deployment-choice comparison, not an MCP ablation (the locked N=105 same-model ablation found parity).
3. **Phase-1 selection used historical mcp-favorable deltas; regression to the mean is visible** (e.g. EB picks chosen for positive locked deltas mostly tied or flipped negative under the new condition pairing; CSB rescue pattern on ccx-vuln-remed-014 replicated exactly: 0.0 baseline → 0.667 with MCP).
4. **Cost is subscription-metered `cost_usd`** as reported by Claude Code; Fable per-token pricing is embedded in that figure.
5. **EB `mcp_only` removes local source** — A's EB deficit conflates "MCP instead of local files" with "weaker model"; hybrid (local+MCP) was out of scope here.

## Environment fixes required for the CSB side (recorded for reproducibility)

The CSB harness had drifted since its April campaign; local-state-only fixes (no CSB repo changes committed):

1. `benchmarks/tasks -> .` symlink restored (harness expects `benchmarks/tasks/<suite>/<task>`).
2. harbor pinned to **0.1.45** in `~/projects/CodeScaleBench/.venv` (system harbor 0.3.0 rejects CSB's `task.toml` schema: requires `task.name`). **The venv must be on PATH at launch** (`export PATH="$HOME/projects/CodeScaleBench/.venv/bin:$PATH"`) — `harbor` otherwise resolves to the user-site 0.3.0 and every job crashes with a TaskGroup ExceptionGroup (re-learned the hard way in the dse follow-on; that launch is quarantined in `runs/mcp_lift_study/_quarantine_badharbor_172020/`).
3. `PYTHONUSERBASE=/home/ds/.local` and `DOCKER_CONFIG=/home/ds/.docker` exported at launch — per-account `HOME` switching otherwise breaks Python user-site imports and the docker compose CLI plugin.
4. `scripts/evaluation/extract_task_metrics.py` run with `PYTHONPATH=$PWD/scripts:$PWD` (its self-inserted sys.path points at `scripts/evaluation`, but `csb_metrics` lives in `scripts/`).

## Bottom line

1. **The cheap-model-plus-MCP configuration is the better dollar-for-quality deal overall** ($1.83 vs $2.96 per quality point; 46% lower total cost at −0.08 mean quality).
2. **Where the work is discovery across org/cross-repo surfaces (CSB org suites), Sonnet+MCP also wins on raw quality** (8/1/1, +0.125) — cheaper *and* better there.
3. **Where the work needs deep local-context reading and long artifact production (EB suite), the frontier model with a local checkout still wins on quality** (6/1, +0.28) — if quality is the only objective on such tasks, pay for the frontier model.
4. MCP measurably changes retrieval *behavior* (4× the search calls, 2.7× the tool calls) but not retrieval *outcomes* (ground-truth coverage is similar across conditions) — and retrieval volume does not convert 1:1 into task reward.

---

# Addendum — CSB 3-arm deepening at n=3 (EnterpriseBench-dse)

**Date:** 2026-06-12 (same day, follow-on) · **Scope:** CSB only — the 10 CSB tasks above. EB numbers in the main report are unchanged (still n=1).
**What changed:** +2 reps on Arm A (Sonnet 4.6 + SG-MCP) and Arm B (Fable 5 baseline) → n=3; **new Arm C (Sonnet 4.6 baseline, no MCP)** at n=3. 90 valid trials total (70 new). This kills the n=1 caveat for CSB and de-confounds model from tooling: **A−C isolates the MCP effect at fixed model; C−B isolates the model effect at fixed tooling.**
**Reproduce:** `python3 results/mcp_lift_study/analyze.py` (now multi-trial + 3-arm aware; per-trial rows in `study_results.json`, aggregates in `study_aggregates.json` under `csb_3arm`).

## Headline (10 tasks × 3 arms × n=3)

| arm | model | tooling | mean reward | mean $/task (logged) | mean $/task (recomputed) | $/quality-pt | mean time |
|---|---|---|---|---|---|---|---|
| **A** | sonnet-4-6 | SG-MCP (no local source) | **0.689** | $0.74 | $0.74 | **$1.08** | 166s |
| **B** | fable-5 | baseline (local checkout) | 0.511 | $1.03 | $1.03 | $2.02 | 117s |
| **C** | sonnet-4-6 | baseline (local checkout) | 0.545 | $0.69 | $0.71 | $1.27 | 203s |

- **MCP effect (A−C, same model): +0.144 mean reward, 6 wins / 2 ties / 2 losses.** On these discovery-heavy CSB org tasks, Sourcegraph MCP lifts Sonnet's quality by ~14 points of normalized reward at essentially equal dollar cost and *less* wall-clock than baseline-sonnet. The n=1 finding survives replication and is now attributable to tooling, not model choice.
- **Model effect (C−B, same tooling): +0.034, 5/2/3 — parity.** Fable-baseline does not outperform Sonnet-baseline on these tasks; the frontier model buys nothing here while costing 1.5× more per task ($1.03 vs $0.69).
- **Deployment comparison (A−B): +0.178, 7/2/1** — strengthened vs the n=1 snapshot (+0.125, 8/1/1) and now at half the cost per quality point ($1.08 vs $2.02).

## Per-task (mean reward per cell, n=3 each)

| task | A | C | B | A−C (MCP) | C−B (model) | A $ | C $ | B $ |
|---|---|---|---|---|---|---|---|---|
| ccx-agentic-223 | 0.625 | 0.475 | 0.537 | +0.151 | −0.062 | 0.56 | 0.46 | 0.99 |
| ccx-config-trace-010 | 1.000 | 1.000 | 1.000 | +0.000 | +0.000 | 0.18 | 0.09 | 0.46 |
| ccx-crossorg-217 | 0.695 | 0.471 | 0.104 | +0.224 | +0.367 | 0.46 | 0.38 | 1.07 |
| ccx-crossorg-288 | 0.735 | 0.708 | 0.833 | +0.027 | −0.125 | 1.32 | 0.93 | 1.45 |
| ccx-incident-145 | 0.172 | 0.177 | 0.172 | −0.005 | +0.005 | 0.55 | 0.44 | 1.19 |
| ccx-migration-274 | 1.000 | 1.000 | 0.928 | +0.000 | +0.072 | 0.94 | 0.55 | 1.09 |
| ccx-migration-289 | 0.681 | 0.639 | 0.574 | +0.042 | +0.065 | 0.85 | 0.72 | 1.32 |
| ccx-vuln-remed-014 | 0.611 | 0.000 | 0.000 | **+0.611** | +0.000 | 1.02 | 2.49 | 0.96 |
| ccx-vuln-remed-126 | 0.759 | 0.777 | 0.735 | −0.018 | +0.042 | 1.34 | 0.34 | 0.84 |
| ccx-vuln-remed-135 | 0.611 | 0.204 | 0.231 | **+0.407** | −0.027 | 0.23 | 0.54 | 0.96 |

The MCP lift concentrates where the n=1 report predicted: vulnerability usage-site discovery (vuln-remed-014: both baselines dead at 0.0 across all 6 trials while MCP scores 0.611±; vuln-remed-135: +0.407) and cross-org discovery (crossorg-217: +0.224). Tasks both baselines already solve (config-trace-010, migration-274) tie at 1.0 — no headroom.

## Cost accounting — logged vs recomputed (the dual-accounting addendum)

Per-run cost is reported two ways: **logged** (Claude Code's `cost_usd`) and **recomputed** from per-model token counts at authoritative per-MTok prices (platform.claude.com pricing, 2026-06-12: sonnet-4-6 $3 in / $15 out / $6 cache-write-1h / $0.30 cache-read; fable-5 $10 / $50 / $20 / $1; haiku-4-5 $1 / $5 / $2 / $0.10).

- Recomputation from the transcript's per-model `modelUsage` reproduces logged cost **exactly** for pure single-model runs (arms A and B: $0.744 vs $0.744, $1.034 vs $1.034) — Claude Code's `cost_usd` is list-price-accurate, not subscription-discounted.
- **Caveat found while reconciling:** `task_metrics.json` token fields cover the **driver model only**. Baseline sonnet runs delegate substantially to haiku subagents (same pattern as the locked-set audit), so a token-field-only recompute understates arm C cost by up to 45% per run. `analyze.py` therefore recomputes from the transcript's full per-model usage; the residual arm-C gap (logged $0.693 vs recomputed $0.711, 2.6%) traces to 429-retry remnants. **Any token-based cost analysis on these runs must use per-model usage, not the task_metrics totals.**

## Run validity (triage applied before counting)

All 90 counted trials: reward non-null, output tokens > 0, `agent_return_code == 0`, arm-pure models. Excluded and visible in `study_results.json`:

- **14 INVALID trials** — 429 five-hour session-limit casualties (instant death, 0 output tokens; concentrated on whichever account a fast task landed on) and 2 full-length runs that exited non-zero. Every casualty was re-run to quota.
- **1 MODEL_MIXED trial** (new triage class): a Fable-arm trial with 2.3k tokens of **claude-opus-4-8** usage — the transcript shows an explicit `model_refusal_fallback` event (`original_model: claude-fable-5 → fallback_model: claude-opus-4-8`), i.e. Claude Code silently swaps models on refusal (a second trial showed the same pattern but was already INVALID). Arm purity is now machine-checked: a trial counts only if its transcript shows the arm's driver model plus at most haiku auxiliary usage.

## Bottom line (supersedes the CSB bullet of the main report)

1. **MCP lift on discovery-heavy CSB tasks is real and model-independent: +0.14 mean reward at fixed model (6/2/2), replicated at n=3.** It is tooling, not model: the same Sonnet without MCP drops to baseline-Fable's level.
2. **The frontier model without retrieval is not a substitute** — Fable-baseline ties Sonnet-baseline on quality (C−B +0.03) at 1.5× the cost, and loses to Sonnet+MCP on 7 of 10 tasks at 2× the cost per quality point.
3. The initial published study can quote the CSB table without the n=1 caveat; per-cell SDs are in `study_aggregates.json` per-task block.
