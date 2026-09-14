# Go-Balance Tag-Removal Audit (2026-04-29)

**Bead:** EnterpriseBench-13t (parent: EnterpriseBench-9yt; predecessor: EnterpriseBench-azt / 0rv.25 follow-up)
**Goal:** Determine whether any of 11 newly landed Go-tagged dual-repo
incident-investigation tasks have *incidentally* Go ecosystem tags that can
be defensibly removed to bring multi-repo Go% under the PRD 40% cap.
**Cheap-path-first:** if this audit closes the gap, mass-authoring is avoided.

## Method

For each candidate task, the audit asks: **is the task Go-tagged because
the *invariant being tested* is Go-specific (real coupling), or because the
demo *repos* happen to be in Go (incidental)?** A removal is defensible
only when the agent can solve the task without reasoning about Go-specific
semantics — i.e., the task is purely conceptual cross-repo navigation that
happens to land in `.go` files.

Reference pattern: the supabase tri task (`schema-evolution-tri-supabase-001`)
keeps `languages = ["sql", "haskell", "go"]` because gotrue's auth flow
genuinely requires Go semantics, even though the task spans three languages.
The correct outcome of the supabase audit was **restoration** of the `go`
tag, not removal — i.e., conservative tagging that reflects the actual
ecosystems the agent must understand.

## Baseline (pre-audit)

- Total active multi-repo tasks (incl. monorepo cross-package): **120**
- Go-tagged multi-repo tasks: **55**
- Multi-repo Go%: **45.83%**
- Cap: <40% (max Go count = 47; need to remove ≥8 Go tags to clear the cap)

Computed via `python3 scripts/validation/task_mix_validator.py`.

## Per-task verdicts

For each task, "Go-essential" means the task tests an invariant that is
Go-specific (e.g., shared Go mutable types, Go concurrency primitives, Go
race detector evidence, Go interface contracts between two Go libraries).
"Incidental" means the bug pattern is conceptual and the repos happen to
be in Go.

| # | Task | Repos | Verdict | Rationale |
|---|---|---|---|---|
| 1 | `incident-investigation-dual-cilium-001` | cilium/cilium ↔ cilium/ebpf | **Go-essential** | Bug is a Go data race on a shared mutable `MapSpec` struct + `bytes.Reader` cursor. Race-detector evidence (`DATA RACE` from Go's race detector) is in the prompt. Remediation requires understanding Go goroutine sharing and deep-copy semantics. |
| 2 | `incident-investigation-dual-cockroach-001` | cockroachdb/cockroach ↔ cockroachdb/pebble | **Go-essential** | Pebble's per-stripe drop-decision is a Go iterator implementation; the SST ingest seam crosses two Go interfaces (`Engine.Ingest`, `pebble.Batch`). Remediation requires reading Go iterator code in Pebble's compaction stripe logic. |
| 3 | `incident-investigation-dual-cortex-001` | cortexproject/cortex ↔ prometheus/alertmanager | **Go-essential** | Failure surface is a 400 silently swallowed by Cortex's Go HTTP client (`newRulerNotifier`); validator is `validateLs` / `IsValidLabelName` in Prometheus. Both the swallow and the validation are Go function-level bugs. |
| 4 | `incident-investigation-dual-loki-001` | grafana/loki ↔ grafana/dskit | **Go-essential** | The asymmetry is between two Go method receivers on `dskit.Ring` (`IsHealthy(Read)` vs `IsHealthy(Reporting)`) consumed by a Go index-gateway client. The fix lives in dskit's Go ring health policy. |
| 5 | `incident-investigation-dual-nats-001` | nats-io/nats.go ↔ nats-io/nats-server | **Go-essential** | Cross-repo *contract* between a Go client (`PullSubscribe`/`nextRequest`) and a Go server (`consumer.checkPending` + `filestore.EraseMsg`). The pending-sequence wire format is defined and consumed entirely in Go; tracing the gap requires reading both Go state machines. |
| 6 | `incident-investigation-dual-nerdctl-001` | containerd/nerdctl ↔ containerd/containerd | **Go-essential** | The bug is in the `RemoteOpt` builder (Go option-pattern) where the platform matcher is omitted, then enforced by containerd's `pkg/unpack` Go branch. Both surfaces are Go. |
| 7 | `incident-investigation-dual-nomad-001` | hashicorp/nomad ↔ hashicorp/raft | **Go-essential** | The apply/ack timing gap is between a Go FSM goroutine (`runFSM`) in hashicorp/raft and Go scheduler workers in Nomad reading the state_store. Race surface is goroutine-level Go concurrency. |
| 8 | `incident-investigation-dual-tempo-001` | grafana/tempo ↔ grafana/dskit | **Go-essential** | Same dskit ring `IsHealthy` Go method as #4; saturation surface is a Go `sync.Mutex` contention in `instance.go`. Both are Go-specific patterns. |
| 9 | `incident-investigation-dual-tikv-001` | tikv/tikv ↔ tikv/pd | **Already mixed (rust+go)** | TiKV is Rust, PD is Go. The task already declares `languages = ["rust", "go"]`. Go side (PD's `processRegionHeartbeat`) is essential to the task; Rust side (TiKV `collect_down_peers`) is essential to the task. No removal applicable. |
| 10 | `incident-investigation-dual-vault-001` | hashicorp/vault ↔ hashicorp/raft | **Go-essential** | The snapshot install seam is between Vault's Go `physical/raft` BoltSnapshotStore and hashicorp/raft's Go `installSnapshot` handler. Both sides use Go interfaces (`SnapshotSink`, `FSM.Restore`). |
| 11 | `incident-inv-dual-cortex-thanos-001` | cortexproject/cortex ↔ thanos-io/thanos | **Go-essential** | Older-style task; the third checkpoint explicitly requires identifying a race condition between two Go subsystems (Cortex ring, Thanos StoreAPI). Race reasoning depends on Go concurrency primitives. |

## Aggregate result

| Verdict | Count |
|---|---|
| Go-essential (no removal) | 10 |
| Already mixed (no removal) | 1 |
| Incidental (defensible removal) | **0** |

**Defensible Go tag removals across the 11 candidates: 0.**

The cheap-path tag-audit cannot close the gap. All 11 are bona-fide
Go ↔ Go (or Rust ↔ Go for tikv) cross-repo contract or concurrency tasks
where the agent must reason about Go-specific semantics to solve the
checkpoints.

## Recomputed Go%

Unchanged: **55/120 = 45.83%** (still over 40% cap).

## Conclusion

- **Primary AC:** PASS on items 1–3 of the bead (per-task verdicts
  recorded; tag-removals applied where defensible — none defensible).
- **Sub-clause `<40%` cap:** **FAIL** — gap unchanged.
- **Recommended next step:** file the follow-up mass-authoring bead (the
  expensive option) with a gap of **8 non-Go multi-repo tasks** to bring
  Go count from 55 down to ≤47.

### Suggested non-Go ecosystems for follow-up authoring

Based on existing distribution (current top non-Go ecosystems by
multi-repo count), the cheapest paths to broaden balance are:

- Python: 22 (could grow to dilute Go%) — e.g., django/wagtail, flask/werkzeug, fastapi/pydantic dual-repo tasks
- TypeScript/JavaScript: 17/14 — e.g., next.js/webpack, babel/swc, react/relay
- Java: 14 — e.g., spring-boot/hibernate, kafka/spring-kafka
- Rust: 13 (already growing; tokio/hyper, tonic/axum)
- C++: 4–5 — envoy/istio internals, grpc/protobuf

Eight new non-Go dual-repo incident_investigation or
support_code_mapping tasks across these ecosystems would close the
gap without further compromising other PRD targets (per-type minimums,
strict-multi-repo ≥45%).

## Closure

Per the bead's instructions:
- Audit doc: complete (this file).
- Tag-removals applied: 0 (none defensible).
- Final Go%: 45.83% (still over cap).
- Outcome on primary AC: **PASS**.
- Outcome on `<40%` cap sub-clause: **FAIL** — follow-up
  mass-authoring bead filed: **EnterpriseBench-2o0**.
