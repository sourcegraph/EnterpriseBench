# We gave an agent AST search. It used grep instead.

The hypothesis was that structural search would be the more agent-natural interface. AST patterns match code the way a developer thinks about code, and go-to-definition/find-references feels like an IDE affordance built for humans clicking around. So: give a coding agent both, and it should prefer the structural one.

It didn't. Across 270 controlled runs, the agent reached for text search first in every single run where it had a choice, and in 52 of 54 runs it never touched the AST tool at all.

## The setup

Five tool conditions over the same 18 retrieval tasks, three repeats each:

- **lexical**: grep, read_file, list_files
- **structural**: ast_search (ast-grep patterns), read_file, list_files
- **codeintel**: definition, references, read_file, list_files, backed by a SCIP index, so resolved symbol identity rather than text
- **combined**: all of the above
- **structural + lexical**: no code intel, to see whether AST search substitutes for it or only supplements grep

Everything else is pinned: same model, same system prompt, same task wording, same repo snapshot, same turn budget, same scorer. Run order is shuffled within each repeat under a recorded seed, so provider drift over the sweep's wall clock can't line up with one arm.

Two design choices did most of the work. The first is that the tool surface is enforced by permissions. Each condition's tools are served over a stdio MCP server, and a fail-closed PreToolUse hook denies everything outside the condition, including the CLI's own built-in Read and Grep. A denied call is logged rather than dropped, because an agent repeatedly reaching for a tool it can't have is telling you something about preference.

The second is that the tool descriptions pass a parity check before any sweep starts: comparable length, and no preference language in any of them. It is very easy to write a description that sells one arm ("use this when you need to find all call sites of a function") and then report the resulting win as a property of the tool. The check makes "we accidentally coached one arm" a testable claim.

## What the agent preferred

| condition | first primitive | grep calls | ast_search calls | definition/references calls |
|---|---|---|---|---|
| lexical | lexical 54/54 | 398 | 0 | 0 |
| structural | structural 50/54 | 0 | 447 | 0 |
| structural + lexical | lexical 54/54 | 383 | 6 | 0 |
| combined | lexical 54/54 | 397 | 2 | 0 |

In `combined`, with every tool available, `definition` and `references` were called zero times across all 54 runs. `ast_search` was called twice. The tools were offered, never denied by the hook, and never returned an error. They were just not chosen.

The escalation story is the same. Across the 108 runs holding more than one search family, cross-class transitions total six. There were no lexical → codeintel transitions and no structural → codeintel transitions anywhere in the pilot. When grep stopped resolving the question, the agent ran more greps and read more files.

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

## What would falsify this

`ast_search` takes ast-grep metavariable syntax. An agent avoiding it might be avoiding the capability or the calling convention, and this pilot can't tell those apart. The next experiment is the ablation: add a two-line syntax example to that one tool description, hold everything else fixed, re-run. If usage jumps, the finding is about the interface and the original hypothesis deserves another hearing. Writing that example into one arm's description as a default would be exactly the coaching the parity check exists to prevent, which is why it's an ablation and not part of the pilot.

The other limits: 18 tasks, so most intervals touch zero and only two whole-corpus contrasts exclude it, both of them codeintel against a structural arm. One model, one generation seam, and the Claude Code CLI has no temperature flag, so "model configuration held constant" currently means "held by the provider's defaults." And this measures retrieval only. Whether better retrieval converts into better task completion needs the end-to-end layer, which isn't built yet.

Run-to-run standard deviation within a task ranged from 0.019 (codeintel) to 0.064 (lexical), so three repeats is enough to separate the codeintel gap from noise and not much else.
