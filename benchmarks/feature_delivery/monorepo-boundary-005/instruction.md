# Impact Analysis: Lockfile v6 Format Change for pnpm v9.0.0

## Context

We're preparing the pnpm v9.0.0 major release. Two core changes affect the lockfile format:

1. **New lockfile version** — `@pnpm/constants` is bumping the lockfile format version to v6. This changes how the lockfile is read, written, and validated throughout the codebase.

2. **Registry URL removal** — `@pnpm/dependency-path` no longer includes the registry URL in the package ID. This affects how dependency paths are computed, stored, and compared.

These are among the most foundational data structures in pnpm — the lockfile format touches nearly every package in the workspace. I need a thorough blast radius analysis before we cut the major version.

## What I Need

1. **Affected packages**: Trace which workspace packages depend on `@pnpm/constants` or `@pnpm/dependency-path` (directly or transitively via `workspace:*` protocol). Pay special attention to packages under `lockfile/`, `packages/`, and `exec/`.

2. **Impact classification**: Per package — given this is a fundamental format change, most should be major. But some packages may only need test updates (still major if their behavior changes).

3. **Boundary violations**: Show me the specific files where lockfile format assumptions are hardcoded — version constants, path format expectations, snapshot type definitions.

## Output

Write findings to `/workspace/agent_output/IMPACT_REPORT.md`.
