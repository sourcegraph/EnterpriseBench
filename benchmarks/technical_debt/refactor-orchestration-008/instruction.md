# Refactor Orchestration: go-grpc-middleware v1 Removal Cascade

## Context

grpc-ecosystem/go-grpc-middleware v1 has been archived. Removing it cleanly
is not a single version bump: v1 and v2 have incompatible interceptor APIs,
some functionality has no direct v2 equivalent and must be reimplemented
rather than swapped in, and the library is consumed both directly and
transitively (through other archived middleware packages) by downstream
services. The removal has to happen as an ordered, multi-repo cascade —
some consumers can move first, others must wait until an upstream consumer
has cleared the way.

## Task

Given these repos at their pre-removal state, investigate how each one
actually uses go-grpc-middleware v1 (direct import, transitive dependency,
which interceptors/features, vendored copies, etc.), determine every
component that has to change, and produce a topologically sorted execution
plan for removing the archived dependency across all of them.

## Repos in Workspace

- `/workspace/go-grpc-middleware/` — go-grpc-middleware v1.4.0 (archived upstream)
- `/workspace/etcd/` — etcd v3.5.17
- `/workspace/kubernetes/` — Kubernetes v1.33.0

## Expected Output

Write `/workspace/REFACTOR_PLAN.md` containing:

1. Numbered list of changes in order
2. Dependency graph
3. Migration strategy per component (v2 update vs. reimplement vs. remove)
4. Parallelization annotations
5. Risk assessment
