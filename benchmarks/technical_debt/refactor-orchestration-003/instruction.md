# Refactor Orchestration: grpc-go v1.72 Bump Cascade

## Context

grpc-go v1.72.1 shipped a breaking change (PR grpc/grpc-go#8155) that some
downstream consumers hit at compile time — specifically ones that implement
mock gRPC servers for testing.

## Task

Given these repositories at their pre-refactor state, produce a topologically
sorted execution plan for propagating the grpc-go v1.72 update through the
stack. You will need to read each repo to determine which interface actually
changed, which repos depend on which (and how directly), and where each
downstream fix has to land.

## Repos in Workspace

- `/workspace/grpc-go/` — grpc-go v1.72.1
- `/workspace/etcd/` — etcd v3.5.17
- `/workspace/kubernetes/` — Kubernetes v1.32.0

## Expected Output

Write `/workspace/REFACTOR_PLAN.md` containing:

1. A numbered list of repos in the order they should be updated
2. Dependency graph (who depends on whom)
3. Parallelization annotations
4. Breaking vs. compatible change annotations per step

## Reference

- grpc/grpc-go v1.72.1 release (2025-05-14)
- kubernetes/kubernetes PR #131838: Bump grpc to v1.72.1 (merged 2025-05-20)
