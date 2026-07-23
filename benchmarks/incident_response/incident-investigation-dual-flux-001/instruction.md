# Incident: HelmRelease Stuck in Not-Ready State After Flux Upgrade

## Alert

After upgrading Flux from v2.1 to v2.2, several HelmRelease resources are stuck in a "not ready" state despite the underlying Helm releases being successfully deployed.

## Symptoms

- `flux get helmreleases` shows the affected releases as `Ready: False` while `helm list` shows them deployed successfully
- helm-controller logs show successful reconciliation, yet the HelmRelease status still reports not ready
- The issue affects only HelmReleases that were in-progress during the upgrade
- New HelmReleases created after the upgrade work correctly
- kustomize-controller and other consumers that gate on HelmRelease readiness are blocked because they read the same stale status

## Investigation So Far

The team has identified the general area:

1. Flux v2.2 ships helm-controller v0.37, which changed the HelmRelease status condition API (the v2beta2 condition types)
2. helm-controller's reconciler writes and re-reads status conditions in the new format after a release completes
3. The Flux CLI reads those same status conditions to render the Ready column operators see
4. HelmReleases that were in-progress during the upgrade retain old-format conditions
5. The new reconciler does not handle the transition from old to new condition format
6. The stale condition causes an infinite reconciliation loop

## Environment

Two repos are checked out in the workspace:

- `/workspace/flux2/` (Flux v2.2.0) — the Flux CLI: how operators observe and trigger HelmRelease reconciliation
- `/workspace/helm-controller/` (helm-controller v0.37.0) — the controller that reconciles HelmRelease resources and owns their status
- Focus on Go code in both repos

## What I Need

1. **Root Cause**: In helm-controller, find where the reconciler reads and writes HelmRelease status conditions and the v2beta2 type/API surface that defines them. In flux2, find where the CLI reads those status conditions to report readiness to the operator.

2. **Error Chain**: Trace the failure: an operator observes not-ready through the flux CLI -> helm-controller's reconciler cannot parse the old-format condition left on an in-progress HelmRelease -> treats the release as not ready -> triggers re-reconciliation -> the stale condition persists -> infinite loop.

3. **Affected Resources**: Which components and Kubernetes resources are affected — the flux CLI readout, the helm-controller reconciler, and the resources downstream of them?

4. **Remediation**: How should the status condition transition be handled?

## Output

Write your findings to `/workspace/agent_output/INCIDENT_REPORT.md`.
