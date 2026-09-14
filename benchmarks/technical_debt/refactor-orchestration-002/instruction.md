# Refactor Orchestration: cobra v1.10 CLI Framework Bump

## Context

The spf13/cobra CLI framework v1.10.2 includes improved command suggestions and
bug fixes. Kubernetes uses cobra for kubectl and all control plane binaries
(kube-apiserver, kube-controller-manager, kube-scheduler, kubelet).

## Task

Given these repositories at their pre-refactor state, produce a topologically
sorted execution plan for bumping cobra across the dependency chain.

## Repos in Workspace

- `/workspace/cobra/` — spf13/cobra v1.10.2 (upstream library)
- `/workspace/kubernetes/` — Kubernetes v1.33.0 (consumer)

## Expected Output

Write `/workspace/REFACTOR_PLAN.md` containing:

1. A numbered list of repos in the order they should be updated, using each
   repo's canonical GitHub `org/repo` identifier (owner/name form)
2. Dependency graph (which repo depends on which) — ground each edge in the
   actual dependency-manifest declaration you find in the repos, not assumption
3. Parallelization annotations (which steps can run concurrently, if any)
4. Risk assessment per step
