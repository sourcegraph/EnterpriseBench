# Incident: HelmRelease Stuck in Not-Ready State After Flux Upgrade

## Alert

After upgrading Flux from v2.1 to v2.2, several HelmRelease resources are stuck in a "not ready" state despite the underlying Helm releases being successfully deployed.

## Symptoms

- HelmRelease resources show status `Ready: False` with reason "ArtifactFailed" or "ReconciliationFailed"
- `helm list` shows the releases as deployed successfully
- helm-controller logs show successful reconciliation but Flux reports the HelmRelease as not ready
- The issue affects only HelmReleases that were in-progress during the upgrade
- New HelmReleases created after the upgrade work correctly
- Flux kustomize-controller also cannot see the HelmRelease as ready

## Environment

- Flux v2.2.0 at `/workspace/flux2/`
- helm-controller v0.37.0 at `/workspace/helm-controller/`
- Focus on Go code in both repos

## What I Need

Investigate both codebases and write an incident report grounded in the actual code — cite the specific files and functions you found, not general reconciliation theory.

1. **Root Cause**: Find where helm-controller's reconciler evaluates HelmRelease readiness status conditions, where it writes those conditions after a release, and where the HelmRelease status condition API surface is defined. Cite each by path and explain why the mismatch between them produces the symptoms above.

2. **Error Chain**: Trace the failure from the helm-controller upgrade to the observed reconciliation behavior, tying each hop to a code path you identified. At least four hops.

3. **Affected Resources**: Which helm-controller/Flux components and Kubernetes resources are affected, and why?

4. **Remediation**: How should the status condition transition be handled, and where would the fix live?

## Output

Write your findings to `/workspace/flux2/INCIDENT_REPORT.md`.
