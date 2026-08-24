# The ten analysis questions, answered

Numbers cited here are rendered in `FINDINGS.md` from `comparison.json`,
`trace_stats.json` and `traces.json`. Pilot: 18 tasks x 5 conditions x 3
repeats = 270 runs, `claude_code_cli` seam, `aligned_suffix_v1` scoring,
paired bootstrap over tasks (10,000 samples, seed 20260823).

This is the retrieval layer only. Anything below that would require an
end-task reward is marked unanswered rather than answered from a proxy.

## 1. When agents have a choice, which primitive do they reach for first?

Lexical, without exception. In `combined` (grep + ast_search + definition +
references) the first search primitive was lexical in **54 of 54** runs, and
in `structural_lexical` likewise **54 of 54**. Reaching-for-first is not a
close call in this pilot; it is unanimous.

The stronger version of the same signal is non-use. Given all three families,
the agent never called `definition` or `references` in **54/54** runs, and
never called `ast_search` in **53/54**. Given grep and ast_search only, it
never called `ast_search` in **52/54**. These tools were offered, never
denied by the allowlist hook, and returned no errors; they were simply not
chosen. The ablation ran this back with worked patterns in `ast_search`'s argument
description (`def $F($$$ARGS)`, `$OBJ.get($KEY)`) instead of only naming the
metavariable forms. Usage did not rise; it went to zero. `ast_search` was
called **0 times in 54 runs** of `structural_lexical_syntax`, against 6 calls
in 2 runs of its control. Showing the agent how to write the pattern does not
make it write one when grep is available.

When only one family is offered, it is used (`ast_search` unused in
just 4/54 structural runs, `definition`/`references` unused in 21/54 and
20/54 codeintel runs -- the codeintel numbers reflect runs solved by
list_files/read_file alone).

## 2. Does structural search outperform lexical search on structurally
defined tasks?

No, not in this pilot. Overall lexical beats structural on recall by +0.062
[-0.009, +0.142] -- directionally lexical, interval touching zero. On the
category built for structural search, `syntactic_pattern`, lexical minus structural is
-0.014 [-0.125, +0.083]: structural is 0.014 ahead, and indistinguishable. Structural does hold the highest
**precision** of any arm (0.424 vs 0.390 lexical), so it returns a tighter
set; it just does not find more of the required files.

The hypothesis that AST search is the more agent-natural interface is not
supported here. It is the arm the agent avoids whenever grep is present, and
when it is forced to use it, it does not retrieve better.

The ablation adds the reason this is not merely a syntax-unfamiliarity
result. In the forced arm, the same worked patterns did help: `structural`
minus `structural_syntax` is -0.025 [-0.059, +0.003] on recall, and the mean
call index of the first relevant result fell from 6.6 to 4.7. So the example
was usable, and the agent still did not choose the tool.

## 3. Does structural search reduce the need for precise code intel?

No. `structural_lexical` is the direct test, and codeintel beats it by +0.069
[+0.014, +0.139] -- an interval excluding zero. Adding structural search to
lexical also does not improve on lexical alone (+0.028 [-0.022, +0.096] in
lexical's favour, i.e. no gain). Structural search neither substitutes for
code intel nor supplements grep measurably at this N.

## 4. On which task classes is precise code intel clearly superior?

Codeintel has the highest recall in 10 of 11 categories (tied at 1.000 on
`identifier_lookup`), and against lexical
the interval excludes zero on four:

* `dependency_impact`  +0.131 [+0.040, +0.266]
* `cross_repo`         +0.105 [+0.019, +0.222]
* `dynamic_ambiguous`  +0.080 [+0.031, +0.132]
* `framework_noise`    +0.081 [+0.008, +0.189]

It is exactly level on `identifier_lookup` (both 1.000 -- grep is sufficient
and nothing can beat sufficient), and slightly behind on `symbol_tracing`
(-0.067 [-0.300, +0.100], the widest interval in the set, n=5 tasks). The
`dynamic_ambiguous` result is worth flagging as a surprise: these are the
cases where index information was expected to run out, and code intel still
led.

## 5. Does giving the agent every tool improve outcomes, or create
tool-selection overhead?

Neither, at this N -- it changes almost nothing, because the extra tools go
untouched. `combined - lexical` is -0.008 [-0.062, +0.040]. Cost is likewise
near-identical ($0.45 vs $0.43, 55.4s vs 49.4s). There is no measurable
confusion penalty and no benefit: the agent behaves as if it were in the
lexical condition. Note what this rules out: the `combined` arm cannot be
read as "the agent picked the best tool per task", because it did not pick.

## 6. Does structural search improve retrieval metrics without improving
end-task completion?

**Unanswered.** End-task reward is not measured at this layer, by prior
decision to build the retrieval layer first. What can be said is the first
half: structural search does not improve recall or F1, and improves only
precision. A claim about completion would require the containerized
end-to-end arm.

## 7. Does structural search reduce token/cost overhead by returning more
targeted results?

No -- it costs more. Structural: 1.09M input tokens, $0.600/run, 77.0s wall,
9.00s tool time, 304k retrieval result characters. Lexical: 718k tokens,
$0.430/run, 49.4s, 3.15s, 143k characters. Structural returns roughly twice
the result text and takes roughly three times the tool time, at higher
precision but lower recall. Codeintel is the most expensive arm ($0.700,
1.21M tokens, 463k result characters) and it is the one that buys accuracy
with that spend.

Cheapest and fastest is lexical, unambiguously.

## 8. Are there characteristic escalation patterns?

Essentially none were observed. Across 108 runs holding more than one search
family, the recorded cross-class transitions total **6** (`combined`:
lexical->structural 1, structural->lexical 1; `structural_lexical`:
lexical->structural 2, structural->lexical 2). There were zero
lexical->codeintel and zero structural->codeintel transitions in the entire
pilot.

Agents did not escalate. They chose lexical first and stayed there, adding
`read_file` rather than switching primitive. Escalation, if it exists, is not
something a 40-turn budget on these tasks elicits without prompting for it.

## 9. Can we identify a practical routing policy?

Provisionally, and the routing signal is the task category rather than the
query string:

* Identifier or literal lookup -> lexical. Every arm reaches 1.000; anything
  more expensive is wasted.
* Dependency impact, cross-repo reasoning, framework-heavy code, and
  semantically ambiguous cases -> precise code intel. These are the four
  categories where its advantage over lexical excludes zero.
* Everything else -> lexical as default, on cost.

Structural search does not earn a slot in this policy on the pilot evidence.
Its one measurable virtue is precision, so the case for it would be a
context-budget argument (fewer irrelevant characters returned), not a
retrieval-quality one -- and it is contradicted by structural's higher total
token spend.

Two cautions. The policy is inferred from between-condition contrasts, not
from a router that was run; and it assumes the tool is imposed, since the
agent will not select code intel on its own.

## 10. Is the best architecture lexical -> structural -> precise code intel?

The pilot supports the two ends and not the middle. Lexical is the right
cheap default (cheapest, fastest, statistically level with everything except
codeintel). Precise code intel is the right escalation for symbol identity
and dependency questions (four categories with intervals excluding zero).
The structural rung is not supported: it does not beat lexical anywhere
measurably, does not reduce the need for code intel, costs more than lexical,
and is skipped by the agent whenever grep is available.

A three-tier architecture would also have to solve the behavioural finding
before the retrieval finding matters: the agent never escalated on its own.
Routing would have to be enforced by the harness, not left to tool choice.

## What would falsify the above

Stated because the brief asks for a falsifiable design and these results
favour a claim the brief was skeptical of:

* N. Eighteen tasks; most intervals touch zero, and only three whole-corpus
  contrasts exclude it.
* The structural interface, now partly closed. The metavariable-syntax
  ablation ran (`structural_lexical_syntax`, `structural_syntax`): the
  example did not raise usage at all, while modestly improving the forced
  arm's retrieval. What remains untested is a different interface shape
  entirely -- a pattern builder, or examples in the system prompt rather than
  the tool schema.
* One model, one seam. Sampling is not settable through the Claude Code CLI
  (`sampling: cli_default_not_settable` in every report); a provider-API
  cross-check has not been run.
* Retrieval only. Question 6 stays open until the end-task layer exists.
