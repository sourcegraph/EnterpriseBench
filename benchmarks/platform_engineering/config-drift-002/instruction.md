# Configuration Drift: Spring Cloud Dataflow External RabbitMQ Inconsistencies

## Context

We're deploying Spring Cloud Dataflow via the bitnami Helm chart and want to use our own managed RabbitMQ instance instead of the bundled subchart. However, the external RabbitMQ configuration is broken in several ways:

- We can't use an existing Kubernetes secret for the RabbitMQ password — the chart doesn't support specifying which key in the secret holds the password.
- When we point the chart at an external RabbitMQ and set the password inline rather than via an existing secret, it still refuses to render until we also supply `rabbitmq.auth.password` and `rabbitmq.auth.erlangCookie` — bundled-subchart values that mean nothing for an external broker.
- The configuration path for external RabbitMQ doesn't follow the same pattern as external databases, which is confusing and inconsistent.

## What I Need

1. **Config hierarchy trace**: Follow the external RabbitMQ config from `values.yaml` through `_helpers.tpl`, `externalrabbitmq-secrets.yaml`, and `skipper/configmap.yaml`. Document how values flow (or fail to flow) through each layer.

2. **Drift points**: Identify every place where:
   - A values.yaml parameter is missing but needed by a template
   - A template references a value that doesn't exist in values.yaml
   - The external RabbitMQ path is inconsistent with the external database path
   - Required fields are unnecessarily mandatory for the external config case

3. **Expected vs actual**: For each drift point, what should the configuration be vs what it currently is?

4. **Override chain**: Show the full path each value takes from values.yaml through the template hierarchy.

## Output

Write your findings to `/workspace/agent_output/DRIFT_REPORT.json` as a JSON object with a `drift_points` array. Each entry needs: `file`, `key`, `expected`, `actual`, and `override_chain`.
