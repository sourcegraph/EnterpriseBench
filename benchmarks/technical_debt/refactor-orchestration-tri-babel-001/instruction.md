# Refactor Orchestration: JS Compilation Pipeline (Babel/webpack/Next.js)

## Context

Three repos in this workspace sit on the same JavaScript compilation
pipeline. Babel is refactoring its parser to support a new
DecoratorMetadata AST node for stage 3 decorators. Because the other two
projects each consume Babel's parser output during their own build steps,
this AST change ripples outward, and each downstream project needs its own
set of updates to stay compatible.

You will need to read each repo to find exactly which subsystem in it is
affected and what a compatible update looks like — the general description
above is not enough to write a credible plan.

## Task

Produce a topologically sorted execution plan for propagating the Babel
parser AST change through the JS build pipeline.

## Repos in Workspace

- `/workspace/babel/` -- Babel v7.22.9 (upstream parser/compiler)
- `/workspace/webpack/` -- webpack v5.88.2 (intermediary bundler)
- `/workspace/nextjs/` -- Next.js v13.4.12 (consumer framework)

## Expected Output

Write `/workspace/REFACTOR_PLAN.md` containing:

1. A numbered list of repos in the order they should be updated
2. Dependency relationships between the repos (which ones must change before others, and why)
3. Parallelization annotations
4. Breaking vs. compatible change annotations per step
5. Risk assessment for each change
