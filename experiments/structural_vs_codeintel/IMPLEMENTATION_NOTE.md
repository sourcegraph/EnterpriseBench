# Structural search vs precise code intel: implementation note

Question: do coding agents use structural/AST search more effectively than
classic code-intelligence navigation, and when should an agent prefer each?

This note is the pre-implementation survey the experiment brief asks for. It
records what already exists, what is being extended, and what is genuinely new,
so the next reader can tell reuse from reinvention.

## What already exists (and is being reused, not rebuilt)

`experiments/toast_retrieval/` is a working policy-layer retrieval harness built
for the Toast-1 vs Code Finder study. It is the right substrate for this
experiment, and it already supplies most of the controls the brief demands:

| Brief requirement | Already present |
|---|---|
| Controlled tool-condition ablation | `run_claude_policy.py` holds model, prompt, sampling, round budget and scorer fixed and varies only the tool surface (`build_generate_fn(model, tools=...)`) and the backend `stores` object. `run_local_policy.py` is condition 1 (lexical) built exactly that way. |
| Leak-free stratified task pool | `configs/retrieval_slice.json` — 63 tasks at <=1/3 prompt disclosure of their own ground-truth files, 44 disclosing nothing. Enforced in CI by `scripts/audit_disclosure.py --gate`. `hard_slice_tasks.json` is the runnable form with store ids and strata. |
| Retrieval metrics | `lib/eb_metrics/ir_metrics.py` (recall/precision/F1/nDCG@k/MRR@k) via `aligned_scoring.py` (contract `aligned_suffix_v1`, which fixes a path-normalization bug that invalidated pre-fix numbers). |
| Repetition methodology | `repeat_variance.py`; prior sweeps ran 2-3 repeats per condition and report run-to-run spread. |
| Cross-condition comparison + report | `compare_policies.py`, `rescore_aligned.py`, `report.html`. |
| Token/cost/latency accounting | provider-reported `input_tokens`/`output_tokens`, `elapsed_seconds` per report; `scripts/cost_tracker.py` for the containerized path. |
| Validity audits | `leakage_audit.py` (prompt disclosure), `audit_traces.py` (are emitted paths real, or parametric-memory guesses). |
| Full raw traces | every report persists the entire message list including tool calls, arguments and tool results — tool-sequence analysis is a post-hoc pass over existing artifacts, no re-run needed. |

Prior results worth comparing against, already measured on this exact slice and
scorer: `code_finder` 0.627 file_recall, `deepsearch` 0.633, `toast-1`
0.435/0.331. Those are search-policy arms, not tool-condition arms, so they are
context rather than a baseline for this study — but they fix the scale.

`EXPERIMENT_PLAN.md` in that directory explicitly lists the code-intel arm
("definitions/references/call graph") as designed but **not built**. This
experiment builds it.

## Which harness is extended

`experiments/toast_retrieval/run_claude_policy.py` — its `TOOLS` list,
`_dispatch_inner` router, and 4-round `run_agent` loop. New conditions are new
backend objects plus new tool schemas passed into the same loop. Nothing about
the loop, prompt, sampling, scorer, or report shape changes, which is what makes
the five conditions comparable to each other and to the existing arms.

The containerized end-to-end path (`scripts/orchestration/run_task.py` +
`mode_gate.py`) is the second layer, for end-task reward rather than retrieval
quality. It is deliberately **not** in the pilot: it is paid Docker dispatch and
its arms are filesystem permissions, so adding a structural arm there is a
larger change that should be informed by the pilot's tool-description findings.

## The five conditions

Same model, same system prompt (modulo the tool schemas each condition
requires), same task text, same repo snapshot, same 4-round budget, same
`max_tokens`/temperature/top_p, same retry policy, same scorer.

1. **lexical** — `grep` (regex), `read_file`, `list_files`. Exists today as
   `run_local_policy.py` over local clones (`git grep -nE`, tracked files only).
2. **structural** — `ast_search` (ast-grep pattern match), `read_file`,
   `list_files`. New.
3. **codeintel** — `definition`, `references`, `read_file`, `list_files`. New,
   backed by the Sourcegraph MCP tools `sg_go_to_definition` /
   `sg_find_references` (SCIP-backed, i.e. resolved symbol identity, not text).
   Probed live against the `sg-evals` mirrors this slice uses: both return real
   results.
4. **combined** — lexical + structural + codeintel, agent chooses freely.
5. **structural+lexical** — no code intel. This is the condition that decides
   whether AST search substitutes for code intel or merely supplements grep.

`submit_files` is present in all five and is the answer contract.

## Where structural search is integrated

A new `StructuralStores` backend in this directory, mirroring
`run_local_policy.LocalStores`: same `files.retrieve` / `files.list` seams over
the local clones in `experiments/toast_retrieval/repos/`, plus an `ast_search`
seam shelling out to `ast-grep --pattern ... --json` scoped to the task's repos.
Language is inferred per repo from file extensions and can be overridden per
call, because ast-grep is per-language by construction.

This is **syntactic**, not semantic: ast-grep matches tree shape and
metavariables. It does not resolve symbol identity, types, or reference edges.
The report must not describe it as code intelligence, and the tool description
given to the model says what it matches, not what it is good for.

## How each condition is isolated

- Tool surface is the only intended difference; it is passed explicitly as
  `tools=` to the shared generation seam.
- Tool descriptions are written to one template — what the tool matches, what it
  returns — at comparable length and specificity. No condition's description
  contains guidance about when to prefer it. A description-parity check
  (length and the absence of preference language) runs before each sweep, so
  "we accidentally coached one arm" is a testable claim rather than an
  assurance.
- Substrate confound, stated plainly: lexical and structural read local clones;
  precise code intel exists only in the Sourcegraph index. The clones are
  pinned to the same revisions the `sg-evals` mirrors index
  (`configs/repo_versions.json`), which bounds the confound but does not remove
  it. Mitigation: a lexical-over-Sourcegraph arm (`run_claude_policy.py`'s
  existing `grep` seam) is run on the same tasks as a bridge, so the
  local-vs-index effect is measured rather than assumed.
- Run ordering is randomized across conditions within a repeat, and each repeat
  uses a distinct seed order, so provider-side drift cannot align with a
  condition.

## What existing metrics already cover

Retrieval precision/recall/F1/nDCG@10/MRR, relevant-vs-irrelevant files
returned, per-task overlap counts, rounds executed, provider-reported tokens,
wall-clock, and the disclosure/hallucination validity audits. Cost follows from
tokens at the pinned model's rate.

## What new instrumentation is required

1. **Tool-sequence statistics** over the persisted transcripts: calls per tool,
   ordered call sequence, first retrieval primitive chosen, query strings and
   near-duplicate reformulations, transitions between primitive classes
   (lexical -> structural -> code intel), escalation patterns, and
   availability-without-use (a richer tool offered and never called). This is a
   new post-hoc analyzer, not new run-time code — the traces already contain
   everything it needs.
2. **Per-tool-call timing and result size**, so "context consumed by retrieval
   results" and tool execution time are separable from model time. Small
   addition to the dispatch path, applied identically to all conditions.
3. **First-relevant and sufficient-evidence markers**: the call index at which a
   ground-truth file first appears in any tool result, and at which all required
   files have appeared. Post-hoc, computed against the same `aligned_scoring`
   matcher used for the headline metric.
4. **Task category labels.** The slice is currently stratified only by repo
   count. The brief's categories (lexical exactness, syntactic pattern,
   call-site discovery, interface implementations, symbol tracing, dependency
   impact, cross-file, cross-repo, refactoring discovery, framework noise,
   dynamically-typed ambiguity) need a manifest. Labels are assigned from each
   task's own ground truth and instruction, recorded in a manifest file with the
   rationale per task, and are never used to select tasks into a condition.
5. **Significance testing**: paired bootstrap over per-task recall between
   conditions, plus per-category effect sizes with intervals, reported with the
   n that produced them.

## Pilot scope

Category coverage before N. Enough tasks to put at least two into each category
that the existing corpus supports, all five conditions, three repeats. The pilot
exists to find harness and tool-description problems — an ast-grep pattern
surface the model cannot use, a code-intel tool that needs a path it does not
have — before any full sweep.

## Tasks authored to close corpus gaps

The 63-task disclosure-gated slice had `identifier_lookup 0` and
`syntactic_pattern 5`: the two categories that separate lexical from structural
search most sharply were the two the corpus barely exercised. Eleven tasks were
authored into `benchmarks/technical_debt/` to close that (the schema's `suite`
enum is closed, so no new suite was invented):

- six `symbol-footprint-*` — the full footprint of one named symbol across a
  pinned repo (Envoy, Kubernetes, Django, Tokio, Grafana);
- five `shape-audit-*` — every occurrence of a syntactic *shape*
  (`dynamic_cast<$T>($X)`, bare `recover()`, `impl Drop for $T`, a `def` whose
  body is only `raise NotImplementedError`).

Every answer key is **measured, not asserted**: `tools/make_navigation_tasks.py`
derives it by running the oracle against the pinned clone and records the exact
command in `ground_truth.json`'s `provenance` block. The `expected` list in
`tools/navigation_specs.py` is a drift tripwire, not the source of truth, so a
re-clone at a different revision fails loudly instead of quietly rewriting the
truth an experiment was scored against.

**These tasks carry oracle bias, and it points both ways.** Identifier tasks are
keyed by `git grep -lw`, a text oracle, which favours the lexical arm. Shape
tasks are keyed by `ast-grep`, a parser oracle, which favours the structural
arm. Precise code intel is the oracle for neither, so it is measured against a
key no condition of its own produced. The bias is recorded in the spec
docstring, in each task's `provenance.bias_note`, and in `build_categories.py`'s
`COVERAGE_GAPS`. Read any headline that leans on these 11 tasks with the
asymmetry in view; the 63 slice tasks, whose keys predate this experiment, do
not carry it.

Leak check: 10 of 11 sit at `disclosed_fraction 0.0`, the eleventh at 0.091 —
far under the slice's ≤1/3 threshold. Merged corpus is 74 tasks; the pilot draws
18 of them, four from each newly filled category.

## The index-coverage preflight

`preflight_index.py` probes each mirror for a precise index before any run, by
asking `sg_go_to_definition` about column-zero definitions read out of the local
clone. Motivation is the scoring trust boundary: an unindexed mirror returns
nothing, and the code-intel arm would post a clean-looking 0.0 for a task it was
never given the means to answer. Such a run is *invalid*, not zero.

`run_conditions.py` reads `index_coverage.json` and drops those units from the
sweep. Missing or incomplete coverage is itself a stop (exit 2), not an empty
exclusion set — "we did not look" must not read as "the arm is eligible". All 97
stores across the merged corpus currently probe as indexed, including
`django--5.0` and `tokio--tokio-1.29.1`, which `configs/sg_indexing_list.json`
does not list.

## A language-mapping defect this work surfaced

`backends.py` mapped `.h` to ast-grep's `c` grammar. In this corpus `.h` is
overwhelmingly a C++ header (Envoy, Ceph), so the structural arm would have
silently misparsed most of two of the largest repos while returning a
well-formed empty result. Now `cpp`, which parses C headers too, with a test
pinning it.

## The second generation seam: Claude Code CLI

The OpenRouter key is dead and five Claude Code OAuth accounts are live, so the
experiment gained a second generation seam rather than waiting. Both write the
same report schema, and `compare_conditions.py` refuses a directory that mixes
them.

Under OpenRouter the tool surface *is* the request: the model cannot reach for
anything not passed in `tools`. Claude Code has no such argument, so the
condition is reconstructed from two pieces:

- `claude_seam/mcp_server.py` — a stdlib-only stdio MCP server exposing exactly
  one condition's tools, descriptions and parameter schemas carried over
  verbatim so `check_description_parity` still governs them. Instrumentation
  stays ours (`Instrumented`), so `tool_log`, `tool_seconds` and
  `retrieval_result_chars` are measured on the same code path as the OpenRouter
  seam. State is persisted after every call, because the CLI owns the process
  lifetime and may kill it.
- `claude_seam/allowlist_hook.py` — a PreToolUse hook that allows only
  `mcp__retrieval__*` names this condition declares and denies everything else.
  This is the half that cannot be skipped: the corpus clones are readable on
  local disk, so one un-denied shell primitive puts lexical search back into the
  structural and code-intel arms. `--disallowed-tools` cannot express it, being
  fail-open by construction — the same objection `mode_gate.py` records, with a
  different mechanism because there is no container here to hold kernel
  permissions. Denials are logged rather than discarded: a model repeatedly
  reaching for a denied tool is evidence about tool preference.

Verified end to end, not assumed. A probe run had `Bash` denied and logged, the
condition's `grep` used, and the correct file submitted.

### What this seam cannot control, recorded in every report

- **Sampling.** No `--temperature`/`--top-p`. The 0.7/0.95 that
  `toast_generate.py` pins, and that makes these arms comparable to the existing
  toast_retrieval numbers, is unavailable. Repeats here measure the CLI's
  stochasticity. Reports carry `sampling: cli_default_not_settable`.
- **The loop.** `rounds_executed` comes from the CLI's `num_turns` and is
  labelled `rounds_unit: cli_turns`, because a CLI turn is not the harness's
  round. `--max-turns 12` is a stop, not a matched control.
- **Forced submission.** The OpenRouter loop compels `submit_files` on its last
  round, so no run there can end without an answer. This seam cannot force it.
  An empty submission is therefore the harness's asymmetry, not a measured
  failure, and routes to `invalid_no_submission` with `ir_scores: null`.
- **Scaffolding overhead.** Measured at ~28.6k tokens and roughly $0.11 per run
  with the system prompt replaced, dynamic sections excluded, strict MCP config
  and every built-in denied. It lands on the efficiency claim, so token fields
  carry `scaffold_overhead_tokens` and must not be compared across seams.
  `input_tokens` sums fresh, cache-write and cache-read context: reporting the
  CLI's bare `input_tokens` would show 6 tokens for a 120k-token run.

`ENABLE_TOOL_SEARCH=false` is mandatory here. A first probe had the CLI reach
for `ToolSearch` to load the retrieval tools' schemas; deferred schemas make the
tool surface something the model must discover, and tool selection is the
dependent variable. `run_task.py` sets the same flag for the same reason.

### Budget exhaustion is an outcome, not an exclusion

Not every unscored run is the same kind of unscored. A disabled account is a
harness fault and drops out cleanly. A run that spent its entire turn budget
without submitting is something the arm did, under a budget that is itself a
stated control, and dropping those runs while reporting the mean over the rest
is a survivorship statistic: it removes an arm's worst cases and flatters it.

Observed, which is why this is written down rather than reasoned about: on
`symbol-footprint-envoy-recursion-guard-001` the structural arm issued 61
`ast_search` calls across 41 turns, spent 524 seconds, and never submitted.
Scoring that 0.0 would assert it retrieved nothing, which its own tool log
contradicts. Silently excluding it would assert the arm never fails that way.

So `compare_conditions.py` reports `completion_rate` per condition beside every
quality mean, each condition row carries `metrics_are: "conditional on the run
completing"`, and the printed table puts a `done` column next to recall. The
turn cap was also raised from 12 to 40 after it bound on code intel; it still
binds on structural, and that is now visible rather than absorbed.

### Failure routing

Three statuses never score: `invalid_no_submission`, `invalid_cli_error` (an
expired token or a disabled account — found in the first smoke run, where
account 2 returns "your organization has disabled Claude subscription access"),
and code-intel units dropped by the index preflight. `compare_conditions.py`
excludes unscored runs and prints the count by status rather than averaging a
zero into the arm.

Accounts are probed before a sweep starts. Runs are spread round-robin, so one
disabled account silently poisons a fixed fraction of every condition; those
runs would route to the re-run channel, but a sweep that loses a fifth of its
cells is one to stop before it starts.

## The pilot ran; what the outputs are

270 runs (18 tasks x 5 conditions x 3 repeats) on the `claude_code_cli` seam,
in `reports_claude_code/`, one JSON per (condition, repeat, task) holding the
full tool log. Two structural runs produced no score (`invalid_turn_limit`,
`invalid_no_submission`) and are excluded from the quality means and reported
as a per-condition completion rate rather than dropped silently.

The analysis chain, each step reading only files the previous one wrote:

```bash
python3 compare_conditions.py --reports reports_claude_code   # comparison.json
python3 analyze_traces.py     --reports reports_claude_code   # trace_stats.json, trace_runs.json
python3 select_traces.py      --reports reports_claude_code   # traces.json
python3 render_findings.py                                    # FINDINGS.md
```

`FINDINGS.md` is generated, not written: every figure in it is read out of the
three JSON files at render time, so it cannot drift from the corpus. The prose
that interprets those numbers lives in `ANALYSIS_QUESTIONS.md` (the brief's ten
questions, one of which is answered "unanswered" because it needs the end-task
layer) and `NEXT_EXPERIMENTS.md`.

`select_traces.py` picks representative traces by rule -- per-condition best and
worst, plus every condition on the task where they disagree most, plus every
unscored run -- because hand-picking is how an anecdote gets into a controlled
result.

### Two additions the pilot's own output forced

`compare_conditions.py` reported a blank precision column: `compute_ir_scores`
returns `precision`/`recall`/`f1` as dicts keyed by k and only `file_recall` as
a file-level scalar, so two of the three quantities the brief asks for were
never being computed. They are now derived set-wise from the counts the scorer
does record. Separately, `cost_usd` is aggregated from what the seam reports
rather than from a local price table, which would drift.

## Known blockers

- **The OpenRouter seam has no key.** `OPENROUTER_API_KEY` returns 403 "Key
  limit exceeded (total limit)"; `OPENROUTER_API_KEY_WORKSPACE` returns 402
  "Insufficient credits". `run_conditions.py` is otherwise ready and plans 18
  tasks x 5 conditions x 3 repeats = 270 runs. Restoring a key is what makes a
  sampling-controlled replication of the Claude Code numbers possible, and that
  cross-seam check is worth doing before any headline claim.
- **Account 2's subscription access is disabled**, so sweeps run on accounts
  1, 3, 4, 5. `run_claude_conditions.py` probes and refuses to start otherwise.
