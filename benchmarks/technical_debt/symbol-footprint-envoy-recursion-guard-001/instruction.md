# Removal impact: AutoDebugRecursionChecker

A cleanup proposal on our Envoy fork removes the debug-only guard type `AutoDebugRecursionChecker` and folds its assertion into the callers directly. Before anyone writes that change we need to know its blast radius exactly.

Report every source file in the repository that would have to be edited: the file that declares the type and every file that names it.

## What to produce

Write `/workspace/agent_output/answer.json` with this shape:

```json
{
  "source_files": [
    {"path": "<repo-relative path>", "why": "<one line: what this file contributes>"}
  ]
}
```

List every source file that qualifies and no others. Exclude tests, vendored
trees, generated code, and documentation. Paths are relative to the repository
root, not to `/workspace`.
