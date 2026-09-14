# Envoy xDS v3 Transport Protocol Implementation

## Your Task

Find all C++ source files in envoyproxy/envoy that implement the xDS v3 transport protocol. Identify: the subscription-layer adapter that wraps a raw gRPC mux behind the generic config-subscription interface, the State-of-the-World (SotW) mux implementation that tracks every watched resource of a type together, the Delta xDS mux implementation that layers incremental resource updates on top of the SotW machinery, the factory that decides at runtime whether a gRPC config subscription should run in SotW or Delta mode, and the unified mux template shared by both variants.

## Context

You are working on a codebase task involving repos from the migration domain.

## Available Resources

## Output Format

Use the published task contract:

- `TASK_WORKDIR=/workspace`
- `TASK_REPO_ROOT=/workspace`
- `TASK_OUTPUT=/workspace/answer.json`

Create a file at `TASK_OUTPUT` (`/workspace/answer.json`) with your findings in the following structure:

```json
{
  "files": [
    {
      "repo": "org/repo-name",
      "path": "relative/path/to/file.go"
    }
  ],
  "symbols": [
    {
      "repo": "org/repo-name",
      "path": "relative/path/to/file.go",
      "symbol": "SymbolName"
    }
  ],
  "chain": [
    {
      "repo": "org/repo-name",
      "path": "relative/path/to/file.go",
      "symbol": "FunctionName"
    }
  ],
  "text": "Narrative explanation of your findings, citing repos and file paths."
}
```
