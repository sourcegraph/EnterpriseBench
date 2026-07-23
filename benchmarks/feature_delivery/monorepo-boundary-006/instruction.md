# Impact Analysis: Lifecycle Scripts Allow-List Default

## Context

Security team flagged that pnpm runs arbitrary lifecycle scripts (postinstall, preinstall, etc.) by default. For v10.0.0 we're flipping the default: lifecycle scripts now require an explicit allow-list in the project's `.npmrc` or `package.json`.

The core change is in `@pnpm/config` — specifically `config/config/src/getOptionsFromRootManifest.ts`. It's a one-line default value change, but the ripple effect matters.

## What I Need

1. **Affected packages**: Which workspace packages read the lifecycle script execution policy? Think about the install pipeline: config → core → build-modules → headless → plugin commands.

2. **Impact classification**: The config package itself is a major bump. But are downstream packages also major (they expose different behavior) or just patch (internal adjustment)?

3. **Boundary violations**: Where in the codebase does code assume lifecycle scripts run by default? Show me test files that would fail with the new default, and source files that check this config value.

## Output

Write findings to `/workspace/agent_output/IMPACT_REPORT.md`.
