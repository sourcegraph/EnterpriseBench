# Unimplemented extension points in the database layer

We are writing a database backend for an in-house column store, and we need to know which parts of the database layer are abstract: the methods it declares without behaviour, leaving the real implementation to a subclass.

Search the tree under `django/db/`. Report every source file there that declares at least one method whose entire body raises the not-implemented error, signalling that a subclass must provide the real behaviour. A method that raises it only on one branch, after doing other work, does not count.

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
