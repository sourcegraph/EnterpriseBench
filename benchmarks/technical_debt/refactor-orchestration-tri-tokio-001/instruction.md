# Refactor Orchestration: Async HTTP Handler Chain (tokio/hyper/axum)

## Context

Three Rust crates live in this workspace: tokio (async runtime), hyper (HTTP
implementation), and axum (web framework). The tokio maintainers are
refactoring the runtime builder API to support configurable task schedulers.
Because hyper and axum each build on tokio in some capacity, this change has
downstream effects that need to be rolled out in the correct order -- but the
exact dependency wiring between the three crates (who depends on whom, and
whether any dependency is only transitive) is not given here. Determine it
from each repo's own manifest and source.

## Task

Investigate the three repos, determine the actual dependency relationships
between them, and produce a topologically sorted execution plan for rolling
out the tokio runtime builder refactoring across the stack. For each repo,
identify the specific API surface in that repo's own code that the change
touches -- don't assume which functions or types are affected without
checking.

## Repos in Workspace

- `/workspace/tokio/` -- tokio 1.29.1 (upstream runtime)
- `/workspace/hyper/` -- hyper 1.0.0-rc.4 (intermediary HTTP library)
- `/workspace/axum/` -- axum 0.6.19 (consumer web framework)

## Expected Output

Write `/workspace/REFACTOR_PLAN.md` containing:

1. A numbered list of repos in the order they should be updated
2. Dependency graph (who depends on whom)
3. Parallelization annotations
4. Breaking vs. compatible change annotations per step
5. Risk assessment for each change
