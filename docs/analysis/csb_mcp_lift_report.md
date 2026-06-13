# Does Sourcegraph MCP make a coding agent better? A controlled 3-arm study

**Study:** CSB MCP-lift, 3-arm deepening at n=3
**Source of truth:** commit `8968d9d` · beads EnterpriseBench-323, -dse, -cbk
**Data:** `results/mcp_lift_study/study_results.json` (per-trial rows), `results/mcp_lift_study/study_aggregates.json` (per-cell means + SDs, `csb_3arm` block)
**Reproduce:** `python3 results/mcp_lift_study/analyze.py`

Every number below is recomputed from the committed trial logs. None is quoted from memory.

## 1. Executive summary

On a 10-task set of discovery-heavy CodeScaleBench org tasks, giving Claude Sonnet 4.6 the Sourcegraph MCP raised its mean normalized reward by **+0.144** (from 0.545 to 0.689), winning 6 of 10 tasks against the same model with only local tools. Swapping the model for a frontier model while holding tooling fixed bought **+0.034** — statistical parity — at ~1.5× the per-task dollar cost. The lift is attributable to the tooling, not the model: the same Sonnet without MCP falls back to the frontier model's baseline level, which is the load-bearing claim of this report.

## 2. Study design

Three arms, each run on the same 10 CSB tasks, n=3 trials per task. 30 cells, **90 trials total, all valid** after re-running infrastructure casualties.

| Arm | Model | Tooling | Workspace |
|---|---|---|---|
| **A** | `claude-sonnet-4-6` | Sourcegraph MCP | no local source — retrieval only |
| **B** | `claude-fable-5` (frontier) | baseline local tools | full local checkout, no MCP |
| **C** | `claude-sonnet-4-6` | baseline local tools | full local checkout, no MCP |

The three arms isolate two effects cleanly:

- **A − C** holds the model fixed (Sonnet) and varies only tooling → **the MCP effect.**
- **C − B** holds the tooling fixed (baseline local) and varies only the model → **the model effect.**
- **A − B** is the end-to-end deployment comparison (cheap model + MCP vs frontier model + local tools).

The 10 tasks: `ccx-agentic-223`, `ccx-config-trace-010`, `ccx-crossorg-217`, `ccx-crossorg-288`, `ccx-incident-145`, `ccx-migration-274`, `ccx-migration-289`, `ccx-vuln-remed-014`, `ccx-vuln-remed-126`, `ccx-vuln-remed-135`. They were selected in Phase 1 (bead -323) as the CSB tasks with the highest expected MCP lift — cross-org discovery, vulnerability usage-site tracing, and config tracing across repositories.

Quality is normalized reward in [0, 1] (CSB harbor reward). Dollar cost is Claude Code's reported `cost_usd` per run, cross-checked against a recompute from per-model transcript token usage at list prices (§5).

## 3. Results

### Per-arm aggregate (10 tasks × n=3)

| Arm | Model | Tooling | Mean reward | Mean $/task | $/quality-point | Mean wall-clock |
|---|---|---|---|---|---|---|
| **A** | sonnet-4-6 | SG-MCP | **0.689** | $0.74 | **$1.08** | 166s |
| **C** | sonnet-4-6 | baseline | 0.545 | $0.71 | $1.30 | 203s |
| **B** | fable-5 | baseline | 0.511 | $1.03 | $2.02 | 117s |

### Pairwise effects

| Comparison | What it isolates | Mean Δ reward | Task wins (win/tie/loss) |
|---|---|---|---|
| **A − C** | MCP effect (fixed model) | **+0.144** | **6 / 2 / 2** |
| **C − B** | Model effect (fixed tooling) | +0.034 | 5 / 2 / 3 |
| **A − B** | Deployment (cheap+MCP vs frontier) | +0.178 | 7 / 2 / 1 |

### Per-task reward (mean of n=3 per cell, ± SD)

| Task | A (Sonnet+MCP) | C (Sonnet base) | B (Fable base) | A−C (MCP) | C−B (model) |
|---|---|---|---|---|---|
| ccx-vuln-remed-014 | 0.611 ± 0.096 | 0.000 ± 0.000 | 0.000 ± 0.000 | **+0.611** | +0.000 |
| ccx-vuln-remed-135 | 0.611 ± 0.024 | 0.204 ± 0.033 | 0.231 ± 0.034 | **+0.407** | −0.027 |
| ccx-crossorg-217 | 0.695 ± 0.047 | 0.471 ± 0.335 | 0.104 ± 0.012 | +0.224 | +0.367 |
| ccx-agentic-223 | 0.625 ± 0.132 | 0.475 ± 0.167 | 0.537 ± 0.116 | +0.151 | −0.062 |
| ccx-migration-289 | 0.681 ± 0.067 | 0.639 ± 0.022 | 0.574 ± 0.038 | +0.042 | +0.065 |
| ccx-crossorg-288 | 0.735 ± 0.094 | 0.708 ± 0.074 | 0.833 ± 0.026 | +0.027 | −0.125 |
| ccx-config-trace-010 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | +0.000 | +0.000 |
| ccx-migration-274 | 1.000 ± 0.000 | 1.000 ± 0.000 | 0.928 ± 0.063 | +0.000 | +0.072 |
| ccx-incident-145 | 0.172 ± 0.009 | 0.177 ± 0.009 | 0.172 ± 0.009 | −0.005 | +0.005 |
| ccx-vuln-remed-126 | 0.759 ± 0.120 | 0.777 ± 0.066 | 0.735 ± 0.036 | −0.018 | +0.042 |

The MCP lift concentrates exactly where retrieval is the bottleneck. On `ccx-vuln-remed-014`, both baseline arms score 0.000 across all 6 of their trials — neither Sonnet nor the frontier model finds the vulnerability usage sites with local tools alone — while Sonnet+MCP scores 0.611. `ccx-vuln-remed-135` (+0.407) and `ccx-crossorg-217` (+0.224) follow the same pattern. Tasks both baselines already solve (`config-trace-010`, `migration-274`) tie at the ceiling: no headroom for tooling to help.

## 4. The de-confounding argument (why the lift is tooling, not model)

This is the section the headline rests on.

A naive deployment comparison — Sonnet+MCP (A) versus Fable-baseline (B) — gives +0.178 in A's favor. On its own that number is ambiguous: A differs from B in *both* model and tooling, so the win could be the model, the tooling, or both. The third arm resolves it.

- **C − B = +0.034 (5/2/3).** Holding tooling fixed at baseline-local and varying only the model, the frontier model does **not** outperform Sonnet. The two are at parity on quality, and the frontier model costs 1.5× more per task ($1.03 vs $0.71). On this task profile, paying for the frontier model buys nothing.

- **A − C = +0.144 (6/2/2).** Holding the model fixed at Sonnet and varying only tooling, MCP lifts reward by 14 points. Removing MCP from arm A drops Sonnet to arm C's level — i.e. back down to where the frontier baseline also sits.

So the +0.178 deployment win decomposes almost entirely into the tooling term (+0.144) plus a negligible model term (+0.034). The conclusion is symmetric and falsifiable: **add MCP and Sonnet jumps; remove MCP and it lands on the frontier baseline.** The model is not doing the work — the retrieval tooling is.

## 5. Methodology hardenings

Two defects were found and corrected before the numbers above were counted. Both are recorded so the result is defensible under audit.

**Arm purity is machine-checked from transcripts (MODEL_MIXED triage class).** Claude Code's `model_refusal_fallback` silently swapped `claude-fable-5 → claude-opus-4-8` mid-run in two Fable-arm trials (the transcript carries an explicit fallback event). A trial that ran partly on a different model would contaminate its arm. A trial now counts only if its transcript's per-model usage shows the arm's intended driver model plus, at most, Haiku auxiliary usage. The contaminated trials were excluded and re-run.

**Cost accounting uses per-model transcript usage, not the driver-only token fields.** `task_metrics.json`'s token fields cover the **driver model only**. Baseline Sonnet runs delegate substantially to Haiku subagents — up to 45% of a run's cost in the baseline arms — which is invisible in the driver-only fields. `analyze.py` recomputes cost from the transcript's full per-model `modelUsage` at list prices (sonnet $3/$15/$6/$0.30, fable $10/$50/$20/$1, haiku $1/$5/$2/$0.10 per MTok in/out/cache-write-1h/cache-read). For pure single-model runs the recompute reproduces Claude Code's logged `cost_usd` exactly (arms A and B match to the cent), confirming the method; the residual 2.6% gap on arm C traces to 429-retry remnants. **Any token-based cost analysis on these runs must use per-model usage, not the `task_metrics` totals.**

### Run validity

All 90 counted trials have non-null reward, output tokens > 0, `agent_return_code == 0`, and an arm-pure model. Excluded and visible in `study_results.json`: 14 invalid trials (429 five-hour session-limit casualties that died with 0 output tokens, plus 2 non-zero exits), each re-run to quota, and 1 MODEL_MIXED trial per the check above.

## 6. Scope limits (read before quoting)

- **CSB only.** This study covers 10 CodeScaleBench org tasks chosen for high expected MCP lift. It does not generalize to all task types; it is the strongest case for MCP, by selection.
- **EB tasks were not deepened.** The EnterpriseBench side of the parent study (bead -323) remains at **n=1** and is not part of this 3-arm result. Do not attach these deltas to EB tasks.
- **This does not touch the locked N=105 parity headline.** The official EnterpriseBench headline — a same-model MCP ablation at N=105 that found parity — stands unchanged. This CSB result is a separate, smaller, selection-biased study and does not reopen or contradict that number. The two coexist: same-model MCP ablation is at parity across the locked N=105 suite, *and* MCP delivers a real +0.144 lift on this discovery-heavy 10-task CSB subset.
- **n=3 per cell.** Enough to kill the n=1 caveat and report per-cell SDs (above); not a large-sample significance claim. The portfolio of 10 tasks is the unit of inference.

## Bottom line

On discovery-heavy cross-repository tasks, Sourcegraph MCP gives a mid-tier model a real, replicated quality lift (+0.144 at fixed model, 6/2/2) at essentially equal dollar cost — and a frontier model without retrieval is not a substitute for it (+0.034 at fixed tooling, 1.5× the cost). When the work is finding the right code across a large codebase, the retrieval tooling matters more than the model.
