# Configuration Drift: Consul Port Mismatch in Headless Service

## Context

We recently upgraded the bitnami/consul Helm chart from 11.0.x to 11.1.x, and Helm upgrades are now failing. The 11.1.0 release added serfWAN port support alongside the existing serfLAN ports, but something went wrong in how the ports are wired through the templates.

The upgrade procedure tries to set new values for port `serflan-udp` in service `consul-headless-service`, and it fails because there is a mismatch between the values defined in `values.yaml` and how they are consumed in the service template.

## What I Need

1. **Drift points**: Examine the configuration hierarchy starting from `bitnami/consul/values.yaml` through `templates/statefulset.yaml` and `templates/consul-headless-service.yaml`. Identify every place where port values (`serfLAN`, `serfWAN`) are referenced inconsistently.

2. **Expected vs actual values**: For each drift point, tell me what value/expression the template should be using and what it currently uses.

3. **Override chain**: Trace how each port value flows from `values.yaml` through the template hierarchy, showing where the chain breaks.

## Output

Write your findings to `/workspace/agent_output/DRIFT_REPORT.json` as a JSON object with a `drift_points` array. Each drift point should have: `file`, `key`, `expected`, `actual`, and `override_chain`.
