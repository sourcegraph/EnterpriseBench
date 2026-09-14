# Phase 2 Investigation Task Selection Criteria

Status: design (bead `EnterpriseBench-0rv.18`)
Scope: investigation-style tasks (`task_type = "incident_investigation"`) authored after Phase 1.

## Why this exists

Phase 1 evaluation showed that LLM judges struggle to distinguish *code navigation* from
*memorized domain knowledge* when the repository under investigation is highly famous
(e.g., `moby/daemon.go`, `kubernetes/kubelet`). Symptoms:

- Strong models can recover the right files from training-data familiarity alone, without
  using MCP retrieval, even on the baseline (no-tool) condition.
- Identical recall on baseline and MCP arms collapses the signal we are trying to
  measure (cross-repo retrieval quality).

Phase 2 investigation tasks must be authored to reduce this confound. The repo and the
specific code paths exercised by the task should be unfamiliar enough that the model
cannot answer from training-data residue alone.

## Selection criteria (CRITERIA-A through CRITERIA-D)

### CRITERIA-A — Low-fame function names

Every required-files entry should reference a file/function whose **fully-qualified
identifier is unlikely to appear in popular tutorials, blog posts, or top-of-page
GitHub README hits**.

Heuristics:

- Avoid entry-point files like `daemon/daemon.go`, `cmd/<tool>/main.go`,
  `pkg/<tool>/server.go`, `pkg/kubelet/kubelet.go`.
- Prefer interior files such as `pkg/unpack/unpacker.go`,
  `internal/snapshotter/<>.go`, `pkg/sandbox/cleanup.go`,
  `daemon/containerd/image_commit.go`, `pkg/registry/distribution/manifest.go`.
- If the file is from a famous repo, the *function* exercised by the task must be
  obscure (e.g., `containerd`'s `applyDiff` instead of `containerd`'s `Run`).

A rough sniff test: type the function name into a search engine. If the first page is
the upstream repo and a handful of tutorials, it's too famous.

### CRITERIA-B — Internal structure not widely documented

Prefer subsystems where the cross-component contract is **not** the subject of a popular
talk, blog post, or vendor whitepaper:

- Good: containerd content store ↔ snapshotter handshake; libnetwork sandbox cleanup
  races; distribution/resolver internals; CNI plugin chaining edge cases; OPA's rego
  partial-eval engine; Vault's storage backend HA replication; Tempo's compaction
  scheduler; Loki's chunk index gateway interaction.
- Bad: kubelet pod lifecycle hooks; Docker container lifecycle; Envoy listener filter
  chain; Istio sidecar injection; Prometheus scrape loop. (These are widely covered.)

### CRITERIA-C — Recent refactors at the pinned revision

Prefer revisions where the relevant code has been **refactored within ~12 months prior
to the model's training cutoff** (or is post-cutoff entirely). Recent refactors push the
code structure outside training-data residue and make memorization-based answers stale.

How to find candidates:

- `git log --follow <file>` → look for >300-line refactor commits in the last year.
- GitHub PRs labeled `refactor`/`cleanup`/`internal` → cross-reference to file paths.
- Avoid pinning to ancient stable releases (e.g., a 2021 LTS) — those are heavily in
  training data.

### CRITERIA-D — Cross-repo internal API interactions

Prefer multi-repo tasks where the failure crosses an **internal API boundary** (one
project's interior calling another's interior) rather than a well-known lifecycle hook:

- Good: containerd `content.Provider` → snapshotter `Mounts()`; runc libcontainer
  `seccomp.PatchedFilter` → containerd `pkg/cri/server`'s spec mutator; OPA `rego`
  built-in registration → custom plugin's `topdown.Builtin` hook; Tempo block-builder
  ↔ ingester WAL-replay sequence.
- Bad: pod start lifecycle, container exit hook, HTTP middleware ordering, generic
  webhook patterns.

## Anti-criteria — repos to avoid for Phase 2

These repos either appear extensively in training data, are too narrowly-scoped to support
multi-repo investigation, or have already been used in Phase 1 with good signal already
captured. Avoid for net-new Phase 2 tasks:

- `kubernetes/kubernetes` (kubelet, kube-apiserver, kube-scheduler entry points)
- `moby/moby` (daemon entry, container lifecycle)
- `docker/cli`
- `prometheus/prometheus` (scrape loop, TSDB top-level)
- `grafana/grafana` (HTTP API top-level)
- `istio/istio` (sidecar injection)
- `envoyproxy/envoy` (listener/filter top-level)

These are NOT banned — but a Phase 2 task using them must explain in `mcp_benefit_rationale`
why the *specific code path* exercised passes CRITERIA-A and CRITERIA-B.

## Ecosystem concentration ceiling

**No single ecosystem (`repo_set_id`) may exceed 30% of multi-repo tasks** in EnterpriseBench
v1. Specifically:

- `kubernetes-ecosystem`, `gitops-ecosystem`, `container-runtime-ecosystem`, and any other
  K8s-derivative ecosystems together must remain < 30% of multi-repo tasks.
- Phase 2 task authoring must monitor `python scripts/validation/ecosystem_diversity_report.py`
  before merging new tasks.
- If a planned new task would push K8s share above 30%, swap to a non-K8s candidate
  ecosystem before merging.

As of 2026-04-28, the report shows K8s-derivative tasks at ~22% of multi-repo tasks. Phase 2
authoring must keep this trend.

## Candidate Phase 2 ecosystems (non-K8s)

The following ecosystems satisfy CRITERIA-A through CRITERIA-D and currently have
≤2 multi-repo tasks each. They are the priority pool for Phase 2:

| Ecosystem candidate                | Cross-repo pattern                                      | Sample obscure interior                                   |
|------------------------------------|----------------------------------------------------------|------------------------------------------------------------|
| `opa-rego-ecosystem`               | OPA core ↔ custom rego built-ins / partial eval          | `topdown/builtins.go`, `rego/topdown/eval.go`              |
| `vault-storage-ecosystem`          | Vault core ↔ raft storage backend HA replication         | `physical/raft/snapshot.go`, `physical/raft/fsm.go`        |
| `tempo-ingest-ecosystem`           | Tempo distributor ↔ ingester ↔ block builder             | `modules/ingester/instance.go`, `tempodb/encoding`          |
| `loki-chunk-ecosystem`             | Loki distributor ↔ ingester ↔ index-gateway              | `pkg/storage/stores/series_index_gateway.go`              |
| `cortex-ruler-ecosystem`           | Cortex ruler ↔ alertmanager federated rule eval          | `pkg/ruler/notifier.go`, `pkg/ruler/manager.go`            |
| `nats-jetstream-ecosystem`         | nats-server ↔ JetStream raft ↔ leaf-node bridging        | `server/jetstream_cluster.go`, `server/raft.go`            |
| `cilium-ebpf-ecosystem`            | cilium agent ↔ ebpf loader (cilium/ebpf)                 | `daemon/cmd/loadinfo.go`, `pkg/datapath/loader/`           |
| `temporal-worker-ecosystem`        | temporal server ↔ matching service ↔ history shard       | `service/matching/taskQueueManager.go`                     |
| `airflow-executor-ecosystem`       | airflow scheduler ↔ celery/k8s executor ↔ worker         | `airflow/jobs/scheduler_job_runner.py`                     |
| `tikv-pd-ecosystem`                | TiKV ↔ Placement Driver region scheduling               | `server/cluster/cluster.go` (PD), `src/raftstore/store.rs`  |
| `cockroachdb-storage-ecosystem`    | CRDB SQL ↔ KV ↔ storage engine (Pebble)                  | `pkg/kv/kvserver/replica_raft.go`, `pebble/compaction.go`   |
| `containerd-snapshotter-ecosystem` | containerd content store ↔ snapshotter ↔ unpacker        | `pkg/unpack/unpacker.go`, `snapshots/overlay/overlay.go`   |
| `garage-storage-ecosystem`         | garage daemon ↔ table replication ↔ block manager       | `src/table/replication/`, `src/block/`                     |
| `materialize-coord-ecosystem`      | Materialize coordinator ↔ compute ↔ storage controller   | `src/adapter/src/coord/timestamp_selection.rs`            |
| `wasmcloud-host-ecosystem`         | wasmcloud-host ↔ provider runtime ↔ NATS lattice         | `crates/host/src/wasmbus/handler.rs`                       |

Each candidate appears in at most 2 existing tasks (most appear in zero). All have been
spot-checked against `cncf.io/projects/` and `apache.org/projects/` for active development
and revisions post-2024.

## Task-construction requirements

Every Phase 2 investigation task must produce these artifacts (also tracked via the
`docs/TASK_AUTHORING_GUIDE.md` checklist):

1. `task.toml` — passes `lib/eb_verify/` schema validation; `task_type = "incident_investigation"`.
2. `instruction.md` — natural-language alert, does not name the answer files.
3. `expected_solution.json` — function-level criteria for each checkpoint.
4. `ground_truth.json` — provenance metadata, PR refs where applicable.
5. `checks/check_*.sh` — one per checkpoint, JSON-output verifier scripts.
6. CRNT validation passes:
   `python scripts/validation/crnt_validator.py benchmarks/<suite>/<task-id>/task.toml`.

The PR adding the task must include the CRNT validator's output (full table) in the
description so reviewers see structural soundness without re-running locally.

## Review gate

A Phase 2 investigation task should not merge if **any** of the following fails:

- CRNT validator returns non-zero exit code.
- A required-file path does not exist at the pinned revision (run
  `python scripts/verify_gt_paths.py` if you have it; otherwise grep on the cloned tree).
- A function/symbol named in `expected_solution.json` is absent at the pinned revision.
- Adding the task pushes K8s-derivative ecosystem share ≥ 30% of multi-repo tasks.

## Open questions / follow-ups

- Should we publish a "famousness score" derived from GitHub stars / Google search hit
  count / known-tutorial corpus to make CRITERIA-A automatable? (Punt to a later bead.)
- Do we need per-task LLM-judge sensitivity tests to prove memorization confound is
  controlled? Probably yes, but out of scope for this bead.
