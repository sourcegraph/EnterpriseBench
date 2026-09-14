# Removal impact: ConvertPodStatusToRunningPod

A proposal on our Kubernetes fork deprecates the helper `ConvertPodStatusToRunningPod` in favour of a conversion the runtime layer already performs. Deprecating it means updating every caller in the same release.

Report every source file under `pkg/` that defines or names that function.

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
