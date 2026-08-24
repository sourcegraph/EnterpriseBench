# We gave an agent AST search. It used grep instead.

The hypothesis was that structural search would be the more agent-natural interface. AST patterns match code the way a developer thinks about code, and go-to-definition/find-references feels like an IDE affordance built for humans clicking around. So: give a coding agent both, and it should prefer the structural one.

It didn't. Across 270 controlled runs, the agent reached for text search first in every single run where it had a choice, and in 52 of 54 runs it never touched the AST tool at all. Showing it worked examples of the AST syntax pushed that to zero.

## The setup

Five tool conditions over the same 18 retrieval tasks, three repeats each:

- **lexical**: grep, read_file, list_files
- **structural**: ast_search (ast-grep patterns), read_file, list_files
- **codeintel**: definition, references, read_file, list_files. These are Sourcegraph's `sg_go_to_definition` and `sg_find_references` over the `sg-evals` mirrors of the same pinned revisions. Index tier varies across the corpus: 75 of 97 stores carry a semantic SCIP index (scip-python, scip-go, scip-typescript, scip-rust, scip-ruby, scip-java) and 22 carry only `scip-syntax`, so this arm resolves symbol identity on most of the corpus rather than all of it
- **combined**: all of the above
- **structural + lexical**: no code intel, to see whether AST search substitutes for it or only supplements grep

Everything else is pinned: same model, same system prompt, same task wording, same repo snapshot, same turn budget, same scorer. Run order is shuffled within each repeat under a recorded seed, so provider drift over the sweep's wall clock can't line up with one arm.

Two design choices did most of the work. The first is that the tool surface is enforced by permissions. Each condition's tools are served over a stdio MCP server, and a fail-closed PreToolUse hook denies everything outside the condition, including the CLI's own built-in Read and Grep. A denied call is logged rather than dropped, because an agent repeatedly reaching for a tool it can't have is telling you something about preference.

The second is that the tool descriptions pass a parity check before any sweep starts: comparable length, and no preference language in any of them. It is very easy to write a description that sells one arm ("use this when you need to find all call sites of a function") and then report the resulting win as a property of the tool. The check makes "we accidentally coached one arm" a testable claim.

## Were the code intel tools even usable?

A zero-usage number has a boring explanation sitting right next to it: the mirror was not indexed, `definition` came back empty, and the agent worked that out before I did. On an unindexed repository the arm posts a clean-looking 0.0 for a task it was never given the means to answer, which is a plumbing failure wearing a measurement's clothes.

So index state is established before anything is scored rather than reconstructed afterwards out of disappointing numbers. `preflight_index.py` pulls three column-zero definitions per store out of the local clone, symbols whose definition has to resolve to the line it was just read from, and asks `sg_go_to_definition` for each. All 97 stores resolve, and `index_coverage.json` lists no task ineligible for the code intel arm. A coverage hole would have routed those units out of the arm as invalid, not scored them.

What that probe establishes is that a definition comes back, not which tier answered it. Sourcegraph's GraphQL API answers the tier question: 75 of the 97 stores have a COMPLETED semantic SCIP index and 22 have only `scip-syntax`. Across the 18 pilot tasks, 12 have a semantic index on every store they use, 4 are mixed, and 2 have none.

The stronger evidence is the codeintel arm itself. Given `definition`, `references`, `read_file` and `list_files`, the agent called definition 129 times and references 129 times across 54 runs, opened with one of them in 38 runs, and posted the best recall in the experiment. The tools resolve and the agent can drive them. In `combined` it does not pick them.

## What the agent preferred

| condition | first primitive | grep calls | ast_search calls | definition/references calls |
|---|---|---|---|---|
| lexical | lexical 54/54 | 398 | 0 | 0 |
| structural | structural 50/54 | 0 | 447 | 0 |
| codeintel | codeintel 38/54 | 0 | 0 | 258 |
| structural + lexical | lexical 54/54 | 383 | 6 | 0 |
| combined | lexical 54/54 | 397 | 2 | 0 |

In `combined`, with every tool available, `definition` and `references` were called zero times across all 54 runs. `ast_search` was called twice. The tools were offered, never denied by the hook, and never returned an error. They were just not chosen.

The escalation story is the same. Across the 108 runs holding more than one search family, cross-class transitions total six. There were no lexical → codeintel transitions and no structural → codeintel transitions anywhere in the pilot. When grep stopped resolving the question, the agent ran more greps and read more files.

One number in that table cuts against the summary above. Inside the codeintel arm, `definition` went unused in 21 of 54 runs and `references` in 20, and that arm logged 538 `read_file` calls, the most of any condition by a wide margin. So the pattern is less grep-specific than it looks. Most of the budget goes on reading files, and the search primitive is what picks which ones.

## What actually retrieved better

Preference and effectiveness are different claims, and here they point in different directions. File recall, with a paired bootstrap over tasks (repeats averaged first, since repeats of one task aren't independent samples):

| condition | recall | precision | USD/run |
|---|---|---|---|
| codeintel | 0.869 | 0.388 | 0.70 |
| lexical | 0.827 | 0.390 | 0.43 |
| combined | 0.819 | 0.372 | 0.45 |
| structural + lexical | 0.799 | 0.370 | 0.44 |
| structural | 0.765 | 0.424 | 0.60 |

Structural search finished last on recall. It has the best precision of any arm, so what it returns is tighter, and it costs 40% more than grep to return less of what the task needed. On `syntactic_pattern`, the category built specifically for it, structural leads lexical by 0.014 with a 95% interval of [−0.083, +0.125]. Indistinguishable, on the tasks where it should have been strongest.

Precise code intel had the highest recall in 10 of 11 categories, tied at 1.000 on identifier lookup, and against lexical the interval excludes zero on four of them:

- dependency impact: +0.131 [+0.040, +0.266]
- cross-repo: +0.105 [+0.019, +0.222]
- framework-heavy code: +0.081 [+0.008, +0.189]
- semantically ambiguous cases: +0.080 [+0.031, +0.132]

On plain identifier lookup, every arm hit 1.000. Grep is sufficient there, and nothing beats sufficient.

The `dynamic_ambiguous` result surprised me. Those are the cases where a compiler index was supposed to run out of road, and code intel still led.

## The awkward reading

The arm that retrieves best is the arm the agent never picks, and the arm that was supposed to be agent-natural is the one it avoids whenever grep is in the room.

So the fix belongs in routing. If the runner imposes the primitive, the ordering is: grep for identifier lookup, code intel for dependency and cross-repo and framework-noise questions, grep as the cheap default everywhere else. If tool choice is left to the agent, it stays on grep and none of the above happens.

## Was it the tool or the syntax?

`ast_search` takes ast-grep metavariable syntax, and an agent avoiding it could be avoiding the capability or the calling convention. Those look identical in a usage count, so we ran the ablation: 108 more runs on the same tasks, with the `pattern` argument showing two worked patterns (`def $F($$$ARGS)` matches any Python function definition, `$OBJ.get($KEY)` matches any `.get` call) instead of naming the metavariable forms abstractly. One argument description changed. Same backend, same tool description, same everything else, and the parity check still passes on the ablated schema: the arm shows syntax without arguing for the tool.

Usage went down. `ast_search` was called zero times in 54 runs, against six calls in the control arm. Showing the agent how to write the pattern did not make it write one.

The second ablation arm is what makes that readable. With grep removed, so the agent has to use ast_search, the same worked patterns helped: recall went from 0.765 to 0.790 (a difference of 0.025 with an interval of [−0.059, +0.003]) and the first relevant result arrived at call 4.7 instead of 6.6. The example was usable. The agent still didn't want the tool.

## What would still falsify this

18 tasks, so most intervals touch zero and only two whole-corpus contrasts exclude it, both of them codeintel against a structural arm. One model, one generation seam, and the Claude Code CLI has no temperature flag, so "model configuration held constant" currently means "held by the provider's defaults." The arms also do not read the same substrate: grep and ast-grep read local clones, code intel reads an index, so some part of the codeintel gap could be the index being a better-organized view of the same code rather than symbol resolution being worth more. Clones are pinned to the revisions the mirrors index, which bounds that without removing it. And this measures retrieval only. Whether better retrieval converts into better task completion needs the end-to-end layer, which isn't built yet. The ablation also tested one wording of the structural interface, not the interface itself: a tool that took a code snippet instead of a pattern might land differently.

The index tier matters more than the aggregate suggests, and cuts against the headline. Splitting codeintel minus lexical by tier: **-0.016** on the 12 tasks whose stores all carry a semantic SCIP index, **+0.069** on the 4 mixed tasks, and **+0.333** on the 2 tasks with only syntactic indexes. The whole-corpus +0.042 comes from the tasks where precise resolution was least available, which is the opposite of the mechanism the result gets attributed to. The split is confounded with difficulty: the fully-indexed tasks sit at 0.975 lexical recall, close enough to the ceiling that no arm can separate, and n=2 on the syntax-only side. It is a reason to re-cut the category analysis by tier before anyone leans on the mechanism, not a finished result.

Run-to-run standard deviation within a task ranged from 0.019 (codeintel) to 0.064 (lexical), so three repeats is enough to separate the codeintel gap from noise and not much else.
