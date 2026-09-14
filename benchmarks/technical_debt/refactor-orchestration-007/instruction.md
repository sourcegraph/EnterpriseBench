# Refactor Orchestration: grpc.Dial -> grpc.NewClient Migration

## Context

grpc-go deprecated `grpc.Dial` and `grpc.DialContext` in v1.64 (June 2024),
replacing them with `grpc.NewClient`. The new API is non-blocking by default,
changing connection semantics. This deprecation cascades through any project
that calls `grpc.Dial` directly or wraps it in its own client library.

## Task

Given these repos, determine which of them actually require this API
migration, the order the migration must land in (respecting who depends on
whom), and whether any of the steps can happen in parallel. Investigate each
repo's real `grpc.Dial`/`grpc.DialContext` call sites, wrapper layers, and
migration history — the actual ordering and evidence should come from what
you find in the code and commit history, not be assumed.

## Repos in Workspace

- `/workspace/grpc-go/` — grpc-go v1.79.0 (upstream, has NewClient)
- `/workspace/etcd/` — etcd v3.5.17 (pre-migration, still uses Dial)
- `/workspace/kubernetes/` — Kubernetes v1.33.0 (consumer)

## Expected Output

Write `/workspace/REFACTOR_PLAN.md` containing:

1. Numbered list of repos in migration order
2. Dependency graph
3. API migration details per repo
4. Behavioral differences (blocking vs non-blocking)
5. Parallelization annotations — state explicitly whether any steps can run
   concurrently, or that the chain is strictly sequential, and ground that
   claim in the actual dependency relationship between the repos
