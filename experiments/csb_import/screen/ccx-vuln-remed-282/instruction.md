# Kubernetes RBAC Authorization Implementation

## Your Task

Find all Go source files in kubernetes/kubernetes that implement the RBAC (role-based access control) authorization pipeline. Identify: the top-level authorization plugin that decides whether an incoming request is permitted, based on the RoleBindings and ClusterRoleBindings held by the acting subject; the lower-level component that determines which subjects (users, groups, or service accounts) are granted a given verb on a given resource; the resolver that computes the effective, transitive set of policy rules a user has been granted through their role bindings; the API handler that services requests asking whether a subject is allowed to perform an action; the bootstrap policy definitions that seed the default RBAC roles present on a freshly created cluster; and the storage layer wrapping persistence of Role and ClusterRoleBinding objects that blocks a user from granting privileges beyond their own.

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
