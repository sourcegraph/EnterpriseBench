# Configuration Drift: ArgoCD Redis-HA Chart Values vs Upstream Defaults

## Context

We recently upgraded Helm to 3.17.1, and ArgoCD's codegen CI is now failing at the "check changes to generated code" step. Regenerating the bundled redis-ha chart (`manifests/ha/base/redis-ha/generate.sh`, which runs `helm dependency update ./chart` and re-renders it with `helm template`) now produces a different `chart/upstream.yaml` than the copy committed to the repo, so the diff check fails.

The ArgoCD HA deployment bundles the redis-ha Helm chart under `manifests/ha/base/redis-ha/chart/` in `/workspace/argo-cd/`. That chart's `values.yaml` overrides the defaults of the upstream redis-ha chart it depends on. The upstream chart source from dandydeveloper/charts is available at `/workspace/dandydeveloper-charts/`.

Nothing in our values file changed. The same overrides that rendered cleanly before now merge differently against the upstream defaults under 3.17.1.

## What I Need

1. **Override hierarchy trace**: Establish which upstream redis-ha chart version our bundled chart actually depends on, and how our overrides layer onto that chart's defaults.

2. **Drift points**: Compare our overrides against the upstream chart's defaults and identify every override that conflicts with the upstream default it lands on. For each one, give the full key path through the value hierarchy, exactly as it is nested in our values file.

3. **Expected vs actual**: For each drift point, give the concrete value the upstream chart defaults that key to — the real default as it appears in the upstream chart, not a description of it — and what our override sets it to instead.

4. **Helm version sensitivity**: Explain what Helm 3.17.1 does with these overrides that earlier versions did not, and why that changes the rendered output.

## Output

Write your findings to `/workspace/agent_output/DRIFT_REPORT.json` as a JSON object with a `drift_points` array. Each entry needs: `file`, `key`, `expected`, `actual`, and `override_chain`.
