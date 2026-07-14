# EnterpriseBench — Release Packaging

This document is the operator's checklist for cutting a public release of
EnterpriseBench. It assumes the analysis pipeline (Phase 7) has produced the
artifacts under `results/analysis/` that the paper depends on.

## 1. Release checklist

Run through every item before tagging a release. Each item links to the
underlying script or file so it can be re-run from a clean checkout.

### 1.1 Task validation

```bash
# (a) All task definitions pass preflight schema + structural checks
make verify-tasks

# (b) Task mix meets PRD targets (strict multi-repo >= 45%, etc.)
make verify-mix

# (c) Cross-Repo Necessity Test on every multi-repo task
make verify-crnt

# (d) expected_solution.json present and valid for llm_curator tasks
make verify-expected-solutions
```

All four targets must exit zero. The `verify-mix` target may report a
non-fatal ecosystem-skew warning (Go > 40%) — this is a known limitation
documented in `paper/paper.md` §6.1 and does not block release, but should
be ratcheted toward target before each subsequent release.

### 1.2 Documentation

- [ ] `README.md` — top-level project overview, quickstart, and structure
- [ ] `LICENSE` — Apache 2.0, present at repo root
- [ ] `docs/ARCHITECTURE.md` — system design and verification flow
- [ ] `docs/TASK_AUTHORING_GUIDE.md` — how external contributors add tasks
- [ ] `docs/TASK_TYPE_PRD.md` — the 10 task type definitions
- [ ] `paper/paper.md` — current draft of the benchmark paper

### 1.3 Code hygiene

- [ ] `configs/` — no API keys, no internal hostnames, no PII
- [ ] `benchmarks/` — no `/home/...`, `~/`, or absolute paths beyond the
      sandbox `/workspace/...` convention
- [ ] `.env*` — not present anywhere except as `.env.example`
- [ ] `git grep -nE 'sk-[a-z0-9]{32,}|xoxb-|ghp_|aws_secret_access'` — empty
- [ ] `gitleaks detect --no-banner --redact` (CI runs this; locally if changed)

### 1.4 Reproducibility surface

- [ ] `Makefile` — `make help` prints; `make paper-figures` succeeds from a
      clean checkout against committed `results/analysis/score_analysis.json`
- [ ] `configs/repo_versions.json` — every task's repos pinned to a SHA
- [ ] `scripts/infra/check_repo_staleness.py` — runs without errors against
      the pinned manifest
- [ ] `paper/figures/` — every figure referenced from `paper/paper.md` is
      present and was produced by the pipeline (not hand-edited)

### 1.5 Sample output

- [ ] `results/sample_runs/` — at least one task per task-type with a
      `run_log.json`, `workspace/` snapshot, and `analysis.md` showing the
      expected results format. (Currently 8 task types with samples; gaps
      tracked in the active backlog.)

### 1.6 Test suite

```bash
# The full test suite (tests/) must be green
make test
```

## 2. Building the release artifacts

### 2.1 Regenerate figures and the analysis report

From a clean checkout:

```bash
make paper-figures
```

This runs `analyze_scores.py` → `generate_charts.py` → `generate_report.py`
end-to-end and copies every PNG into `paper/figures/`. Re-run on every
results refresh; never hand-edit anything in `paper/figures/`.

### 2.2 Inspect the analysis report

```bash
${PAGER:-less} results/analysis/report.md
```

Confirm the headline numbers match `paper/paper.md` §5. If they have drifted,
update the paper from the report — the paper trails the analysis pipeline,
not the other way around.

### 2.3 Tag the release

```bash
git tag -a v0.1.0 -m "EnterpriseBench v0.1.0"
git push origin v0.1.0
```

Tag scheme:
- `v0.1.0` — first public draft, paper draft + 158 tasks + Phase 7 analysis
- `v0.x.0` — subsequent task-set or pipeline updates
- `v1.0.0` — peer-review-ready release with reproducibility 3× run + multi-driver results

## 3. Distribution

### 3.1 Repository contents

The public repository contains everything needed to:

1. Read the paper (`paper/paper.md`, `paper/figures/`)
2. Inspect every task definition (`benchmarks/`, `schemas/task.schema.json`)
3. Reproduce every figure (`make paper-figures` against committed analysis JSON)
4. Run a single task (`scripts/orchestration/run_task.py`)
5. Validate the task set (`make verify`)

### 3.2 What is NOT in the public repo

- Raw model outputs from individual runs beyond `results/sample_runs/`
- Internal evaluation infrastructure (`.beads/`, `.claude/`)
- Internal-only docs under `docs/internal/`

### 3.3 External contributor entry points

A new contributor who wants to add a task should follow:

1. `docs/TASK_AUTHORING_GUIDE.md`
2. Use an existing task in the same suite as a template
3. Run `make verify-tasks` before sending a PR

## 4. Post-release follow-ups

These are not blockers for v0.1.0 but should be opened as issues at release
time:

- 3× repeat-run reproducibility on a 25-task stratified subset
- Second agent driver (e.g., OpenHands) results
- Ecosystem rebalancing to bring Go below 40% of the multi-repo set
- Expansion of `security_operations` and `platform_engineering` suites

## 5. Operator quick-reference

```bash
# Full release verification (run before tagging)
make verify
make paper-figures
make test

# If any of these fail, the release is not ready.
```
