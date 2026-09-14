# Refactor Plan: etcd 3.6 Client Update Cascade

## Current State

| Repo | Module | Pinned Version |
|------|--------|----------------|
| `/workspace/etcd` | `go.etcd.io/etcd/client/v3` | **v3.6.0** (source, already released) |
| `/workspace/kubernetes` | root `go.mod` | **v3.5.16** (needs update) |
| kubernetes staging: `k8s.io/apiserver` | `staging/src/k8s.io/apiserver/go.mod` | **v3.5.16** (needs update) |
| kubernetes staging: `k8s.io/apiextensions-apiserver` | `staging/src/k8s.io/apiextensions-apiserver/go.mod` | **v3.5.16** (needs update) |
| kubernetes staging: `k8s.io/kube-aggregator` | `staging/src/k8s.io/kube-aggregator/go.mod` | **v3.5.16** (indirect, needs update) |
| kubernetes staging: `k8s.io/cloud-provider` | `staging/src/k8s.io/cloud-provider/go.mod` | **v3.5.16** (indirect, needs update) |
| kubernetes staging: `k8s.io/controller-manager` | `staging/src/k8s.io/controller-manager/go.mod` | **v3.5.16** (indirect, needs update) |
| kubernetes staging: `k8s.io/pod-security-admission` | `staging/src/k8s.io/pod-security-admission/go.mod` | **v3.5.16** (indirect, needs update) |
| kubernetes staging: `k8s.io/sample-apiserver` | `staging/src/k8s.io/sample-apiserver/go.mod` | **v3.5.16** (indirect, needs update) |

---

## Dependency Graph

```
go.etcd.io/etcd v3.6.0 (upstream — already released)
├── go.etcd.io/etcd/api/v3
├── go.etcd.io/etcd/client/pkg/v3
├── go.etcd.io/etcd/client/v3          ← primary consumer-facing library
│   └── go.etcd.io/etcd/client/v3/kubernetes  (Kubernetes integration sub-package)
├── go.etcd.io/etcd/client/v2          (deprecated, indirect only)
├── go.etcd.io/etcd/pkg/v3
├── go.etcd.io/etcd/raft/v3
└── go.etcd.io/etcd/server/v3

kubernetes/kubernetes (consumer, all staging in one monorepo PR)
├── k8s.io/apiserver  [DIRECT — highest risk]
│   ├── go.etcd.io/etcd/api/v3
│   ├── go.etcd.io/etcd/client/pkg/v3
│   ├── go.etcd.io/etcd/client/v3
│   └── go.etcd.io/etcd/server/v3
│   Key files:
│   └── staging/src/k8s.io/apiserver/pkg/storage/etcd3/
│       ├── store.go          (36 KB — main KV backend)
│       ├── watcher.go        (22 KB — watch cache)
│       ├── lease_manager.go  (clientv3.Lease usage)
│       ├── latency_tracker.go (clientv3.KV decorator)
│       └── healthcheck.go
│   └── staging/src/k8s.io/apiserver/pkg/storage/storagebackend/factory/
│       └── etcd3.go          (client construction + TLS + keepalive)
│
├── k8s.io/apiextensions-apiserver  [DIRECT — depends on apiserver]
│   ├── go.etcd.io/etcd/client/pkg/v3
│   └── go.etcd.io/etcd/client/v3
│
├── k8s.io/kube-aggregator           [INDIRECT — via apiserver]
├── k8s.io/cloud-provider            [INDIRECT]
├── k8s.io/controller-manager        [INDIRECT]
├── k8s.io/pod-security-admission    [INDIRECT]
└── k8s.io/sample-apiserver          [INDIRECT]
```

---

## Execution Plan (Topologically Sorted)

### Step 1 — Verify upstream etcd v3.6.0 release
**Repo:** `go.etcd.io/etcd` (`/workspace/etcd`)
**Status:** COMPLETE — v3.6.0 is already tagged and available on pkg.go.dev.
**Action:** None required. Confirm sub-module versions are all consistent at v3.6.0:
- `go.etcd.io/etcd/api/v3 v3.6.0`
- `go.etcd.io/etcd/client/pkg/v3 v3.6.0`
- `go.etcd.io/etcd/client/v3 v3.6.0`
- `go.etcd.io/etcd/server/v3 v3.6.0`
- `go.etcd.io/etcd/pkg/v3 v3.6.0`
- `go.etcd.io/etcd/raft/v3 v3.6.0`

**Risk:** LOW — upstream already released.

---

### Step 2 — Update k8s.io/apiserver staging module
**Repo:** `kubernetes/kubernetes` (monorepo)
**File:** `staging/src/k8s.io/apiserver/go.mod`
**Parallelizable with:** Nothing — this must complete before Steps 3–5 start (apiserver is the root direct consumer).

**Actions:**
1. Bump all `go.etcd.io/etcd/*` `require` entries from `v3.5.16` → `v3.6.0`
2. Audit `staging/src/k8s.io/apiserver/pkg/storage/etcd3/` for v3.6 API changes:
   - `store.go` — check `clientv3.Client`, `clientv3.KV`, `clientv3.OpOption` signatures
   - `watcher.go` — check watch event types and `clientv3.WatchChan`
   - `lease_manager.go` — check `clientv3.LeaseID`, `clientv3.LeaseGrantResponse`
   - `latency_tracker.go` — verify `clientv3.KV` interface still matches decorator
   - `healthcheck.go` — check `clientv3.Maintenance` interface
3. Audit `staging/src/k8s.io/apiserver/pkg/storage/storagebackend/factory/etcd3.go`:
   - Client construction options (dial options, keepalive)
   - TLS config via `go.etcd.io/etcd/client/pkg/v3/transport`
   - `go.etcd.io/etcd/client/v3/kubernetes` integration package

**Risk:** HIGH
- 365 → 247 total package reduction means some packages/APIs may be removed or moved
- `client/v3/kubernetes` sub-package is a direct import in the storage backend — verify it still exists at v3.6.0
- Watch API behavioral changes could break the caching layer
- `etcd/server/v3` is imported in staging for testserver — integration test setup may break

---

### Step 3 — Update k8s.io/apiextensions-apiserver staging module
**Repo:** `kubernetes/kubernetes` (monorepo)
**File:** `staging/src/k8s.io/apiextensions-apiserver/go.mod`
**Parallelizable with:** Steps 4 and 5 (all indirect-only staging modules can update together after Step 2).

**Actions:**
1. Bump `go.etcd.io/etcd/client/pkg/v3` and `go.etcd.io/etcd/client/v3` from `v3.5.16` → `v3.6.0`
2. Verify no direct usage of deprecated etcd v3.5 APIs in CRD storage code

**Risk:** MEDIUM — direct dependency, but passes through apiserver abstraction layer; likely low code-level impact.

---

### Step 4 — Update indirect-only staging modules
**Repo:** `kubernetes/kubernetes` (monorepo)
**Files (update in parallel):**
- `staging/src/k8s.io/kube-aggregator/go.mod`
- `staging/src/k8s.io/cloud-provider/go.mod`
- `staging/src/k8s.io/controller-manager/go.mod`
- `staging/src/k8s.io/pod-security-admission/go.mod`
- `staging/src/k8s.io/sample-apiserver/go.mod`
**Parallelizable with:** Step 3 (all can run concurrently after Step 2).

**Actions:**
1. Bump all indirect `go.etcd.io/etcd/*` entries to `v3.6.0`
2. No code changes expected (indirect transitive deps only)

**Risk:** LOW — purely `go.mod` version bumps; no direct etcd API usage in these modules.

---

### Step 5 — Update root kubernetes go.mod and vendor
**Repo:** `kubernetes/kubernetes` (monorepo)
**File:** `go.mod`, `vendor/`
**Parallelizable with:** Nothing — must run after Steps 2, 3, 4.

**Actions:**
1. Update root `go.mod`:
   - `go.etcd.io/etcd/api/v3 v3.5.16` → `v3.6.0`
   - `go.etcd.io/etcd/client/pkg/v3 v3.5.16` → `v3.6.0`
   - `go.etcd.io/etcd/client/v3 v3.5.16` → `v3.6.0`
   - `go.etcd.io/etcd/client/v2 v2.305.16` → `v2.306.0` (check etcd v2 compatibility module version)
   - `go.etcd.io/etcd/pkg/v3 v3.5.16` → `v3.6.0`
   - `go.etcd.io/etcd/raft/v3 v3.5.16` → `v3.6.0`
   - `go.etcd.io/etcd/server/v3 v3.5.16` → `v3.6.0`
2. Run `go mod tidy` to reconcile transitive deps (365 → 247 total packages)
3. Run `go mod vendor` to refresh `vendor/go.etcd.io/etcd/`
4. Verify all vendored files in `vendor/go.etcd.io/etcd/` are at v3.6.0

**Risk:** HIGH
- Vendor refresh can surface unexpected missing packages (365 → 247 reduction means ~118 packages removed)
- `vendor/modules.txt` must be consistent with all go.mod files
- Any test infrastructure that uses vendored etcd server packages may break

---

### Step 6 — Run test suite
**Repo:** `kubernetes/kubernetes`
**Parallelizable with:** Nothing — must run after Step 5.

**Actions:**
1. Run unit tests for the storage backend:
   ```
   go test ./staging/src/k8s.io/apiserver/pkg/storage/etcd3/...
   go test ./staging/src/k8s.io/apiserver/pkg/storage/storagebackend/...
   ```
2. Run integration tests (requires embedded etcd server from `testserver/`):
   ```
   go test ./staging/src/k8s.io/apiserver/...
   go test ./staging/src/k8s.io/apiextensions-apiserver/...
   ```
3. Run CI suite including kube-apiserver e2e storage tests

**Risk:** MEDIUM — test failures are expected and diagnostic; they surface API incompatibilities missed in code review.

---

## Parallelization Summary

```
Step 1 (etcd upstream verify) ──────────────────────────────► DONE
                                                              │
Step 2 (k8s.io/apiserver update)  ◄────────────────────────── (must be first)
                                                              │
                     ┌────────────────┬────────────────┐      │
Step 3 (apiextensions)  Step 4a (kube-aggregator)  Step 4b (cloud-provider) ...
                     │           [ALL PARALLEL after Step 2]
                     └────────────────┴────────────────┘
                                      │
Step 5 (root go.mod + vendor) ◄────────
                                      │
Step 6 (tests) ◄──────────────────────
```

**Critical path:** Step 1 → Step 2 → Step 5 → Step 6
**Parallelizable:** Steps 3 and 4 (all run concurrently, gated on Step 2)

---

## Risk Assessment Summary

| Step | Risk | Rationale |
|------|------|-----------|
| Step 1 | LOW | etcd v3.6.0 already released; no action required |
| Step 2 | HIGH | Core storage backend; direct clientv3 API usage; watch/lease/KV interface changes possible |
| Step 3 | MEDIUM | Direct dependency but passes through apiserver abstraction |
| Step 4 | LOW | Indirect dependencies only; pure version bump |
| Step 5 | HIGH | Vendor refresh with ~118 removed packages; go.mod reconciliation across 9 sub-modules |
| Step 6 | MEDIUM | Test failures expected; integration tests require embedded etcd v3.6 server |

### Key Risk Factors

1. **Package removal (365 → 247):** The 118 removed packages in v3.6 may include packages that Kubernetes imports. The vendor refresh in Step 5 will surface these as missing imports.

2. **`client/v3/kubernetes` sub-package:** `staging/src/k8s.io/apiserver/pkg/storage/storagebackend/factory/etcd3.go` imports `go.etcd.io/etcd/client/v3/kubernetes`. This sub-package must be verified to exist at v3.6.0.

3. **Watch API changes:** The watcher.go (22 KB) and cacher layer depend on watch event structure from `clientv3.WatchChan`. Behavioral changes in watch semantics between v3.5 and v3.6 are a high-impact risk.

4. **gRPC dependency alignment:** etcd v3.6 may update its gRPC dependency. Kubernetes pins gRPC separately; version conflicts in vendor/ can cause build failures.

5. **bbolt version:** `go.etcd.io/bbolt` is an indirect dep; if v3.6 bumps it, vendor consistency checks may fail.

---

## Reference

- etcd v3.6 release notes: https://github.com/etcd-io/etcd/blob/main/CHANGELOG/CHANGELOG-3.6.md
- kubernetes/kubernetes PR #128419: etcd 3.6 client update
- Key implementation files:
  - `staging/src/k8s.io/apiserver/pkg/storage/etcd3/store.go`
  - `staging/src/k8s.io/apiserver/pkg/storage/etcd3/watcher.go`
  - `staging/src/k8s.io/apiserver/pkg/storage/storagebackend/factory/etcd3.go`
  - `staging/src/k8s.io/apiserver/pkg/storage/etcd3/lease_manager.go`
  - `staging/src/k8s.io/apiserver/pkg/storage/etcd3/latency_tracker.go`
