# Teardown-hook inventory in the scheduler

We are changing the order in which the runtime tears itself down, and any type that runs code at destruction time inside the scheduler is affected by that ordering.

Search the tree under `tokio/src/runtime/scheduler/`. Report every source file there that provides a destructor implementation for a type, that is, an implementation block for the trait the language runs when a value goes out of scope.

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
