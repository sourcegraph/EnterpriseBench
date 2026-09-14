# Toast-1 vs Code Finder: a retrieval-policy comparison over Sourcegraph

Standalone experiment. **Not** part of the frozen `rryas` headline study
(EB's MCP-vs-baseline-vs-CLI paper capsule) — it must never touch that
capsule or its paid-dispatch gate. See bead
`EnterpriseBench-rryas` / `EnterpriseBench-rryas.36` for why: `rryas` is
mid-flight, frozen, and specifically scoped to Codex/OpenCode Finder parity
slots.

## The question

Given the *same* Sourcegraph retrieval substrate, is Mixedbread's Toast-1 (a
model trained specifically as a search policy) a better search policy than
EnterpriseBench's existing `code_finder` MCP tool
(`agents/harnesses/claude/mcp/sourcegraph.py`, arms `mcp_code_finder` /
`mcp_assisted`)? Toast-1 itself:
https://www.mixedbread.com/blog/toast-1. Its harness (Apache-2.0, the piece
that's actually open) is what this experiment integrates against:
https://github.com/mixedbread-ai/toast-harness.

## What changed from the original proposal

The original plan was to author new gold-evidence tasks against
`sourcegraph/sourcegraph` itself. Two live checks against
`demo.sourcegraph.com` (the instance EB's `mcp_only`/`mcp_code_finder` arms
already use) killed that plan:

- `github.com/sourcegraph/sourcegraph` is **not indexed** there — only
  smaller sourcegraph-owned tool repos (`scip-*`, `deploy-sourcegraph-*`) and
  the `sg-evals/` benchmark mirrors (1146 repos total) are.
- Sourcegraph employee access to an instance that *does* index the core repo
  wasn't available in this session.

Rather than mirror a large repo onto a shared demo instance, or block on
finding/authorizing a different instance, **we pivoted to EnterpriseBench's
own task corpus**: 129 existing tasks already carry real
file-level `required_files` ground truth
(`lib/eb_metrics/retrieval_extraction.py`), and their repos are already
`sg-evals/`-mirrored and indexed on the same instance the `code_finder` MCP
tool searches. This means:

- No new task authoring.
- No new repo mirroring.
- No confound from comparing across two different corpora or instances —
  Toast and Code Finder search the literal same index.
- A real complexity stratification for free: single/dual/tri-repo tasks
  (`select_tasks.py` buckets on this) stand in for the
  localized/multi-file/cross-repo split the original proposal wanted.

## What's built and verified (no MXBAI_API_KEY needed for any of this)

- **`graphql.py`** — thin sync GraphQL client, same auth/header pattern as
  `scripts/infra/verify_sg_indexing.py` (`token` auth, custom User-Agent —
  the instance 403s the stock urllib UA).
- **`sourcegraph_client.py`** — `SourcegraphRetrievalClient`, a real
  implementation of `agent_harness.AsyncRetrievalClient` over the
  Sourcegraph GraphQL Search API (`search`, `grep`, `list_chunks`,
  `metadata_facets`, `files.retrieve`, `files.list`). Chunk identity
  (`store_id`/`file_id`/`chunk_index`) is a fixed 60-line window so a
  search-hit's chunk index and a later `get_chunks` call for the same file
  resolve to the same chunk — verified live, see below. Design notes and
  what's deliberately *not* faithfully mapped (metadata_facets, `sort_by`,
  grep's `targets`) are documented in the module docstring.
- **`verify_adapter.py`** — runs
  `agent_harness.testing.verify_retrieval_client` (scripted generation, no
  model call) against the live adapter. **Passed**, both single-repo
  (`git--v2.41.0`) and tri-repo (`git`+`curl`+`openssl`, the
  `dep-traversal-tri-openssl-001` repo set): bootstrap search, metadata
  facets, `list_chunks` type-sampling, `grep`, `get_chunks`, and a voluntary
  `submit_ranking` over handles minted from the adapter's own results all
  round-tripped correctly.
  ```
  export SOURCEGRAPH_ACCESS_TOKEN=...
  export AGENT_HARNESS_REQUIRE_EXACT_TOKENIZER=0   # heuristic token counting; fine for this check
  python3 verify_adapter.py --store github.com/sg-evals/git--v2.41.0 \
      --query sslCAInfo --grep-pattern sslCAInfo
  ```
- **`select_tasks.py`** — scans `benchmarks/` for tasks with real
  `required_files` ground truth, live-checks each repo's `sg-evals/` mirror
  is actually cloned (not a config assumption), stratifies by repo count,
  and writes a candidate list. Run once already:
  `candidate_tasks.json` (20 tasks: 6 single-repo, 6 dual-repo, 8 tri-repo,
  drawn from 74 available — `dependency_management` and `feature_delivery`
  suites).
- **`run_toast.py`** — runs one candidate task's instruction through Toast-1
  against the Sourcegraph adapter and IR-scores the result with
  `lib/eb_metrics/ir_metrics.compute_ir_scores` (recall/precision/F1/nDCG/MRR
  @k, same functions EB's own retrieval rollups use — see the module-level
  note on why it loads `ir_metrics.py` by path rather than
  `import eb_metrics`: that package's `__init__.py` pulls in `codeprobe`, an
  undeclared dependency per this repo's own `CLAUDE.md`, and isn't installed
  even in the main repo venv). Confirmed the full pipeline up to the model
  call: task loading, `instruction.md` extraction (not the stale
  `task.toml` `[task].prompt` — same trap `CLAUDE.md` warns about
  elsewhere), ground-truth parsing, and Sourcegraph client construction all
  work; it fails exactly where it should, on the missing key.
- **`test_sourcegraph_client.py`** — offline unit tests for the chunk/id
  scheme (6 tests, passing). Live retrieval correctness is
  `verify_adapter.py`'s job, not this suite's.

Dependencies are isolated in `.venv/` (`requirements.txt`) — **not** added to
`lib/pyproject.toml`. Toast's harness isn't on PyPI yet as of 2026-08-14
(published to GitHub the day before); installed via
`pip install git+https://github.com/mixedbread-ai/toast-harness.git`.

## Result: Toast-1 does not belong in the MCP server, but "strict subset" was wrong

The question is whether adding Toast-1 would help our MCP server, by replacing
`code_finder`, augmenting it, or beating `deepsearch`. Read the headline off
the 63-task **hard slice** below, not off the 20-task corpus: a third of the
20-task corpus hands the required filenames to the agent in its own prompt
(see the disclosure section), which manufactures agreement between policies.

### The leak-free measurement: 63 hard-retrieval tasks

Every task here keeps at most a third of its required files namable from the
prompt, and 44 of the 63 disclose nothing at all.

| policy | recall | r@10 | P@5 | MRR | nDCG@10 | files | perfect | zero | median |
|---|---|---|---|---|---|---|---|---|---|
| toast-1 | 0.435 | 0.419 | 0.232 | 0.515 | 0.382 | 8.9 | 7 | 14 | 26s |
| `code_finder` | 0.627 | **0.594** | 0.270 | 0.555 | 0.480 | 9.4 | **21** | 9 | **22s** |
| `deepsearch` | **0.633** | 0.593 | **0.302** | **0.586** | **0.488** | 11.4 | 17 | **5** | 221s |

By repo count, `toast-1` / `code_finder` / `deepsearch`: single (n=16) 0.396 /
0.494 / 0.586, dual (n=35) 0.471 / 0.750 / 0.671, tri (n=12) 0.383 / 0.444 /
0.583. Toast-1 loses every stratum.

The Toast row is its *better* of two identical sweeps; the repeat scored 0.331
(see Repeats).

**Replace: no.** It trails both shipped policies by ~0.2 recall and doubles the
zero-recall count.

**Augment: this is the finding that changed.** On the leaky 20-task corpus
Toast-1's correct set was a strict subset of `code_finder`'s -- union lift
+0.000 on 20 of 20 tasks. On the hard slice it is not: union recall is 0.690
against `code_finder`'s 0.627, a **+0.064 lift**, and Toast-1 contributes a
required file `code_finder` missed on **9 of 63 tasks** (Envoy
`conn_pool_base.h` and `upstream_request.cc`, kustomize
`api/types/kustomization.go`, Cilium `pkg/bpf/collection.go`,
`h11/_connection.py`, `requests/utils.py`, React internals, `.go-version`,
`package.json`). The subset result was an artifact of shared prompt strings,
not of Toast-1 having nothing to add.

An independent repeat of the Toast sweep reproduces this. Toast r2 scores much
worse overall (0.331 against r1's 0.435) yet still adds a file `code_finder`
missed on 9 of 63 tasks, for a union lift of +0.056, and **6 of those 9 tasks
are the same tasks in both runs** (`ccx-incident-032`,
`config-drift-tri-kustomize-001`, `err-provenance-tri-httpx-proxy-001`,
`incident-investigation-dual-cilium-001`,
`refactor-orchestration-tri-babel-001`, `support-mapping-001`). Twelve tasks
appear in the union of the two runs. So the complementarity is a stable
property of a small slice of the corpus, not a count that happens to land on 9
twice.

That is a real but small lift, and it is not free: it costs a second policy in
the loop. `code_finder` + `deepsearch` -- two policies we already ship -- give
a larger one (union 0.714, lift +0.081, 14 of 63 tasks).

**Between the two shipped policies:** on hard tasks they converge (0.627 vs
0.633, inside the run-to-run noise measured below) and `deepsearch` costs 10x
the latency for it (221s vs 22s median). `code_finder` gets more tasks
perfectly right (21 vs 17); `deepsearch` fails fewer outright (5 zeros vs 9)
and is the more stable of the two across repeats.

### The 20-task corpus, for comparison

Same policies, same index, same scorer, plus the fourth (model x access) arm.
These are the numbers the disclosure problem inflates.

| policy | recall | r@10 | P@5 | MRR | nDCG@10 | files | perfect | zero | median |
|---|---|---|---|---|---|---|---|---|---|
| toast-1 | 0.575 | 0.575 | 0.290 | 0.506 | 0.457 | 11.3 | 8 | 5 | n/a |
| `code_finder` | **0.858** | **0.792** | **0.410** | **0.748** | **0.666** | 16.1 | **14** | **1** | 31s |
| `deepsearch` | 0.804 | 0.713 | 0.370 | 0.604 | 0.580 | 14.4 | 12 | **1** | 224s |
| claude-over-adapter | 0.742 | 0.633 | 0.320 | 0.622 | 0.534 | 18.9 | 12 | 3 | **24s** |

The gap to Toast-1 is not an artifact of returning more files: truncating
`code_finder` to Toast-1's own per-task set size still gives 0.717 against
0.575.

One task defeats all three retrieval policies
(`dep-graph-tri-boto3-urllib3-requests-001`).

### Caveats that belong with those numbers

- Toast-1 reaches the index only through the adapter in this directory;
  `code_finder` and `deepsearch` are full agentic loops with Sourcegraph's own
  internal tool access. This compares products as shipped, not model to model.
- The adapter mapping is worth up to 0.12 mean recall on its own (below), so
  Toast-1 is reported at its best measured mapping. The gap to `code_finder` is
  still ~1.6x that confound on the hard slice and ~2.4x on the 20-task corpus.
- `code_finder` and `deepsearch` cite files in three different formats and pick
  per call, with no field saying which. Parsing only the JSON form scored 19 of
  20 tasks as zero; adding blob links still zeroed 6. `test_run_mcp_policy.py`
  pins all three shapes, and `raw_response` is stored in every report so a
  parser change is `--rescore`, not another sweep.

## The model x access ablation: whose fault was 0.575?

The comparison above has a confound it names but cannot remove: Toast-1
reached the index through our adapter while `code_finder` has Sourcegraph's
internal access. So a fourth arm holds the tools fixed and swaps the model:
Claude (`anthropic/claude-sonnet-5` via OpenRouter, `run_claude_policy.py`)
drives the exact `SourcegraphRetrievalClient` seams Toast drove -- same
`search_corpus`, `grep`, file read, file list; same instruction.md; same
4-round budget Toast used on all 20 tasks; same sampling parameters as
`toast_generate.py`.

| policy | tools | recall (20) | recall (excl. 2 punts) |
|---|---|---|---|
| toast-1 | adapter | 0.575 | 0.639 |
| claude-over-adapter | adapter | 0.742 | 0.824 |
| `code_finder` | internal | 0.858 | 0.852 |

Claude over the external adapter posts identical per-task recall to
`code_finder` on 17 of 20 tasks and never beats it. Two of the three losses are
`schema-evolution-001/-002`, where the model answered the forced final round
with `{"files": []}`: both instructions end by demanding a written report
deliverable (`SCHEMA_IMPACT.md`), and the model punted rather than answer with
files. Conditional on answering, external adapter access lands within 0.028 of
internal access on this corpus.

Readout:

- **Access was not the bottleneck.** A general frontier model over the same
  tools Toast had closes all but 0.028 of the Toast-to-`code_finder` gap.
- **Toast-1 the model is the deficit**: 0.575 vs 0.742 with tools, budget,
  and sampling held fixed -- and Toast is the policy *purpose-trained* for
  this tool surface.
- **The "Toast with first-class internal access" experiment is dead.** The
  access lever measures 0.028 here against a model gap of 0.213; internal
  access has no headroom to rescue that.
- Cost does not rescue it either: Toast ran ~$0.037/task against
  claude-over-adapter's ~$0.18/task, but 0.575 vs 0.824 recall is not a
  trade, and `code_finder`'s serving cost is Sourcegraph's, not ours.

Caveats: `code_finder`'s internal model is unconfirmed, so "model roughly
fixed" in the access comparison is an assumption; one sample per task at
temperature 0.7; and the two punts are real single-shot behavior, which is why
both the 0.742 and the 0.824 readings are reported. The empty-submit failure
is the same deliverable-mismatch `code_finder` showed when it apologized about
`IMPACT_REPORT.md` -- a prompt-contract issue, not retrieval.

During this sweep the demo instance also rejected the adapter's
`tree(recursive:)` GraphQL argument that `verify_adapter.py` had passed with
three days earlier -- the schema moved to `entries(recursive:)`. Fixed in
`sourcegraph_client.py`; ten tasks initially died on it, which is also why
`run_claude_policy.py` now returns backend errors to the model as tool
results instead of voiding the task.

## Repeats: the single-sweep table had error bars, and they cut against Toast

Every policy was re-run on the same 20 tasks (`*_r2`/`*_r3` directories;
`repeat_variance.py` computes this). Mean file recall per repeat:

| policy | r1 | r2 | r3 | unstable tasks | mean per-task swing |
|---|---|---|---|---|---|
| toast1 | 0.575 | 0.504 | 0.490 | 11/20 | 0.364 |
| code_finder | 0.858 | 0.729 | 0.812 | 7/20 | 0.163 |
| deepsearch | 0.804 | 0.821 | 0.787 | 5/20 | 0.075 |
| claude-over-adapter | 0.782* | 0.724* | (blocked*) | 4/13 | 0.250 |

\* The OpenRouter key hit its total spend limit 13 tasks into r2
(2026-08-17); both rows resume from cache once the limit is raised. The
claude-over-adapter row is over those 13 tasks only, which is why its r1 reads
0.782 rather than the 20-task 0.742.

What the repeats change and don't change:

- **The ranking survives.** Toast-1 never beats `code_finder` in any repeat;
  its best run (0.575) is the one the headline table shows, so the published
  gap (0.283) is the *floor* — the three-repeat means put it at 0.523 vs
  0.800.
- **Toast-1 is the least stable policy measured**: more than half its tasks
  change recall between identical runs, with a mean swing of 0.36 recall.
  At the infrastructure layer this matters independently of the mean — a
  search agent that intermittently misses a required file forces callers to
  re-verify everything it returns.
- **`code_finder` is not free of this**: r2 dipped to 0.729, and
  `ccx-dep-trace-106` swung a full 1.0 between runs. Single-sweep
  comparisons of any two policies on this corpus carry roughly ±0.13 of
  run-to-run noise, which is wider than the `code_finder`-to-`deepsearch` gap
  on the hard slice; `deepsearch` is the most stable of the four (means
  within 0.034, per-task swing 0.075).
- **The hard slice makes Toast's instability worse, not better.** A second
  63-task sweep scored 0.331 against r1's 0.435, with zero-recall tasks going
  from 14 to 26 and perfect tasks from 7 to 4. 35 of 63 tasks changed recall
  between the two identical runs, mean swing 0.44 on the tasks that moved.
  Toast r2 is the run to quote if the question is what a caller can rely on:
  0.331 is roughly half `code_finder`'s 0.627.

## Methods note: sizing the adapter confound

Three full 20-task sweeps have run (2026-08-17), identical in every respect
except how the adapter's `search_corpus` seam maps onto Sourcegraph:

| mapping | mean recall | rc1 | rc2 | rc3 | p@5 | perfect | zero | cost |
|---|---|---|---|---|---|---|---|---|
| `patternType: standard` | 0.496 | 0.653 | 0.500 | 0.375 | 0.270 | 2 | **3** | $0.64 |
| `patternType: keyword` | 0.458 | 0.222 | 0.583 | 0.542 | 0.230 | 3 | 6 | $0.59 |
| `nls_search` (MCP) | **0.575** | 0.611 | 0.750 | 0.417 | **0.290** | **8** | 5 | $0.73 |

Same model, same policy, same corpus, same ground truth, same tasks. **13 of
20 tasks changed outcome on the retrieval mapping alone**, two of them a full
0.0 -> 1.0 swing (`ccx-dep-trace-106`, `camel-routing-arch-001`).

This is calibration of *this* adapter, not a finding about Sourcegraph's
products. It is recorded because it sets the error bar: a Toast-vs-`code_finder`
gap smaller than 0.12 would not have been attributable to the policy. The
measured gap is 0.283 on the 20-task corpus and 0.192 on the hard slice, so it
survives both. Toast-1 runs at `nls_search`, its best
mapping, in the comparison above.

Why the text-search paths lose, measured rather than assumed:

- `standard` treats a multi-word query as a phrase. `search_corpus` receives
  natural language, so it matches only where that phrasing appears verbatim:
  "optimization pass manager" against gcc and "client session configuration"
  against boto3 both return `matchCount=0` on an index holding 134,769 and
  1,581 files respectively. `"avatar source"` against zulip returns 5 files
  purely because `default_avatar_source` is a real identifier there.
- `keyword` ANDs every term, which starves the long instruction-derived
  queries single-repo tasks produce — rc1 recall collapses 0.653 -> 0.222.
- `nls_search` (OR semantics with stemming) is the only one of the three that
  is a natural-language seam, and it triples the perfect-recall task count.

Neither text-search pattern type is a retrieval API, and the default one
fails *silently* — zero results, not an error.

## Limits

Toast token counts turned out to be exact after all. The
`"token_counting": "chars_per_4_estimate"` label in the first sweep reflected
the harness's *internal budgeter* mode (no exact tokenizer installed), but the
totals the harness returns come from the API's own `usage` blocks:
`toast_generate.py` now also records provider usage per call into each
report's `api_usage` field, and on all 40 repeat-sweep tasks the two ledgers
match integer-for-integer. OpenRouter cannot meter Toast-1 (the model is
served only by Mixedbread; OpenRouter reports usage only for calls it
proxies) — the response field is the exact count, and we had it all along.
`code_finder` and `deepsearch` internal token spend is Sourcegraph-side and
unobservable from here; their cost is reported as wall-clock, which is
measured.

`leakage_audit.py` quantifies the corpus problem that moved the headline:
33/65 required files (51%) in the 20-task corpus are disclosed by the task's
own prompt (11 by basename, 22 by stem), and only 3/20 tasks disclose nothing.
Scores there partly measure string-matching. That is fixed at the source
rather than noted as a caveat (see the disclosure section), and the 63-task
hard slice exists so the headline does not rest on it.

The 20-task corpus is 20 tasks over two suites, strata of 6/6/8; the hard
slice is 63 tasks with strata 16/35/12. Per-stratum magnitudes are indicative
at both sizes.

`sg_code_finder` takes a single `task` string, so each policy got
`instruction.md` plus an appended repo scope. These are full agentic task
prompts, and `code_finder` sometimes reads them as instructions to execute:
six responses opened by explaining it could not write
`/workspace/analysis/IMPACT_REPORT.md`. It still did the retrieval and cited
files, so the scores stand, but a retrieval tool handed an agentic prompt is a
tool-description question worth separating from retrieval quality.

## Validity audit: are the retrieved paths real, and did the scorer count them?

`audit_traces.py` and `aligned_scoring.py` exist because a recall number is
only a retrieval measurement if two things hold: the paths a policy emitted
exist in the repo it searched, and the scorer credits the ones that match.
Neither was checked before, and one of them was wrong.

### The scorer was under-crediting every arm, unevenly

`lib/eb_metrics/ir_metrics.py::normalize_path` strips one leading path segment
unless it is in a 22-name allowlist (`src`, `lib`, `pkg`, `cmd`, ...). Ground
truth is repo-qualified for part of the corpus and repo-relative for the rest
(`EnterpriseBench-7x8v`); retrieval output is uniformly repo-relative. For a
repo whose top directory is not on the list — `sdk`, `httpx`, `axum`, `tokio`,
`packages`, `pilot`, `source`, `fastapi`, `pydantic` — ground truth loses only
its repo name while the retrieved path loses a **real** directory:

```
GT        otel-go/sdk/trace/batch_span_processor.go  ->  sdk/trace/batch_span_processor.go
retrieved         sdk/trace/batch_span_processor.go  ->      trace/batch_span_processor.go
```

A correct retrieval scored as a miss. EB's own `compute_run_ir_scores` is
insulated because it runs `_align_retrieved_to_required` first — but that
aligner repairs only the opposite asymmetry (retrieved *longer* than required,
`/workspace/zulip/zerver/x.py`); its guard is `len(comps) >= n`. All four
runners here called `compute_ir_scores` directly and skipped it.

`aligned_scoring.py` matches on a component-wise suffix relation in **either**
direction, so it is agnostic to which side carries the repo name, with a guard
that a bare filename may only match another bare filename. Every sweep on disk
was rescored offline (`rescore_aligned.py`; pre-fix values are preserved under
`ir_scores_unaligned` in each report, and `--rollup` refreshes the embedded
copies in the `-o sweep.json` files so no pre-fix score is stranded in a file
that reads like a sweep record):

| sweep | before | after | delta | tasks improved |
|---|---|---|---|---|
| toast-1, hard slice | 0.314 | 0.435 | +0.121 | 16/63 |
| `code_finder`, hard slice | 0.470 | 0.627 | +0.156 | 17/63 |
| `deepsearch`, hard slice | 0.485 | 0.633 | +0.148 | 18/63 |
| toast-1, 20 tasks | 0.500 | 0.575 | +0.075 | 3/20 |
| `code_finder`, 20 tasks | 0.758 | 0.858 | +0.100 | 4/20 |
| `deepsearch`, 20 tasks | 0.729 | 0.804 | +0.075 | 3/20 |
| claude-over-adapter, 20 tasks | 0.667 | 0.742 | +0.075 | 3/20 |

The rankings survive, but the correction is **not uniform across arms** — it
depends on each repo's top-level directory name and on the citation form each
policy emits — so the uncorrected numbers were a confound, not a constant
offset. The `code_finder`-to-Toast gap on the hard slice widens from 0.156 to
0.192.

The obvious worry about a suffix matcher is that it over-credits, and a first
pass did. Two constraints close that, each one an answer to a false credit
found in the reports rather than a hypothetical:

- **Bounded prefix drop**, at most 2 components. The only legitimate reason for
  a length difference is a qualifier prefix: a repo name, and at most one
  conventional source root above it (`src/flask/json/__init__.py` cited as
  `json/__init__.py`). Unbounded, `listener/ConcurrentMessageListenerContainer.java`
  was credited against a seven-deep Spring path, and gatekeeper's **vendored
  copy** `constraint/vendor/github.com/open-policy-agent/opa/v1/ast/parser.go`
  was credited against OPA's own `ast/parser.go` in a different repo.
- **One-to-one assignment.** Reports carry no per-path repo tag, so one cited
  path can suffix-match two required files in a multi-repo task:
  `pkg_resources/__init__.py` matches both setuptools' copy and pip's vendored
  one in `config-drift-dual-setuptools-pip-001`. Three paths across the sweeps
  matched more than one required file. A policy that cited one path retrieved
  one file, so each cited path now claims at most one required file and each
  required file is claimed at most once, resolved by smallest prefix drop
  first.

Both constraints were added after the table above was computed, and neither
moved a published score: every real credit also came from a citation exact
enough to survive the tightening. So this is correctness insurance, not a
renumbering. `test_aligned_scoring.py` pins the behaviour in 26 tests built
from paths taken out of actual reports, and newly-credited matches were also
read by hand across `otel-go/`, `axum/axum/`, `envoy/`, `istio/`, `babel/`,
`fastapi/`, `pydantic/`, and `spring-framework/`.

Corrected reports carry `"scoring": "aligned_suffix_v1"`, and
`compare_policies.py` exits with the rescore command rather than read a
directory whose reports lack the stamp. A pre-fix sweep cannot be quietly
averaged together with corrected ones.

Filed against the shipped metric as `EnterpriseBench-28lja`; EB's own scorer is
deliberately left alone, because `compute_ir_scores` feeds the frozen `rryas`
capsule.

### Do the emitted paths exist?

Every (repo, path) pair from the three hard-slice sweeps was verified against
the same demo.sourcegraph.com index the policies searched, in two stages: an
exact blob lookup, then — for anything that failed it — a search for any file
in the repo *ending with* that path. The second stage matters: `sansio/app.py`
is a real Flask file cited without its `src/flask/` prefix, which costs
precision but is a real retrieval, not an invention.

| policy | paths | abbreviated | nonexistent | credit from guessable root files |
|---|---|---|---|---|
| toast-1 | 561 | 0 (0.0%) | **0 (0.0%)** | 7/85 (8.2%) |
| `code_finder` | 595 | 5 (0.8%) | 8 (1.3%) | 6/119 (5.0%) |
| `deepsearch` | 720 | 31 (4.3%) | 36 (5.0%) | 7/125 (5.6%) |

Toast-1 emitted **zero** paths that do not exist — every path it returned is a
real file, which is what a retrieval policy over a real index should produce
and is a point in its favor the recall numbers do not show. `deepsearch`
invents the most (5.0%), and the inventions are plausible: a Django migration
filename that was never created, `Modules/_ssl.c` (a CPython file) in a
`requests` task.

The "guessable root files" column is the discount that belongs on all three: files
like `package.json`, `go.mod`, and `.go-version` score identically whether a
policy searched for them or named them from memory. It is 5-8% of credit
everywhere, so it does not move the ranking. The column counts credited
required *files*, the same unit `file_recall` uses, so it reads against the
recall tables directly.

**What this audit cannot check**, and the reason is a gap worth closing: it
cannot tell whether an existing, non-guessable path was reached by a tool call
or recalled parametrically from training. That needs the tool-call transcript.
`run_toast.py` persists only `retrieved_paths` and token counts;
`run_mcp_policy.py` persists `raw_response`, which is the model's prose rather
than its calls. Filed as `EnterpriseBench-h4kba`.

## Why the corpus leaked, and the gate that now stops it

The obvious question about a 51%-disclosed corpus is what the task QA was
doing. Three separate things, none of them a lapse on its own:

**The gate could not see this tier.** `scripts/audit_instructions.py`'s
GT-leakage check is `if path in instruction` — the exact full path. Zero of
the 33 disclosures were full paths; all were basename (`realms.py`) or stem
(`avatar`). The check reported the corpus clean because the corpus is clean
by its definition. It also ran nowhere: CI invoked only
`audit_consistency.py`, and `make verify` covers mix, schema preflight, and
the cross-repo necessity test.

**Most of these prompts are supposed to name files.** The tasks were authored
for agentic execution. `feature_delivery/schema-evolution-004` names its
migration and three call sites because the deliverable is a written impact
analysis of exactly those files; its `required_files` are the files it names.
Nothing leaked there — retrieval was never the measured skill. What created
the problem is this experiment repurposing `required_files` as *retrieval*
ground truth, which converts deliberate scenario setup into an answer key.

**Selection had no disclosure axis.** `select_tasks.py` stratified by repo
count and checked that each repo was indexed. Retrieval difficulty was not a
criterion, because nothing measured it until `leakage_audit.py`.

What exists now (EnterpriseBench-vkhav, -yso6z):

| Piece | What it does |
|---|---|
| `scripts/audit_disclosure.py` | Canonical tiered audit — full path / basename / stem (≥4 chars, word-boundary), over the same `required_files_from_ground_truth` extractor scoring uses. Reports all tasks, gates enrolled ones. |
| `configs/retrieval_slice.json` | Enrollment manifest: 63 tasks, `max_disclosed_fraction` 0.334. Keys are suite-qualified — task ids are not unique (`calibration-002` lives in three suites at different disclosure). |
| CI step in `ci.yml` | Runs `--gate` after `audit_consistency.py`. An enrolled task that grows a hint fails the build; so does manifest drift, rather than passing silently. |
| `select_tasks.py --max-disclosure` | Default 1/3. Every candidate now carries `disclosed_fraction`. |
| `tests/test_audit_disclosure.py` | 15 tests: each tier boundary, both gate failure modes, and the committed manifest against the committed corpus. |

Enforcement is opt-in by design. Disclosure is legitimate for execution tasks;
only tasks scored on retrieval are enrolled.

Corpus-wide, the audit finds 138/377 required files disclosed across 129
tasks.

## Reproducing

```bash
export SOURCEGRAPH_ACCESS_TOKEN=...          # MXBAI_API_KEY in .env.local
export AGENT_HARNESS_REQUIRE_EXACT_TOKENIZER=0

python3 run_toast.py --all --outdir reports -o sweep.json      # Mixedbread, ~$0.73
python3 run_mcp_policy.py --policy code_finder --outdir reports_code_finder
python3 run_mcp_policy.py --policy deepsearch  --outdir reports_deepsearch
python3 run_mcp_policy.py --rescore --outdir reports_code_finder   # free re-parse
python3 run_claude_policy.py --outdir reports_claude_adapter   # OpenRouter, ~$3.52
python3 clone_repos.py                                # 39 shallow clones, 3.6 GB
python3 run_local_policy.py --outdir reports_claude_local      # OpenRouter
python3 compare_policies.py --json comparison.json

# repeats land in *_r2/_r3 directories; then:
python3 repeat_variance.py
python3 leakage_audit.py

# the hard slice: 63 tasks at <=1/3 prompt disclosure
python3 select_tasks.py --limit 300 --all-suites --max-disclosure 0.334 \
    -o hard_slice_tasks.json
python3 run_mcp_policy.py --policy code_finder --candidates hard_slice_tasks.json \
    --outdir reports_hard_code_finder
python3 run_toast.py --all --candidates hard_slice_tasks.json \
    --outdir reports_hard_toast -o hard_sweep_toast.json
python3 run_mcp_policy.py --policy deepsearch --candidates hard_slice_tasks.json \
    --outdir reports_hard_deepsearch
python3 compare_policies.py --dir toast1=reports_hard_toast \
    --dir code_finder=reports_hard_code_finder \
    --dir deepsearch=reports_hard_deepsearch --json hard_comparison.json

# validity audit: path existence against the index, and the scoring correction
python3 audit_traces.py --dir toast1=reports_hard_toast \
    --dir code_finder=reports_hard_code_finder \
    --dir deepsearch=reports_hard_deepsearch --json trace_audit.json
python3 rescore_aligned.py --dry-run reports_hard_toast   # drop --dry-run to write
python3 -m pytest test_aligned_scoring.py -q             # 26 tests over real paths

# the QA gate itself (from the repo root)
python3 scripts/audit_disclosure.py --gate configs/retrieval_slice.json
PYTHONPATH=lib python3 -m pytest tests/test_audit_disclosure.py -q

.venv/bin/python -m pytest test_sourcegraph_client.py test_run_mcp_policy.py \
    test_run_claude_policy.py test_run_local_policy.py test_toast_generate.py \
    test_aligned_scoring.py -q
```

Neither MCP policy needs Docker or a Claude account; both are tool calls over
the same transport `agents/harnesses/claude/mcp/sg_cli.py` uses. Total
Mixedbread spend for all three Toast-1 sweeps: $1.96.

## Next steps

1. Nothing here justifies putting Toast-1 in the MCP server as a replacement
   for `code_finder`: it loses 0.435 to 0.627 on the hard slice, loses every
   repo-count stratum, is the least stable policy measured, and the ablation
   puts the access lever at 0.028 against a model gap of 0.213.
2. The augment case is open rather than closed. Toast-1 contributes a required
   file `code_finder` misses on 9 of 63 hard tasks (+0.064 union lift), and an
   independent repeat reproduces it on 9 of 63 (+0.056) with 6 tasks shared,
   so what the numbers support is a second-opinion policy, not a replacement.
   Two policies we already ship do better on the same axis (`code_finder` +
   `deepsearch`, +0.081), so the bar for adding a third is that lift, not
   zero.
3. `code_finder` and `deepsearch` converge on hard tasks (0.627 vs 0.633,
   inside the ±0.13 repeat noise) while `deepsearch` costs 10x the latency.
   That is a result about our own products and the one most worth confirming.
4. The ablation's side finding is worth its own thread: a 4-round frontier
   model over a thin external adapter (nls_search + grep + read + list)
   matched `code_finder` task-for-task on 17/20. Whatever `code_finder` spends
   beyond that budget bought nothing measurable on this corpus.
5. Make the toast arm auditable (`EnterpriseBench-h4kba`): it persists no
   tool-call transcript, so a credited path cannot be traced to the search
   that found it.
6. A frontier-agent-direct-MCP arm (Claude Code, `mcp_only`) remains a real
   extra point and needs paid Docker dispatch, which must not touch the frozen
   `rryas` capsule or its paid-dispatch gate.
