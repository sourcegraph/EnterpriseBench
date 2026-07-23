# Incident: Cortex ruler silently loses alerts for tenants with UTF-8 label names

## Alert

A new multi-tenant Cortex 1.18 cluster is dropping alerts for one tenant
without surfacing any error in the usual ruler-group failure metrics:

```
level=error msg="alert delivery failed" tenant=team-utf8 alerts=4
level=warn  msg="non-recoverable error sending alert" status_code=400
level=warn  msg="error sending alerts" err="bad_data: invalid label name"
(no further entries — affected alerts never retry, never page)
```

`alertmanager_alerts_received_total` for the affected receiver stays flat
even though the ruler's evaluation metrics show the alert firing.

## Symptoms

- Only tenants whose recording rules emit UTF-8 label names (e.g.
  `http.status_code`, `k8s.namespace`) are affected. Classic ASCII tenants
  are unaffected.
- The cluster is running with the default (legacy) name-validation scheme
  on the ruler side and Alertmanager v0.27.0 in classic label-validation
  mode.
- `cortex_prometheus_rule_group_iterations_failed_total` is flat for the
  affected groups — the failure is invisible to the per-group error path.

## Environment

- `/workspace/cortex/` — `cortexproject/cortex` v1.18.0
- `/workspace/alertmanager/` — `prometheus/alertmanager` v0.27.0

Both Go projects. The failure lives at the seam between the Cortex ruler's
outbound alert-send path and Alertmanager's inbound label validation; you
will need to read both repos to locate it.

## What I need

Investigate the two codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
description of the symptoms above.

1. **Root cause** — name the specific file and function on each side of the
   boundary: the Cortex code path that constructs and sends alerts to
   Alertmanager, and the Alertmanager code that validates alert label
   names. Cite each by path.
2. **Error chain** — trace from the rule evaluation that produces the
   UTF-8 label to the alert being dropped with no surfaced error, at least
   four hops, each tied to a code path you identified.
3. **Affected components** — identify every component across both repos
   that participates in the alert delivery path from rule evaluation to
   Alertmanager.
4. **Remediation** — propose a concrete, code-grounded fix and say where
   it belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
