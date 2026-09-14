# Incident: Critical Alert Silenced by Misconfigured Inhibition Rule

## Alert

A critical disk-usage alert (`DiskUsageCritical`) failed to fire during a production outage. The alert rule evaluates correctly in Prometheus, but Alertmanager never dispatches the notification.

## Symptoms

- Alert rule `DiskUsageCritical` evaluates to firing state in Prometheus
- Prometheus sends the alert to Alertmanager (confirmed in Prometheus logs)
- Alertmanager receives the alert but never dispatches notification
- Alertmanager UI shows the alert as "suppressed" by an inhibition rule
- The inhibiting alert is `NodeNotReady`, which fires based on a recording rule named `node:ready:condition`
- `NodeNotReady` appears to be firing continuously, even when nodes report healthy

## Environment

- Prometheus v2.48.0 at `/workspace/prometheus/`
- Alertmanager v0.26.0 at `/workspace/alertmanager/`
- Focus on Go backend code for both projects

## What I Need

Investigate both codebases and write an incident report grounded in the actual
code — cite the specific files and functions you found, not general
inhibition-rule or recording-rule theory.

1. **Root Cause**: Find where Alertmanager implements inhibition rule matching
   (name the file and the type/function that decides which alerts get muted),
   and find where Prometheus evaluates recording rules (name the file). Explain
   what about the recording rule's label handling could make `NodeNotReady`
   fire continuously.

2. **Error Chain**: Trace the full chain from recording-rule evaluation through
   to the suppressed notification, with each hop tied to a specific code path
   you identified in the repos (not just the symptom text above).

3. **Affected Components**: Which alerting components and alert rules are
   affected by this pattern? Identify them by the actual source files/paths
   that implement each stage of the pipeline.

4. **Remediation**: How should the recording rule and/or inhibition rule be
   fixed, and where (which repo/file) does that fix belong?

## Output

Write your findings to `/workspace/alertmanager/INCIDENT_REPORT.md`.
