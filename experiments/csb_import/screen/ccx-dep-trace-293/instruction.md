# Roslyn C# Type Inference and Overload Resolution Chain

## Your Task

Trace the type inference and method overload resolution pipeline in dotnet/roslyn. Find all C# source files that participate in overload resolution. Identify: the overload-resolution driver class and its main resolve methods, the type-inference helper that performs generic-type-argument inference for method calls, the helper that ranks two function-member candidates against each other, the conversion-ranking class used to compare implicit conversions, and the helper that infers a best common type for array and conditional expressions. Report each file path and key class.

## Context

You are working on a codebase task involving repos from the crossrepo tracing domain.

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
