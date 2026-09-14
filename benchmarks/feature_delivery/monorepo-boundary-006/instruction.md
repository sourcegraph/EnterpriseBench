# Impact Analysis: Lifecycle Scripts Allow-List Default

## Context

Security team flagged that pnpm runs arbitrary lifecycle scripts (postinstall, preinstall, etc.) by default. For v10.0.0 we're evaluating flipping the default: lifecycle scripts would require an explicit allow-list in the project's `.npmrc` or `package.json`.

## What I Need

Investigate the pnpm monorepo and write findings grounded in what you actually find in the code and tests — cite specific packages, files, and functions, not general semver theory.

1. **Affected packages**: Which workspace packages read, enforce, or exercise the lifecycle-script execution policy? Trace the config → install/rebuild pipeline to find every package whose runtime behavior or test suite depends on this default.

2. **Impact classification**: For each affected package, classify the semver impact (major / minor / patch) of flipping this default, and justify each classification from the code and tests you found — don't just assert a number without tying it to what the package actually exposes or depends on.

3. **Boundary violations**: Identify the source file(s) that implement or check this config default, and the test files that would need to change (or fail) under the new default.

## Environment

- `/workspace/pnpm/` — the pnpm monorepo (workspaces include `config/`, `exec/`, `pnpm/`, and others)

## Output

Write findings to `/workspace/pnpm/IMPACT_REPORT.md`.
