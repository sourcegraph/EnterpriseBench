# Non-Go Multi-Repo Mass-Authoring (2026-04-29)

**Bead:** EnterpriseBench-2o0 (follow-up to EnterpriseBench-13t / 0rv.25)
**Goal:** Author enough non-Go multi-repo tasks to bring multi-repo Go% under
the PRD 40% cap without falsifying language tags on existing tasks.

## Baseline

- Pre-authoring: 120 multi-repo tasks; 55 Go-tagged → 45.83%.
- Cap: <40%. Math required denominator ≥138, i.e. ≥18 new non-Go tasks
  *if and only if* none of them happens to also carry a `go` tag.
- The audit doc `docs/audits/go_balance_audit_2026_04.md` confirmed no
  defensible tag removals among the 11 candidate tasks; mass-authoring
  was the only remaining lever.

## Authored tasks (21 total)

All under `benchmarks/customer_escalation/`, all
`task_type = support_code_mapping`, `difficulty_stratum = dual_repo`,
3 differentiated checkpoints (`error_source` / `error_chain` /
`trigger_conditions`) following the `support-mapping-dual-httpx-httpcore-001`
template.

### Python (4)

| Task ID | Repos | Bug surface |
|---|---|---|
| `support-mapping-dual-django-wagtail-treepath-001` | wagtail v5.2.2 ↔ django 4.2.10 | MP_Node path-index dropped via custom Page Meta override |
| `support-mapping-dual-flask-werkzeug-ctxvar-001` | flask 2.3.3 ↔ werkzeug 2.3.7 | Request-context loss across asyncio thread-pool boundary (contextvars not copied) |
| `support-mapping-dual-fastapi-pydantic-validator-001` | fastapi 0.110.0 ↔ pydantic v2.6.4 | Discriminated-union field drop via `model_validator(mode='before')` |
| `support-mapping-dual-celery-kombu-prefetch-001` | celery v5.3.6 ↔ kombu v5.3.5 | Channel re-create after broker drop loses `basic_qos` |

### TypeScript / JavaScript (4)

| Task ID | Repos | Bug surface |
|---|---|---|
| `support-mapping-dual-babel-swc-decorators-001` | babel v7.24.0 ↔ swc v1.4.6 | DI metadata absent under SWC because `decoratorMetadata` is opt-in |
| `support-mapping-dual-react-relay-suspense-001` | react v18.2.0 ↔ relay v16.2.0 | Relay store invalidation forces Suspense remount via reconciler offscreen drop |
| `support-mapping-dual-jest-babel-cache-001` | jest v29.7.0 ↔ babel v7.24.0 | `babel-jest` cache key omits plugin content hash → stale transform on rerun |
| `support-mapping-dual-next-turbopack-resolve-001` | next.js v14.1.4 ↔ turbo v1.13.2 | Turbopack does not honor `transpilePackages` for pnpm-symlinked workspaces |

### Java (4)

| Task ID | Repos | Bug surface |
|---|---|---|
| `support-mapping-dual-spring-hibernate-flush-001` | spring-framework v6.1.4 ↔ hibernate-orm 6.4.4 | Native query inside @Transactional misses dirty-entity flush (`DefaultAutoFlushEventListener` skipped for native SQL) |
| `support-mapping-dual-log4j-slf4j-async-001` | slf4j v_2.0.13 ↔ log4j2 rel/2.23.1 | `AsyncQueueFullPolicy.Discard` silently drops sub-ERROR events at burst |
| `support-mapping-dual-jackson-spring-deserializer-001` | spring-boot v3.2.4 ↔ jackson-databind 2.16.2 | `@JsonComponent` registers only on primary ObjectMapper; secondary beans bypass |
| `support-mapping-dual-spring-kafka-rebalance-002` | spring-kafka v3.1.4 ↔ kafka 3.6.1 | `ackMode=BATCH` defers commit; rebalance discards pending acks → double-process |

### Rust (3)

| Task ID | Repos | Bug surface |
|---|---|---|
| `support-mapping-dual-tokio-hyper-graceful-001` | hyper v1.2.0 ↔ tokio tokio-1.36.0 | Graceful shutdown waits on active long-poll connection; tokio cannot force-cancel parked tasks |
| `support-mapping-dual-axum-tower-middleware-001` | axum axum-v0.7.5 ↔ tower tower-0.4.13 | `Router::route_layer` does not wrap routes added via `Router::merge` after the call |
| `support-mapping-dual-tonic-tokio-streamcancel-001` | tonic v0.11.0 ↔ tokio tokio-1.36.0 | Stream cancel race: tokio mpsc receiver waits for sender refcount=0; tonic transport task holds clone |

### C++ / mixed (3)

| Task ID | Repos | Bug surface |
|---|---|---|
| `support-mapping-dual-envoy-istio-xds-001` | envoy v1.29.4 ↔ istio 1.21.1 | Pilot full-snapshot ADS push triggers Envoy listener drain even when content unchanged |
| `support-mapping-dual-grpc-protobuf-reflection-001` | grpc v1.62.0 ↔ protobuf v25.2 | Protobuf v4 per-ABI descriptor pool sharding leaves cross-`.so` descriptors invisible to grpc-cpp reflection |
| `support-mapping-dual-openssl-curl-cipher-001` | curl curl-8_6_0 ↔ openssl openssl-3.0.13 | OpenSSL 3.0 default security level 2 rejects RSA/SHA1 ciphers libcurl requests |

### Top-up (3)

`envoy-istio-xds-001` carries a `go` language tag (Istio Pilot is genuinely
written in Go and required for the cross-repo coupling). Per the audit
principle, falsifying that tag would be wrong, so 3 additional non-Go
tasks were added to push the denominator past 141:

| Task ID | Repos | Bug surface |
|---|---|---|
| `support-mapping-dual-sqlalchemy-alembic-metadata-001` | sqlalchemy rel_2_0_29 ↔ alembic rel_1_13_1 | Per-subclass DeclarativeBase MetaData; Alembic autogenerate sees only one |
| `support-mapping-dual-vitest-vite-optimize-001` | vitest v1.4.0 ↔ vite v5.2.6 | Vitest worker retains stale optimizeDeps chunk after Vite re-bundle |
| `support-mapping-dual-prisma-zod-codegen-001` | prisma 5.12.0 ↔ zod v3.23.0 | DMMF preserves schema.prisma source order → zod discriminated-union drift |

## Validation

- **CRNT** (`scripts/validation/crnt_validator.py`): all 21 PASS — every
  declared repo has ≥1 entry in `ground_truth.required_files`.
- **Schema preflight** (`scripts/validate_tasks_preflight.py`): 180/180
  ready. The 21 new tasks contribute no errors; warnings are limited to
  the standard `environment/` / `mirror_config` items consistent with
  other dual-repo tasks (httpx-httpcore, tokio-tower, etc.).
- **Task-mix validator** (`scripts/validation/task_mix_validator.py`):
  ALL TARGETS MET.
  - Strict multi-repo ≥45%: **71.7%** (129/180).
  - All 10 task types have ≥2 multi-repo variants.
  - No ecosystem >40%: max is `go` at **39.7%** (down from 45.8%).

## Math reconciliation

- Pre-auth: 120 multi-repo tasks, 55 Go-tagged → 45.83%.
- After 18 new tasks where one (envoy-istio) is also Go-tagged:
  138 total, 56 Go → 40.58% (still over).
- After +3 top-up tasks: 141 total, 56 Go → 39.72%.
- Validator reports 39.7% on a denominator of 141 (multi-repo strata
  total: dual_repo + tri_repo + multi_repo + monorepo_cross_package).

## Generator

Tasks were authored via `scripts/authoring/non_go_multirepo_tasks.py`
which consumes structured `TaskDef` records from
`scripts/authoring/non_go_multirepo_tasks_data.py`. The generator emits
the standard 5-file layout (`task.toml`, `instruction.md`,
`ground_truth.json`, `expected_solution.json`, `checks/*.sh`) with the
support_code_mapping check template (file_extraction plugin +
keyword-matched chain steps).

## Out of scope

- No tags were removed from existing Go tasks. The 11 audit-flagged
  candidates remain Go-tagged.
- No mirror configs (`configs/sg_mirrors/*.json`) were created — these
  are warnings, not blockers. Existing reference dual-repo tasks
  (httpx-httpcore, tokio-tower) ship without them.
- No environment/Dockerfile variants were generated; same precedent.

## Closure

- Author 8+ (preferably 18) non-Go multi-repo tasks: **21 authored**.
- Schema validation: **PASS** (180/180 ready).
- CRNT: **PASS** (21/21).
- task_mix_validator: **ALL TARGETS MET**.
- Per-task quality follows the spring-kafka / httpx-httpcore template
  with semantically distinct checkpoints.

Bead **EnterpriseBench-2o0** closes with `gc.outcome=pass`.
