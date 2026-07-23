# Incident: Informers Miss Events After Receiving DELETE Watch Event

## Alert

SRE team has identified a subtle data consistency issue: Kubernetes informers occasionally get stuck with a stale resourceVersion after processing DELETE watch events, causing them to miss subsequent changes.

## Symptoms

- After receiving a DELETE event from a namespace-scoped watch, the informer's tracked resourceVersion jumps backwards
- If the watch connection drops and is re-established, events between the stale and current resourceVersion are permanently missed
- This affects any namespace-scoped watch where objects can be modified to move between namespaces
- The issue does NOT occur when watching etcd directly (bypassing the watch cache)
- client-go informers are the primary affected consumers

## Reproduction Scenario

1. Establish a namespace-scoped watch via API server (watch cache enabled)
2. An object currently in the watched namespace is modified so it moves to a different namespace (or its labels change so it no longer matches the filter)
3. The watch cache correctly converts this to a DELETE event
4. Inspect the resourceVersion on the DELETE event -- it shows the old object's version, not the event's version
5. The etcd3 direct watcher handles this case correctly by copying the event resourceVersion onto the previous object

## Key Observation

The etcd3 watcher and the legacy etcd watcher both have logic to set the correct resourceVersion on delete events. The watch cache layer appears to be missing this logic.

## Environment

- Kubernetes v1.9.1
- The codebase is at `/workspace/kubernetes/`
- Focus on the API server's storage layer

## What I Need

1. **Root Cause**: Find the exact code in the watch cache filtering logic that sends PrevObject without updating its resourceVersion for DELETE events.

2. **Error Chain**: Trace the full path from a client-go informer watch through the API server to the watch cache event processing, and compare with the etcd direct watcher path.

3. **Affected Components**: What components are affected by this bug?

4. **Remediation**: How should the watch cache be fixed? Reference the existing correct implementations in the etcd watchers.

## Output

Write your findings to `/workspace/agent_output/INCIDENT_REPORT.md`.
