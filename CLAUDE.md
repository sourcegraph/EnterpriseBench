# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

See `HANDOFF.md` for current status, open work, and the branch map.

EnterpriseBench measures how well coding agents **find and understand the right code** across large, distributed codebases — context retrieval quality, not code generation. It evolves CodeScaleBench (CSB, 275 tasks): fix and extend that foundation, don't rebuild it. Sourcegraph is a first-class showcase, but tool access is a **controlled independent variable**, not an assumed win.

A local checkout may contain `enterprisebench-*` git worktree directories at the root. They are not part of the source tree — scope searches with `git ls-files` rather than globbing the root.

## Commands

```bash
pip install -e "lib/[dev]"        # [dev] pulls pytest + scoring + chart deps. CI does exactly this.
```

### Tests

`make test` requires `PYTEST_PATHS`; the target errors rather than let a bare `pytest` walk the repo root, and treats "0 tests collected" as a failure, not a pass.

```bash
make test                                   # full suite, markers 'not network and not docker'
make test PYTEST_PATHS=tests/integrity PYTEST_ARGS='-v'
PYTHONPATH=lib python3 -m pytest tests/eb_metrics/test_ir_metrics.py -q          # single module
PYTHONPATH=lib python3 -m pytest tests/test_scoring.py::test_name -q             # single test
```

`PYTHONPATH=lib` matters. `tests/conftest.py` prepends this repo's `lib/` so tests import the package under test — without it, a stale machine-global editable install silently exercises another worktree's `eb_verify` and reports green (guarded by `tests/integrity/test_eb_verify_import_provenance.py`). Markers: `network`, `docker`, `security`.

### Validation gates

```bash
make verify                    # verify-mix + verify-tasks + verify-crnt
make verify-expected-solutions
python3 scripts/audit_consistency.py      # CI runs this as a separate gate
```

### Running tasks

```bash
python3 scripts/infra/check_infra.py                  # OAuth + docker + disk. Run before any batch; exit 0 = ready.
python3 scripts/infra/headless_login.py --account 4   # when a token has expired

# One task (single-task by design — one positional task.toml, one process)
python3 scripts/orchestration/run_task.py <task.toml> --mode mcp_only --account 1
python3 scripts/orchestration/run_task.py <task.toml> --dry-run    # stops after MCP/sgx preflight

# Many tasks — this is the parallel path, not hand-rolled `&`+`wait`
python3 scripts/run_benchmark.py benchmarks/ --all --account 1-5 -j0 --mode baseline
python3 scripts/run_sweep.py --execute --account 1-5               # plans the task×mode matrix, then dispatches
```

`run_benchmark.py` parallelizes via `ProcessPoolExecutor` with round-robin account assignment (`-j0` = one worker per account). Never run tasks sequentially.

### Analysis

```bash
make analyze | charts | report | paper-figures    # paper-figures regenerates every figure in paper/paper.md
python3 scripts/triage/triage_run.py --format table   # classify failures: pass / A_infra / B_setup / C_verifier / D_agent / E_timeout
```

## Architecture

### Tasks

A task is a directory with `task.toml` (schema: `schemas/task.schema.json`), `instruction.md`, `checks/*.sh`, and optionally `expected_solution.json`. `benchmarks/` holds active tasks by suite; `benchmarks/_archived/` is retired-but-preserved.

Two traps in the TOML:

- `[[checkpoints]].verifier` is a **relative path to a shell script**, not a plugin name. What resolves to a plugin is `[artifacts].required = ["answer"]`.
- `[task].prompt` is stale CSB-migration metadata that diverges from `instruction.md` in many tasks. **`instruction.md` is what the agent actually sees** — grade and audit against it.

### Verification: one library, no copies

`lib/eb_verify/` is the single verification library, installed into every sandbox. Per-task verifier copies are banned — CSB had 549 of them. The `eb-verify` console script exposes `run` / `check` / `validate` / `validate-artifact`.

Plugins are a plain module-level dict populated by hand-written `register()` calls at import time (`plugins/__init__.py`) — no entry_points, no discovery. A plugin implements `artifact_type: str` + `validate(workspace) -> ValidationResult`.

**numpy/scikit-learn must stay optional.** A numpy-less install is the canonical one; `fact_triples` registers only if `fact_coverage.scoring_deps_available()` passes, and `tests/integrity/test_plugin_registration_deps.py` pins the contract from both sides. Do not promote them into `[project] dependencies`.

Ground truth is layered: Tier 1 deterministic (AST/import graphs, grep checks) always runs; Tier 2 LLM curator runs only when `verification_modes` contains `llm_curator` **and** `expected_solution.json` exists, and acts strictly as a ceiling — `final = min(grep_score, judge_score)`.

### The scoring trust boundary — the invariant that governs everything

Read `lib/eb_verify/scorer_guard.py`'s docstring before touching any scoring path. A score is valid **only** if the pristine verifier ran on real agent output. Any infra, verifier, or judge failure must surface as `verifier_infra_error` and route to the re-run channel. It must never be recorded as a legitimate `0.0`.

The distinction is load-bearing, and the failure modes are opposite claims:

- **exit 1** = the agent scored zero (a real, measured failure).
- **exit 2** = no score exists; the harness failed. A caller must not fold 2 into 1.
- `total_score: INVALID` prints non-numerically on purpose, so a downstream text parser crashes rather than banking a fabricated 0.0.
- `integrity_violation` (tampered grading assets) scores 0.0 and is **never** re-runnable — tampering must not be a free mulligan.

When you add a failure mode, route it into one of these channels. Do not let it score.

### The sandbox: three identities, one container

`run_task.py` builds a Docker image per task, clones repos into `/workspace/{repo}/`, and runs the agent. Three identities share the container and **no two may collapse into one**: `root` owns the sealed grading assets (`/workspace/.verifiers`, `/workspace/.task`, `/workspace/.eb_verify`, `/workspace/test.sh`); `ebscorer` (UID 2000) executes the checks but cannot own them; `agent` owns the repos and can never read the graders.

`eb_verify` is **not baked into the image** — it is `docker cp`'d in at runtime, and the `mkdir -p /workspace/.eb_verify` before the copy is mandatory (copying a dir onto a non-existent path copies its *contents* and drops the package dir).

### Tool-access arms are filesystem permissions

Four arms: `baseline`, `mcp_only`, `hybrid`, `cli` (sgx). `cli` is fully wired but deliberately excluded from the default sweep so an existing sweep doesn't silently gain a 4th arm and ~33% cost.

The ablation is a **kernel permission, not a prompt and not a tool denylist** (`scripts/orchestration/mode_gate.py` — read its docstring). Both alternatives are rejected in writing: `--disallowed-tools` strips the shell along with search tools (so the delta measures "lost a shell") and fails open; omitting repos from the image blinds the *scorer* too, since checkpoints grade against `/workspace` in the same container. So repos stay on disk, the toolset is identical across arms, and only directory permissions differ.

Consequences worth knowing:

- All four modes build a **byte-identical image**. The mode-suffixed tag (`eb-<task_id>-mcp_only`; baseline gets no suffix) prevents concurrent `docker build` tag races — it does not encode image content. Mode is applied at runtime.
- An `mcp_only` agent still *has* grep and Bash; they just return `Permission denied` on repo source. The gate is proven in both directions (agent lost read, `ebscorer` kept it) or the run is `invalid`.
- An `mcp_only` run with zero MCP calls, or a `cli` run with zero sgx calls, is routed `invalid` — not scored. MCP is that arm's only path to code.

### Session types: only `single` is production

`session_type = "single"` is the only type that runs a real containerized agent — everything above applies to it alone. `chain` simulates repos (`setup_workspace` git-inits an empty repo rather than cloning) and skips Docker; `event_replay` scores offline only, with no sandbox; `resume` is unimplemented and `run_benchmark.py` skips it. Don't assume the docs' four-way session table describes working code.

### eb_metrics

`lib/eb_metrics/` consumes run artifacts post-hoc (`agent_trace.jsonl`, `ground_truth.json`, `results.json`) to compute retrieval/IR metrics. It does not import `eb_verify`. It projects onto schemas owned by `codeprobe`, declared as the `metrics` extra in `lib/pyproject.toml` and included in `[dev]`. Its scoring primitives are deliberately deterministic: a metric a model re-judged per call would not reproduce across rescores.

## Conventions

- **Real OSS repos only** — no synthetic or toy repositories, and enterprise-scale ones. Every task must be a realistic enterprise use case.
- **Repos are pinned** in `configs/repo_versions.json`; check drift with `scripts/infra/check_repo_staleness.py`.
- **All work on `main`.** Run prototypes before committing to architectural decisions.
- **Tests ship with the fix**, in the same commit.
- `scripts/analysis/` is mostly frozen one-shot artifacts named after the bead that produced them (`rescore_*_uu17.py`). Read the docstring before reusing one.
- Skills: `/diverge` (multi-perspective research), `/diverge-prototype` (isolated worktrees), `/converge` (structured debate).

## Counts and status live in code, not here

Task totals, suite/stratum breakdowns, and test counts go stale fast and mislead the next agent — every such number in a previous version of this file was wrong. Compute them:

```bash
make verify-mix                                                   # task mix vs PRD targets
git ls-files 'benchmarks/**/task.toml' | grep -v _archived | wc -l # active task count
```

`README.md` carries the public task-type and suite tables. `docs/internal/PRD.md`, `docs/ARCHITECTURE.md`, `docs/TASK_TYPE_PRD.md`, and `docs/TASK_AUTHORING_GUIDE.md` carry design intent — but the ARCHITECTURE.md directory tree and mix table have drifted from the code; trust the code.
