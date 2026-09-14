# Removal impact: ArrayMaxLengthValidator

Our platform team is consolidating the PostgreSQL-specific length checks in our Django fork onto the generic ones. The first candidate is `ArrayMaxLengthValidator`.

Report every source file under `django/` that defines that class or names it.

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
