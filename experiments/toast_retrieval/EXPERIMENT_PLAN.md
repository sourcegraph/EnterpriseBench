# Five-condition design: plan and status

The question, per condition, is where retrieval value comes from: the general
model, the search policy, or the retrieval substrate. Same tasks, same
scoring (`eb_metrics.ir_metrics` via the shared extractor), same 4-round
interaction budget wherever the loop is ours, ≥3 repeats per condition.

## Conditions → runners

| # | Condition | Policy (who searches) | Substrate | Runner | Status |
|---|-----------|----------------------|-----------|--------|--------|
| 1 | Local baseline | Claude (4-round loop) | git grep + reads over local clones | `run_local_policy.py` | Built + tested; clones on disk; **blocked on OpenRouter key limit** |
| 2 | SG MCP (parent drives) | Claude (4-round loop) | Sourcegraph via the adapter's 4 verbs | `run_claude_policy.py` | 20-task r1 done (0.742); r2 stopped 13/20 at the key limit; resumes from cache. Not run on the hard slice. |
| 3 | Code Finder | SG's specialized agent | Sourcegraph, native | `run_mcp_policy.py --policy code_finder` | **3 repeats on 20 tasks**: 0.858 / 0.729 / 0.812; **hard slice**: 0.627 |
| 4 | TOAST-1 + Mixedbread | Toast-1 | Mixedbread stores | not built | **Budget-gated**: needs the 39 repos ingested into MXB stores; ~$1.5 of credit remains after the Toast repeats. Probe ingestion cost on one small repo (rust-csv) before deciding scope. |
| 5 | TOAST-1 + SG (killer) | Toast-1 | Sourcegraph via adapter | `run_toast.py` | **3 repeats on 20 tasks**: 0.575 / 0.504 / 0.490; **hard slice, 2 repeats**: 0.435 / 0.331 |
| + | Deep Search (extra arm) | SG's deep search agent | Sourcegraph, native | `run_mcp_policy.py --policy deepsearch` | **3 repeats on 20 tasks**: 0.804 / 0.821 / 0.787; **hard slice**: 0.633 |

Every recall figure in this file is scored through `aligned_scoring.py`
(contract `aligned_suffix_v1`). Sweeps run before that fix under-report by
0.08-0.16 and are not comparable to these; see the validity-audit section of
`README.md`.

Notes on fidelity to the original design:

- Condition 2 here is the *adapter-verb* form (search/grep/read/list), not
  the full SG MCP tool surface (no commit/diff search, no code intel). That
  makes conditions 1, 2, and 5 differ **only** in substrate and model — the
  clean ablation — at the price of understating what a parent agent could do
  with the whole MCP server. The full-surface form is EB's containerized
  `mcp_only` arm (paid Docker dispatch; must not touch the frozen rryas
  capsule) and is the right follow-up once the policy-layer readout is in.
- Condition 1 drops `search_corpus` entirely: ripgrep has no semantic analog;
  the absence is the condition's definition. Everything else (model, rounds,
  sampling, prompt, scoring) is byte-identical to condition 2.
- The second-phase "give the specialist code-intel superpowers" comparison
  (definitions/references/call graph) applies to conditions 2 and 5 and is
  not built; the adapter deliberately exposes only the common retrieval
  surface first, exactly as the design specifies.

## Token accounting (the OpenRouter question)

OpenRouter cannot meter Toast-1: the model is served only by Mixedbread's
API and OpenRouter only reports usage for calls it proxies. But Mixedbread's
OpenAI-compatible responses carry a real `usage` block — verified live — and
`toast_generate.py` now captures it per call into each report's `api_usage`
field. The dual accounting settled the follow-on question too: on all 40
repeat-sweep tasks the harness's reported totals match provider usage
integer-for-integer, so the first sweep's numbers were exact despite their
`chars_per_4_estimate` label (that label describes the harness's internal
context budgeter, not the reported totals). All five conditions report exact
provider-side tokens.

## The corpus problem, and the gate that now prevents it

`leakage_audit.py` over the original 20 tasks: **33/65 required files (51%)
are disclosed by their own prompt** (0 by full path, 11 by basename, 22 by
stem); only 3/20 tasks disclose nothing. That corpus measured string-matching
as much as search.

This was not a QA lapse so much as a QA blind spot with three parts, all now
closed:

1. **The gate could not see this tier.** `scripts/audit_instructions.py`'s
   GT-leakage check tests `if path in instruction` — the exact full path.
   Every one of the 33 disclosures was at basename or stem tier, so the check
   read clean. It also ran in neither the Makefile nor CI (`ci.yml` invoked
   only `audit_consistency.py`).
2. **Disclosure is legitimate for most of these tasks.** They were authored
   for agentic execution: `feature_delivery/schema-evolution-004` names its
   migration and the three call sites because the deliverable is a written
   impact analysis, and its `required_files` are exactly the files it names.
   Retrieval was never the measured skill there. Repurposing `required_files`
   as retrieval ground truth is what turned scenario setup into leakage.
3. **Selection had no disclosure axis.** `select_tasks.py` picked by
   repo-count strata and store availability only.

What now exists:

- `scripts/audit_disclosure.py` — the canonical tiered audit (full path /
  basename / stem at a word boundary, ≥4 chars), reading ground truth through
  the same `required_files_from_ground_truth` extractor scoring uses. Reports
  every task; gates only enrolled ones.
- `configs/retrieval_slice.json` — the enrollment manifest (63 tasks,
  `max_disclosed_fraction` 0.334). Keys are suite-qualified: task ids are not
  unique (`calibration-002` exists in three suites at different disclosure).
- A CI step running `audit_disclosure.py --gate` after `audit_consistency.py`,
  so a retrieval-enrolled task that grows a hint fails the build. Manifest
  drift (an enrolled task losing auditable ground truth) fails too, rather
  than passing silently.
- `select_tasks.py --max-disclosure` (default 1/3), with `disclosed_fraction`
  recorded on every candidate.
- `tests/test_audit_disclosure.py` — 15 tests pinning each tier boundary and
  both gate failure modes.

### The hard-retrieval slice (63 tasks, run)

Regenerating selection with the filter reproduces the low-disclosure pool
exactly: **63 tasks at ≤1/3 disclosure, 44 of them fully hidden**, every repo
indexed on the demo instance (`hard_slice_tasks.json`). Strata: 16 single-repo,
35 dual, 12 tri+.

Three arms have run it (toast-1, Code Finder, Deep Search), and it bites. Code
Finder averaged 0.800 file_recall across three sweeps of the disclosed corpus
and scores **0.627** here, with 9 of 63 tasks at zero. Toast-1 drops 0.575 →
0.435, and a repeat of that same sweep scored 0.331 with 26 zeros, so the hard
slice widens Toast's run-to-run spread rather than narrowing it.

It also reversed a headline. On the disclosed corpus Toast-1's correct set was
a strict subset of Code Finder's (union lift +0.000, 20/20 tasks); on the
slice it contributes a file Code Finder missed on 9 of 63 (+0.064 union lift),
and the repeat reproduces that on 9 of 63 again (+0.056), 6 of them the same
tasks. The subset result was shared prompt strings manufacturing agreement
between policies, not a property of the policies. Numbers and the validity
audit live in `README.md`.

What curation still owes: tagging each task against the seven categories below
and balancing the mix, plus authored clue-stripped variants of the
higher-disclosure tasks (which is where paired natural/stripped measurement
comes from). Categories:

1. **Lexical exactness** — error strings, symbol names, config keys (grep
   should win; calibrates the floor).
2. **Vocabulary mismatch** — intent words absent from the implementation
   ("prevent duplicate job execution" vs. generation/lease/fence code).
3. **Structural** — implementers of an interface, call sites, dependency
   edges (code intel should win).
4. **Cross-repo** — producer → client → config → tests chains.
5. **History** — "what changed that could explain this regression?"
6. **Distributed evidence** — 3–5 required pieces, no single hub file.
7. **Distractors** — deprecated implementations, generated code,
   near-duplicate tests, multiple versions in scope.

The paired variants measure lexical leakage directly: `recall(natural) −
recall(stripped)` per policy is each policy's dependence on the prompt
handing it strings.

## Three measurement layers

- **Retrieval**: Recall@k, nDCG@10, precision/F1 vs. required evidence,
  first-relevant rank, evidence coverage — `ir_metrics`, already wired.
- **Search agent**: tool calls, rounds, provider-reported tokens, wall time,
  cost, size and relevant-token density of what reaches the parent —
  transcripts + `api_usage` cover ours; Code Finder/Deep Search internals are
  visible only as latency + response size (their token spend is
  Sourcegraph-side and unobservable from here; note this asymmetry in any
  cost table).
- **End-to-end**: task completion, tests passed, total tokens/cost/latency —
  requires the EB sandbox/verifier path (containerized runs), out of scope
  for the policy-layer sweeps; planned once the slice exists.
- **Variance across repeats**: `repeat_variance.py` — per-repeat means,
  spread, per-task instability. A policy that intermittently drops a
  required file is worse infrastructure than its mean suggests.

## Decision tree (unchanged from the design)

- TOAST+SG > Code Finder → search-policy opportunity (decomposition,
  pruning, budgeting, termination, ranking).
- TOAST+MXB > TOAST+SG on semantic tasks → candidate-generation gap in SG.
- TOAST+SG > TOAST+MXB on structural/cross-repo → SG's code-native substrate
  carries value under someone else's policy.
- Code Finder > TOAST+SG → what does Code Finder know that a generic policy
  can't exploit?
- Parity at 2–4× fewer specialist tokens → the evidence-economics claim
  reproduces in coding.

Two branches are answered, and they hold off the grep-solvable regime: Code
Finder > TOAST+SG (0.627 vs 0.435 on the hard slice, every stratum), and
Claude-over-adapter lands within 0.028 of Code Finder on answered tasks — the
policy, not the substrate access, is where Toast loses. What the hard slice
added is that "Toast contributes nothing" was corpus artifact: the augment
branch is open at +0.064 union lift (+0.056 on the repeat, 6 of the 9 tasks
shared between runs), below what two already-shipped policies give each other
(+0.081).

## Blockers needing a decision or an action outside this repo

1. **OpenRouter key limit** (hit 2026-08-17 mid-sweep): conditions 1 and 2
   repeats resume with the same commands once raised; completed tasks are
   cached and not re-billed.
2. **Mixedbread ingestion budget** for condition 4: remaining credit after
   Toast repeats ≈ $1.5; 39 repos (3.6 GB working trees) will not fit. Probe
   with one small repo, then decide subset vs. top-up.
3. **End-to-end layer** needs paid containerized dispatch (EB accounts), and
   the hard slice should exist first so the spend lands on tasks that
   discriminate.
