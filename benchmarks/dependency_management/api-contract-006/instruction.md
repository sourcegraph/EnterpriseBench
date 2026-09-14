# Impact Analysis: Envoy xDS v2 API Deprecation

## Context

The Envoy project is removing xDS v2 API support in an upcoming release train. Our stack uses istio as the control plane, which communicates with Envoy sidecars via the go-control-plane library. We don't yet know how severe this is for us — whether it's a soft, compile-time nuisance or something that can take a running sidecar down — so we need this nailed down before we schedule the migration.

We need to understand the full migration path: envoy → go-control-plane → istio.

## What I Need

1. **Envoy changes**: What exactly is being deprecated? Which xDS v2 proto types and API versions are affected? How does Envoy actually behave once a v2 request hits the deprecated path, and where in the Envoy codebase is that behavior enforced?

2. **go-control-plane impact**: The library maintains v2 and v3 package trees (e.g., `pkg/cache/v2/` vs `pkg/cache/`). Which v2 packages need to be removed and what's the v3 equivalent?

3. **istio impact**: Find all files in istio's pilot component that reference v2 xDS types — Cluster, Endpoint, Listener, Route discovery services. These all need to migrate to v3 equivalents.

4. **Dependency chain**: Map the three-repo propagation chain. How do envoy's proto definitions flow through go-control-plane's Go bindings into istio's pilot implementation?

## Output

Write your findings to `/workspace/analysis/IMPACT_REPORT.md` with per-repo file lists, the dependency chain diagram, and migration priority (which files to migrate first).
