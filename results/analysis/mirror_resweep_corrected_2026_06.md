# Corrected sg-evals Mirror Re-Sweep — 2026-06 (bead EnterpriseBench-768k.1 / dfjw)

**Prereq for the 768k batch fanout** (Stephanie-authorized 91-pin mirror remediation).
Re-probes the rvvc sweep's "91 unregistered MISSING-404" using the **exact**
`create_sg_mirrors` naming scheme (`rev[:8]` for hex revs; full tag, `/`→`_` for tags),
and classifies against the **authoritative live list** of sg-evals repos (`gh repo list sg-evals`,
453 repos) rather than per-pin probes (which are bug-prone — see Methodology).

## Headline

| | rvvc (2026-06-03) | corrected (2026-06-04) |
|---|---|---|
| unique mirrors (active corpus) | 256 | 256 |
| registered (repo_versions.json) MISSING | 14 | **0** |
| unregistered MISSING | 91 | **88 truly-missing** (+ 5 false-positive-exists) |

**The 768k create batch resizes 91 → 88 truly-missing.** 5 unregistered pins that rvvc
flagged MISSING already exist, are PUBLIC, and are anonymously cloneable — **drop them from
the batch**. All 163 registered pins resolve to existing public mirrors.

## Corrected partition of the unregistered set (93 pins: 91 tag + 2 hex)

- **TRULY-MISSING: 88** (86 tag + 2 hex) — need creation via `scripts/infra/create_sg_mirrors.py --execute`. Impact: **51 active tasks** have ≥1 truly-missing mirror.
- **FALSE-POSITIVE-EXISTS: 5** (all tag, all PUBLIC + anon-cloneable):

| mirror | created_at | cause of rvvc false-MISSING |
|---|---|---|
| `sg-evals/tokio--tokio-1.0.0` | 2026-06-03T12:24:52Z | created AFTER rvvc sweep (interim) |
| `sg-evals/hyper--v0.14.0` | 2026-06-03T12:24:39Z | created AFTER rvvc sweep (interim) |
| `sg-evals/tonic--v0.6.0` | 2026-06-03T12:25:13Z | created AFTER rvvc sweep (interim) |
| `sg-evals/urllib3--2.0.0` | 2026-03-29T19:56:39Z | existed since March — genuine rvvc false-404 |
| `sg-evals/requests--v2.28.2` | 2026-03-29T19:54:35Z | existed since March — genuine rvvc false-404 |

## On worker-4's `rev[:8]` over-count hypothesis

**Confirmed as a real mechanism, but NOT the cause of the unregistered over-count.**
- Verified: **0** existing sg-evals repos use a full-40-char hex suffix; **212** use the 8-char (`rev[:8]`) suffix. So a sweep probing hex pins under their full 40-char rev would 404 every hex mirror — the mechanism is real.
- BUT the unregistered MISSING set is **91 tags + 2 hex**. Tag mirrors are named with the full tag (no truncation), so `rev[:8]` cannot have caused a false-404 on them. Both unregistered hex pins are genuinely missing even under `rev[:8]`.
- The actual 5-pin over-count is: **3 interim-created** (post-sweep, 2026-06-03 12:24Z) + **2 genuine rvvc false-404s** (existed since March). Not truncation.

## Methodology / caveats

- Pins enumerated from all `benchmarks/*/*/task.toml` (excl. `_archived`), replicating `extract_mirrors_from_task` exactly (strip `https://`/`.git`, `repo_name = upstream.split('/')[-1]`, hex→`rev[:8]`, tag→full with `/`→`_`).
- "Registered" = `(normalized_url, pinned_rev)` present in `configs/repo_versions.json`.
- Existence/visibility from `gh repo list sg-evals --limit 1000 --json name,visibility` (authoritative; 452 PUBLIC + 1 PRIVATE).
- Anonymous cloneability of all 5 false-positives verified via unauthenticated `git ls-remote` (all ✓). 4-pin spot-check of truly-missing confirmed 404 + non-cloneable.
- **Probe-bug note:** `gh api repos/<x> --jq '{p:.private}'` emits a non-empty object on 404 (jq runs over the error body, `.private`→null), so naive emptiness checks misclassify 404s as public. Matching against the live repo list avoids this class of bug.
- REPORT ONLY — no mirrors created, no visibility flips, no repins, no manifest/task edits.

## TRULY-MISSING (88) — need creation

| mirror | pin | tasks |
|---|---|---|
| `sg-evals/alembic--rel_1_12_0` | tag | api-contract-dual-sqlalchemy-alembic-001 |
| `sg-evals/alembic--rel_1_13_1` | tag | support-mapping-dual-sqlalchemy-alembic-metadata-001 |
| `sg-evals/alertmanager--v0.27.0` | tag | incident-investigation-dual-cortex-001 |
| `sg-evals/axum--axum-v0.7.0` | tag | refactor-dual-axum-tower-001 |
| `sg-evals/axum--axum-v0.7.5` | tag | support-mapping-dual-axum-tower-middleware-001 |
| `sg-evals/babel--v7.24.0` | tag | support-mapping-dual-babel-swc-decorators-001, support-mapping-dual-jest-babel-cache-001 |
| `sg-evals/cilium--v1.16.4` | tag | incident-investigation-dual-cilium-001 |
| `sg-evals/cockroach--v24.2.5` | tag | incident-investigation-dual-cockroach-001 |
| `sg-evals/cortex--v1.16.0` | tag | incident-inv-dual-cortex-thanos-001 |
| `sg-evals/cortex--v1.18.0` | tag | incident-investigation-dual-cortex-001 |
| `sg-evals/cryptography--41.0.0` | tag | dep-graph-dual-cryptography-paramiko-001 |
| `sg-evals/curl--curl-8_6_0` | tag | support-mapping-dual-openssl-curl-cipher-001 |
| `sg-evals/django--4.2.10` | tag | support-mapping-dual-django-wagtail-treepath-001 |
| `sg-evals/dskit--53283a0f` | hex | incident-investigation-dual-loki-001 |
| `sg-evals/dskit--736c44c8` | hex | incident-investigation-dual-tempo-001 |
| `sg-evals/ebpf--v0.16.0` | tag | incident-investigation-dual-cilium-001 |
| `sg-evals/envoy--v1.29.4` | tag | support-mapping-dual-envoy-istio-xds-001 |
| `sg-evals/fastapi--0.110.0` | tag | support-mapping-dual-fastapi-pydantic-validator-001 |
| `sg-evals/fastapi--0.99.1` | tag | api-contract-dual-pydantic-fastapi-001 |
| `sg-evals/flask--2.3.3` | tag | support-mapping-dual-flask-werkzeug-ctxvar-001 |
| `sg-evals/frameworks--v0.20.0` | tag | incident-investigation-dual-opa-001 |
| `sg-evals/grpc--v1.62.0` | tag | support-mapping-dual-grpc-protobuf-reflection-001 |
| `sg-evals/hibernate-orm--6.0.0.Final` | tag | config-drift-tri-javax-jakarta-spring-001 |
| `sg-evals/hibernate-orm--6.2.0` | tag | dep-graph-dual-spring-hibernate-001 |
| `sg-evals/hibernate-orm--6.4.4` | tag | support-mapping-dual-spring-hibernate-flush-001 |
| `sg-evals/httpx--0.27.0` | tag | support-mapping-dual-httpx-httpcore-001 |
| `sg-evals/hyper--v1.0.0` | tag | api-contract-dual-hyper-reqwest-001 |
| `sg-evals/hyper--v1.2.0` | tag | support-mapping-dual-tokio-hyper-graceful-001 |
| `sg-evals/istio--1.21.1` | tag | support-mapping-dual-envoy-istio-xds-001 |
| `sg-evals/jackson-databind--jackson-databind-2.15.0` | tag | api-contract-dual-jackson-spring-001 |
| `sg-evals/jackson-databind--jackson-databind-2.16.2` | tag | support-mapping-dual-jackson-spring-deserializer-001 |
| `sg-evals/jaeger--v1.51.0` | tag | config-drift-dual-jaeger-otel-001, error-prov-dual-otel-jaeger-001 |
| `sg-evals/jest--v29.7.0` | tag | support-mapping-dual-jest-babel-cache-001 |
| `sg-evals/junit5--r5.10.0` | tag | dep-graph-dual-junit-mockito-001 |
| `sg-evals/kafka--3.6.1` | tag | support-mapping-dual-spring-kafka-rebalance-002 |
| `sg-evals/kombu--v5.3.5` | tag | support-mapping-dual-celery-kombu-prefetch-001 |
| `sg-evals/logging-log4j2--rel_2.20.0` | tag | dep-graph-dual-log4j-spring-001 |
| `sg-evals/logging-log4j2--rel_2.23.1` | tag | support-mapping-dual-log4j-slf4j-async-001 |
| `sg-evals/loki--v3.3.0` | tag | incident-investigation-dual-loki-001 |
| `sg-evals/mockito--v5.5.0` | tag | dep-graph-dual-junit-mockito-001 |
| `sg-evals/nats-server--v2.10.22` | tag | incident-investigation-dual-nats-001 |
| `sg-evals/nats.go--v1.37.0` | tag | incident-investigation-dual-nats-001 |
| `sg-evals/nerdctl--v2.0.0` | tag | incident-investigation-dual-nerdctl-001 |
| `sg-evals/netty--netty-4.1.100.Final` | tag | error-prov-dual-netty-spring-001 |
| `sg-evals/next.js--v14.1.4` | tag | support-mapping-dual-next-turbopack-resolve-001 |
| `sg-evals/nomad--v1.9.3` | tag | incident-investigation-dual-nomad-001 |
| `sg-evals/opa--v0.69.0` | tag | incident-investigation-dual-opa-001 |
| `sg-evals/openssl--openssl-3.0.13` | tag | support-mapping-dual-openssl-curl-cipher-001 |
| `sg-evals/opentelemetry-collector--v0.89.0` | tag | config-drift-dual-jaeger-otel-001 |
| `sg-evals/opentelemetry-go--v1.21.0` | tag | error-prov-dual-otel-jaeger-001 |
| `sg-evals/paramiko--3.2.0` | tag | dep-graph-dual-cryptography-paramiko-001 |
| `sg-evals/pd--v8.5.0` | tag | incident-investigation-dual-tikv-001 |
| `sg-evals/pebble--v1.1.2` | tag | incident-investigation-dual-cockroach-001 |
| `sg-evals/pip--23.0` | tag | config-drift-dual-setuptools-pip-001 |
| `sg-evals/prisma--5.12.0` | tag | support-mapping-dual-prisma-zod-codegen-001 |
| `sg-evals/protobuf--v25.2` | tag | support-mapping-dual-grpc-protobuf-reflection-001 |
| `sg-evals/pydantic--v2.0.0` | tag | api-contract-dual-pydantic-fastapi-001 |
| `sg-evals/pydantic--v2.6.4` | tag | support-mapping-dual-fastapi-pydantic-validator-001 |
| `sg-evals/raft--v1.7.1` | tag | incident-investigation-dual-nomad-001, incident-investigation-dual-vault-001 |
| `sg-evals/relay--v16.2.0` | tag | support-mapping-dual-react-relay-suspense-001 |
| `sg-evals/reqwest--v0.11.22` | tag | api-contract-dual-hyper-reqwest-001 |
| `sg-evals/rust-csv--1.3.0` | tag | api-contract-dual-serde-csv-001 |
| `sg-evals/serde--v1.0.193` | tag | api-contract-dual-serde-csv-001 |
| `sg-evals/setuptools--v67.0.0` | tag | config-drift-dual-setuptools-pip-001 |
| `sg-evals/slf4j--v_2.0.13` | tag | support-mapping-dual-log4j-slf4j-async-001 |
| `sg-evals/spring-boot--v3.0.0` | tag | config-drift-tri-javax-jakarta-spring-001 |
| `sg-evals/spring-boot--v3.1.0` | tag | api-contract-dual-jackson-spring-001, dep-graph-dual-log4j-spring-001, dep-graph-dual-spring-hibernate-001 |
| `sg-evals/spring-boot--v3.2.4` | tag | support-mapping-dual-jackson-spring-deserializer-001 |
| `sg-evals/spring-framework--v6.1.4` | tag | support-mapping-dual-spring-hibernate-flush-001 |
| `sg-evals/spring-kafka--v3.1.4` | tag | support-mapping-dual-spring-kafka-rebalance-002 |
| `sg-evals/sqlalchemy--rel_2_0_0` | tag | api-contract-dual-sqlalchemy-alembic-001 |
| `sg-evals/sqlalchemy--rel_2_0_29` | tag | support-mapping-dual-sqlalchemy-alembic-metadata-001 |
| `sg-evals/swc--v1.4.6` | tag | support-mapping-dual-babel-swc-decorators-001 |
| `sg-evals/tempo--v2.6.0` | tag | incident-investigation-dual-tempo-001 |
| `sg-evals/thanos--v0.32.0` | tag | dep-graph-dual-prometheus-thanos-001, incident-inv-dual-cortex-thanos-001 |
| `sg-evals/tikv--v8.5.0` | tag | incident-investigation-dual-tikv-001 |
| `sg-evals/tokio--tokio-1.35.0` | tag | error-prov-dual-tokio-tower-001 |
| `sg-evals/tokio--tokio-1.36.0` | tag | support-mapping-dual-tokio-hyper-graceful-001, support-mapping-dual-tonic-tokio-streamcancel-001 |
| `sg-evals/tomcat--10.1.0` | tag | config-drift-tri-javax-jakarta-spring-001 |
| `sg-evals/tonic--v0.11.0` | tag | support-mapping-dual-tonic-tokio-streamcancel-001 |
| `sg-evals/tower--tower-0.4.13` | tag | error-prov-dual-tokio-tower-001, refactor-dual-axum-tower-001, support-mapping-dual-axum-tower-middleware-001 |
| `sg-evals/turbo--v1.13.2` | tag | support-mapping-dual-next-turbopack-resolve-001 |
| `sg-evals/vault--v1.18.1` | tag | incident-investigation-dual-vault-001 |
| `sg-evals/vite--v5.2.6` | tag | support-mapping-dual-vitest-vite-optimize-001 |
| `sg-evals/vitest--v1.4.0` | tag | support-mapping-dual-vitest-vite-optimize-001 |
| `sg-evals/wagtail--v5.2.2` | tag | support-mapping-dual-django-wagtail-treepath-001 |
| `sg-evals/werkzeug--2.3.7` | tag | support-mapping-dual-flask-werkzeug-ctxvar-001 |
| `sg-evals/zod--v3.23.0` | tag | support-mapping-dual-prisma-zod-codegen-001 |

## FALSE-POSITIVE-EXISTS (5) — drop from create batch

| mirror | pin | tasks |
|---|---|---|
| `sg-evals/hyper--v0.14.0` | tag | dep-graph-dual-tokio-hyper-001, dep-graph-tri-tokio-hyper-tonic-001 |
| `sg-evals/requests--v2.28.2` | tag | error-prov-dual-urllib3-requests-001 |
| `sg-evals/tokio--tokio-1.0.0` | tag | dep-graph-dual-tokio-hyper-001, dep-graph-tri-tokio-hyper-tonic-001 |
| `sg-evals/tonic--v0.6.0` | tag | dep-graph-tri-tokio-hyper-tonic-001 |
| `sg-evals/urllib3--2.0.0` | tag | dep-graph-tri-boto3-urllib3-requests-001, error-prov-dual-urllib3-requests-001 |
