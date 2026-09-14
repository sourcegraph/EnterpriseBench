# Incident: Container Start Failure with "exec format error" on Multi-Arch Cluster

## Alert

A multi-architecture Kubernetes cluster reports that containers fail to start on arm64 nodes with "exec format error" despite pulling the correct platform-specific image.

## Symptoms

- `docker run` fails with: "OCI runtime create failed: runc create failed: unable to start container process: exec: '/entrypoint.sh': permission denied or exec format error"
- The image manifest lists both amd64 and arm64 variants
- `docker inspect` shows the correct arm64 image is pulled
- The error occurs in runc during process creation, not during image pull
- containerd logs show the container is created successfully but start fails
- The same image works without issue on amd64 nodes; only specific images trigger the failure on arm64, and only on some of those images

## Environment

- Docker (Moby) v24.0.7 at `/workspace/moby/`
- containerd v1.7.8 at `/workspace/containerd/`
- runc v1.1.10 at `/workspace/runc/`
- Focus on Go code across all three repos

## What I Need

Investigate the three codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
OCI runtime theory.

1. **Root Cause**: Identify the exact code path in runc where the container's
   entrypoint process is started, and explain precisely why that path
   produces this error only for certain entrypoints on arm64 nodes. Cite the
   file and function.

2. **Error Chain**: Trace the full error propagation, hop by hop, from the
   point of failure inside runc, through containerd, up to the user-visible
   Docker daemon error. Tie each hop to the code you found.

3. **Affected Components**: Which components in each of the three repos are
   involved in the failure?

4. **Remediation**: How should this be prevented or detected earlier?

## Output

Write your findings to `/workspace/moby/INCIDENT_REPORT.md`.
