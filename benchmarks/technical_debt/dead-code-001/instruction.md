# Dead Code Necropsy: React Legacy Event System

## Context

React migrated its event system from a legacy plugin-based architecture to a modern system. After this migration completed, several files, functions, and exports in the legacy event system became dead code — they are no longer imported, called, or referenced anywhere in the codebase.

Your task is to perform a dead code analysis and identify all unreachable code related to the legacy event system.

## What to Look For

1. **Entire dead files** — files in `packages/react-dom/src/legacy-events/` or elsewhere that have zero importers
2. **Dead functions/exports** — functions that exist in live files but are never called (e.g., event pooling functions like `getPooled`, `destructor`)
3. **Dead feature flags** — flags that are permanently on or off, making their conditional branches dead code
4. **Dead injection code** — initialization/injection modules that no longer serve a purpose

## Output

Write your findings to `/workspace/agent_output/dead_code_report.json` as a JSON array where each entry has:
- `file`: relative path from repo root
- `symbol`: function/class/export name (use `"default"` for entire file being dead)
- `kind`: one of `function`, `class`, `method`, `file`, `export`
- `confidence`: `high`, `medium`, or `low`
- `evidence`: brief explanation of why this code is dead

## Scoring

- **Precision matters most**: incorrectly flagging live code as dead is heavily penalized
- Finding more dead code improves recall but never flag something you're unsure about
- Provide clear evidence for each claim
