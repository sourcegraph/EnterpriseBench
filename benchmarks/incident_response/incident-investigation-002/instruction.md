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
5. Watching etcd directly (bypassing the watch cache) does not reproduce this symptom, so whatever is wrong is isolated to the watch cache's own event-conversion path

## Environment

- Kubernetes v1.9.1
- The codebase is at `/workspace/kubernetes/`
- Focus on the API server's storage layer

## What I Need

1. **Root Cause**: Find the exact code where the watch cache converts a filtered-out modification into a DELETE event, and determine why the resourceVersion it attaches to that event is wrong.

2. **Error Chain**: Trace the full path from a client-go informer watch through the API server to the watch cache event processing, and compare with how a direct storage watcher (bypassing the cache) handles the same object transition.

3. **Affected Components**: What components are affected by this bug?

4. **Remediation**: How should the watch cache be fixed? Look for how the codebase already solves this same problem correctly elsewhere, and use that as your reference implementation.

## Output

Write your findings to `/workspace/kubernetes/INCIDENT_REPORT.md`.
