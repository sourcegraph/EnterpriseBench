# A retrieval tool, not a better model, drives the MCP lift on discovery-heavy tasks

*Paper section draft — CSB MCP-lift 3-arm study. Source of truth: commit `8968d9d`; beads EnterpriseBench-323, -dse. Data: `results/mcp_lift_study/study_aggregates.json` (`csb_3arm` block) and `study_results.json`. Reproduce: `python3 results/mcp_lift_study/analyze.py`. Every figure below is recomputed from the committed trial logs.*

## Summary

On a 10-task set of discovery-heavy CodeScaleBench organization tasks, Sourcegraph MCP raises Claude Sonnet 4.6's mean normalized reward from 0.545 to 0.689, a gain of +0.144 (6 wins, 2 ties, 2 losses across the 10 tasks) at essentially equal per-task dollar cost. Holding tooling fixed and substituting a frontier model for Sonnet adds +0.034, which is parity, at roughly 1.5× the cost. A third arm separates the two effects: the deployment-level advantage of cheap-model-plus-MCP over frontier-model-without-it (+0.178) decomposes almost entirely into the tooling term. The lift is a property of the retrieval tool, not of model capability.

This is a separate, smaller, deliberately selection-biased study. It does not reopen or modify the locked N=105 same-model MCP-ablation headline, which reports parity across the full suite. The two results coexist: same-model MCP ablation is at parity across N=105, and MCP delivers a real +0.144 lift on this discovery-heavy 10-task subset chosen for high expected lift.

## Design

Three arms run on the same 10 CSB organization tasks, three trials per task, for 30 cells and 90 trials. The tasks were selected in an earlier phase (bead EnterpriseBench-323) as the CSB tasks with the highest expected MCP lift: cross-organization discovery, vulnerability usage-site tracing, and configuration tracing across repositories. The selection is intentional. This is the strongest case for MCP, not a representative sample.

| Arm | Model | Tooling | Workspace |
|---|---|---|---|
| A | `claude-sonnet-4-6` | Sourcegraph MCP | retrieval only, no local source |
| B | `claude-fable-5` (frontier) | baseline local tools | full local checkout, no MCP |
| C | `claude-sonnet-4-6` | baseline local tools | full local checkout, no MCP |

The arms isolate two effects by construction. A minus C holds the model fixed at Sonnet and varies only tooling, giving the MCP effect. C minus B holds tooling fixed at baseline-local and varies only the model, giving the model effect. A minus B is the end-to-end deployment comparison between a mid-tier model with retrieval and a frontier model with a local checkout. Quality is normalized harbor reward in [0, 1]. Dollar cost is Claude Code's reported `cost_usd` per run, cross-checked against a recompute from per-model transcript token usage at list prices.

## Results

The per-arm aggregate over 10 tasks at three trials each:

| Arm | Model | Tooling | Mean reward | Mean $/task | $/quality-point | Mean wall-clock | Mean tool calls |
|---|---|---|---|---|---|---|---|
| A | sonnet-4-6 | SG-MCP | 0.689 | $0.74 | $1.08 | 166s | 29.2 |
| C | sonnet-4-6 | baseline | 0.545 | $0.71 | $1.30 | 203s | 47.6 |
| B | fable-5 | baseline | 0.511 | $1.03 | $2.02 | 117s | 10.1 |

The pairwise effects:

| Comparison | Isolates | Mean Δ reward | Win / tie / loss |
|---|---|---|---|
| A − C | MCP effect (model fixed) | +0.144 | 6 / 2 / 2 |
| C − B | Model effect (tooling fixed) | +0.034 | 5 / 2 / 3 |
| A − B | Deployment | +0.178 | 7 / 2 / 1 |

Per-task reward, mean of three trials per cell with standard deviation:

| Task | A (Sonnet+MCP) | C (Sonnet base) | B (Fable base) | A−C | C−B |
|---|---|---|---|---|---|
| ccx-vuln-remed-014 | 0.611 ± 0.096 | 0.000 ± 0.000 | 0.000 ± 0.000 | +0.611 | +0.000 |
| ccx-vuln-remed-135 | 0.611 ± 0.024 | 0.204 ± 0.033 | 0.231 ± 0.034 | +0.407 | −0.027 |
| ccx-crossorg-217 | 0.695 ± 0.047 | 0.471 ± 0.335 | 0.104 ± 0.012 | +0.224 | +0.367 |
| ccx-agentic-223 | 0.625 ± 0.132 | 0.475 ± 0.167 | 0.537 ± 0.116 | +0.151 | −0.062 |
| ccx-migration-289 | 0.681 ± 0.067 | 0.639 ± 0.022 | 0.574 ± 0.038 | +0.042 | +0.065 |
| ccx-crossorg-288 | 0.735 ± 0.094 | 0.708 ± 0.074 | 0.833 ± 0.026 | +0.027 | −0.125 |
| ccx-config-trace-010 | 1.000 ± 0.000 | 1.000 ± 0.000 | 1.000 ± 0.000 | +0.000 | +0.000 |
| ccx-migration-274 | 1.000 ± 0.000 | 1.000 ± 0.000 | 0.928 ± 0.063 | +0.000 | +0.072 |
| ccx-incident-145 | 0.172 ± 0.009 | 0.177 ± 0.009 | 0.172 ± 0.009 | −0.005 | +0.005 |
| ccx-vuln-remed-126 | 0.759 ± 0.120 | 0.777 ± 0.066 | 0.735 ± 0.036 | −0.018 | +0.042 |

The lift concentrates where retrieval is the bottleneck. On `ccx-vuln-remed-014`, both baseline arms score 0.000 across all six of their trials; neither Sonnet nor the frontier model locates the vulnerability usage sites with local tools alone, while Sonnet with MCP reaches 0.611. The next two largest gains, `ccx-vuln-remed-135` (+0.407) and `ccx-crossorg-217` (+0.224), share that shape. The two tasks both baselines already solve, `config-trace-010` and `migration-274`, tie at the ceiling, where tooling has no headroom to help. One detail in the per-arm table sharpens the mechanism: arm A reaches its higher reward in fewer tool calls (29.2) and less wall-clock (166s) than baseline Sonnet in arm C (47.6 calls, 203s). MCP does not buy a longer search; it buys a shorter one that lands.

## The de-confounding argument

The deployment comparison alone is ambiguous. Sonnet-plus-MCP beats Fable-baseline by +0.178, but arm A differs from arm B in both model and tooling, so the win could belong to either factor or to both. The third arm settles it.

Holding tooling fixed at baseline-local and varying only the model, C minus B is +0.034 (5/2/3). The frontier model does not outperform Sonnet on these tasks, and it costs about 1.5× more per task ($1.03 against $0.71). Paying for the frontier model buys nothing here. Holding the model fixed at Sonnet and varying only tooling, A minus C is +0.144 (6/2/2). Removing MCP from arm A drops Sonnet to arm C's level, which is where the frontier baseline also sits.

The +0.178 deployment advantage therefore separates into a tooling term of +0.144 and a negligible model term of +0.034. The claim is symmetric and falsifiable: add MCP and Sonnet rises; remove it and Sonnet lands on the frontier baseline. The retrieval tool is doing the work.

## Methodology hardenings

Two defects surfaced during analysis and were corrected before the numbers above were counted. Both are recorded so the result holds under audit.

Arm purity is checked from transcripts. Claude Code's `model_refusal_fallback` silently swapped `claude-fable-5` for `claude-opus-4-8` mid-run in two Fable-arm trials, each carrying an explicit fallback event in its transcript. A trial that runs partly on a different model contaminates its arm, so a new MODEL_MIXED triage class excludes any trial whose per-model usage shows a driver other than the arm's intended model, allowing only Haiku auxiliary usage. The contaminated trials were excluded and re-run.

Cost accounting uses per-model transcript usage rather than the driver-only token fields. The token fields in `task_metrics.json` cover the driver model only. Baseline Sonnet runs delegate substantially to Haiku subagents, up to 45% of a run's cost in the baseline arms, which the driver-only fields do not capture. The analysis recomputes cost from each transcript's full per-model `modelUsage` at list prices: Sonnet 4.6 at $3 / $15 / $6 / $0.30, Fable 5 at $10 / $50 / $20 / $1, and Haiku 4.5 at $1 / $5 / $2 / $0.10 per million tokens for input, output, one-hour cache write, and cache read. For pure single-model runs the recompute reproduces Claude Code's logged `cost_usd` to the cent (arms A and B), which confirms the method; the residual 2.6% gap on arm C traces to 429-retry remnants. Any token-based cost analysis on these runs must use per-model usage, not the `task_metrics` totals.

All 90 counted trials have non-null reward, output tokens above zero, `agent_return_code == 0`, and an arm-pure model. Excluded and visible in `study_results.json`: 14 invalid trials, which are 429 five-hour session-limit casualties that died with zero output tokens plus two non-zero exits, each re-run to quota, and one MODEL_MIXED trial.

## Scope and threats to validity

The study covers CSB organization tasks only, selected for high expected MCP lift, so it is the upper bound of the effect rather than its average. The EnterpriseBench side of the parent study (bead EnterpriseBench-323) was not deepened and remains at n=1; the deltas reported here apply to the CSB subset and must not be attached to EB tasks.[^eb] Three trials per cell is enough to retire the n=1 caveat and report per-cell standard deviations, not to support a large-sample significance claim; the portfolio of 10 tasks is the unit of inference. The locked N=105 same-model MCP-ablation headline, which finds parity across the full suite, is untouched by this result.

The practical reading is narrow and defensible: when a task is dominated by finding the right code across a large, multi-repository surface, a code-intelligence retrieval tool given to a mid-tier model outperforms a frontier model working from a local checkout, at lower cost. The open question this leaves for the full benchmark is where that boundary falls. The locked-suite parity says the retrieval advantage does not generalize to every task type, and the next measurement is which task profiles sit inside the discovery-bound regime where it does.

[^eb]: **Preliminary, EB side.** The EnterpriseBench net-new multi-repo tasks in the parent study (EnterpriseBench-323) were run at a single trial per cell and are not part of this 3-arm result. They are reported separately and should be read as preliminary until deepened.
