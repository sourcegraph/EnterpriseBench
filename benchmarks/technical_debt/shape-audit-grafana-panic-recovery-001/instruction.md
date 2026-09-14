# Panic-recovery inventory across the service layer

After an outage where a background goroutine took the process down, we are standardising how services contain a panic instead of letting it reach the top of the stack. Step one is an inventory of the current state.

Search the tree under `pkg/services/`. Report every source file there that installs a panic-containment call, that is, a file that calls the built-in which stops a panicking goroutine and returns the panic value. Exclude tests. A file counts if it contains at least one such call, whether or not it is inside a deferred function.

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
