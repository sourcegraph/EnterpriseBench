# Refactor Orchestration: Protobuf v1 to v2 Import Migration

## Context

The Go protobuf ecosystem has migrated from `github.com/golang/protobuf` to
`google.golang.org/protobuf`. This is a major import path change that ripples
through any Go module that still imports the legacy path, directly or via a
dependency.

One of the repos below performed this migration internally in PR #6919
(merged 2024-01-30); the others reference it as a dependency. You will need
to read each repo's `go.mod` and source imports to work out which repo plays
which role and in what order they must be updated.

## Task

Given these repositories at their pre-migration state, produce a topologically
sorted execution plan for the import path migration.

## Repos in Workspace

- `/workspace/protobuf-go/` — google.golang.org/protobuf v1.32.0
- `/workspace/grpc-go/` — grpc-go v1.62.0
- `/workspace/etcd/` — etcd v3.5.12

## Expected Output

Write `/workspace/REFACTOR_PLAN.md` containing:

1. A numbered list of repos in the order they should be updated
2. Dependency graph
3. Import path changes required at each step
4. Parallelization annotations
5. Risk assessment

## Reference

- grpc-go PR #6919: move from github.com/golang/protobuf to google.golang.org/protobuf
