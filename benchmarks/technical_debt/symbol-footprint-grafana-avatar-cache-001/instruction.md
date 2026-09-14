# Removal impact: AvatarCacheServer

Our hardening review wants the outbound image cache served by `AvatarCacheServer` removed from the deployment entirely. Removing a server type means removing its construction and its route wiring in the same change.

Report every source file under `pkg/` that declares that type or names it.

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
