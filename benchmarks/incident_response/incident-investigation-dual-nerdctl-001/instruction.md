# Incident: nerdctl pull intermittently fails on devmapper hosts

## Alert

Some build farm hosts using `nerdctl pull --snapshotter=devmapper` against
containerd 1.7.24 fail intermittently:

```
$ nerdctl pull --snapshotter=devmapper my.registry/myimage:1.2.3
ERROR: failed to pull and unpack image: unable to initialize unpacker:
  no unpack platforms defined
```

## Symptoms

- About 20% of pulls fail with the same error.
- Same image, same registry, same nerdctl/containerd versions.
- Hosts using the default `overlayfs` snapshotter are unaffected.
- Workaround: re-run the pull; eventually it succeeds.

## Environment

- `/workspace/nerdctl/` — `containerd/nerdctl` v2.0.0
- `/workspace/containerd/` — `containerd/containerd` v1.7.24

Both Go projects. The failure lives at the seam between nerdctl's pull path
and containerd's unpacker; you will need to read both repos to locate it.

## What I need

Investigate the two codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
theory about snapshotters or platforms.

1. **Root cause** — name the specific file and function on each side that
   need to cooperate to populate the unpack platforms list: the nerdctl code
   that builds the pull options, and the containerd code that consumes the
   platforms list during unpack. Cite each by path.
2. **Error chain** — trace the pull from `nerdctl pull` invocation through
   the unpacker, at least four hops, each hop tied to a code path you
   identified.
3. **Affected components** — identify every component across both repos that
   participates in constructing, resolving, or consuming the unpack platforms
   list along this path.
4. **Remediation** — propose a concrete, code-grounded fix and say where it
   belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
