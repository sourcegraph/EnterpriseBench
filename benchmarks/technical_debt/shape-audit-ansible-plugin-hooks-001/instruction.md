# Unimplemented extension points in the plugin system

We maintain an internal distribution of Ansible and are documenting every hook a plugin author is required to implement, as opposed to the ones that already have working defaults.

Search the tree under `lib/ansible/`. Report every source file there that declares at least one method whose entire body raises the not-implemented error. A method that raises it conditionally, after doing other work, does not count.

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
