# Node.js CommonJS to ESM Module Loader Migration Surface

## Your Task

Find all JavaScript and C++ source files in nodejs/node that implement the ESM (ECMAScript Modules) loader and the CommonJS-to-ESM interoperability bridge. Your inventory should cover: the core class that orchestrates module resolution, loading, and format dispatch for ES modules; the function inside the CommonJS loader that kicks in when a `require()` target turns out to actually be an ES module, and hands resolution off to the ESM machinery; the translators that turn raw source text into evaluable modules for each supported format (CommonJS, JSON, Wasm, etc.); the component that resolves bare and relative ESM specifiers down to file URLs; the abstraction representing a single module's link/instantiate/evaluate lifecycle as it moves through the loading pipeline; the code path that decides, at process startup, whether the entry point should be loaded as CommonJS or as an ES module; and the native C++ binding that wraps a V8 module record for JS-level manipulation.

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
    {"repo": "org/repo-name", "path": "relative/path/to/file.go"}
  ],
  "symbols": [
    {"repo": "org/repo-name", "path": "relative/path/to/file.go", "symbol": "SymbolName"}
  ],
  "chain": [
    {"repo": "org/repo-name", "path": "relative/path/to/file.go", "symbol": "FunctionName"}
  ],
  "text": "Narrative explanation of your findings, citing repos and file paths."
}
```

Include only the fields relevant to this task. Your answer is evaluated against a closed-world oracle — completeness matters.

## Evaluation

Your answer will be scored on:
- **File recall and precision**: Did you find all relevant files?
