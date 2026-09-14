# Downcast audit in the HTTP layer

A performance review flagged run-time type checks on the request hot path. Before we decide whether to replace any of them we need an inventory of where the HTTP layer converts a base-class pointer or reference down to a derived type using a checked run-time conversion (the conversion form that returns null or throws when the object is not of the target type, rather than the unchecked form).

Search the tree under `source/common/http/`. Report every source file there that performs at least one such conversion. Ignore conversions that are unchecked, and ignore ones that only appear in a comment.

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
