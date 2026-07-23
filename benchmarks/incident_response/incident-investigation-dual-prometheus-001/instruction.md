# Incident: Critical Alert Silenced by Misconfigured Inhibition Rule

## Alert

A critical disk-usage alert (`DiskUsageCritical`) failed to fire during a production outage. The alert rule evaluates correctly in Prometheus, but Alertmanager never dispatches the notification.

## Symptoms

- Alert rule `DiskUsageCritical` evaluates to firing state in Prometheus
- Prometheus sends the alert to Alertmanager (confirmed in Prometheus logs)
- Alertmanager receives the alert but never dispatches notification
- Alertmanager UI shows the alert as "suppressed" by an inhibition rule
- The inhibiting alert is `NodeNotReady`, which fires based on a recording rule
- The recording rule `node:ready:condition` has a subtle label mismatch

## Investigation So Far

The team traced the suppression to the Alertmanager inhibition pipeline:

1. Alertmanager inhibition rules suppress alerts when a "source" alert matches
2. The `NodeNotReady` alert fires from recording rule `node:ready:condition`
3. The recording rule aggregates `kube_node_status_condition` but drops the `condition` label during aggregation, causing it to always evaluate to 1
4. This means `NodeNotReady` is perpetually firing, inhibiting `DiskUsageCritical`
5. The inhibition rule matches on `severity` label: source=critical inhibits target=critical

## Environment

- Prometheus v2.48.0 at `/workspace/prometheus/`
- Alertmanager v0.26.0 at `/workspace/alertmanager/`
- Focus on Go backend code for both projects

## What I Need

1. **Root Cause**: Find where Alertmanager implements inhibition rule matching and where Prometheus evaluates recording rules with label manipulation.

2. **Error Chain**: Trace the full inhibition chain: recording rule misconfiguration -> perpetual source alert -> inhibition match -> suppressed target alert.

3. **Affected Components**: Which alerting components and alert rules are affected by this pattern?

4. **Remediation**: How should the recording rule and/or inhibition rule be fixed?

## Output

Write your findings to `/workspace/agent_output/INCIDENT_REPORT.md`.
