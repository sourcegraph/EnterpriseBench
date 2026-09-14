# Node.js child_process Command Injection Guards

## Your Task

Find all JavaScript and C++ source files in nodejs/node that implement input validation and command injection prevention for the `child_process` module. Identify: the JS-level helper that normalises arguments for process creation and performs null-byte validation, the three public entry points (shell-string command running, file running with an argument array, and direct process creation), the internal JS class that bridges to the C++ layer, the C++ native binding that wraps the libuv process handle, and the validator helper used to reject null bytes in argument values.

## Context

You are working on a codebase task involving repos from the security domain.

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
