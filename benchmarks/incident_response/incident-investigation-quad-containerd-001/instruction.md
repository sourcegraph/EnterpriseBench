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

- The custom seccomp profiles were authored against kernel 5.10 and have not been updated for newer syscalls (`clone3`, `close_range`, `openat2`, `faccessat2`)
- The cluster nodes are running kernel 6.1, which uses these newer syscalls in glibc
- The CRI seccomp flow is: kubelet SecurityContext conversion -> containerd CRI plugin -> OCI runtime spec -> runc BPF filter
- moby's default seccomp profile includes these newer syscalls; the custom profiles do not

## Your Task

Investigate the full seccomp profile handling chain across all four repositories to determine:

1. Where and how kubelet converts pod-level seccomp profiles into CRI requests
2. How containerd's CRI plugin maps the seccomp profile into the OCI runtime specification
3. How runc's libcontainer generates the BPF seccomp filter and handles unrecognized syscalls
4. How moby's default seccomp profile differs from the failing custom profiles
5. The complete error propagation path from runc filter failure back to the user-visible kubelet error

## Deliverable

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.

The report should include root cause identification with specific files and functions in each repository, the full processing chain, and remediation recommendations.
