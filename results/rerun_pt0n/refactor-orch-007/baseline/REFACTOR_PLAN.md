# gRPC Dial → NewClient Migration: Refactor Plan

## 1. Migration Order (Topologically Sorted)

1. **grpc-go** (v1.79.0) — upstream library; already ships `grpc.NewClient`. No changes required; serves as the reference implementation.
2. **etcd** (v3.5.17) — depends on grpc-go; must migrate before kubernetes can consume the updated client library.
3. **kubernetes** (v1.33.0) — depends on etcd's client library (`go.etcd.io/etcd/client/v3`); migrated last, after etcd is updated.

---

## 2. Dependency Graph

```
grpc-go (google.golang.org/grpc)
    └──► etcd (go.etcd.io/etcd/client/v3)
              └──► kubernetes (k8s.io/apiserver, kubeadm, etc.)
```

etcd also directly imports grpc-go for gateway and test connections.  
Kubernetes directly imports both grpc-go (for kubelet, CSI, KMS, CRI plugins) and the etcd client library.

---

## 3. API Migration Details Per Repo

### 3.1 grpc-go — Reference (No Changes Needed)

| API | Status | Signature |
|-----|--------|-----------|
| `grpc.NewClient` | **Current** (v1.63+) | `func NewClient(target string, opts ...DialOption) (*ClientConn, error)` |
| `grpc.Dial` | **Deprecated** | `func Dial(target string, opts ...DialOption) (*ClientConn, error)` |
| `grpc.DialContext` | **Deprecated** | `func DialContext(ctx context.Context, target string, opts ...DialOption) (*ClientConn, error)` |

Source: `/workspace/grpc-go/clientconn.go:183,263,278`

**Key internal detail:** `DialContext` now calls `NewClient` internally (line 287), prepends `withDefaultScheme("passthrough")` and `WithLocalDNSResolution()` for backward compatibility, then kicks the channel out of idle.

Deprecated `DialOption`s (ignored by `NewClient`):
- `WithBlock` — causes `Dial` to wait until state is `Connected`
- `WithTimeout` — connection deadline
- `WithReturnConnectionError` — surface dial errors
- `FailOnNonTempDialError` — fail on permanent errors

Reference: `/workspace/grpc-go/Documentation/anti-patterns.md`

---

### 3.2 etcd — Primary Migration Target

**All usages to migrate:**

#### Production code (2 files)

| File | Line | Call | Notes |
|------|------|------|-------|
| `client/v3/client.go` | 318 | `grpc.DialContext(dctx, target, opts...)` | Core client dial; context carries `DialTimeout` |
| `server/embed/etcd.go` | 818 | `grpc.DialContext(ctx, addr, opts...)` | gRPC-gateway dial function |

**Migration for `client/v3/client.go:318`** (most critical):

```go
// BEFORE
dctx := c.ctx
if c.cfg.DialTimeout > 0 {
    var cancel context.CancelFunc
    dctx, cancel = context.WithTimeout(c.ctx, c.cfg.DialTimeout)
    defer cancel()
}
conn, err := grpc.DialContext(dctx, target, opts...)

// AFTER (following etcd PR #21282 pattern)
conn, err := grpc.NewClient(target, opts...)
if err != nil {
    return nil, err
}
// Replicate DialTimeout via health check if configured
if c.cfg.DialTimeout > 0 {
    tctx, cancel := context.WithTimeout(c.ctx, c.cfg.DialTimeout)
    defer cancel()
    // Use grpc health protocol or WaitForStateChange to validate reachability
    conn.Connect()
    for {
        s := conn.GetState()
        if s == connectivity.Ready {
            break
        }
        if !conn.WaitForStateChange(tctx, s) {
            // timeout
            conn.Close()
            return nil, tctx.Err()
        }
    }
}
```

**Migration for `server/embed/etcd.go:818`**:

```go
// BEFORE
conn, err := grpc.DialContext(ctx, addr, opts...)

// AFTER
conn, err := grpc.NewClient(addr, opts...)
if err == nil {
    conn.Connect() // kick out of idle immediately (replaces DialContext eager-connect)
}
```

#### Generated gateway files (8 instances, 3 files — require protobuf regeneration)

| File | Lines | Handlers |
|------|-------|---------|
| `api/etcdserverpb/gw/rpc.pb.gw.go` | 2450, 2615, 2684, 2921, 3086, 3323 | KV, Watch, Lease, Cluster, Maintenance, Auth |
| `server/etcdserver/api/v3lock/v3lockpb/gw/v3lock.pb.gw.go` | 154 | Lock |
| `server/etcdserver/api/v3election/v3electionpb/gw/v3election.pb.gw.go` | 294 | Election |

These files are **auto-generated** from protobuf definitions via the grpc-gateway plugin. They must be regenerated with an updated grpc-gateway version that emits `grpc.NewClient` instead of `grpc.Dial`. **Do not manually edit these files.**

#### Test/functional code (3 files — deprecated options require pattern change)

| File | Line | Deprecated options |
|------|------|--------------------|
| `tests/functional/tester/cluster.go` | 88 | `grpc.Dial`, `WithInsecure()`, `WithTimeout(5s)`, `WithBlock()` |
| `tests/functional/rpcpb/member.go` | 81 | `grpc.Dial`, `WithTimeout(5s)`, `WithBlock()` |
| `tests/integration/clientv3/naming/resolver_test.go` | 73 | `grpc.Dial`, `WithInsecure()` |

**Migration pattern for tests using `WithBlock`/`WithTimeout`**:

```go
// BEFORE
opts := []grpc.DialOption{
    grpc.WithInsecure(),
    grpc.WithTimeout(5 * time.Second),
    grpc.WithBlock(),
}
conn, err = grpc.Dial(addr, opts...)

// AFTER
opts := []grpc.DialOption{
    grpc.WithTransportCredentials(insecure.NewCredentials()), // replaces WithInsecure
}
conn, err = grpc.NewClient(addr, opts...)
if err != nil {
    return nil, err
}
// Replicate blocking dial with timeout
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()
conn.Connect()
for conn.GetState() != connectivity.Ready {
    if !conn.WaitForStateChange(ctx, conn.GetState()) {
        conn.Close()
        return nil, fmt.Errorf("timed out waiting for connection")
    }
}
```

#### Documentation (1 instance — cosmetic only)

| File | Line | Status |
|------|------|--------|
| `client/v3/naming/doc.go` | 42 | Commented-out example; update comment text only |

---

### 3.3 kubernetes — Consumer Migration

Kubernetes uses the etcd client library and makes its own direct gRPC calls.

#### Etcd client library consumers (indirect — blocked on etcd migration)

| File | Line | Notes |
|------|------|-------|
| `staging/src/k8s.io/apiserver/pkg/storage/storagebackend/factory/etcd3.go` | 309 | `grpc.WithBlock()` in DialOptions passed to `clientv3.New()` |
| `cluster/images/etcd/migrate/migrate_client.go` | 91 | `grpc.WithBlock()` in DialOptions |
| `cmd/kubeadm/app/util/etcd/etcd.go` | 126 | `grpc.WithBlock()` in DialOptions |

After etcd migrates to `NewClient`, `grpc.WithBlock()` passed via `clientv3.Config.DialOptions` will be **silently ignored**. Each of these sites must implement the state-polling pattern (or rely on etcd client's internal health check mechanism from PR #21282) to preserve connection-validation behavior.

#### Direct gRPC calls in kubernetes (independent of etcd migration)

These can be migrated **in parallel** with the etcd migration:

**Using `grpc.Dial` (must migrate):**

| File | Line | Subsystem |
|------|------|-----------|
| `pkg/kubelet/cm/dra/plugin/plugin.go` | 85 | DRA plugin (has explicit TODO comment) |
| `pkg/serviceaccount/externaljwt/plugin/plugin.go` | 53 | External JWT signer |
| `pkg/volume/csi/csi_client.go` | 536 | CSI driver connections |
| `staging/src/k8s.io/apiserver/pkg/storage/value/encrypt/envelope/grpc_service.go` | 64 | KMS v1beta1 |
| `staging/src/k8s.io/apiserver/pkg/storage/value/encrypt/envelope/kmsv2/grpc_service.go` | 59 | KMS v2 |

**Using `grpc.DialContext` (should migrate):**

| File | Lines | Subsystem |
|------|-------|-----------|
| `pkg/kubelet/apis/podresources/client.go` | 45, 64 | Pod resources API |
| `pkg/kubelet/cm/devicemanager/plugin/v1beta1/client.go` | 131 | Device plugin client |
| `pkg/kubelet/cm/devicemanager/plugin/v1beta1/stub.go` | 288 | Device plugin stub |
| `pkg/kubelet/pluginmanager/operationexecutor/operation_generator.go` | 184 | Plugin manager |
| `pkg/kubelet/pluginmanager/pluginwatcher/example_handler.go` | 142 | Plugin watcher |
| `pkg/probe/grpc/grpc.go` | 70 | gRPC liveness/readiness probes |
| `staging/src/k8s.io/cri-client/pkg/remote_runtime.go` | 121 | CRI runtime service |
| `staging/src/k8s.io/cri-client/pkg/remote_image.go` | 89 | CRI image service |

**Already migrated to `grpc.NewClient`:**

| File | Line | Subsystem |
|------|------|-----------|
| `test/e2e_node/testdeviceplugin/device-plugin.go` | 176 | E2E device plugin test |

---

## 4. Behavioral Differences: Blocking vs Non-Blocking

| Behavior | `grpc.Dial` / `grpc.DialContext` | `grpc.NewClient` |
|----------|----------------------------------|------------------|
| I/O at creation | **Yes** — immediately starts connecting | **No** — lazy connection |
| Default name resolver | `passthrough` | `dns` |
| `WithBlock` support | Yes — waits for `Connected` state | **Ignored** |
| `WithTimeout` support | Yes — connection deadline | **Ignored** |
| `WithReturnConnectionError` | Yes | **Ignored** |
| `FailOnNonTempDialError` | Yes | **Ignored** |
| `WithInsecure` | Supported (deprecated) | Use `grpc.WithTransportCredentials(insecure.NewCredentials())` |
| Context cancellation | Via `DialContext` context | Not applicable at creation; applies to RPCs |
| RPC on idle conn | Waits for connection | Triggers connect, waits per-RPC deadline |

**Critical semantic change:** Code relying on `grpc.Dial` to validate connectivity at startup will silently succeed with `grpc.NewClient` even if the server is unreachable. Connection errors surface at first RPC time instead.

**Workaround to preserve validation behavior** (from grpc-go anti-patterns doc):
```go
conn, err := grpc.NewClient(target, opts...)
// ...
conn.Connect()
for {
    s := conn.GetState()
    if s == connectivity.Ready { break }
    if !conn.WaitForStateChange(ctx, s) {
        return fmt.Errorf("failed to connect: %w", ctx.Err())
    }
}
```

**Name resolver change:** If a target like `"localhost:2379"` is passed without a scheme, `Dial` resolves it as-is (passthrough), while `NewClient` applies DNS resolution. For custom dialers expecting the raw target string, add `grpc.WithNoProxy()` or explicitly use `"passthrough:///host:port"` scheme.

---

## 5. Parallelization Annotations

```
Phase 1 (Sequential — no parallelism):
  [1] grpc-go: Already complete. Verify NewClient API is stable in v1.79.0. ✓

Phase 2 (Sequential — etcd must precede kubernetes's etcd-client changes):
  [2] etcd: Migrate production code (client/v3/client.go, server/embed/etcd.go)
      └─ Blocked by: Phase 1 complete
      └─ Blocks: kubernetes etcd-client consumers (Phase 3a)

Phase 3 (Partially Parallel):
  [3a] kubernetes — etcd-client consumer sites      ← BLOCKED on Phase 2
       - storagebackend/factory/etcd3.go
       - migrate_client.go
       - kubeadm/etcd.go

  [3b] kubernetes — direct grpc.Dial sites          ← INDEPENDENT (can run in parallel with Phase 2)
       - pkg/kubelet/cm/dra/plugin/plugin.go
       - pkg/serviceaccount/externaljwt/plugin/plugin.go
       - pkg/volume/csi/csi_client.go
       - staging/.../envelope/grpc_service.go (KMS v1beta1)
       - staging/.../kmsv2/grpc_service.go (KMS v2)

  [3c] kubernetes — direct grpc.DialContext sites   ← INDEPENDENT (can run in parallel with Phase 2)
       - pkg/kubelet/apis/podresources/client.go
       - pkg/kubelet/cm/devicemanager/plugin/v1beta1/client.go
       - pkg/kubelet/cm/devicemanager/plugin/v1beta1/stub.go
       - pkg/kubelet/pluginmanager/operationexecutor/operation_generator.go
       - pkg/probe/grpc/grpc.go
       - staging/src/k8s.io/cri-client/pkg/remote_runtime.go
       - staging/src/k8s.io/cri-client/pkg/remote_image.go

Phase 4 (After Phase 2 — etcd internal):
  [4a] etcd — regenerate gateway pb.gw.go files via updated grpc-gateway plugin
  [4b] etcd — update functional/integration test files
```

**Summary of parallelizable work:**
- Phases 3b and 3c are fully independent of etcd and can begin immediately.
- Phase 3a must wait for Phase 2 (etcd) to complete.
- Phase 4 (etcd gateway regen + test updates) can proceed alongside Phase 3.
