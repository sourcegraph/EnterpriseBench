# Incident: Service Mesh 503 Errors After Istio Upgrade

## Alert

After upgrading Istio from 1.19 to 1.20, intermittent 503 errors are observed for a subset of HTTP requests in the service mesh. The affected requests target VirtualServices that use header-based routing with regex matching.

## Symptoms

- 503 UC (Upstream Connection) errors in Envoy access logs
- Only affects routes using regex header matching in VirtualService, e.g. `headers: { x-canary: { regex: "^true$" } }`
- Stable routes with exact header matching are unaffected
- The issue appeared immediately after the Istio control plane upgrade
- Envoy sidecars show the routes in their config dump, but matching fails

## Environment

- `/workspace/istio/` — `istio/istio` 1.20.0
- `/workspace/envoy/` — `envoy/envoy` v1.28.0

Both projects are in play: Istio's control plane produces the Envoy config that the data plane then consumes. You will need to read both repos to find the seam where the regex header match stops working.

## What I Need

Investigate the two codebases and write an incident report grounded in the actual code — cite the specific files and functions you found, not general Envoy/regex theory.

1. **Root Cause**: Name the specific file and function on each side of the failure — the Istio-side code that turns a VirtualService's header match into an Envoy route config entry, and the Envoy-side code that evaluates that entry against a request. Explain what changed between the two Istio versions that breaks the regex match.

2. **Error Chain**: Trace from the VirtualService config through to the 503, with each hop tied to a code path you identified — at least four hops.

3. **Affected Components**: Which service mesh components and VirtualService patterns are affected?

4. **Remediation**: Propose a concrete, code-grounded fix and say where it belongs (which repo, and at which layer — mesh config vs. VirtualService spec).

## Output

Write your findings to `/workspace/istio/INCIDENT_REPORT.md`.
