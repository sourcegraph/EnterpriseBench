# A retrieval tool beat a frontier model, and we have the third arm to prove it

On one CSB task, `ccx-vuln-remed-014`, two different models running with a full local checkout scored zero. Six trials between them, three on Claude Sonnet 4.6 and three on a frontier model, every one of them 0.000. Neither could find where the vulnerable code was actually used across the repositories. Give Sonnet the Sourcegraph MCP instead of a local checkout and the same task scores 0.611. That single contrast is the whole study in miniature, and the rest of this post is about making sure it means what it looks like it means.

The question underneath it is one anyone choosing an agent configuration runs into: when you have a fixed budget, do you spend it on a stronger model or on better tooling? A cleaner model is the obvious lever. The result here points the other way, and the only reason that claim survives is a third experimental arm that most A/B comparisons skip.

## The trap in a two-way comparison

The deployment question is usually framed as a head-to-head: a cheap model with retrieval against an expensive model without it. Run that comparison on these 10 discovery-heavy CSB tasks and cheap-plus-retrieval wins by +0.178 mean reward, taking 7 of 10 tasks. Ship it, write the blog post, done.

That number is a trap. The winning configuration differs from the losing one in two ways at once, model and tooling, so the +0.178 could be the retrieval, the model, or any mix of the two. A reader who already believes frontier models are overrated will read it as proof of that; a reader who believes the opposite will dismiss it as a tooling artifact. Both are guessing, because the experiment as built cannot tell them apart.

## Three arms instead of two

The fix is to add the arm the two-way comparison leaves out. We ran three configurations on the same 10 tasks, three trials each, 90 trials total after re-running the infrastructure casualties:

- **Arm A:** Sonnet 4.6 with Sourcegraph MCP and no local source, retrieval only.
- **Arm B:** a frontier model (`claude-fable-5`) with a normal local checkout, no MCP.
- **Arm C:** Sonnet 4.6 with the same local checkout as B, no MCP.

Arm C is the one that does the work. Compare A to C and the model is held fixed at Sonnet, so the only thing changing is the tooling; that difference is the MCP effect. Compare C to B and the tooling is held fixed at a plain local checkout, so the only thing changing is the model; that difference is the model effect. The two-way deployment number, A against B, is still there, but now it can be taken apart.

| Arm | Model | Tooling | Mean reward | $/task | $/quality-point |
|---|---|---|---|---|---|
| A | Sonnet 4.6 | Sourcegraph MCP | 0.689 | $0.74 | $1.08 |
| C | Sonnet 4.6 | local checkout | 0.545 | $0.71 | $1.30 |
| B | Fable 5 | local checkout | 0.511 | $1.03 | $2.02 |

## The deployment win was almost all tooling

Take the +0.178 deployment advantage apart along the two arms and it comes out lopsided. The model effect, C minus B, is +0.034 across the 10 tasks, a 5/2/3 split that is parity by any honest reading. The frontier model does not beat Sonnet on these tasks, and it charges about 1.5× more per task to tie. The tooling effect, A minus C, is +0.144, a 6/2/2 split, at essentially the same dollar cost as the baseline checkout.

So the headline deployment win is the tooling term plus a rounding error: +0.144 from MCP, +0.034 from the frontier model. The cleanest way to say it is symmetric and testable. Add MCP to Sonnet and its reward jumps. Strip MCP back out and Sonnet falls to exactly where the frontier model sits. The model was never the thing moving the number.

There is a mechanism detail worth keeping, because it cuts against the intuition that retrieval just means more searching. Arm A reached its higher score in 29 tool calls and 166 seconds on average. Baseline Sonnet in arm C, hunting through a local checkout, took 48 tool calls and 203 seconds to land lower. The retrieval tool bought a shorter path to the answer, not a longer one.

## Where the lift lives, and where it doesn't

The gain is not spread evenly, and the places it concentrates tell you what MCP is actually for. The three biggest wins, `vuln-remed-014` (+0.611), `vuln-remed-135` (+0.407), and `crossorg-217` (+0.224), are all cases where the answer is scattered across repositories and the bottleneck is finding it. On the two tasks both baselines already solve, `config-trace-010` and `migration-274`, all three arms tie at a perfect score; there is no headroom for tooling to add anything. MCP helps exactly when retrieval is the hard part and does nothing when it isn't, which is the behavior you want from a retrieval tool and the opposite of a result that generalizes everywhere.

That last point is the guardrail. These 10 tasks were picked on purpose as the CSB cases with the highest expected MCP lift, so this is the strongest case for the tool, not the average one. On the full locked EnterpriseBench suite of 105 tasks, a same-model MCP ablation comes out at parity. Both numbers are real and they do not fight: across everything, MCP is a wash, and on the discovery-heavy slice it is worth +0.144. The interesting work is mapping the line between those two regimes.

## What we had to fix before trusting any of it

A study that contradicts the obvious choice gets read closely, so two defects we caught during analysis are worth naming. The first was a model that wasn't the model we asked for. Claude Code carries a `model_refusal_fallback` that silently swapped the frontier model for `claude-opus-4-8` partway through two trials, which would have quietly contaminated the arm meant to isolate the model effect. We now check arm purity straight from each transcript's per-model usage and throw out any trial that ran on the wrong driver. The second was a cost undercount: the per-task token fields only cover the driver model, and the baseline Sonnet runs hand a lot of work to cheaper Haiku subagents, up to 45% of a run's cost, which the driver-only fields never see. Recomputing from full per-model usage reproduces Claude Code's own logged dollar figure to the cent on the clean single-model runs, which is how we know the recompute is right.

If you take one operational habit from this, take that second one: when you attribute cost to a model, attribute the subagents too, or your cheap arm will look cheaper than it is.

## The number that's still open

The clean version of the finding is one sentence. On tasks dominated by finding the right code across a large, multi-repository codebase, a retrieval tool given to a mid-tier model beats a frontier model working from a local checkout, at lower cost; on the suite as a whole, that edge washes out. What it doesn't tell you yet is where the boundary sits. The locked-suite parity says the retrieval advantage is real but bounded, and the next measurement is the one that draws the line: which task profiles fall inside the discovery-bound regime where retrieval wins, and which fall outside it. That is the question the full benchmark exists to answer.

---

*Numbers in this post come from the CSB MCP-lift 3-arm study, commit `8968d9d`, and are recomputed from the committed trial logs in `results/mcp_lift_study/`. Reproduce with `python3 results/mcp_lift_study/analyze.py`. The EnterpriseBench side of the parent study remains at one trial per cell and is preliminary; the 3-arm result above is CSB-only.*
