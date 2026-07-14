# File-Discovery Scoring: Set-F1 Replaces Recall-Only

**Decision record — bead EnterpriseBench-vdeyx (2026-07-14).**
Applies benchmark-wide to every checkpoint that scores an agent's list of
files against `ground_truth.required_files`: the 26 inlined check-script blobs
(EnterpriseBench-rmz1x) and `eb_verify.plugins.file_extraction`
(EnterpriseBench-ssikq). One primitive, `eb_verify.scoring.score_file_set`,
owns the math for all of them.

## Problem

Every file-discovery checkpoint scored `matched / len(required_files)` —
recall with no precision term. Listing extra files cost nothing:

```
GT required_files = ['httpx/httpx/_config.py']
Agent answers source_files = every plausible .py in httpx (9–200 files,
one of which is _config.py)  =>  score 1.0, passed
```

The affected tasks need ~4 files out of a few hundred candidates across 2–3
well-known repos (httpx/httpcore/h11). An agent can enumerate every suspect
file per repo and near-guarantee full credit on a 0.40-weight checkpoint
without doing the investigation the checkpoint exists to measure. That is a
direct threat to what EB claims to measure — context retrieval quality — and
it flatters every arm equally, so no A/B anomaly would ever surface it.

ssikq closed the *ambiguity* half of over-credit (one under-specified guess
such as `_client.py` can no longer claim several required files at once).
This decision closes the remaining half: over-listing.

## Options considered

**(a) Set-F1 / Jaccard instead of recall — ADOPTED (F1).** Penalizes
over-listing continuously. Jaccard is a monotone transform of F1
(J = F1/(2−F1)): same ranking, harsher values, less standard. F1 chosen.

**(b) Recall with a cap on |answer|** (score 0 if the agent lists more than
3×|required_files|). Subsumed by F1: at the pass threshold 0.5, full recall
passes iff |answer| ≤ 3|GT| — the same bar, but continuous, with no arbitrary
constant, and a bare cap still gives free credit *inside* the cap for 3×
enumeration. The subsumption holds for **every** matcher, not just exact
equality, because credit is a 1-to-1 pairing (see Semantics). That is a
precondition, not a freebie: score precision off "listed files matching ≥1
required file" instead, and a lenient matcher voids the theorem — 51 differently
-prefixed spellings of one found file each suffix-match it, precision pins at
1.0, and spray scores 1.0 again. The pairing is what makes F1 strictly stronger
than the cap rather than weaker.

**(c) Precision-weighted partial credit (R·P^β).** Introduces a tunable β
with no principled setting.

**(d) Accept and document the ceiling.** Leaves a published-benchmark exploit
that flatters every arm equally.

F1 also matches the diagnostics axis (`lib/eb_metrics/ir_metrics.py`
`f1_at_k`), so reward and observability tell one story.

## Semantics

Implemented in `eb_verify.scoring.FileSetScore` / `score_file_set`
(pure stdlib; tests in `tests/test_file_set_scoring.py`):

- Both lists deduplicated by exact string first. Repeating the right answer
  is not spray; repeating filler earns no extra penalty.
- **One true-positive count, `n_matched`, shared by both sides**, computed as a
  maximum 1-to-1 pairing between listed and required files: a listed file is
  worth at most one required file however many it matches, and a required file
  is found at most once however many spellings of it were listed.
- `recall = n_matched / |GT|`, `precision = n_matched / |listed|`,
  `F1 = 2PR/(P+R)` (0.0 when both are 0). Precision is vacuously 1.0 when
  nothing was listed, which cannot leak a pass: recall is then 0, so F1 is 0.
- Empty `required_files` **raises**. A checkpoint with nothing to find is a
  task-authoring bug; scoring it vacuously perfect would pass every agent.
- `passed` stays `score >= 0.5` (`FILE_SET_PASS_THRESHOLD`, inclusive).
- `detail` exposes found/total, listed count, and P/R/F1.
- **Matcher-agnostic — enforced, not assumed.** The matcher owns all path
  semantics; the primitive owns the math. Because credit runs through the
  pairing, a matcher decides *which* files are credited but never *how many*: no
  matcher, however lenient, can make over-listing free, and no vague guess can
  claim several required files at once. Two independently-counted true-positive
  totals (one per side) would surrender exactly that guarantee — which is why
  there is only one. Blob-style call sites pass a pairwise predicate to
  `score_file_set`; `file_extraction.py`'s refinement rule (per-answer credit,
  ambiguous guesses) constructs `FileSetScore` from its counts directly, owing
  the same contract: counts over distinct files, `n_matched` a 1-to-1 pairing.

## Adoption

Both call-site owners must route through the primitive, or the 28 checkpoints
stop being comparable:

- **EnterpriseBench-ssikq** — `file_extraction.py` replaces its recall-only
  score with `FileSetScore(n_required, n_listed, n_matched)` built from
  `score_answer()`'s output. An ambiguous guess counts as listed and credits
  nothing, so it costs precision — the same bargain the pairing strikes.
- **EnterpriseBench-rmz1x** — the 26 blob scripts repoint at
  `file_extraction.py`, inheriting the primitive through it.

## Impact on historical scores

F1 moves historical file-discovery scores in both directions. Sprayed answers
drop — that is the point. Precise partial answers can *rise past the pass
bar*: an agent that listed 2 of 5 required files and nothing else scored 0.40
(fail) under recall-only but F1 = 2·(1.0·0.4)/1.4 ≈ 0.57 (pass) now, because
harmonic-mean averaging rewards the perfect precision recall-only ignored.
Exact full answers are unchanged at 1.0. The affected tasks already require
re-runs under the rmz1x under-credit fix, so no separate rescoring pass is
scheduled for this change.
