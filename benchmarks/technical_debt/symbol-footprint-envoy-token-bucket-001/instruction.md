# Removal impact: AtomicTokenBucketImpl

We are replacing the lock-free rate-limiter class `AtomicTokenBucketImpl` with a shared implementation from our platform library. The migration has to land in one commit, so we need the full list of files it touches before we start.

Report every source file in the repository that declares, defines, or names that class.

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
