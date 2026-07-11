# EnterpriseBench: Measuring Codebase Understanding and Context Gathering Across Distributed Codebases

**Draft — v0.1**

## Abstract

We introduce **EnterpriseBench**, a benchmark for evaluating how well coding
agents find and comprehend the right code across large, distributed enterprise
codebases. While prior benchmarks (SWE-bench, ITBench, DevOps-Gym) primarily
test single-file patches in single repositories, enterprise developers spend a
substantial fraction of their time on cross-repo navigation, dependency
tracing, incident investigation, and producing diverse artifacts that are not
code patches. EnterpriseBench provides 158 tasks spanning ten task types
across seven enterprise workflow suites, drawn exclusively from real
open-source codebases, with 67.7% of tasks requiring true multi-repo
reasoning. The benchmark evaluates agents under three controlled tool-access
modes (baseline, MCP-only, hybrid) so that the contribution of code
intelligence tooling can be measured directly. Verification uses a centralized
plugin library with nine artifact-type-aware validators and 2–5 graduated
checkpoints per task for partial credit. We report initial results on 108
tasks evaluated in all three modes, with a calibration bias check confirming
the benchmark does not unfairly favor MCP-equipped agents on easy tasks.

## 1. Introduction

### Motivation

Enterprise software work is rarely confined to a single repository, a single
file, or a single artifact type. A typical week for a senior engineer mixes:

- tracing a breaking API change from a shared library through 3–4 consumer
  services,
- reading an alert and following its origin across half a dozen services and
  a metrics pipeline to a root cause,
- assessing the blast radius of a database schema migration across a monorepo
  before approving a PR,
- writing a runbook, a postmortem, and a configuration change in support of
  the same incident.

These activities exercise a single underlying capability — **codebase
understanding and context gathering** — but most existing coding benchmarks
test a narrower capability (apply a patch to make tests pass) on a narrower
substrate (one file in one repository).

### The gap

| Benchmark        | Scope                       | Artifact            | Multi-repo? |
| ---------------- | --------------------------- | ------------------- | ----------- |
| SWE-bench        | One repo, one PR            | Code patch          | No          |
| HumanEval / MBPP | Toy snippets                | Single function     | No          |
| ITBench          | SRE incident playbook       | Action sequence     | Partial     |
| DevOps-Gym       | Build / config tasks        | Config edits        | Partial     |
| LiveCodeBench    | Competitive programming     | Code                | No          |
| **EnterpriseBench** | **Real OSS, distributed** | **9 artifact types** | **Yes (67.7%)** |

EnterpriseBench is positioned to measure what enterprise developers actually
do, with tool access treated as a controlled independent variable so that
contributions like Sourcegraph MCP can be evaluated honestly.

### Contributions

1. A 158-task benchmark drawn from real OSS repositories with genuine
   dependency relationships, organized by enterprise workflow rather than
   artificial SDLC/Org splits.
2. A taxonomy of ten task types selected via a structured convergence debate,
   each chosen for measurable codebase-understanding signal and bounded
   verifiability.
3. A centralized verification library (`eb_verify`) with nine
   artifact-type-aware plugins, replacing per-task verifier copies and
   eliminating verifier drift.
4. A controlled three-mode evaluation protocol (baseline, MCP-only, hybrid)
   with a calibration stratum that catches benchmarks-bias toward
   MCP-equipped agents.
5. Layered ground truth combining deterministic checks, LLM-curated relevance
   labels, and a solve-verification step that confirms context sufficiency.

## 2. Benchmark Design

### 2.1 Task taxonomy

EnterpriseBench contains ten task types selected through a structured
convergence debate over thirty candidate task ideas, prioritized by their
expected signal on cross-repo code intelligence (Sourcegraph MCP rating in
parentheses):

| Task type                  | MCP signal | Active tasks | Description                                              |
| -------------------------- | ---------- | -----------: | -------------------------------------------------------- |
| `api_contract`             | ★★★★★      |           18 | Trace breaking API changes across consumer repos          |
| `refactor_orchestration`   | ★★★★★      |           13 | Plan and execute multi-repo refactoring                   |
| `dependency_graph`         | ★★★★½      |           26 | Traverse and reason about dependency chains               |
| `monorepo_boundary`        | ★★★★½      |           10 | Navigate cross-package dependencies in monorepos          |
| `db_schema_evolution`      | ★★★★       |            8 | Assess schema change impact across services               |
| `error_provenance`         | ★★★★       |           18 | Trace error messages to originating code paths            |
| `support_code_mapping`     | ★★★½       |           13 | Map customer-facing behavior to underlying implementation |
| `dead_code_necropsy`       | ★★★★       |           10 | Identify unreachable code and feature-flag remnants       |
| `incident_investigation`   | ★★★        |           26 | Root-cause analysis across multi-service incidents        |
| `config_drift`             | ★★★        |           16 | Detect divergence between config sources and deployments  |
| **Total**                  |            |      **158** |                                                          |

### 2.2 Workflow suites

Tasks are organized into seven enterprise workflow suites that reflect
natural developer workflows rather than artificial SDLC/Org boundaries:

| Suite                   | Tasks | Representative task                                                |
| ----------------------- | ----: | ------------------------------------------------------------------ |
| Dependency Management   |    44 | Trace gRPC-Go metadata-API change through `etcd` and `kubernetes`  |
| Customer Escalation     |    34 | Map an error message to its originating service across boundaries  |
| Feature Delivery        |    18 | Assess schema-migration impact across monorepo packages            |
| Technical Debt          |    23 | Plan multi-repo refactor with topological execution order          |
| Incident Response       |    26 | Investigate alerts spanning multiple interconnected services       |
| Platform Engineering    |    16 | Detect drift between IaC definitions and live configuration        |
| Security Operations     |     7 | Assess vulnerability exposure across a dependency tree             |

(Counts are based on the active benchmark snapshot used for paper figures and
will be updated automatically by `make paper-figures`.)

### 2.3 Repository selection

Every task uses **real open-source repositories** with **real dependency
chains**. Synthetic, toy, and minimally constructed repositories are
explicitly out of scope. Representative chains include:

- **Go:** `grpc-go` → `etcd` → `kubernetes`
- **Python:** `requests` → `boto3` → `awscli`
- **Java:** `protobuf-java` → `grpc-java` → `envoy-control-plane`
- **TypeScript:** `typescript` → `eslint` → `next.js`
- **Cross-language:** Protobuf schemas → Go server → Python client → TypeScript frontend

Repos and exact revisions are pinned in `configs/repo_versions.json` for
reproducibility, with staleness detection via
`scripts/infra/check_repo_staleness.py`.

### 2.4 Difficulty stratification

The PRD targets a strict multi-repo share of at least 45% to ensure the
benchmark cannot be solved with single-repo navigation alone. The current
mix exceeds that floor:

| Stratum                  | Share | Tasks |
| ------------------------ | ----: | ----: |
| Calibration              |  8.9% |    14 |
| Large single-repo        | 15.8% |    25 |
| Dual-repo                | 46.2% |    73 |
| Tri-repo                 | 12.7% |    20 |
| Multi-repo (4–5)         |  8.9% |    14 |
| Monorepo cross-package   |  7.6% |    12 |

**Strict multi-repo share: 67.7%** (107/158, well above the 45% PRD floor).

The calibration stratum is a deliberate single-repo, small-codebase set
designed so that MCP-equipped agents should have minimal advantage. We use it
to detect benchmark bias (§6.5); §3.1 situates this calibration check
alongside the information-parity precondition a prior CodeScaleBench audit
motivates.

This stratification is structural, keyed to repo count. §3.2 develops a
second, behavioral axis, localization difficulty, orthogonal to repo count
and read from baseline transcripts rather than task metadata.

### 2.5 Cross-Repo Necessity Test (CRNT)

Multi-repo tasks are only meaningful if cross-repo reasoning is genuinely
required to solve them. We enforce this with the **Cross-Repo Necessity
Test** (`scripts/validation/crnt_validator.py`): a task is rejected as
multi-repo if the ground-truth answer can be assembled by inspecting only one
of its repos. CRNT validation runs as a pre-flight gate before tasks enter
the benchmark. CRNT is a structural check on repo necessity; §3.3 develops a
complementary axis, instruction leakage, that asks a different question:
whether the instruction text gives away where in the code the answer sits,
independent of how many repos are involved.

### 2.6 Ground truth (layered)

Single-source curators are vulnerable to systematic blind spots. Following
prior CodeScaleBench analysis (curator F1 = 0.70 on multi-repo), we use a
three-tier ground-truth pipeline:

| Tier                       | Method                                         | Coverage                                | Uses LLM |
| -------------------------- | ---------------------------------------------- | --------------------------------------- | -------- |
| 1. Deterministic           | AST parsing, import graphs, dependency manifests | Structural, mechanically verifiable     | No       |
| 2. LLM curator             | Semantic relevance, cross-backend cross-check    | Configs, docs, cross-cutting concerns   | Yes      |
| 3. Solve-verification      | Different model attempts task using only curated context | Confirms context sufficiency        | Yes      |

The curator never has access to Sourcegraph or any other tool that is being
evaluated, ensuring tool-independent ground truth.

## 3. Methodology

This section states the analytical design used to interpret cost and outcome
differences between EnterpriseBench's tool-access modes, building on prior
measurement from CodeScaleBench, EnterpriseBench's direct predecessor (§8). No
retrieval effect in this paper is reported as a single corpus-wide number:
every effect is reported within a difficulty stratum, because the underlying
task mix otherwise determines the aggregate on its own. The design rests on
seven commitments: an information-parity precondition on the baseline, a
localization-difficulty stratification distinct from the repo-count strata of
§2.4, a corpus leakage audit, a subagent-inclusive cost decomposition, a
retrieval-quality axis scored separately from reward, a tiered screening
protocol, and an arm-by-regime comparison over EnterpriseBench's three
tool-access modes.

### 3.1 The baseline receives the same information as the retrieval arm

A retrieval effect is only interpretable when both configurations see the same
code. CodeScaleBench's predecessor corpus violated this: a prior parity audit
of a frozen CodeScaleBench snapshot classified 288 tasks as a paired checkout,
48 as an empty baseline workspace, 10 as partial, and 21 as unknown, and those
48 empty-baseline tasks alone carried the entire apparent retrieval lift, a
delta of +0.091 reward; across the 288 correctly paired tasks the difference
was statistically null.[^csb-parity]

EnterpriseBench's own sandbox construction runs the same risk in the opposite
direction. `Dockerfile` (baseline) performs a standard local clone of every
required repository; `Dockerfile.sg_only` (the `mcp_only` mode) ships an empty
workspace with Sourcegraph MCP mandatory; `Dockerfile.hybrid` clones locally
and also exposes MCP (`scripts/sandbox/dockerfile_generator.py`). That
asymmetry is deliberate, an ablation meant to isolate MCP-only capability, but
it means an `mcp_only`-vs-baseline delta is not an information-parity
comparison in the sense CodeScaleBench's audit defines: baseline holds
strictly more local information than `mcp_only` by construction. Hybrid, a
strict superset of baseline plus MCP, is the parity-matched arm, and §7.2's
note on mode comparability follows from the same logic. EnterpriseBench does
not yet run an outcome-blind census of baseline workspace completeness
comparable to CodeScaleBench's `parity_audit.py`; adding one is the natural
extension of the calibration check already in place (§6.5).

### 3.2 Localization difficulty is the stratifying variable

The repo-count strata of §2.4 (calibration, large single-repo, dual, tri,
multi, monorepo) capture how many repositories a task touches, not how hard
the relevant code is to find within them. CodeScaleBench's stratifying
variable for that second, orthogonal axis is baseline grep-breadth: the number
of distinct search-and-scan operations the local-grep baseline issues before
it localizes an answer, read directly from the baseline transcript.
Grep-breadth is observable without an oracle and defined on the baseline arm
alone, so it does not depend on which retrieval configuration is under test.
It orders tasks from well-localized, where a named symbol resolves in one
grep, to navigation-hard, where the answer is spread across files the baseline
must scan broadly to assemble.

The variable earns its place because the cost effect of retrieval tracks it.
On CodeScaleBench's 113-task paired substitution run, the per-task cost delta
between retrieval and baseline correlated r = −0.57 with baseline
grep-breadth: retrieval's cost penalty over baseline closes and then reverses
as the baseline's own search broadens.[^csb-grepbreadth] EnterpriseBench
inherits the same prediction, that `mcp_only` and `hybrid` should look worst,
cost-wise, on the well-localized end of the distribution and best on the
navigation-hard end, and the three-mode design (§4.1) already produces the
paired per-task deltas needed to test it. Cutting EnterpriseBench's own
grep-breadth strata and reporting per-stratum task counts is pending the
arm×regime run described in §3.7.

<!-- TODO(jn73.11): grep-breadth cut points + per-stratum task counts for
     EnterpriseBench's own task corpus, from the arm x regime runs. -->

### 3.3 Corpus leakage audit

An instruction that names the answer's location turns a retrieval task into a
lookup, and a corpus dense with such instructions caps any aggregate retrieval
effect near zero regardless of tool quality. CodeScaleBench's audit is a
census over its 275 maintained tasks that classifies each instruction by what
it hands the agent: an exact file path leaked verbatim, a symbol or directory
name leaked, a behavioral description whose terms map directly to a greppable
string, or grep-hard, where localization needs navigation that no instruction
term shortcuts. That census found the corpus heavily lookup-shaped: 38% of
instructions leak an exact path, symbol, or directory outright, a further 59%
are behavioral-only, and enough of the behavioral-only remainder maps to
greppable strings that 74% of the corpus is grep-trivial by construction; the
`feature` and `test` categories score 0% enterprise-complex, meaning they
measure authoring rather than retrieval.[^csb-leakage] Retrieval-win rate rose
monotonically as leakage fell across CodeScaleBench's 113-task paired
analysis, evidence that leakage, not tool capability, drove that corpus's
aggregate:

| Leakage bucket                 | Retrieval-win rate |
| ------------------------------ | ------------------: |
| (a) exact path leaked          |                  0% |
| (b) symbol / directory leaked  |                  4% |
| (c) behavioral, greppable      |                 28% |
| grep-hard                      |                 32% |

The gradient is monotonic: the retrieval arm wins more often exactly as the
instruction gives away less, which is why a leakage bucket belongs alongside
every stratified effect.

Leakage answers a different question than the Cross-Repo Necessity Test of
§2.5. CRNT rejects a task if its ground truth can be assembled from one repo;
leakage classifies whether the instruction text gives away where in the code
the answer sits, repo count aside. A single-repo task can be leakage-clean and
a five-repo task can leak an exact path. EnterpriseBench has not yet run an
equivalent census over its own 158-task corpus; doing so is the natural
companion to CRNT as a pre-flight gate (§2.5) and would let every reported
effect carry a leakage bucket alongside its repo-count stratum.

### 3.4 Cost is decomposed on a subagent-inclusive basis

CodeScaleBench's cost unit sums `total_cost_usd` over the main agent and every
subagent it spawns, priced by token type at the model's published
per-million-token rates for uncached input, cache-creation, cache-read, and
output. The basis matters because a main-agent-only count omits delegated
subagent work and overstates the retrieval penalty: on CodeScaleBench's fair,
subagent-inclusive basis the retrieval arm's cost gap over baseline was +24%,
not the +42–48% a main-only count reported, and the gap itself was dominated
by cache-creation (roughly two-thirds of the delta), then cache-read and
output, with uncached input contributing almost nothing.[^csb-cost] The
mechanism CodeScaleBench identified is accumulated-reasoning re-creation:
retrieval concentrates comparable total work into fewer, heavier turns, and
each heavy turn rewrites more of the prompt cache, rather than a simple
function of retrieved-payload size.

EnterpriseBench's own cost accounting (§5.3, `scripts/cost_tracker.py`)
already breaks tokens into the same four classes, input, output, cache-write,
cache-read, priced per model, carrying forward CodeScaleBench's token-class
scheme. It is not yet subagent-inclusive: `cost_tracker.py` sums usage from a
single `agent_trace.jsonl` per result directory
(`root_dir.rglob("agent_trace.jsonl")`), and whether a delegated subagent's
turns land in that same trace file or need a separate walk is an open
question to resolve before EnterpriseBench reports a fair-basis cost delta of
its own.

<!-- TODO(jn73.11): per-regime cost split and cache-creation / cache-read /
     output attribution from EnterpriseBench's own arm x regime runs. -->

### 3.5 Retrieval quality is measured on its own axis

Outcome reward says whether an agent succeeded, not whether its retrieval was
precise, and a configuration can raise reward by over-fetching.
EnterpriseBench already carries the machinery to separate the two:
`lib/eb_metrics/ir_metrics.py` is, by its own docstring, "a faithful port of
the retrieval-quality core of CodeScaleBench's `scripts/csb_metrics/ir_metrics.py`,"
providing precision@k, recall@k, F1@k, mean reciprocal rank,
nDCG@k, mean average precision, file-level recall, and a context-efficiency
ratio over normalized, repo-relative file paths (co-4cb.1, merged to main).
These are standard code information-retrieval measures (Li et al., 2025; Li et
al., 2026), and reporting them beside reward, rather than folding them into
it, is what lets a genuine precision gain be told apart from an over-injection
effect that an outcome-only benchmark cannot see (Wang et al., 2025). The port
deliberately excludes CodeScaleBench's dollar-cost and
time-to-first-relevant-file fields, since those belong to §3.4's cost lever,
not this retrieval-quality axis. EnterpriseBench's layered ground truth (§2.6)
and checkpoint scoring (§4.4) already produce the required-file and
sufficient-file oracle sets these metrics score against; applying
`ir_metrics.py` to the current run set and reporting the result in §6 is the
remaining step, not new instrumentation.

### 3.6 A tiered screening protocol precedes the representative comparison

Screening a candidate tool configuration and estimating its representative
effect are different jobs, and conflating them wastes trials. CodeScaleBench's
protocol runs three tiers: a smoke tier of three tasks chosen for maximum
signal rather than representativeness, giving a first directional read and
catching breakage; a confirm tier of nine cost-biased tasks at three trials
each, testing whether an effect that survives smoke holds under paired
per-task deltas; and a stratum tier, drawn from the leakage-audited,
grep-breadth-stratified frame of §3.2–§3.3, that estimates the representative
per-regime effect and is reached only by configurations that survive confirm.
Cost and reward verdicts require confirm or higher: CodeScaleBench observed
the smoke tier repeatedly over-reward turn reduction, because its tasks are
exactly where retrieval loops dominate, so a smoke-tier cost verdict inverted
or shrank once retested at confirm.[^csb-protocol] The unit of analysis
throughout is the paired per-task delta; a cost delta without a matching
call-count delta is noise, and arm means are never compared directly.

EnterpriseBench's current run set (§5.4: 326 results, 108 tasks matched across
all three modes) is a single flat pass rather than a tiered one. Adopting the
smoke → confirm → stratum sequence would matter most the next time a new MCP
client version, or a new arm such as a batched-search or CLI variant (§3.7),
needs screening before it runs across all 158 tasks.

### 3.7 Arms and regimes

CodeScaleBench's comparison crosses retrieval arms with localization regimes,
holding the retrieval backend and index fixed while varying only the
interface: a local-grep baseline, a stock retrieval-tool configuration over
the remote index (Anthropic, 2024; Sourcegraph, 2023), a batched-search
variant issuing several queries per call, and a shell-composable command-line
form over the same endpoint, following the observation that the
agent-computer interface itself changes agent behavior (Yang et al., 2024).
EnterpriseBench's own three tool-access modes, baseline, `mcp_only`, and
hybrid (§4.1), are its arms, though not an exact match to CodeScaleBench's
taxonomy: `mcp_only` withholds local search tools entirely rather than merely
offering an alternative interface over the same index, the harder ablation
§3.1 discusses. The regimes are the localization-difficulty strata of §3.2,
once cut for EnterpriseBench's own corpus.

The design does not seek a single best arm. Its deliverable is a routing rule
keyed on localization difficulty: which mode minimizes cost at reward parity
within each regime, and where the crossover between modes falls.
EnterpriseBench's own arm-by-regime cost and reward matrix, the per-regime
paired statistics, and the routing crossover are the results this design is
built to produce.

<!-- TODO(jn73.11): arm x regime cost/reward matrix, per-regime paired
     statistics and win counts, and the routing crossover, from
     EnterpriseBench's own arm x regime runs. -->

[^csb-parity]: Figures from CodeScaleBench's internal parity audit of a frozen
    snapshot (`parity_audit.py`): census OK 288 / EMPTY 48 / PARTIAL 10 /
    UNKNOWN 21. The empty-workspace split is an information-parity artifact in
    that corpus, not evidence about retrieval; it complements the
    contamination controls that pin an index to the exact revision under test
    (Weng et al., 2026; Riddell et al., 2024).

[^csb-grepbreadth]: 113-task substitution run, Sonnet-class agent, both arms,
    main+subagent cost basis (§3.4). Reported as a point correlation; no
    confidence interval is available or invented for this figure.

[^csb-leakage]: CodeScaleBench internal 275-task census. Bucket
    classification is a per-instruction structural check, distinct from the
    parity gate of §3.1: parity concerns whether the baseline has the code,
    leakage concerns whether the instruction gives away where in the code the
    answer sits.

[^csb-cost]: Main+subagent basis, confirm-tier decomposition: retrieval
    $0.627 vs. baseline $0.506 per trial. The correction is an
    accounting-basis change; it does not by itself change arm rankings, since
    the same basis applies to every arm.

[^csb-protocol]: Observed repeatedly across CodeScaleBench's tool-design
    campaign: smoke-tier cost verdicts inverted or shrank at confirm; reward
    and payload verdicts needed the confirm tier to be trustworthy.

## 4. Evaluation Framework

### 4.1 Tool-access modes

Every task is runnable in three controlled tool-access modes:

| Mode       | Tools available                                            |
| ---------- | ---------------------------------------------------------- |
| `baseline` | Local-only (grep, find, file read, basic shell)            |
| `mcp_only` | Sourcegraph MCP only — no local find/grep on source files  |
| `hybrid`   | Both local and MCP tools (the realistic developer setting) |

Mode is controlled at sandbox-build time via mode-suffixed Docker image tags
(`eb-task-mcp_only`, etc.) to prevent build-time collisions during parallel
runs.

### 4.2 Session types

| Type           | Container lifecycle             | State mechanism                    |
| -------------- | ------------------------------- | ---------------------------------- |
| `single`       | One container, one shot         | None                               |
| `chain`        | N containers, sequential        | Git branch carries state forward   |
| `event_replay` | One container, event stream     | `events.jsonl` → `actions.jsonl`   |
| `resume`       | One container, pre-populated    | Git branch + progress doc          |

### 4.3 Verification

A single library, `eb_verify`, ships in every sandbox container — there are
no per-task verifier copies. Nine artifact-aware plugins validate the diverse
outputs that EnterpriseBench tasks elicit:

| Plugin                | Validates                                            |
| --------------------- | ---------------------------------------------------- |
| `answer`              | Structured JSON answers with expected fields         |
| `code_patch`          | Git diffs against ground-truth patches               |
| `config_validator`    | Configuration file correctness                       |
| `incident_report`     | Incident analysis structure and content              |
| `runbook`             | Operational runbook completeness                     |
| `security_assessment` | Security finding accuracy                            |
| `reproduction_script` | Bug reproduction script execution                    |
| `topological_order`   | Dependency ordering correctness                      |
| `call_graph`          | Function call graph accuracy                         |

### 4.4 Checkpoint-based scoring

Each task defines 2–5 graduated checkpoints. Partial credit is awarded for
intermediate progress, so even high-difficulty tasks produce signal rather
than collapsing to a single pass/fail bit. The scoring layers are:

| Layer            | Mechanism                            | Effect                                       |
| ---------------- | ------------------------------------ | -------------------------------------------- |
| Required files   | Deterministic + curator agreement    | Missing required = significant penalty       |
| Sufficient files | Curator-identified, lower confidence | Missing sufficient = small penalty           |
| Chunk-level      | Line ranges within files             | Block-level precision/recall                 |
| Checkpoints      | 2–5 graduated per task               | Partial credit for intermediate progress     |

## 5. Experimental Setup

### 5.1 Agent configurations

The runs reported here used a single agent driver (`scripts/orchestration/run_task.py`)
against Anthropic models (Claude Opus / Sonnet, model recorded in each
`run_log.json`). Token costs and tool calls are captured per run.

### 5.2 Sandbox

Each task runs in a Docker container with all required repositories pre-
cloned to `/workspace/{repo-name}/` at the SHAs pinned in
`configs/repo_versions.json`. Mode-suffixed image tags (`eb-task-baseline`,
`eb-task-mcp_only`, `eb-task-hybrid`) are used so that mode variants can be
built and run in parallel without collision.

### 5.3 Cost tracking

Per-run cost is computed from token counts using current Anthropic pricing
(input/output rates). The 60/40 input/output split observed in early sample
runs is used as the default token-mix assumption when provider-side counts
are not directly available; provider counts are preferred when present.
`scripts/cost_tracker.py` breaks out actual usage into four token classes,
input, output, cache-write, cache-read, per model, whenever a per-task
`agent_trace.jsonl` is present, so the 60/40 default applies only to the gap
where a trace is missing. §3.4 discusses whether this accounting is
subagent-inclusive.

### 5.4 Run set used in this paper

The results in Section 6 use the most recent end-to-end run set produced by
the Phase 7 analysis pipeline:

- 326 task results across three modes
- 108 tasks have results in all three modes (baseline / mcp_only / hybrid)
- Coverage skews toward dependency-management and customer-escalation suites
  in absolute count, with smaller suites (security_operations, platform_engineering)
  underrepresented as a known limitation

## 6. Results

> All numbers in this section are produced from
> `results/analysis/score_analysis.json` via the pipeline in
> `scripts/analyze_scores.py` → `scripts/generate_charts.py` →
> `scripts/generate_report.py`. Re-running `make paper-figures` regenerates
> every figure and table from the underlying data.

### 6.1 Headline numbers

- **Tasks scored across modes:** 326
- **Tasks present in all three modes:** 108
- **Baseline pass rate (score ≥ 0.7):** 51.8%

### 6.2 Distribution by mode

| Mode       |   N | Mean   | Median | Std    | Pass rate |
| ---------- | --: | ------ | ------ | ------ | --------- |
| baseline   | 108 | 0.6675 | 0.7846 | 0.3334 | 51.8%     |
| hybrid     | 109 | 0.5579 | 0.6250 | 0.3836 | 38.5%     |
| mcp_only   | 109 | 0.5523 | 0.6233 | 0.3888 | 44.0%     |

![Score distribution by mode](figures/score_by_mode.png)

### 6.3 Distribution by suite

![Score heatmap by suite × mode](figures/score_heatmap.png)

Suite-level means span a wide range, from `dependency_management` (baseline
0.89, suggesting near-saturation for the current agent at this suite) to
`technical_debt` (baseline 0.50, where checkpoint-level partial credit is
doing most of the work because the 0.7 pass threshold is not crossed in any
mode).

### 6.4 MCP impact analysis

Paired comparisons on the 108 tasks with all three modes:

**Hybrid vs baseline**

- Mean delta: −0.111 (regression), median delta: 0.000
- Tasks improved: 16.7% / degraded: 31.5% / unchanged: 51.8%
- Cohen's d: −0.31 (small), Wilcoxon p = 0.0026 (significant at α = 0.05)

**MCP-only vs baseline**

- Mean delta: −0.116 (regression), median delta: 0.000
- Tasks improved: 20.4% / degraded: 35.2% / unchanged: 44.4%
- Cohen's d: −0.32 (small), Wilcoxon p = 0.0027 (significant at α = 0.05)

![MCP delta vs baseline](figures/mcp_delta.png)

These deltas are *negative* in the current run set. We interpret this with
care: (a) the calibration check (Section 6.5) shows the benchmark itself is
not biased toward baseline, (b) sample size is small, particularly in
several suites, and (c) the agent-side MCP integration has known coverage
gaps in this run that we expect to close in subsequent runs. The headline is
that MCP delta is *measurable and signed*, not that MCP is empirically
unhelpful — see Section 7 for limitations.

### 6.5 Calibration bias check

The 35-task calibration stratum is small, single-repo, and designed so that
no mode should have a meaningful advantage. The observed maximum mode delta
on calibration tasks is **0.077**, below the 0.10 bias threshold:

- baseline mean: 0.6585
- hybrid mean: 0.5931
- mcp_only mean: 0.5819

**Calibration check: PASS.** The benchmark does not unfairly favor any
particular tool-access mode on easy tasks.

![Calibration check](figures/calibration_check.png)

### 6.6 Cost per task

Per-task cost ranges from $0.05 (Sonnet, dead-code) to $0.32 (Opus,
api_contract baseline). MCP modes consistently use 23–33% fewer tokens than
baseline at the same task. The full benchmark in 3-mode config runs at an
estimated $42–$61 in token + compute (see `results/analysis/cost_model.md`).

### 6.7 Reproducibility

A 3× repeat-run reproducibility report on a 25-task stratified subset is
**not yet generated**; the harness (`scripts/reproducibility_check.py`) is in
place and will populate `results/analysis/reproducibility.md` on completion.
This section will report mean, std, and ICC per stratum once those runs are
available; the placeholder is enforced by `make paper-figures` so the paper
cannot be packaged with a stale reproducibility number.

## 7. Discussion

### 7.1 Limitations

- **Sample size.** Several suites (security_operations, platform_engineering)
  have small absolute task counts; per-suite deltas should be read as
  directional, not conclusive.
- **Reproducibility.** Multi-run variance is not yet quantified; this is
  acknowledged in §6.7 and gated on the upcoming 3× run.
- **Ecosystem skew.** The current multi-repo task mix skews Go (46.2%),
  exceeding the PRD ceiling of 40% per ecosystem. Closing this gap is on the
  active task list and shifts results in §6.3 only marginally because suite
  means are weighted across ecosystems.
- **Single agent driver.** Results in §6 use a single driver against
  Anthropic models. The benchmark is agent-agnostic at the contract level
  (task TOML + sandbox + `eb_verify`) and is intended to be run against
  additional drivers (OpenHands, etc.) before publication.
- **MCP coverage.** Some MCP modes were run with a partially-instrumented
  client; planned closures are tracked in the active backlog.

### 7.2 Threats to validity

- **Curator leakage.** The LLM curator could over-weight code its training
  set has memorized; cross-backend agreement and solve-verification mitigate
  but do not eliminate this.
- **Repo drift.** OSS repositories evolve; we pin to specific SHAs and check
  staleness, but a task may become "easier" if upstream changes alter the
  observable surface (e.g., a removed deprecation comment). Staleness
  detection runs as part of pre-flight.
- **Mode comparability.** Hybrid is intended to be a strict superset of
  baseline + mcp_only in capability terms, so a *negative* hybrid delta is a
  signal worth investigating: either the agent is making bad tool-routing
  decisions or one of the underlying tools is degrading the other. §3.1
  formalizes this as an information-parity argument, using a prior
  CodeScaleBench audit as evidence for why the precondition matters, and notes
  that `mcp_only`, which withholds local tools entirely by sandbox
  construction, is a capability ablation rather than a parity-matched arm
  relative to baseline.

### 7.3 Future work

- Reproducibility 3× run on a 25-task stratified subset
- Additional agent drivers (OpenHands, others)
- Ecosystem rebalancing to bring Go below the 40% ceiling
- Expansion of security_operations and platform_engineering suites
- Baseline-workspace parity census (§3.1), CodeScaleBench-style, run against
  EnterpriseBench's own mode-suffixed sandbox builds
- Corpus leakage audit (§3.3) over the active 158-task corpus, alongside CRNT
- Tiered smoke → confirm → stratum screening (§3.6) adopted for future arm
  additions such as a batched-search or CLI-form MCP variant

## 8. Related Work

- **SWE-bench / SWE-bench-Verified.** Single-repo PR resolution; complementary
  to EnterpriseBench but does not exercise cross-repo reasoning.
- **HumanEval, MBPP, LiveCodeBench.** Toy or competitive-programming snippets.
  Useful for raw code generation; do not test codebase navigation.
- **ITBench, DevOps-Gym.** SRE / build / config tasks. Closer in spirit to
  EnterpriseBench's incident_investigation and config_drift suites, but
  smaller scope and limited multi-repo coverage.
- **CodeScaleBench (CSB, internal).** EnterpriseBench's direct predecessor;
  275 single-repo, single-session tasks. EnterpriseBench inherits CSB's
  metadata pipeline, retires its duplicated verifiers, and adds true
  multi-repo coverage and artifact diversity. The stratification,
  leakage-audit, cost-decomposition, and tiered-protocol methodology in §3 is
  ported from an unpublished internal CSB draft
  (`paper/retrieval-effectiveness/`) rather than duplicated as a separate
  paper.

## 9. Conclusion

EnterpriseBench targets a capability that prior coding benchmarks largely do
not: finding and comprehending the right code across distributed
codebases. By drawing every task from real open-source repositories with
real dependency chains, organizing tasks by enterprise workflow rather than
artificial SDLC splits, and treating tool access as a controlled independent
variable with a calibration check, the benchmark gives a more faithful
picture of agent performance under the conditions in which enterprise
developers actually work. The results presented here are an early read on a
single agent driver; the design is set up for more agents, more runs, and a
quantified reproducibility envelope before broad release.

## References

- Anthropic. 2024. *Model Context Protocol.* https://modelcontextprotocol.io
- Jiménez, C. E., Yang, J., Wettig, A., Yao, S., Pei, K., Press, O., &
  Narasimhan, K. 2024. *SWE-bench: Can Language Models Resolve Real-World
  GitHub Issues?* ICLR. arXiv:2310.06770.
- Li, X., Dong, K., Lee, Y. Q., Xia, W., Zhang, H., Dai, X., Wang, Y., & Tang,
  R. 2025. *CoIR: A Comprehensive Benchmark for Code Information Retrieval
  Models.* ACL 2025. arXiv:2407.02883.
- Li, H., Zhu, L., Zhang, B., Feng, R., Wang, J., Pan, Y., Barr, E. T., Sarro,
  F., Chu, Z., & Ye, H. 2026. *ContextBench: A Benchmark for Context
  Retrieval in Coding Agents.* arXiv:2602.05892.
- Riddell, M., Ni, A., & Cohan, A. 2024. *Quantifying Contamination in
  Evaluating Code Generation Capabilities of Language Models.* ACL 2024.
  arXiv:2403.04811.
- Sourcegraph. 2023. *SCIP: A Code Intelligence Protocol.*
  https://sourcegraph.com/blog/announcing-scip
- Wang, Z. Z., Asai, A., Yu, X. V., Xu, F. F., Xie, Y., Neubig, G., & Fried,
  D. 2025. *CodeRAG-Bench: Can Retrieval Augment Code Generation?* Findings
  of ACL: NAACL 2025. arXiv:2406.14497.
- Weng, H., Yang, Q., Fu, H., Pan, H., & Lv, X. 2026. *When Retrieval Hurts
  Code Completion: A Diagnostic Study of Stale Repository Context.*
  arXiv:2605.14478.
- Yang, J., Jiménez, C. E., Wettig, A., Lieret, K., Yao, S., Narasimhan, K.,
  & Press, O. 2024. *SWE-agent: Agent-Computer Interfaces Enable Automated
  Software Engineering.* NeurIPS 2024. arXiv:2405.15793.

## Appendix A — Reproducibility

All figures and tables in this paper are regenerated by:

```bash
make paper-figures
```

which runs the analysis pipeline end-to-end (`analyze_scores.py` →
`generate_charts.py` → `generate_report.py`) and copies the resulting charts
into `paper/figures/`. The per-run inputs are committed under
`results/analysis/`. Repository SHAs are pinned in
`configs/repo_versions.json`.

## Appendix B — Task list

The active task set is enumerated in `benchmarks/`, one TOML per task. The
PRD-aligned task-mix validator (`scripts/validation/task_mix_validator.py`)
prints the current distribution and PASS/FAIL status against the targets
described in Section 2.4.
