# Handoff: state of EnterpriseBench as of 2026-09-14

This repository was developed by one maintainer in a personal repo and copied here when they left Sourcegraph. Nothing here has been published as a validated result. This file records what works, what does not, and where the unfinished work lives, so the next owner does not have to reconstruct it from commit history.

Companion repos:

- [sourcegraph/CodeScaleBench](https://github.com/sourcegraph/CodeScaleBench) is the predecessor benchmark (275 tasks). EnterpriseBench started as a fix-and-extend of it. The `experiments/csb_import` work on the snapshot branch converts CSB tasks into this format.
- [sourcegraph/codeprobe](https://github.com/sourcegraph/codeprobe) mines tasks from a repo's own history and owns the trace-quality schemas that `lib/eb_metrics` imports. It is on PyPI as `codeprobe`.
- [sourcegraph/migration-evals](https://github.com/sourcegraph/migration-evals) is a separate evaluation framework for automated code migrations.

## Status in one paragraph

The harness runs. A task builds a per-task Docker image, clones pinned OSS repos, runs Claude Code under one of four tool-access arms, and scores the output with a single verification library. The corpus has 141 active tasks. Do not cite the numbers in `results/`. The headline study was reset to compare MCP, baseline, and CLI arms on a curated subset. The retrieval-quality metric axis is quarantined. Several open bugs change which tasks can score at all. `README.md` carries the same warning.

## Getting started

```bash
pip install -e "lib/[dev]"     # pulls scoring deps, codeprobe, pytest
make test PYTEST_PATHS=tests/integrity
python3 scripts/infra/check_infra.py
python3 scripts/orchestration/run_task.py benchmarks/<suite>/<task>/task.toml --dry-run
```

`CLAUDE.md` is the most accurate architecture guide in the repo. Read it before `docs/ARCHITECTURE.md`, whose directory tree and task-mix table have drifted. Read the docstrings of `lib/eb_verify/scorer_guard.py` and `scripts/orchestration/mode_gate.py` before changing any scoring or arm logic.

## Environment assumptions that do not transfer

- **Claude Code accounts.** Parallel runs round-robin across several Claude Code logins stored as `~/.claude-homes/account<N>/.claude/.credentials.json`. `scripts/infra/capacity.py` and `scripts/infra/accounts_config.json` read that layout. A new owner needs their own accounts or an API-key harness.
- **Sourcegraph instance.** The MCP and CLI arms need `SOURCEGRAPH_ACCESS_TOKEN` and an MCP endpoint (`SOURCEGRAPH_MCP_ENDPOINT` / `SOURCEGRAPH_MCP_URL`). Task repos are mirrored into a Sourcegraph instance so that search sees the same pinned commit the sandbox clones. The mirror lists are `configs/sg_indexing_list.json` and `configs/mirror_creation_manifest.json`.
- **Absolute paths.** Some one-shot scripts under `scripts/analysis/` and a few audit scripts contain the original machine's home directory. They are frozen artifacts; fix the path if you rerun one.
- **Issue tracker.** Work was tracked in a local [beads](https://github.com/gastownhall/beads) database that is not in git. The open items that matter are summarized below.
- **Multi-agent worktrees.** Many local branches were produced by parallel agent worktrees. They are listed below by name only.

## Tests

The CI command is `pytest tests/ -m "not network and not docker"` after `pip install -e "lib/[dev]"`. Counts from a clean virtualenv on 2026-09-14:

| Tree | Failed | Passed | Collection errors |
| --- | ---: | ---: | ---: |
| `main` (this branch) | 235 | 5493 | 4 |
| `wip/local-uncommitted-2026-09-14` | 384 | 6807 | 6 |
| `consolidate/worktree-merge-2026-09-13` | 456 | 5826 | 12 |

The `main` row was measured in a clean virtualenv. The other two rows were measured in the maintainer's environment, where an incomplete codeprobe install added some collection errors.

Most failures are per-task verifier tests for tasks that were only partly authored (`tests/test_support_mapping_verifiers.py`, `tests/test_schema_evolution_verifiers.py`, `tests/test_all_tasks_valid.py`, `tests/security/test_check_scripts_injection.py`). They are corpus gaps, not harness regressions.

The four collection errors on `main` are tests that import helpers (`retired_accounts` in `scripts/infra/capacity.py`, `split_variant_label` in `scripts/lib/shared.py`) which were written but never committed. Those helpers exist on the snapshot branch, together with new arm modes they depend on.

## Branch map

- **`main`**: The last committed state of the maintainer's checkout plus this handoff commit. Start here.
- **`wip/local-uncommitted-2026-09-14`**: One commit on top of `main` holding every uncommitted change from the maintainer's working copy (about 1,700 files). It includes the extra MCP arm variants (`mcp_code_finder`, `mcp_toast`, and others), `experiments/toast_retrieval`, `experiments/csb_import`, and `results/mcp_lift_study`. It was never reviewed as a unit. Mine it; do not merge it wholesale.
- **`consolidate/worktree-merge-2026-09-13`**: An attempt to fold about 85 agent-branch commits into `main`. Its last merge stopped with a conflict in `lib/eb_verify/fact_coverage.py`, and it fails more tests than `main`. Treat it as a record of what was being merged.
- **`work/EnterpriseBench-*`, `fix/eb-*`, `audit/*`, `feature/*`, `integration/*`, `review-lane-*`, `rescue/*`, `worktree-agent-*`**: Branches with commits not on `main`. The suffix is the tracker ID of the item it addressed. `git cherry main <branch>` shows that 12 of these 46 branches landed some or all of their commits on `main` under different SHAs, so check before porting anything.
- **`wip/d900w-fork-b-unlanded`**: A rejected design alternative, kept for reference. Do not merge.

Two local scratch branches were not copied here: a temporary main check and a consolidation fork marked do-not-use.

## Open work that matters

Ordered roughly by what blocks a credible headline number.

1. **Headline study reset.** Compare `baseline`, `mcp_only`, and `cli` arms on a curated subset with build-time checks that each arm had the access it claims. The freeze of the "v7" headline configuration was in progress. See `docs/internal/rryas_curated_dataset_handoff.md`.
2. **Deliverables inside repo trees.** 37 tasks declare their graded deliverable at a path inside a cloned repo tree, and they score a structural 0.0 in every gated arm.
3. **Tier-2 judge lookup key.** The LLM judge looks up `expected_solution` by verifier-script name, which voids 87 tasks as having an incomplete expected solution.
4. **Prompt-echo check on JSON deliverables.** The check that a score is not earned by repeating the prompt never exercises JSON-shaped answers, so tasks with JSON deliverables are certified clean without being tested.
5. **MCP config ownership in the sandbox.** MCP config and trust files are chowned by a user without permission to chown, so JavaScript-repo tasks fail MCP pre-flight silently.
6. **Corpus size.** The strict curated set cannot reach 50 enterprise tasks under the current gates. The decision recorded was to author more tasks or relax a gate explicitly, not to tune gates until the number fits.
7. **Retrieval-quality metrics.** Recall landed in `lib/eb_metrics`. Localization@k and time-to-first-relevant exist on the unmerged `worktree-agent-a2d746ee838a4052a` branch. The axis stays quarantined because the baseline arm's shell searches are not fully observable in traces.
8. **Ecosystem balance.** `make verify-mix` fails because Go is 49% of multi-repo tasks against a 40% cap.
9. **Uncommitted helpers on `main`.** Four test modules on `main` fail to import, as described under Tests. Porting `retired_accounts` and `split_variant_label` from the snapshot branch clears them, but the second one brings the extra arm modes with it.
