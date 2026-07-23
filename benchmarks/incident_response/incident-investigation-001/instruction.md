# Incident: Controllers Missing Watch Events After CRD Registration

## Alert

Our Kubernetes cluster monitoring has detected an intermittent but critical issue: controllers that watch custom resource types are silently missing watch events. This means newly created or updated objects are not being processed by their controllers.

## Symptoms

- Controllers that establish watches shortly after a CRD is registered miss the first few events
- The issue is intermittent and depends on timing between list and watch operations
- No error messages are logged -- the events are silently dropped
- Restarting the affected controller temporarily resolves the issue
- The problem is most common during CRD/TPR initialization

## Reproduction Scenario

The following sequence consistently triggers the bug:

1. Register a new CRD type
2. Controller does a `List` request, receives `resourceVersion=10`
3. Controller starts a `Watch` from `resourceVersion=10`
4. Another client creates an object (now at `resourceVersion=11`)
5. The controller never receives the creation event

## Environment

- Kubernetes v1.7.3
- The codebase is at `/workspace/kubernetes/`
- Focus on the API server's storage layer, particularly the watch cache implementation

## What I Need

1. **Root Cause**: Identify the exact file and function where the bug occurs. What is the off-by-one error in the watch cache?

2. **Error Chain**: Trace how a watch request flows from the API server through the watch cache to the etcd storage backend. Show the full call chain.

3. **Affected Components**: List all components in the Kubernetes API server that are involved in this error path.

4. **Remediation**: What should the fix do? How should the watch cache handle this edge case?

## Output

Write your findings to `/workspace/agent_output/INCIDENT_REPORT.md` with clearly labeled sections for each of the four items above.
