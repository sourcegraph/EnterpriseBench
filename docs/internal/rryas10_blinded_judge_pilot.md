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
| Arm-neutral agent output | `.../agent_output/answer.json` | what the judge reads |
| Deterministic reward | `run_log.json` / `results.json` (`total_score`, per-checkpoint `score`/`weight`) | the outcome to agree/disagree with |
| Backends | `judge/backends.py` (`cc:<model>` via `claude -p`, or `claude-*` via Anthropic SDK) | judge model host |

The existing Tier-2 judge is **already arm-blind at its input contract**:
`CheckpointJudgeInput` (`judge/models.py`) has no `mode`/arm field; the prompt
receives only task/checkpoint text, expected solution, criteria, and the agent
answer. Arm identity (`baseline`/`mcp_only`/`hybrid`/`cli`) lives only in the
`run_task.py` orchestration layer — `eb_verify` never sees it.

### The three net-new pieces (implemented in `scripts/analysis/rryas10_blinded_judge_pilot.py`)

1. **Read arm-neutral artifacts + randomize.** Judge from `answer.json` (the
   final structured answer), not `agent_trace.jsonl`. The trace is the largest
   arm tell — it records MCP/sgx tool calls that self-identify the arm, and no
   trace scrubber exists in the repo. Units are shuffled under a fixed seed so
   processing order leaks nothing, and a fail-closed assertion refuses to judge
   any payload that names an arm.
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
  scoring, for analysis only. Opaque blind ids (`U0007`), shuffled order.
- **Determinism / reproducibility:** `temperature=0.0`, pinned `--model`, pinned
  `--seed`. Same inputs + same rubric hash → comparable runs. (Caveat: an LLM at
  temp 0 is only near-deterministic; the ledger preserves each raw response so a
  rescore is auditable rather than assumed identical.)
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
- **Rubric coarseness.** A 3-point scale (0/0.5/1.0) vs continuous deterministic
  scores inflates quantization disagreement; agreement is therefore reported
  both as continuous abs-diff and as binary pass-agreement at the 0.5 line.
- **Answer-only blindness undersells retrieval arms.** Judging `answer.json`
  alone ignores *how* the answer was found. That is deliberate (it is the only
  arm-neutral artifact), but it means the pilot measures answer quality, not
  retrieval-path quality — a limitation to state in the report, not to fix by
  re-introducing the trace.
- **Prompt drift across runs.** Guarded by `rubric_sha256`.

## Feasibility

A balanced post-hoc sample already exists under `results/sample_runs/`
(`<group>/<baseline|mcp>/<task>/…/answer.json`) with a sibling `run_log.json`
carrying the deterministic score. Curated `expected_solution.json` currently
exists for the `customer_escalation` tasks (some still uncommitted in the base
tree — beads hllf4/g165d), which bounds the demonstrable sample to those tasks.
The judge backend runs locally via the `claude` CLI. **Conclusion: a real,
blinded, balanced pilot is feasible now, post hoc, with no change to the headline
run.** Because it reads retained artifacts, it does not gate or delay the clean
run — the only requirement is that `answer.json`, `run_log.json`/`results.json`,
and the task's `expected_solution.json` are retained (they are).

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
sha256 `9e5b50fcd63ef178`. (Small: bounded by current `expected_solution.json`
coverage. Wiring + agreement demonstration, not a powered study.)

| task / arm | judge total | deterministic total | Δ (judge − det) |
|---|---|---|---|
| err-provenance-01 / baseline | 0.50 | 0.50 | **0.00** |
| err-provenance-01 / mcp | 0.80 | 1.00 | −0.20 |
| support-mapping-001 / baseline | 0.575 | 0.50 | +0.075 |
| support-mapping-001 / mcp | 0.70 | 1.00 | −0.30 |

- Checkpoint abs-diff: mean **0.114**, median **0.0**, max 0.5 (n=14).
- Pass-agreement at the 0.5 line: **0.857** (12/14).
- Unit-total abs-delta: mean **0.144**, median 0.138, max 0.3 (n=4).

**Where it diverges is the interesting part.** Both disagreements are units the
deterministic grep gate scored **1.0**; the blinded judge scored those two
checkpoints **0.5**. Rationale (persisted — the runner would have discarded it):

> *err-provenance-01 / error_source:* "correctly identifies the two required
> source files but … does not reference the specific surface error message …"
>
> *support-mapping-001 / code_paths:* "correctly identified all required file
> paths … but the output lacks code-specific details: no function names, type
> names, or explanation …"

The grep gate credits *file presence*; the judge withholds credit for a
*shallow but file-correct* answer against the checkpoint's `evaluation_criteria`.
That is non-redundant signal a file-match verifier cannot produce, and the judge
did **not** rubber-stamp the deterministic 1.0s. It agreed exactly on the
lower-scored baseline units. Note the divergence tracks *deterministic score
level* (both perfect-scored units), not the arm — the judge was blind to arm.

Ledger: `results/analysis/rryas10_pilot/verdicts.jsonl`;
report: `results/analysis/rryas10_pilot/report.json`.

## Recommendation

**Include as a separate secondary outcome — with three required revisions
before it appears in the tokenomics report.** Do not blend into deterministic
reward; report it as a parallel column.

The pilot clears the feasibility and reliability bars for *inclusion as
secondary*: the existing pipeline ran the balanced sample post hoc with 0 infra
errors, is already arm-blind at its input contract, produced high pass-agreement
(12/14) with the deterministic gate, and — most importantly — its disagreements
are *interpretable and non-redundant* (it caught shallow-but-file-correct answers
the grep gate rated perfect). That is the profile of a useful secondary signal:
mostly corroborating, diverging only where it adds information.

Required revisions (why "include (revise)" and not plain "include"):

1. **Cross-family judge.** The demonstration judge is `cc:haiku` — the same
   family as the agent-under-test (Claude Code Sonnet 5). Same-family self-favor
   is unmeasured here. Before the report, re-run on a non-Claude backend and
   report same-family vs cross-family agreement as an explicit bias probe. This
   is the one blocker to trusting absolute numbers.
2. **Scale + balance.** 4 units is a wiring check, not evidence. A reportable
   pilot needs a balanced sample across task types and all headline arms
   (baseline/mcp_only/hybrid/cli), which requires `expected_solution.json`
   coverage beyond `customer_escalation` (currently the limiting factor, some
   still uncommitted — beads hllf4/g165d).
3. **Persist rationale in the retained artifacts.** Either land the pilot
   harness as the post-hoc path (it already keeps `reasoning`/`evidence`/
   `confidence`), or have the production runner stop discarding them. The
   divergence analysis above is only possible because the pilot kept them.

**Does not gate the clean run.** All three revisions operate on retained
artifacts post hoc, so the headline run proceeds unblocked as long as
`answer.json`, `run_log.json`/`results.json`, and `expected_solution.json` are
retained per task (they are).

**Explicitly out of scope / omit for now:** judging `agent_trace.jsonl` (leaks
arm identity, no scrubber exists) and any path that folds the judge into primary
reward.
