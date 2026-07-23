# Dead Code Necropsy: React enableRenderableContext Feature Flag

## Context

React uses feature flags extensively to gate experimental features. These flags are defined in `packages/shared/ReactFeatureFlags.js` and forked across multiple configuration files for different build targets (www, native, test-renderer, etc.).

When a feature flag becomes permanently enabled (set to `true` in the main flags file and all forks), the `else` branches in every file that checks that flag become dead code. This dead code should be removed to reduce complexity and bundle size.

## What to Look For

1. **Permanently-enabled feature flags** — flags set to `true` in the main file and all forks
2. **Dead `else` branches** — code paths that execute when the flag is `false` (which never happens)
3. **Dead types/symbols** — types, constants, or React symbols only used by the dead branches
4. **Dead fork entries** — flag declarations in fork files that are always the same value

## Output

Write your findings to `/workspace/agent_output/dead_code_report.json` as a JSON array where each entry has:
- `file`: relative path from repo root
- `symbol`: function/type/branch description
- `kind`: one of `function`, `class`, `method`, `export`, `branch`
- `confidence`: `high`, `medium`, or `low`
- `evidence`: brief explanation of why this code is dead

## Scoring

- **Precision matters most**: incorrectly flagging live code as dead is heavily penalized
- Must identify the primary feature flag by name
- Evidence should trace the flag through its forks
