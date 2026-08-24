# Recommended next experiments, in priority order

Ordered by how much each one would change a conclusion in
`ANALYSIS_QUESTIONS.md`, not by cost.

## 1. Ablate the structural arm's calling convention -- RUN, null result

Done: `structural_lexical_syntax` and `structural_syntax`, 108 runs on the
same tasks, differing from their controls in one argument description (worked
patterns instead of the metavariable forms named abstractly), with the parity
gate still passing on the ablated schema.

`ast_search` usage did not rise. It fell to zero: 0 calls in 54 runs against
6 calls in the control. In the forced arm the same example helped a little
(recall -0.025 [-0.059, +0.003], first relevant result at call 4.7 rather
than 6.6), which is what rules out "the example was unusable" as the reason
the preference did not move.

What is still open is a larger interface change, not another wording tweak:
a pattern builder that accepts a code snippet instead of a pattern, or
examples placed in the system prompt where the agent reads them before
choosing a tool rather than while calling one.

## 2. Forced-primitive arms

Every contrast here is confounded by the fact that a condition's tool set and
the agent's choice move together. Running arms where the primitive is imposed
per task (grep-only, ast_search-only, definition/references-only, with no
alternative) separates "what works" from "what gets picked". The permission
machinery for this already exists; it is the same shape as `mode_gate.py`.

## 3. Raise N to the full 74-task corpus

Three whole-corpus contrasts exclude zero at 18 tasks. The category-level
intervals -- which carry the routing policy -- are built on 4 to 10 tasks
each and are correspondingly wide (`symbol_tracing` spans [-0.300, +0.100]).
The corpus and the runner already support it; this is compute, not code.
Prioritise the categories the policy rests on.

## 4. A second generation seam for the sampling control

Sampling is not settable through the Claude Code CLI, so "model configuration
held constant" is currently held by the provider's defaults rather than by
us. A provider-API seam over the same conditions, on a subset, would show
whether the ordering is stable when temperature is pinned. Needs an
OpenRouter key restored.

## 5. The end-task layer

Question 6 -- whether better retrieval converts into better completion --
cannot be answered at this layer at all. This is the containerized arm that
was deliberately deferred, and it is the largest single piece of remaining
work.

## 6. Escalation under a prompt that permits it

Zero lexical->codeintel transitions in 270 runs is a strong result, but the
system prompt neither encourages nor discourages switching. One arm with an
explicitly permissive instruction ("if text search is not resolving the
question, other primitives are available") would establish whether the
absence of escalation is a capability limit or a default-behaviour effect.
This changes the system prompt, so it is a separate experiment rather than a
condition inside this one.

## 7. Context-budget framing for structural search

Structural's only advantage was precision (0.424, the highest of any arm)
while spending more tokens overall. A run under a tight context budget, where
returning fewer irrelevant characters is what matters, is the one setting
where that trade could pay. Worth one small arm before concluding structural
search has no niche.
