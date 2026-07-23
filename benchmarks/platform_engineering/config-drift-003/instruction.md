# Configuration Drift: Redis Password Generation Mismatch

## Context

We deployed the bitnami/redis Helm chart with `serviceBindings.enabled=true` and without setting an explicit password (expecting the chart to auto-generate one). Applications binding through the ServiceBinding then fail to authenticate against Redis.

Running `helm template` with `--set serviceBindings.enabled=true` and no password produces output where rendered resources disagree about the password.

## What I Need

1. **Password flow trace**: Follow how the password is resolved from `values.yaml` through the `redis.password` helper in `_helpers.tpl` to every template that consumes it. List every file that calls `{{ include "redis.password" ... }}` or equivalent.

2. **Drift points**: For each template that consumes the password, identify whether it gets the same or a different value. If different, document the drift.

3. **Root cause**: Explain the Helm mechanism that causes different password values to be generated. This is not a simple typo — it is a fundamental issue with how Helm evaluates template includes.

4. **Override chain**: For each drift point, trace the full resolution path: values.yaml -> helper -> consuming template.

## Output

Write your findings to `/workspace/agent_output/DRIFT_REPORT.json` as a JSON object with a `drift_points` array and a `root_cause` string. Each drift point needs: `file`, `key`, `expected`, `actual`, and `override_chain`.
