# EnterpriseBench — Un-run Tasks Backlog

102 of 180 active tasks have **no valid scored run** (results.json with success=true + scored checkpoints).
Prioritized strict-multi-repo first. Run all 3 modes (baseline / mcp_only / hybrid) per task.

- Strict multi-repo (>=2 repos) un-run: **100**
- Run in parallel across accounts 1-5 (mode-suffixed image tags). Single-task `run_task.py` -> caller parallelizes.

## tri_repo (15)

- [ ] `api-contract-tri-envoy-001` — 3 repo — dependency_management — `benchmarks/dependency_management/api-contract-tri-envoy-001`
- [ ] `api-contract-tri-protobuf-001` — 3 repo — dependency_management — `benchmarks/dependency_management/api-contract-tri-protobuf-001`
- [ ] `dep-graph-tri-boto3-urllib3-requests-001` — 3 repo — dependency_management — `benchmarks/dependency_management/dep-graph-tri-boto3-urllib3-requests-001`
- [ ] `dep-graph-tri-prometheus-alertmanager-grafana-001` — 3 repo — dependency_management — `benchmarks/dependency_management/dep-graph-tri-prometheus-alertmanager-grafana-001`
- [ ] `dep-graph-tri-tokio-hyper-tonic-001` — 3 repo — dependency_management — `benchmarks/dependency_management/dep-graph-tri-tokio-hyper-tonic-001`
- [ ] `dep-traversal-tri-boto3-001` — 3 repo — dependency_management — `benchmarks/dependency_management/dep-traversal-tri-boto3-001`
- [ ] `dep-traversal-tri-jackson-001` — 3 repo — dependency_management — `benchmarks/dependency_management/dep-traversal-tri-jackson-001`
- [ ] `dep-traversal-tri-openssl-001` — 3 repo — dependency_management — `benchmarks/dependency_management/dep-traversal-tri-openssl-001`
- [ ] `schema-evolution-tri-supabase-001` — 3 repo — feature_delivery — `benchmarks/feature_delivery/schema-evolution-tri-supabase-001`
- [ ] `incident-investigation-tri-containerd-001` — 3 repo — incident_response — `benchmarks/incident_response/incident-investigation-tri-containerd-001`
- [ ] `config-drift-tri-javax-jakarta-spring-001` — 3 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-tri-javax-jakarta-spring-001`
- [ ] `config-drift-tri-kustomize-001` — 3 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-tri-kustomize-001`
- [ ] `refactor-orchestration-tri-babel-001` — 3 repo — technical_debt — `benchmarks/technical_debt/refactor-orchestration-tri-babel-001`
- [ ] `refactor-orchestration-tri-spring-001` — 3 repo — technical_debt — `benchmarks/technical_debt/refactor-orchestration-tri-spring-001`
- [ ] `refactor-orchestration-tri-tokio-001` — 3 repo — technical_debt — `benchmarks/technical_debt/refactor-orchestration-tri-tokio-001`

## multi_repo (1)

- [ ] `incident-investigation-quad-containerd-001` — 4 repo — incident_response — `benchmarks/incident_response/incident-investigation-quad-containerd-001`

## dual_repo (84)

- [ ] `err-provenance-dual-celery-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/err-provenance-dual-celery-001`
- [ ] `err-provenance-dual-docker-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/err-provenance-dual-docker-001`
- [ ] `err-provenance-dual-grafana-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/err-provenance-dual-grafana-001`
- [ ] `err-provenance-dual-requests-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/err-provenance-dual-requests-001`
- [ ] `err-provenance-dual-terraform-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/err-provenance-dual-terraform-001`
- [ ] `error-prov-dual-netty-spring-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/error-prov-dual-netty-spring-001`
- [ ] `error-prov-dual-otel-jaeger-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/error-prov-dual-otel-jaeger-001`
- [ ] `error-prov-dual-tokio-tower-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/error-prov-dual-tokio-tower-001`
- [ ] `error-prov-dual-urllib3-requests-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/error-prov-dual-urllib3-requests-001`
- [ ] `support-mapping-dual-ansible-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-ansible-001`
- [ ] `support-mapping-dual-axum-tower-middleware-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-axum-tower-middleware-001`
- [ ] `support-mapping-dual-babel-swc-decorators-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-babel-swc-decorators-001`
- [ ] `support-mapping-dual-celery-kombu-prefetch-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-celery-kombu-prefetch-001`
- [ ] `support-mapping-dual-django-wagtail-treepath-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-django-wagtail-treepath-001`
- [ ] `support-mapping-dual-envoy-istio-xds-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-envoy-istio-xds-001`
- [ ] `support-mapping-dual-fastapi-pydantic-validator-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-fastapi-pydantic-validator-001`
- [ ] `support-mapping-dual-flask-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-flask-001`
- [ ] `support-mapping-dual-flask-werkzeug-ctxvar-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-flask-werkzeug-ctxvar-001`
- [ ] `support-mapping-dual-grafana-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-grafana-001`
- [ ] `support-mapping-dual-grpc-protobuf-reflection-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-grpc-protobuf-reflection-001`
- [ ] `support-mapping-dual-httpx-httpcore-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-httpx-httpcore-001`
- [ ] `support-mapping-dual-jackson-spring-deserializer-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-jackson-spring-deserializer-001`
- [ ] `support-mapping-dual-jest-babel-cache-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-jest-babel-cache-001`
- [ ] `support-mapping-dual-log4j-slf4j-async-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-log4j-slf4j-async-001`
- [ ] `support-mapping-dual-next-turbopack-resolve-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-next-turbopack-resolve-001`
- [ ] `support-mapping-dual-nextjs-webpack-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-nextjs-webpack-001`
- [ ] `support-mapping-dual-openssl-curl-cipher-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-openssl-curl-cipher-001`
- [ ] `support-mapping-dual-prisma-zod-codegen-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-prisma-zod-codegen-001`
- [ ] `support-mapping-dual-react-relay-suspense-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-react-relay-suspense-001`
- [ ] `support-mapping-dual-spring-hibernate-flush-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-spring-hibernate-flush-001`
- [ ] `support-mapping-dual-spring-kafka-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-spring-kafka-001`
- [ ] `support-mapping-dual-spring-kafka-rebalance-002` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-spring-kafka-rebalance-002`
- [ ] `support-mapping-dual-sqlalchemy-alembic-metadata-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-sqlalchemy-alembic-metadata-001`
- [ ] `support-mapping-dual-tokio-hyper-graceful-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-tokio-hyper-graceful-001`
- [ ] `support-mapping-dual-tonic-tokio-streamcancel-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-tonic-tokio-streamcancel-001`
- [ ] `support-mapping-dual-vitest-vite-optimize-001` — 2 repo — customer_escalation — `benchmarks/customer_escalation/support-mapping-dual-vitest-vite-optimize-001`
- [ ] `api-contract-dual-envoy-istio-001` — 2 repo — dependency_management — `benchmarks/dependency_management/api-contract-dual-envoy-istio-001`
- [ ] `api-contract-dual-fastapi-001` — 2 repo — dependency_management — `benchmarks/dependency_management/api-contract-dual-fastapi-001`
- [ ] `api-contract-dual-hyper-reqwest-001` — 2 repo — dependency_management — `benchmarks/dependency_management/api-contract-dual-hyper-reqwest-001`
- [ ] `api-contract-dual-jackson-spring-001` — 2 repo — dependency_management — `benchmarks/dependency_management/api-contract-dual-jackson-spring-001`
- [ ] `api-contract-dual-pydantic-fastapi-001` — 2 repo — dependency_management — `benchmarks/dependency_management/api-contract-dual-pydantic-fastapi-001`
- [ ] `api-contract-dual-serde-csv-001` — 2 repo — dependency_management — `benchmarks/dependency_management/api-contract-dual-serde-csv-001`
- [ ] `api-contract-dual-sqlalchemy-alembic-001` — 2 repo — dependency_management — `benchmarks/dependency_management/api-contract-dual-sqlalchemy-alembic-001`
- [ ] `dep-graph-dual-cryptography-paramiko-001` — 2 repo — dependency_management — `benchmarks/dependency_management/dep-graph-dual-cryptography-paramiko-001`
- [ ] `dep-graph-dual-junit-mockito-001` — 2 repo — dependency_management — `benchmarks/dependency_management/dep-graph-dual-junit-mockito-001`
- [ ] `dep-graph-dual-log4j-spring-001` — 2 repo — dependency_management — `benchmarks/dependency_management/dep-graph-dual-log4j-spring-001`
- [ ] `dep-graph-dual-nextjs-webpack-001` — 2 repo — dependency_management — `benchmarks/dependency_management/dep-graph-dual-nextjs-webpack-001`
- [ ] `dep-graph-dual-prometheus-thanos-001` — 2 repo — dependency_management — `benchmarks/dependency_management/dep-graph-dual-prometheus-thanos-001`
- [ ] `dep-graph-dual-spring-hibernate-001` — 2 repo — dependency_management — `benchmarks/dependency_management/dep-graph-dual-spring-hibernate-001`
- [ ] `dep-graph-dual-tokio-hyper-001` — 2 repo — dependency_management — `benchmarks/dependency_management/dep-graph-dual-tokio-hyper-001`
- [ ] `schema-evolution-dual-django-001` — 2 repo — feature_delivery — `benchmarks/feature_delivery/schema-evolution-dual-django-001`
- [ ] `schema-evolution-dual-sentry-001` — 2 repo — feature_delivery — `benchmarks/feature_delivery/schema-evolution-dual-sentry-001`
- [ ] `schema-evolution-dual-spring-flyway-001` — 2 repo — feature_delivery — `benchmarks/feature_delivery/schema-evolution-dual-spring-flyway-001`
- [ ] `incident-inv-dual-cortex-thanos-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-inv-dual-cortex-thanos-001`
- [ ] `incident-investigation-dual-cilium-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-cilium-001`
- [ ] `incident-investigation-dual-cockroach-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-cockroach-001`
- [ ] `incident-investigation-dual-cortex-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-cortex-001`
- [ ] `incident-investigation-dual-django-celery-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-django-celery-001`
- [ ] `incident-investigation-dual-flux-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-flux-001`
- [ ] `incident-investigation-dual-istio-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-istio-001`
- [ ] `incident-investigation-dual-kafka-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-kafka-001`
- [ ] `incident-investigation-dual-loki-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-loki-001`
- [ ] `incident-investigation-dual-nats-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-nats-001`
- [ ] `incident-investigation-dual-nerdctl-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-nerdctl-001`
- [ ] `incident-investigation-dual-nomad-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-nomad-001`
- [ ] `incident-investigation-dual-opa-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-opa-001`
- [ ] `incident-investigation-dual-prometheus-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-prometheus-001`
- [ ] `incident-investigation-dual-tempo-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-tempo-001`
- [ ] `incident-investigation-dual-tikv-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-tikv-001`
- [ ] `incident-investigation-dual-vault-001` — 2 repo — incident_response — `benchmarks/incident_response/incident-investigation-dual-vault-001`
- [ ] `config-drift-dual-argocd-001` — 2 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-dual-argocd-001`
- [ ] `config-drift-dual-jaeger-otel-001` — 2 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-dual-jaeger-otel-001`
- [ ] `config-drift-dual-prometheus-001` — 2 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-dual-prometheus-001`
- [ ] `config-drift-dual-setuptools-pip-001` — 2 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-dual-setuptools-pip-001`
- [ ] `config-drift-dual-terraform-001` — 2 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-dual-terraform-001`
- [ ] `config-drift-dual-tokio-hyper-001` — 2 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-dual-tokio-hyper-001`
- [ ] `config-drift-dual-webpack-babel-001` — 2 repo — platform_engineering — `benchmarks/platform_engineering/config-drift-dual-webpack-babel-001`
- [ ] `dead-code-dual-angular-001` — 2 repo — technical_debt — `benchmarks/technical_debt/dead-code-dual-angular-001`
- [ ] `dead-code-dual-django-wagtail-001` — 2 repo — technical_debt — `benchmarks/technical_debt/dead-code-dual-django-wagtail-001`
- [ ] `dead-code-dual-k8s-001` — 2 repo — technical_debt — `benchmarks/technical_debt/dead-code-dual-k8s-001`
- [ ] `dead-code-dual-react-001` — 2 repo — technical_debt — `benchmarks/technical_debt/dead-code-dual-react-001`
- [ ] `dead-code-dual-spring-hibernate-001` — 2 repo — technical_debt — `benchmarks/technical_debt/dead-code-dual-spring-hibernate-001`
- [ ] `refactor-dual-axum-tower-001` — 2 repo — technical_debt — `benchmarks/technical_debt/refactor-dual-axum-tower-001`
- [ ] `refactor-dual-flask-werkzeug-001` — 2 repo — technical_debt — `benchmarks/technical_debt/refactor-dual-flask-werkzeug-001`

## calibration (2)

- [ ] `chain-err-flask-import-001` — 1 repo — customer_escalation — `benchmarks/customer_escalation/chain-err-flask-import-001`
- [ ] `event-replay-click-ci-001` — 1 repo — incident_response — `benchmarks/incident_response/event-replay-click-ci-001`

