# Blinded PR-review-like judging as a secondary EnterpriseBench outcome

**Bead:** EnterpriseBench-rryas.10 (parent: rryas — headline MCP vs baseline vs
CLI study). **Status:** design + feasibility pilot. **Author:** pool worker,
2026-07-19.

## Question

Can an independent, blinded, PR-review-like LLM judge serve as a *secondary*
outcome for headline-run artifacts — reported alongside, never blended into,
the deterministic verifier/unit-test reward — and is EnterpriseBench's existing
judge pipeline reliable enough to include in the tokenomics report?

The deterministic reward stays primary and authoritative. This pilot only asks:
does a separate blinded reviewer corroborate it, and where does it diverge?

## What already exists (reuse, do not reinvent)

| Piece | Location | Reused as |
|---|---|---|
| LLM judge engine | `lib/eb_verify/judge/engine.py` (`LLMJudge.evaluate_checkpoint`) | the reviewer |
| Fixed rubric | `lib/eb_verify/judge/prompts.py` (`CHECKPOINT_EVAL_PROMPT`, CSB-derived, 3-point 0/0.5/1.0 anchors, JSON `{score,passed,reasoning,evidence,confidence}`) | the scoring protocol |
| Curated ground truth | `benchmarks/*/<task>/expected_solution.json` (per-checkpoint `expected_solution` + `evaluation_criteria`) | reference answer |
| Agent output (arm-neutral only after scrubbing) | `.../agent_output/answer.json` | what the judge reads |
| Deterministic reward | `run_log.json` / `results.json` (`total_score`, per-checkpoint `score`/`weight`) | the outcome to agree/disagree with |
| Backends | `judge/backends.py` (`cc:<model>` via `claude -p`, or `claude-*` via Anthropic SDK) | judge model host |

The existing Tier-2 judge is **already arm-blind at its input contract**:
`CheckpointJudgeInput` (`judge/models.py`) has no `mode`/arm field; the prompt
receives only task/checkpoint text, expected solution, criteria, and the agent
answer. Arm identity (`baseline`/`mcp_only`/`hybrid`/`cli`) lives only in the
`run_task.py` orchestration layer — `eb_verify` never sees it.

The pilot narrows that input further, and not deliberately: it calls
`evaluate_checkpoint(..., task_description="", checkpoint_description=name)`
(`rryas10_blinded_judge_pilot.py:496`), so the rendered prompt's
`## Task Description` section is empty and the checkpoint description is a
duplicate of the checkpoint name. See "Bias and reproducibility risks" — this is
a defect in the demonstration, not a blinding measure.

### The three net-new pieces (implemented in `scripts/analysis/rryas10_blinded_judge_pilot.py`)

1. **Read arm-neutral artifacts + randomize.** Judge from `answer.json` (the
   final structured answer), not `agent_trace.jsonl`. The trace is the largest
   arm tell — it records MCP/sgx tool calls that self-identify the arm, and no
   trace scrubber exists in the repo. `answer.json` is not arm-neutral either,
   though: the pilot scrubs the harness `tool_usage` block out of it and then
   fails closed on anything left that names an arm (see "Blinding" below for
   what that does and does not cover). Units are also shuffled under a fixed
   seed; that is replay insurance, not a blinding mechanism (see below).
2. **Persist the rationale.** Production discards it: `runner.py:_run_judge_checkpoint`
   ends with `return result.score`, dropping `reasoning`/`evidence`/`confidence`.
   The pilot writes a `verdicts.jsonl` ledger keeping all three — required to
   audit *why* the judge diverged from the deterministic gate.
3. **Agreement report, no blending.** A separate `report.json`; the pilot never
   touches a scoring path and never computes `min(grep, judge)`.

## Blinded protocol (fixed)

- **Rubric:** frozen = `CHECKPOINT_EVAL_PROMPT` + `CHECKPOINT_EVAL_SYSTEM`,
  recorded by a `rubric_sha256` in every report so a prompt edit invalidates
  comparability rather than silently drifting.
- **Blinding:** the judge receives answer text + expected solution + criteria
  only. Arm is withheld entirely and re-attached to each verdict *after*
  scoring, for analysis only. Opaque blind ids (`U0007`).

  Withholding the arm *name* is only half of it. Blinding runs on two axes,
  because the corpus leaks on both.

  **Axis 1 — the arm name.** For parseable JSON, `_assert_arm_neutral` refuses
  on two rules, neither of which requires guessing the field name a leak will
  arrive under:

  1. *By key.* Any value under a condition-identity key (`arm`,
     `experiment_arm`, `tool_access`) is refused, so a newly added arm cannot
     slip past a stale vocabulary. Key matching folds case, unicode width and
     separators, so `toolAccess` and `tool-access` are the same key.
  2. *By value.* Any string anywhere in the structure that **is** an arm token,
     at any depth, under any key. Matching the whole string rather than a
     substring is what keeps `"run the cli to reproduce"` judgeable while
     refusing a bare `"cli"`.

  Rule 2 is what makes the guard robust rather than merely careful. Every
  earlier version keyed *every* pass off one list of field names, so a name
  nobody anticipated — `{"provenance": "mcp_only"}`, `{"harness_mode":
  "hybrid"}`, or a bare `["mode", "mcp_only"]` with no matching key at all —
  was invisible to all passes simultaneously. That is the same shape as the
  original defect: a guard aimed at an axis the data does not leak on.

  It also disposes of an over-refusal. An earlier version rejected *any* value
  under `mode`/`condition`, which rejects an Ansible `"mode": "0644"`, a
  Grafana alert `"condition": "B"`, a Terraform `validation { condition = ... }`
  and a Celery `"mode": "prefork"` — all shapes this repo's own suites produce.
  Since a refusal aborts the run, one such answer would have taken the whole
  pilot with it. Under rule 2 those pass, because `0644` is not an arm. It
  trades that class for a narrower but real one of its own, recorded under
  "Known residuals" below rather than counted as a clean win.

  A text pass covers payloads that will **not** parse as JSON (a python-repr
  `{'arm': 'hybrid'}`), where there is no structure to walk. It is deliberately
  *not* run on valid JSON: anchored to the end of a value, it refused ordinary
  prose closing on `"...retry mode: cli"` — reintroducing the destroy-the-run
  failure through a second door.

  **Axis 2 — tool provenance, which is what actually leaked.** Every
  `answer.json` under `results/sample_runs` carries a `tool_usage` block echoed
  from `run_log.json`, and its *shape* separates the arms perfectly: baseline
  reports `grep_calls`, mcp reports `sourcegraph_searches` and
  `symbol_navigations`. No arm is named, so a name-matching guard sees nothing
  — yet a judge reading that block can label the arm 6/6. Telemetry is harness
  state rather than answer content, so the pilot **scrubs** it before judging
  instead of refusing over it; refusing would abort on every unit and blind
  nobody. The scrub records the field path it removed per unit, so "removed
  some harness noise" stays distinguishable from "removed a chunk of answer".

  Provenance field names (`grep_calls`, `sourcegraph_searches`,
  `symbol_navigations`, …) are additionally refused **anywhere** they appear —
  as a key, or as a value that survived under a wrapper the scrub did not
  recognize, e.g. `{"events": [{"type": "tool_usage", "grep_calls": 24}]}`.
  Unlike `mode` or `condition` these are never ordinary answer vocabulary, so
  there is no false-positive cost to refusing them outright.

  Every report records `config.blinding` — units checked, units scrubbed, and a
  `vocabulary_sha256` over the key and token sets — on the same reasoning as
  `rubric_sha256`: two reports blinded to different standards should not be
  silently comparable. The regexes are compiled from those same sets under a
  total ordering, so the fingerprint covers every pass and is stable across
  processes rather than varying with the hash seed.

  **Known residuals**, each pinned by a test so it stays a decision:

  - An arm token inside a longer sentence ("solved via hybrid retrieval", "ran
    with mode=hybrid") is not refused. Blocking tokens inside prose is not
    viable — `cli`, `baseline` and `hybrid` are ordinary engineering vocabulary
    — and judging whether such a mention is self-identification is a semantic
    call, not a substring test. Note this residual got *wider* deliberately:
    the narrower version refused one spelling of prose and allowed another,
    while also refusing legitimate answers.
  - A future arm named after ordinary vocabulary is caught only where it sits
    under an identity key; the value scan matches known tokens.
  - **Over-refusal, whole-batch blast radius.** Rule 2 is key-independent by
    design, so *any* string equal to an arm token refuses — as a value or as a
    key — even when the surrounding field makes it plainly not an arm. Confirmed
    by probe: `{"encryption_scheme": "hybrid"}`, `{"deployment_model":
    "hybrid"}`, `{"interface": "cli"}` and `{"affected_component": "cli"}` are
    all refused on the value, and `{"baseline": "the pre-change metric"}` is
    refused on the *key* — `baseline` being ordinary performance vocabulary.
    `_blind_all` raises if any unit offends, so one such answer stops the entire
    batch. Blast radius, stated precisely: the guard is a pre-pass that runs
    before `LLMJudge` is constructed, so **no judge calls are wasted and no
    partial `report.json` is destroyed** — but nothing is judged at all until a
    human removes or rewrites the offending unit, and the run must then be
    restarted from zero. Not yet realized on the current corpus (all 6
    `results/sample_runs` answers pass), so this is prospective for the headline
    run, where `answer.json` is free-form agent output. Per-unit
    skip-and-report — excluding the offender the way an infra error is excluded,
    rather than aborting the batch — is the obvious fix and is tracked as
    bead rryas.10.3 rather than done here.
  - On unparseable payloads only the token-bound text pass runs, so an unknown
    arm name in a python-repr payload is not caught.

- **Shuffling (not a blinding mechanism).** Processing order is shuffled under
  `--seed`, but it protects a channel that does not exist in this architecture:
  each checkpoint is an independent stateless call at temperature 0, with no
  cross-call memory to exploit, and the ledger records the arm on every row
  anyway. It is kept as cheap insurance against a future batched or
  conversational judge, and is pinned by a reproducibility test. Do not cite it
  as evidence of blinding.
- **Determinism / reproducibility:** `temperature=0.0`, pinned `--model`, pinned
  `--seed`. Same inputs + same rubric hash → comparable runs. (Caveat: an LLM at
  temp 0 is only near-deterministic, so a rescore must be auditable rather than
  assumed identical.) The ledger therefore keeps `raw_response`, the backend's
  full decoded payload, alongside the five mapped fields — without it a change
  in how we *read* a response is indistinguishable from a change in what the
  model *said*. Two limits, both real: this is the decoded payload, not the
  byte-level text (both backends parse before returning, `backends.py:60`, so
  capturing bytes would mean changing `eb_verify`, which this pilot does not
  touch); and **the committed `verdicts.jsonl` predates the field**, so the
  demonstration's 14 rows do not carry it. An earlier version of this document
  claimed they did.
- **Independence:** one judge call per checkpoint, no cross-unit context, no
  visibility into the deterministic score or the other arm's answer for the same
  task.
- **Infra-error handling:** a backend/parse failure or a non-score response is
  recorded as an infra error and **not coerced to 0.0** (honors the
  `scorer_guard` invariant that a missing score never becomes a real one). Those
  checkpoints are excluded from agreement stats, not banked as disagreement.

## Bias and reproducibility risks (and mitigations)

- **Self-judging / same-family bias (primary risk).** The agent-under-test is
  Claude Code Sonnet 5; the repo's default judge is `cc:haiku` — same model
  family. Haiku-vs-Sonnet is a cost compromise, not a true cross-family debias,
  and a Claude judge may systematically favor Claude-style answers. *Mitigation
  for the real pilot:* run the judge on a **non-Claude** family (e.g. a GPT- or
  Gemini-class backend via `AnthropicBackend`'s sibling or a new backend) and
  report cross-family vs same-family agreement as a bias probe. This
  demonstration uses `cc:haiku` because it is the only backend reachable in this
  environment; treat its absolute numbers as a wiring check, not the bias
  verdict.
- **Ground-truth ceiling.** The judge can only be as good as
  `expected_solution.json`. A thin or wrong expected solution caps agreement
  regardless of the answer's quality. The ledger's `evidence` field surfaces
  these cases for human spot-check.
- **Coverage-denominator mismatch (affects how Δ must be read).** `judge_total`
  is renormalized over the checkpoints the pilot could pair; `deterministic_total`
  comes from `run_log.json` over *all* of that run's checkpoints. Where curated
  `expected_solution.json` covers fewer checkpoints than the run recorded, the
  two totals have different denominators and Δ would measure coverage rather
  than disagreement. Each unit row therefore carries `checkpoints_covered`,
  `checkpoints_in_run_log` and `full_coverage`, and partial-coverage units are
  excluded from the aggregate `abs_delta` (counted in
  `units_excluded_partial_coverage`). The demonstration below had full coverage
  on all 4 units — 3/3, 3/3, 4/4 and 4/4 checkpoints judged against each
  `run_log.json`'s own checkpoint list (see "Artifact provenance") — so its Δ
  figures are denominator-clean. Note that `report.json`'s "14 judged = 14
  paired" does *not* show this: both sides of that equality come from the
  pilot's own pairing, and neither is the `run_log` denominator this bullet is
  about.
- **Rubric coarseness.** A 3-point scale (0/0.5/1.0) vs continuous deterministic
  scores inflates quantization disagreement; agreement is therefore reported
  both as continuous abs-diff and as binary pass-agreement at the 0.5 line.
- **Answer-only blindness undersells retrieval arms.** Judging `answer.json`
  alone ignores *how* the answer was found. That is deliberate (it is the only
  arm-neutral artifact), but it means the pilot measures answer quality, not
  retrieval-path quality — a limitation to state in the report, not to fix by
  re-introducing the trace.
- **Prompt drift across runs.** Guarded by `rubric_sha256`. Verified current:
  `_rubric_hash()` on today's tree still returns `9e5b50fcd63ef178`, the value
  in the committed `report.json`, so the rubric has not drifted since the
  demonstration.
- **The demonstration judged with no task description (defect, not design).**
  `task_description=""` and `checkpoint_description=<checkpoint name>` mean the
  judge saw the expected solution and criteria but never the task the agent was
  actually given. This plausibly *caused* the divergences quoted below: both
  0.5-vs-1.0 verdicts are "lacks code-specific details" judgments, which is
  exactly the call a missing task statement degrades. It also means blinding
  cannot take credit for withholding text that was never passed in the first
  place. Fix before the re-run (revision 1) by threading the task's
  `instruction.md` through — note that `instruction.md`, not `[task].prompt`, is
  what the agent saw.

## Feasibility

A balanced set of artifacts in the retained-run *shape* exists under
`results/sample_runs/` (`<group>/<baseline|mcp>/<task>/…/answer.json`) with a
sibling `run_log.json` carrying a deterministic score. The judge backend runs
locally via the `claude` CLI. **Conclusion: a real, blinded, balanced pilot is
feasible now, post hoc, with no change to the headline run.** Because it reads
retained artifacts, it does not gate or delay the clean run — the only
requirement is that `answer.json`, `run_log.json`/`results.json`, and the task's
`expected_solution.json` are retained (they are).

> **`results/sample_runs/` is not agent output.** It is a set of hand-authored
> calibration fixtures, added alongside the tasks themselves in commit `f8ee2a2`
> ("implement Phase 0-2 task types"), and its own README says so:
> `results/sample_runs/provenance/analysis.md:50` lists "Run full suite (all 10
> tasks × 2 modes) **once agent infrastructure is available**" as a *next step*.
> The fixtures were built to prove the verifiers discriminate, so the arm
> structure in them is authored rather than measured: the mcp `answer.json` is
> the expected solution reproduced verbatim (`analysis.md:27` — "serves as upper
> bound"; compare `provenance/mcp/err-provenance-01/…/answer.json`'s
> `error_chain` against the four steps in that task's
> `expected_solution.json`), the baseline answer is a deliberately partial one,
> and the `tool_usage` blocks are the round numbers from `analysis.md`'s own
> comparison table (18,500 / 14,200 tokens).
>
> Everything downstream inherits this. **No claim in this document is evidence
> about a real agent, a real arm difference, or a real judge–gate agreement
> rate.** What the pilot demonstrates is that the *machinery* runs end to end on
> correctly-shaped inputs. That is genuinely the question this bead asked
> ("is the existing pipeline reliable enough to include"), and the answer to it
> does not depend on the inputs being real — but it means the recommendation
> below rests on feasibility, never on the numbers.

**What actually bounds the sample size** — worth stating precisely, because an
earlier version of this document named the wrong constraint and would have sent
the scale-up effort at a non-problem. Ground-truth coverage is *not* the binding
limit: 82 `expected_solution.json` files are git-tracked outside `_archived`.
The two real constraints are:

1. **Corpus.** `results/sample_runs/` holds 8 groups but only 3 distinct tasks
   that emit an `answer.json` at all — `err-provenance-01`, `err-provenance-06`,
   `support-mapping-001`. Of those, `err-provenance-06` lives under
   `benchmarks/_archived/` and is filtered out by `_find_task_dir`, leaving 2
   tasks × 2 arms = the 4 units judged. Scaling means running more tasks, not
   curating more ground truth.
2. **One artifact type.** `discover_units` globs `**/agent_output/answer.json`
   only. The other suites in the same corpus emit different final artifacts —
   `dead_code_report.json` (dead_code), `REFACTOR_PLAN.md`
   (refactor_orchestration), `IMPACT_REPORT.md` (api_contract) — so they are
   invisible to the pilot regardless of their ground-truth coverage. Reaching
   task-type balance requires teaching `discover_units` those artifact types,
   and deciding what "the answer" is for a free-form Markdown deliverable.

**Reproducibility gap in the committed demonstration.** Both
`expected_solution.json` files the run consumed (`err-provenance-01`,
`support-mapping-001`) are untracked in the base tree and absent from this
worktree entirely — the run read them from an uncommitted working copy. The
committed `report.json` therefore **cannot be regenerated from committed content
by anyone else**, including by a future session on this branch. Landing those
two files (beads hllf4/g165d) is a prerequisite for any independent replication.

## Demonstration run

Command (worktree script reading base-tree fixtures):

```
PYTHONPATH=lib python3 scripts/analysis/rryas10_blinded_judge_pilot.py \
  --repo-root /home/ds/projects/EnterpriseBench \
  --runs-root results/sample_runs --model cc:haiku --seed 20260719 \
  --out results/analysis/rryas10_pilot
```

**Sample:** 4 units (`support-mapping-001`, `err-provenance-01` × baseline/mcp),
14 checkpoints, **0 infra errors**. Judge `cc:haiku`, seed 20260719, rubric
sha256 `9e5b50fcd63ef178`. (Small: bounded by the corpus and artifact-type
constraints above, not by ground-truth coverage. Wiring + agreement
demonstration, not a powered study.)

| task / arm | judge total | deterministic total | Δ (judge − det) |
|---|---|---|---|
| err-provenance-01 / baseline | 0.50 | 0.50 | **0.00** |
| err-provenance-01 / mcp | 0.80 | 1.00 | −0.20 |
| support-mapping-001 / baseline | 0.575 | 0.50 | +0.075 |
| support-mapping-001 / mcp | 0.70 | 1.00 | −0.30 |

- Checkpoint abs-diff: mean **0.114**, median **0.0**, max 0.5 (n=14).
- Pass-agreement at the 0.5 line: **0.857** (12/14) — see the caveat below
  before quoting this.
- Unit-total abs-delta: mean **0.144**, median 0.138, max 0.3 (n=4).

**The pass-agreement rate is close to uninformative, in two independent ways.**
First, `judge_passed` is `true` on all 14 rows: the rubric's floor is 0.5 and
`engine.py:79` sets `passed = score >= 0.5`, so a judge that never scores 0
cannot fail a checkpoint. A constant "pass" predictor scores the same 12/14 on
this sample. Second, the 2 misses are not disagreements with the deterministic
*gate* — every checkpoint in all four `run_log.json` files carries
`"passed": true`, including the 0.33 and 0.35 rows. The pilot re-derives a
deterministic pass by thresholding the score at 0.5
(`run_pilot` → `_agreement_report`, `rryas10_blinded_judge_pilot.py:569-573`) rather than reading the gate's own
flag; against the gate's actual decisions, agreement is 14/14. Report the
continuous abs-diff instead, and if a binary rate is wanted, define the
deterministic side explicitly and say which definition was used.

**Where it diverges is the interesting part — and it diverges in both
directions.** These are two different row sets, and an earlier version of this
document conflated them. Of 14 checkpoints, 8 agree exactly, 4 have the judge
scoring *below* the deterministic gate, and 2 have it scoring *above*.

*Largest magnitude (abs-diff 0.5), judge stricter.* Two checkpoints the
deterministic grep gate scored **1.0** the judge scored **0.5**. Both are mcp
units. Rationale (persisted — the runner would have discarded it):

> *err-provenance-01 / mcp / error_source:* "correctly identifies the two
> required source files but … does not reference the specific surface error
> message …"
>
> *support-mapping-001 / mcp / code_paths:* "correctly identified all required
> file paths … but the output lacks code-specific details: no function names,
> type names, or explanation …"

The grep gate credits *file presence*; the judge withholds credit for a
*shallow but file-correct* answer against the checkpoint's `evaluation_criteria`.
That is non-redundant signal a file-match verifier cannot produce, and the judge
did not rubber-stamp the deterministic 1.0s.

*The two pass-disagreements (the 2 of 14 behind the 0.857 rate), judge more
generous.* These are different rows, and they run the other way — the judge
**passed two checkpoints the deterministic gate failed**:

| checkpoint | judge | deterministic |
|---|---|---|
| err-provenance-01 / baseline / trigger_conditions | 0.5 (pass) | 0.33 (fail) |
| support-mapping-001 / baseline / code_paths | 0.5 (pass) | 0.35 (fail) |

A secondary outcome that is only ever *stricter* would be safe to read as a
tie-breaker. This one is not: at the 0.5 line it moves credit in both
directions, so it must be reported as a parallel column and never used to adjust
a deterministic verdict.

**Arm and deterministic score level are confounded in this sample; this run
cannot separate them.** The two units the deterministic gate scored 1.0 *are*
the two mcp units, and unit-level direction is 100% arm-aligned (judge < det for
both mcp units, judge ≥ det for both baseline units). At n=4 there is no way to
tell whether the judge is harder on high-scoring answers or harder on mcp
answers — and on a run that leaked tool provenance 6/6 (see below), arm
independence is precisely the conclusion the data cannot license. An earlier
version of this document asserted it anyway; it is withdrawn.

Nor did the judge "agree exactly on the baseline units": support-mapping-001 /
baseline has Δ +0.075, and err-provenance-01 / baseline's unit totals coincide
at 0.50 only after weighting — per checkpoint it is judge 0.5/0.5/0.5 against
deterministic 0.67/0.5/0.33, diverging in both directions at once.

**The one arithmetic fact worth carrying forward: the judge compresses the
arm gap.** The deterministic mcp − baseline gap is 0.50 on both tasks; the
judge's is 0.30 (`err-provenance-01`) and 0.125 (`support-mapping-001`) — a 1.7×
and 4× compression, and the direction is consistent across all four units. For
an MCP-vs-baseline study this is the property that decides whether a secondary
judge column changes the headline conclusion, so it is the number to re-measure
first. State it as arithmetic, not as a finding: on this corpus the mcp
"answers" are the expected solutions verbatim (see Feasibility), so their
deterministic 1.0 is true by construction and the gap it anchors is authored,
not observed.

Ledger: `results/analysis/rryas10_pilot/verdicts.jsonl`;
report: `results/analysis/rryas10_pilot/report.json`.

> **This demonstration run was not blind, and its figures are provisional.**
> It ran before telemetry scrubbing existed, so all **4 judged units** reached
> the judge with their `tool_usage` block intact — `grep_calls` for baseline,
> `sourcegraph_searches` + `symbol_navigations` for mcp. (The separate "6/6"
> figure elsewhere in this document is a different population: all **6
> `answer.json` files** under `results/sample_runs`, including the archived
> `err-provenance-06` pair the demonstration never judged. Both are correct;
> neither is a restatement of the other.) The exposure is not merely theoretical, and
> not merely read — it was **load-bearing in the judge's stated reasoning**. Six
> of the 14 verdicts cite tool telemetry as scoring evidence, and in all four
> mcp rows that cite it, it is cited as affirmative support for a 1.0:
>
> - `U0000` (baseline): "Tool usage stats (15 grep_calls, 42 file_reads)
>   suggest code exploration occurred, but output lacks the required
>   specificity."
> - mcp / `error_chain`: "Tool usage metrics (8 sourcegraph searches, 12 symbol
>   navigations, 28 file reads) indicate actual code exploration **rather than
>   retrieval of generic knowledge**."
> - mcp / `ownership`: "(34 file reads, 18 symbol navigations, 11 Sourcegraph
>   searches) **confirm** actual code navigation rather than generic knowledge."
>
> "Sourcegraph searches" names the mcp arm's tool outright. So the judge did not
> merely have access to an arm tell; it reasoned from one, in the direction that
> favors the arm the tell identifies. Nothing in this run can be read as an
> agreement rate. Re-running under the corrected guard is required before any of
> these figures appear in the tokenomics report, and is tracked as a required
> revision below rather than done here — a re-run means new judge calls and
> therefore different evidence, which would invalidate the divergence rationales
> quoted above.

**Artifact provenance.** The committed `report.json` is the raw output of the
run above and predates both the per-unit coverage fields
(`checkpoints_covered`, `full_coverage`, `units_excluded_partial_coverage`) and
the `config.blinding` block; it is kept as-is rather than regenerated, for the
reason just given. Coverage for that run was verified directly against the
source `run_log.json` files instead — 3/3, 3/3, 4/4 and 4/4 checkpoints judged
for the four units — so every unit had full coverage and the Δ figures above
are denominator-clean on that axis. Future runs emit both inline.

## Recommendation

**Include as a separate secondary outcome — with five required revisions
before it appears in the tokenomics report.** Do not blend into deterministic
reward; report it as a parallel column.

The pilot clears the **feasibility** bar for *inclusion as secondary*, and only
that bar: the existing pipeline ran 4 balanced units post hoc, end to end, with
0 infra errors and a ledger the production runner would have thrown away. That
is a wiring result, and it holds regardless of what the inputs were — which
matters here, because the inputs were hand-authored fixtures rather than agent
output (see Feasibility), so "0 infra errors" is a statement about the harness
and nothing else.

Its disagreements are at least *interpretable* — it withheld credit from
shallow-but-file-correct answers the grep gate rated perfect, which is signal a
file-match verifier cannot produce. That is the profile of an independent
column rather than a noisier copy of the deterministic one, and it is the reason
to keep going. But "independent" cuts both ways: the judge also passed two
checkpoints the deterministic gate failed. This is not a conservative signal
that can only subtract credit, and nothing here licenses using it to break ties
or adjust a deterministic verdict. Report it beside the primary reward; do not
let it touch it.

**Nothing in this document is an agreement measurement.** The demonstration ran
on authored fixtures, with an empty task description, and with tool telemetry
reaching the judge and being cited in its reasoning. Any one of those three
would disqualify the numbers on its own. Revision 1 is not a refinement of these
figures — it is the first real measurement.

It does **not** yet clear the reliability bar, and the demonstration numbers
(12/14 pass-agreement) do not count toward it — that run was not blind on the
tool-provenance axis. What the guard now establishes is that the *input
contract* can be made arm-neutral; whether the judge's agreement survives
actual blinding is the open question revision 1 answers.

Required revisions (why "include (revise)" and not plain "include"):

1. **Re-run on real agent output, under the corrected guard, with a task
   description.** Three separate defects invalidate the committed figures and
   all three must be fixed together, or the re-run inherits one of them:
   *(a)* the inputs were hand-authored fixtures, not agent artifacts, so the
   run must wait for headline-run output (this is the item that makes revision 1
   depend on the clean run rather than being available today); *(b)* `tool_usage`
   reached the judge and was cited in its reasoning — fixed by the scrub, which
   has never actually been exercised on a judged run; *(c)* `task_description`
   was empty (`rryas10_blinded_judge_pilot.py:496`), so thread the task's
   `instruction.md` through first. Until this runs, **no rate from this document
   belongs in the report** — not the 0.857, not the 0.114, not the arm-gap
   compression.
2. **Cross-family judge.** The demonstration judge is `cc:haiku` — the same
   family as the agent-under-test (Claude Code Sonnet 5). Same-family self-favor
   is unmeasured here. Before the report, re-run on a non-Claude backend and
   report same-family vs cross-family agreement as an explicit bias probe. This
   is the one blocker to trusting absolute numbers.
3. **Scale + balance.** 4 units is a wiring check, not evidence. A reportable
   pilot needs a balanced sample across task types and all headline arms
   (baseline/mcp_only/hybrid/cli). The two things that must change are the ones
   named under "What actually bounds the sample size": get a real run corpus
   (`results/sample_runs` is fixtures, and yields 2 usable tasks even as
   fixtures), and teach
   `discover_units` the artifact types other suites emit
   (`dead_code_report.json`, `REFACTOR_PLAN.md`, `IMPACT_REPORT.md`) instead of
   `answer.json` alone. Ground-truth coverage is *not* the bottleneck — 82
   `expected_solution.json` files are tracked — though the two this
   demonstration used are still uncommitted (beads hllf4/g165d) and must land
   for the committed figures to be reproducible at all.
4. **Close the prose-level arm tell.** `_assert_arm_neutral` now refuses any
   arm-identifying *field* structurally, but an answer whose prose describes its
   own tooling ("solved via hybrid retrieval") still reaches the judge, and a
   substring rule cannot separate that from ordinary use of the same words. At
   scale this needs a measurement, not a guard: sample the judged answers and
   report what fraction contain a self-identifying mention, so the residual is
   quantified rather than assumed negligible.
5. **Persist rationale in the retained artifacts.** Either land the pilot
   harness as the post-hoc path (it already keeps `reasoning`/`evidence`/
   `confidence`), or have the production runner stop discarding them. The
   divergence analysis above is only possible because the pilot kept them.

**Does not gate the clean run.** The dependency runs one way: every revision
consumes run artifacts post hoc, and none of them changes the runner, the
verifiers, or any scoring path. The headline run proceeds unblocked as long as
`answer.json`, `run_log.json`/`results.json`, and `expected_solution.json` are
retained per task (they are). Revision 1 now *waits on* that run rather than
being available today — since `results/sample_runs` turned out to be fixtures,
the first real measurement needs real output — but waiting on the run is the
opposite of gating it.

**Explicitly out of scope / omit for now:** judging `agent_trace.jsonl` (leaks
arm identity, no scrubber exists) and any path that folds the judge into primary
reward.
