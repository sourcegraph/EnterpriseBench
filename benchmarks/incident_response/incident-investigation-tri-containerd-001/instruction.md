# Incident: Container Start Failure with "exec format error" on Multi-Arch Cluster

## Alert

A multi-architecture Kubernetes cluster reports that containers fail to start on arm64 nodes with "exec format error" despite pulling the correct platform-specific image.

## Symptoms

- `docker run` fails with: "OCI runtime create failed: runc create failed: unable to start container process: exec: '/entrypoint.sh': permission denied or exec format error"
- The image manifest lists both amd64 and arm64 variants
- `docker inspect` shows the correct arm64 image is pulled
- The error occurs in runc during process creation, not during image pull
- containerd logs show the container is created successfully but start fails
- The issue only affects images using a shell script as entrypoint with a missing or incorrect shebang line

## Investigation So Far

The team has identified the general area of the failure:

1. Docker daemon delegates container lifecycle to containerd via gRPC
2. containerd creates a shim process and delegates exec to runc
3. runc uses the Linux kernel's execve() syscall to start the container process
4. When the entrypoint is a script without a shebang, the kernel falls back to /bin/sh
5. The image's /bin/sh may be a different architecture binary
6. The error message varies: "exec format error" for wrong arch, "permission denied" for non-executable files

## Environment

- Docker (Moby) v24.0.7 at `/workspace/moby/`
- containerd v1.7.8 at `/workspace/containerd/`
- runc v1.1.10 at `/workspace/runc/`
- Focus on Go code across all three repos

## What I Need

1. **Root Cause**: Find where runc executes the container process using execve and how it handles the exec format error. Trace through all three repos.

2. **Error Chain**: Trace the full error propagation: runc execve failure -> containerd shim error -> Docker daemon error response -> user-visible error.

3. **Affected Components**: Which components in each of the three repos are involved in the failure?

4. **Remediation**: How should this be prevented or detected earlier?

## Output

Write your findings to `/workspace/agent_output/INCIDENT_REPORT.md`.
