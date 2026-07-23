# Incident: Docker Save Produces Incomplete Image Archives with Missing Layer Blobs

## Alert

Operations team managing air-gapped deployments reports that `docker save` exports are producing corrupt archives. Images loaded on air-gapped production servers fail to start with missing layer errors.

## Reproduction Steps

On a Docker host with the containerd image store (containerd snapshotter) enabled:

```
# Step 1: Pull first image — export works fine
docker pull python:3.11-slim
docker save python:3.11-slim > image-a.tar   # OK — all layers present

# Step 2: Pull second image sharing base layers — export is broken
docker pull python:3.12-slim
docker save python:3.12-slim > image-b.tar   # BROKEN — shared layers missing
```

## Observed Behavior

Inspecting the exported archive shows missing layer blobs:

```
$ tar tf image-b.tar | head -20
manifest.json
oci-layout
blobs/sha256/...   # config and manifest present
                   # but shared layer blobs are MISSING or 0-byte
```

On the air-gapped target:

```
$ docker load < image-b.tar
Error processing tar file(exit status 1): open layer.tar: no such file or directory
```

## Diagnostic Clues

Investigating the staging host's containerd state reveals an asymmetry between the content store and snapshotter:

```
# Layer blob is MISSING from content store
$ ctr -n moby content ls 2>/dev/null | grep sha256:a1b2c3
(no output)

# But the snapshot (extracted filesystem) EXISTS
$ ctr -n moby snapshots --snapshotter overlayfs ls 2>/dev/null | grep sha256:a1b2c3
sha256:a1b2c3...   Committed   sha256:parent...
```

The snapshot for the shared layer exists (the extracted filesystem is there), but the original compressed layer blob was never stored in the content store. This only affects layers that were already present as snapshots when the second image was pulled.

## Environment

- Docker (moby) v28.0.0 at `/workspace/moby/`
- containerd v1.7.24 at `/workspace/containerd/`
- Containerd image store / containerd snapshotter enabled
- Focus on the image pull pipeline and its interaction with containerd's unpack and content store

## What I Need

1. **Root Cause**: Why are layer blobs missing from the content store when their snapshots exist? Which code path in the pull/unpack pipeline skips fetching layer content when a snapshot is already present?

2. **Error Chain**: Trace the full flow from the `docker pull` API call through moby's pull implementation, into containerd's unpack logic, showing exactly where and why layer content fetch is skipped for layers whose snapshots already exist. Then trace forward to how `docker save` (image export) handles the missing content.

3. **Affected Components**: What parts of both moby and containerd are involved in this bug? Include specific files and functions across both repositories.

4. **Remediation**: How should the pull pipeline be fixed so that layer blobs are always available in the content store, even when snapshots already exist?

## Output

Write your findings to `/workspace/agent_output/INCIDENT_REPORT.md`.
