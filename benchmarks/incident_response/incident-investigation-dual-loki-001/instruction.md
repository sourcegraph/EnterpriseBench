# Incident: Loki query frontend reports "too many unhealthy instances" during rolling index-gateway restarts

## Alert

Grafana Loki 3.3 query frontends on a Kubernetes cluster begin returning
errors to users immediately after a rolling restart of the index-gateway
StatefulSet:

```
level=error msg="failed to get chunk refs" err="too many unhealthy instances in the ring"
level=warn  msg="ring instance LEAVING" instance=loki-index-gateway-1
level=error msg="chunk lookup failed" err="at least 1 healthy replica required, could only find 0 - unhealthy
instances: loki-index-gateway-1 (LEAVING)"
```

## Symptoms

- Errors only occur in the window where one or more index-gateway pods are
  in the `LEAVING` ring state during a rolling restart.
- The index-gateway processes themselves are still serving on their gRPC
  port — `kubectl exec` and direct port-forwarded probes succeed.
- The gateway-server side of the ring reports the same instances as healthy
  under the `Reporting` operation; the gateway-client (inside the query
  frontend) reports them as unhealthy under the `Read` operation.
- Restarting the query frontend clears the failure transiently.

## Environment

- `/workspace/loki/`  — `grafana/loki` v3.3.0
- `/workspace/dskit/` — `grafana/dskit` commit `53283a0f6b41` (the pin from
  Loki v3.3.0's `go.mod`, pseudo-version
  `v0.0.0-20241007172036-53283a0f6b41`)

Both Go projects. The failure lives at the seam between Loki's storage layer
and the dskit ring library it depends on for instance-health decisions; you
will need to read both repos to locate it.

## What I need

Investigate both codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
ring theory.

1. **Root cause** — name the specific file and function on each side of the
   contradiction: the Loki code path that requests chunk data from the index
   gateway, and the dskit code that decides whether a `LEAVING` instance is
   healthy for a given ring operation. Cite each by path.
2. **Error chain** — trace from the rolling restart to the user-facing
   failure, at least four hops, each hop tied to a code path you identified.
3. **Affected components** — identify every component across both repos that
   participates in the chunk-ref lookup and ring-health path.
4. **Remediation** — propose a concrete, code-grounded fix and say where it
   belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
