# CRI Seccomp Profile Chain Failure

## Context

After a coordinated cluster upgrade (Kubernetes v1.33, containerd v1.7.24, runc v1.2.4, moby v28.0.0), application teams report that workloads with custom seccomp profiles fail to start. The failure is immediate — containers exit before the entrypoint runs.

The error surfaces in kubelet logs as:

```
container_linux.go: starting container process caused:
  seccomp: unable to apply seccomp profile: operation not permitted
```

And in some cases:

```
seccomp filter did not recognize syscall: clone3
  action=SCMP_ACT_ERRNO errno=EPERM
```

Pods using the `RuntimeDefault` seccomp profile continue to work correctly. Only pods that reference a custom seccomp profile (either via `securityContext.seccompProfile` or the legacy annotation) are affected.

## What We Know

- The node kernel was upgraded (5.10 -> 6.1) in the same maintenance window as the container runtime upgrade.
- The custom seccomp profiles predate this window and were not revisited as part of it.
- Only pods with a custom profile are affected; pods left on the platform default are unaffected.

## Your Task

The failure spans four repositories: `kubernetes` (kubelet), `containerd`, `runc`, and `moby`. Read the actual code in each — do not rely on general seccomp/CRI knowledge — and determine, citing the specific file(s) and function(s) you find in each repo:

1. Where and how kubelet converts a pod's seccomp profile into the request it hands to the container runtime.
2. Where the container runtime maps that request into the on-disk runtime spec the low-level OCI runtime consumes.
3. Where the low-level OCI runtime turns that spec into an enforced syscall filter, and what it does with a syscall the filter doesn't recognize.
4. What the platform's own default profile permits that the failing custom profile does not, and why that particular gap matters given the kernel upgrade.
5. The complete failure path, hop by hop, from the low-level filter decision back to the error the user sees.

## Deliverable

Write your incident report to `/workspace/moby/INCIDENT_REPORT.md`.

The report should include root cause identification with specific files and functions in each repository, the full processing chain, and remediation recommendations.
