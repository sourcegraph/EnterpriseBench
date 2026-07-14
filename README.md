> ⚠️ **WORK IN PROGRESS — RESULTS NOT VALIDATED.** This repository is under active revision. The headline numbers, rescore artifacts, and FINDINGS in `results/` (including the MCP-vs-baseline comparison) are preliminary and NOT yet validated. The retrieval-quality axis is currently quarantined pending methodology fixes, and the arm design is being reworked (MCP vs baseline vs CLI on a curated dataset with build-time image guards). Do not cite these numbers as validated results.

# EnterpriseBench

A benchmark for evaluating how well coding agents understand and navigate code across large, distributed enterprise codebases.

Existing benchmarks (SWE-bench, ITBench, DevOps-Gym) test agents in isolated, single-repo settings. EnterpriseBench tests what enterprise developers actually do: trace dependencies across repositories, investigate incidents that span services, and produce diverse artifacts — not just code patches.

## Key Properties

- **112 tasks** across 10 task types, organized into 7 enterprise workflow suites
- **Real OSS codebases only** — no synthetic or toy repositories
- **Multi-repo by default** — 51% of tasks span 2-5 repos connected by real dependency chains
- **Diverse artifacts** — code patches, incident reports, runbooks, configs, security assessments, call graphs
- **Checkpoint-based scoring** — 2-5 graduated checkpoints per task for partial credit
- **Tool-access as a controlled variable** — baseline, MCP-only, and hybrid modes
- **Multi-session support** — single-shot, chained, event-replay, and resume session types

## Task Suites

| Suite                 | Tasks | Example Work                                                      |
| --------------------- | ----: | ----------------------------------------------------------------- |
| Dependency Management |    27 | Trace breaking changes across `grpc-go` -> `etcd` -> `kubernetes` |
| Customer Escalation   |    24 | Map error messages to root causes across service boundaries       |
| Technical Debt        |    19 | Orchestrate refactors and dead code removal across repos          |
| Feature Delivery      |    18 | Assess schema evolution impact across monorepo packages           |
| Incident Response     |    12 | Investigate alerts spanning multiple interconnected services      |
| Platform Engineering  |    10 | Detect configuration drift between IaC definitions and live state |
| Security Operations   |     2 | Assess vulnerabilities across dependency trees                    |

## Task Types

| Type                     | Description                                               |
| ------------------------ | --------------------------------------------------------- |
| `api_contract`           | Trace breaking API changes across consumer repos          |
| `config_drift`           | Detect divergence between config sources and deployments  |
| `db_schema_evolution`    | Assess schema change impact across dependent services     |
| `dead_code_necropsy`     | Identify unreachable code and feature flag remnants       |
| `dependency_graph`       | Traverse and reason about dependency chains               |
| `error_provenance`       | Trace error messages to their originating code paths      |
| `incident_investigation` | Root-cause analysis across multi-service incidents        |
| `monorepo_boundary`      | Navigate cross-package dependencies in monorepos          |
| `refactor_orchestration` | Plan and execute multi-repo refactoring                   |
| `support_code_mapping`   | Map customer-facing behavior to underlying implementation |

## Task Mix

| Stratum                | Share | Tasks |
| ---------------------- | ----: | ----: |
| Calibration            | 12.5% |    14 |
| Large single-repo      | 25.9% |    29 |
| Dual-repo              | 25.0% |    28 |
| Tri-repo               | 14.3% |    16 |
| Multi-repo (4-5)       | 11.6% |    13 |
| Monorepo cross-package | 10.7% |    12 |

## Repository Structure

```
EnterpriseBench/
├── benchmarks/              # 112 active task definitions by suite
│   ├── dependency_management/
│   ├── customer_escalation/
│   ├── feature_delivery/
│   ├── technical_debt/
│   ├── incident_response/
│   ├── platform_engineering/
│   ├── security_operations/
│   └── _archived/           # 28 retired tasks (preserved for reference)
├── lib/
│   └── eb_verify/           # Centralized verification library
│       └── plugins/         # 9 artifact validators
├── schemas/
│   └── task.schema.json     # Task definition schema
├── scripts/
│   ├── mining/              # Task sourcing from OSS history
│   ├── sandbox/             # Multi-repo sandbox management
│   ├── orchestration/       # Session chaining, event replay
│   └── validation/          # Task mix and cross-repo validators
├── configs/                 # Run configurations and repo version pins
├── results/                 # Run results and sample outputs
├── tests/                   # 779+ tests across 19 modules
└── docs/                    # Architecture, task authoring guide, design docs
```

## Task Definition Format

Each task is defined in TOML with structured metadata:

```toml
difficulty_stratum = "dual_repo"
verification_modes = ["deterministic"]

[task]
id = "api-contract-grpc-metadata-001"
suite = "dependency_management"
task_type = "api_contract"
difficulty = "hard"
session_type = "single"
description = "Trace impact of gRPC-Go metadata context separation on etcd consumers"
prompt = """
The gRPC-Go team has merged a change that separates incoming and outgoing
metadata in the context. Find all files in etcd that use the old metadata
APIs and will break...
"""

[[repos]]
url = "https://github.com/grpc/grpc-go"
rev = "v1.60.0"
role = "dependency"

[[repos]]
url = "https://github.com/etcd-io/etcd"
rev = "v3.5.10"
role = "primary"

[[checkpoints]]
id = "cp1"
description = "Identify all breaking API changes"
points = 25
verifier = "answer"
```

See [`schemas/task.schema.json`](schemas/task.schema.json) for the full schema and [`docs/TASK_AUTHORING_GUIDE.md`](docs/TASK_AUTHORING_GUIDE.md) for authoring instructions.

## Verification

EnterpriseBench uses a single centralized verification library (`eb_verify`) with a plugin architecture. Nine artifact-type-aware validators handle the diverse outputs agents produce:

| Plugin                | Validates                                    |
| --------------------- | -------------------------------------------- |
| `answer`              | Structured JSON answers with expected fields |
| `code_patch`          | Git diffs against ground-truth patches       |
| `config_validator`    | Configuration file correctness               |
| `incident_report`     | Incident analysis structure and content      |
| `runbook`             | Operational runbook completeness             |
| `security_assessment` | Security finding accuracy                    |
| `reproduction_script` | Bug reproduction script execution            |
| `topological_order`   | Dependency ordering correctness              |
| `call_graph`          | Function call graph accuracy                 |

Ground truth uses a layered approach: deterministic checks, LLM-based curation, and solve-verification.

## Multi-Repo Design

Tasks use real open-source dependency chains rather than artificially combined repos:

- **Go:** `grpc-go` -> `etcd` -> `kubernetes`
- **Python:** `requests` -> `boto3` -> `awscli`
- **Java:** `protobuf-java` -> `grpc-java` -> `envoy-control-plane`
- **TypeScript:** `typescript` -> `eslint` -> `next.js`
- **Cross-language:** `protobuf` schema -> Go server -> Python client -> TypeScript frontend

Four atomic multi-repo patterns: **propagate**, **investigate**, **enforce**, **orchestrate**.

## Running Tasks

Tasks are executed in Docker sandboxes with repos cloned into `/workspace/{repo-name}/`. Each task can run in three tool-access modes:

| Mode       | Description                                               |
| ---------- | --------------------------------------------------------- |
| `baseline` | Agent uses only local tools (grep, find, etc.)            |
| `mcp_only` | Agent uses Sourcegraph MCP for code search and navigation |
| `hybrid`   | Agent has access to both local tools and MCP              |

```bash
# Run a single task
python scripts/orchestration/run_task.py --task api-contract-grpc-metadata-001 --mode baseline

# Validate task mix against PRD targets
python scripts/validation/task_mix_validator.py
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — system design and verification flow
- [Task Type PRD](docs/TASK_TYPE_PRD.md) — detailed definitions for all 10 task types
- [Task Authoring Guide](docs/TASK_AUTHORING_GUIDE.md) — how to add new tasks
- [Convergence Report](docs/CONVERGENCE_REPORT.md) — architecture decisions from structured debate

## License

Apache 2.0 — see [LICENSE](LICENSE).
