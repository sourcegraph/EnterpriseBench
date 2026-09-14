# Protobuf v1 → v2 Import Migration: Refactor Plan

## Overview

This plan covers the migration from `github.com/golang/protobuf` (v1 API) to
`google.golang.org/protobuf` (v2 API) across three repositories, in topological
dependency order. The reference migration is grpc/grpc-go PR #6919
(merged 2024-01-30).

---

## 1. Execution Order (Topologically Sorted)

```
Step 1 — protobuf-go      (google.golang.org/protobuf)    [no code changes; already IS the v2 target]
Step 2 — grpc-go          (google.golang.org/grpc)         [migrate 8 files; tag new release]
Step 3 — etcd             (go.etcd.io/etcd)                [migrate 4 hand-written files + regenerate 16 pb files; bump grpc dep]
```

---

## 2. Dependency Graph

```
github.com/golang/protobuf  ─────(bridge only)──────────►  google.golang.org/protobuf
                                                                    │
                                                           (Step 1: source of truth)
                                                                    │
                                                                    ▼
                                            google.golang.org/grpc  (grpc-go)
                                            currently requires BOTH v1 and v2:
                                              github.com/golang/protobuf v1.5.3  ← remove
                                              google.golang.org/protobuf v1.32.0 ← keep
                                                                    │
                                                           (Step 2: migrate)
                                                                    │
                                                                    ▼
                                                  go.etcd.io/etcd (etcd v3.5.x)
                                                  currently: grpc v1.59.0 + golang/protobuf v1.5.3
                                                  after:     grpc ≥v1.62.1 + google/protobuf direct
                                                                    │
                                                           (Step 3: migrate)
```

---

## 3. Per-Step Import Path Changes

### Step 1 — `protobuf-go` (`/workspace/protobuf-go/`)

**Status:** Already the v2 implementation. No source changes required.

**Context:** `google.golang.org/protobuf` is the migration target. Its `go.mod`
declares a dependency on `github.com/golang/protobuf v1.5.0` solely for its
`protoadapt` bridge package, which provides bidirectional conversion between v1
and v2 message types.

**Action:** Tag v1.32.0 (already done upstream). Downstream repos point to this tag.

---

### Step 2 — `grpc-go` (`/workspace/grpc-go/`)

**8 files require migration:**

#### A. `channelz/service/service.go`

| Old import | New import |
|---|---|
| `"github.com/golang/protobuf/ptypes"` | remove; use `*pb` constructors directly |
| `wrpb "github.com/golang/protobuf/ptypes/wrappers"` | `"google.golang.org/protobuf/types/known/wrapperspb"` |

- `ptypes.TimestampProto(t)` → `timestamppb.New(t)`
- `ptypes.DurationProto(d)` → `durationpb.New(d)`
- `wrpb.Int64Value{Value: v}` → `wrapperspb.Int64(v)`
- `wrpb.BoolValue{Value: v}` → `wrapperspb.Bool(v)`

#### B. `channelz/service/func_linux.go`

| Old import | New import |
|---|---|
| `"github.com/golang/protobuf/ptypes"` | remove |
| `durpb "github.com/golang/protobuf/ptypes/duration"` | `"google.golang.org/protobuf/types/known/durationpb"` |

- `ptypes.DurationProto(d)` → `durationpb.New(d)`
- `durpb.Duration` → `durationpb.Duration`

#### C. `channelz/service/service_test.go`

| Old import | New import |
|---|---|
| `"github.com/golang/protobuf/proto"` | `"google.golang.org/protobuf/proto"` |
| `"github.com/golang/protobuf/ptypes"` | remove |

- `ptypes.MarshalAny(m)` → `anypb.New(m)` (already imported elsewhere)
- `ptypes.UnmarshalAny(a, m)` → `a.UnmarshalTo(m)`
- `proto.Equal(a, b)` → `proto.Equal(a, b)` (same call, different package)

#### D. `channelz/service/service_sktopt_test.go`

| Old import | New import |
|---|---|
| `"github.com/golang/protobuf/ptypes"` | remove |
| `durpb "github.com/golang/protobuf/ptypes/duration"` | `"google.golang.org/protobuf/types/known/durationpb"` |

- `ptypes.DurationProto(d)` → `durationpb.New(d)`

#### E. `credentials/credentials.go`

| Old import | New import |
|---|---|
| `"github.com/golang/protobuf/proto"` | `"google.golang.org/protobuf/proto"` |

- `proto.Marshal(m)` → `proto.Marshal(m)` (identical call; v2 `proto.Message` is interface-compatible via generated code)
- May require `protoadapt.MessageV2Of(m)` for any v1-only messages passed to Marshal

#### F. `internal/pretty/pretty.go`

| Old import | New import |
|---|---|
| `protov1 "github.com/golang/protobuf/proto"` | remove |

- `protov1.MessageV1` type check → use `protoadapt.MessageV2Of(m)` for conversion
- Already imports `google.golang.org/protobuf/...`; drop v1 dependency entirely
- Replace `protov1.MessageReflect(m)` / `protov1.MessageV2` calls with `protoadapt` equivalents

#### G. `reflection/grpc_testing_not_regenerate/testv3.go`

| Old import | New import |
|---|---|
| `proto "github.com/golang/protobuf/proto"` | `proto "google.golang.org/protobuf/proto"` |

Generated file; may need regeneration with updated `protoc-gen-go`.

#### H. `xds/internal/xdsclient/bootstrap/bootstrap.go`

| Old import | New import |
|---|---|
| `"github.com/golang/protobuf/jsonpb"` | `"google.golang.org/protobuf/encoding/protojson"` |

- `jsonpb.Unmarshaler{AllowUnknownFields: true}.Unmarshal(r, m)` → `protojson.UnmarshalOptions{DiscardUnknown: true}.Unmarshal(b, m)`

**`go.mod` changes:**
```diff
-  github.com/golang/protobuf v1.5.3
+  github.com/golang/protobuf v1.5.3 // indirect  (or remove entirely)
   google.golang.org/protobuf v1.32.0
```

---

### Step 3 — `etcd` (`/workspace/etcd/`)

#### A. Hand-written files (4 files — can proceed in parallel with B)

**`server/etcdserver/api/v3rpc/codec.go`**

| Old | New |
|---|---|
| `"github.com/golang/protobuf/proto"` | `"google.golang.org/protobuf/proto"` + `"google.golang.org/protobuf/protoadapt"` |

```go
// Old
func (c *codec) Marshal(v interface{}) ([]byte, error) {
    b, err := proto.Marshal(v.(proto.Message))  // v1 proto.Message
    ...
}

// New
func (c *codec) Marshal(v interface{}) ([]byte, error) {
    vv, ok := v.(proto.Message)  // try v2 first
    if !ok {
        vv = protoadapt.MessageV2Of(v.(protoadapt.MessageV1))
    }
    b, err := proto.Marshal(vv)
    ...
}
```

**`server/etcdserver/util.go`**

| Old | New |
|---|---|
| `"github.com/golang/protobuf/proto"` | `"google.golang.org/protobuf/proto"` |

- `proto.Size(m)` → `proto.Size(m)` (same call; update type assertions as needed)

**`api/etcdserverpb/raft_internal_stringer.go`**

| Old | New |
|---|---|
| `proto "github.com/golang/protobuf/proto"` | `proto "google.golang.org/protobuf/proto"` |

**`server/wal/walpb/record_test.go`**

| Old | New |
|---|---|
| `"github.com/golang/protobuf/descriptor"` | `"google.golang.org/protobuf/reflect/protodesc"` or use message's `ProtoReflect().Descriptor()` |

#### B. Generated `.pb.go` files (13 files — regenerate in parallel with A)

All generated protobuf files must be regenerated using an updated `protoc-gen-go`
(>= v1.28.0) which emits v2-compatible code:

| File | Package |
|---|---|
| `api/authpb/auth.pb.go` | `go.etcd.io/etcd/api/v3/authpb` |
| `api/etcdserverpb/etcdserver.pb.go` | `go.etcd.io/etcd/api/v3/etcdserverpb` |
| `api/etcdserverpb/rpc.pb.go` | `go.etcd.io/etcd/api/v3/etcdserverpb` |
| `api/etcdserverpb/raft_internal.pb.go` | `go.etcd.io/etcd/api/v3/etcdserverpb` |
| `api/mvccpb/kv.pb.go` | `go.etcd.io/etcd/api/v3/mvccpb` |
| `api/membershippb/membership.pb.go` | `go.etcd.io/etcd/api/v3/membershippb` |
| `raft/raftpb/raft.pb.go` | `go.etcd.io/etcd/raft/v3/raftpb` |
| `server/lease/leasepb/lease.pb.go` | `go.etcd.io/etcd/server/v3/lease/leasepb` |
| `server/wal/walpb/record.pb.go` | `go.etcd.io/etcd/server/v3/wal/walpb` |
| `server/etcdserver/api/snap/snappb/snap.pb.go` | `go.etcd.io/etcd/server/v3/.../snappb` |
| `server/etcdserver/api/v3election/v3electionpb/v3election.pb.go` | v3election |
| `server/etcdserver/api/v3lock/v3lockpb/v3lock.pb.go` | v3lock |
| `tests/functional/rpcpb/rpc.pb.go` | functional test rpc |

#### C. Generated `.pb.gw.go` files (3 files — regenerate after B)

| File |
|---|
| `api/etcdserverpb/gw/rpc.pb.gw.go` |
| `server/etcdserver/api/v3lock/v3lockpb/gw/v3lock.pb.gw.go` |
| `server/etcdserver/api/v3election/v3electionpb/gw/v3election.pb.gw.go` |

Regenerate with `protoc-gen-grpc-gateway` compatible with v2 protobuf.

**`go.mod` / `api/go.mod` changes:**
```diff
# go.mod
-  google.golang.org/grpc v1.59.0
+  google.golang.org/grpc v1.62.1  # first release after PR #6919 merge (2024-01-30)
-  github.com/golang/protobuf v1.5.3 // indirect
+  # remove or keep as transitive-only indirect
+  google.golang.org/protobuf v1.32.0  # promote from indirect to direct

# api/go.mod
-  github.com/golang/protobuf v1.5.3
+  github.com/golang/protobuf v1.5.3 // indirect  (or remove)
+  google.golang.org/protobuf v1.32.0
```

---

## 4. Parallelization Annotations

```
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 1: protobuf-go  (no changes required — already v2)             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  unblocks
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 2: grpc-go                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ channelz/svc │  │ credentials  │  │ xds bootstrap│  ... (all 8) │
│  │ (4 files)    │  │ (1 file)     │  │ jsonpb→json  │  parallel    │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│  All 8 files can be edited concurrently by separate PRs / authors.  │
│  Single integration test run gates the release tag.                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  unblocks after new grpc-go tag
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 3: etcd                                                        │
│  ┌─────────────────────────┐   ┌───────────────────────────────┐   │
│  │ A: Hand-written (4 files)│   │ B: Regenerate pb.go (13 files)│   │
│  │ codec.go, util.go,       │   │ Run protoc with updated plugin │   │
│  │ stringer.go, record_test │   │ (parallel with A)             │   │
│  └─────────────────────────┘   └──────────────┬────────────────┘   │
│                                               │ blocks             │
│                                               ▼                    │
│                            ┌──────────────────────────────────┐    │
│                            │ C: Regenerate pb.gw.go (3 files) │    │
│                            └──────────────────────────────────┘    │
│  A and B are independent and can proceed in parallel.               │
│  C requires B to complete (gateway files import generated types).   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Risk Assessment

### High Risk

| Risk | Location | Mitigation |
|---|---|---|
| **`codec.go` type assertion breakage** | `etcd/server/etcdserver/api/v3rpc/codec.go` | v1 `proto.Message` and v2 `proto.Message` are different interfaces; all messages passed to the codec must satisfy the v2 interface or be wrapped via `protoadapt`. Existing generated code uses v1 types — if not regenerated before codec change, runtime panics result. **Sequence B before A, or use dual-path in codec.** |
| **pb.go regeneration behavioral drift** | All 13 `.pb.go` files in etcd | Newer `protoc-gen-go` may change field access patterns (e.g., getter/setter semantics). Run full test suite. Particularly risky for raft state machine code (`raftpb`). |
| **jsonpb → protojson API difference** | `grpc-go/xds/.../bootstrap.go` | `protojson` is stricter about unknown fields by default; `DiscardUnknown: true` must be set to match `AllowUnknownFields: true` behavior. Missing this silently changes behaviour for xDS bootstrap JSON parsing. |

### Medium Risk

| Risk | Location | Mitigation |
|---|---|---|
| **ptypes helper removal** | `grpc-go/channelz/service/` (4 files) | `ptypes.TimestampProto`, `ptypes.DurationProto` are removed; direct constructors (`timestamppb.New`, `durationpb.New`) have subtly different nil/zero handling. |
| **grpc-gateway v1 incompatibility** | `etcd/api/.../gw/*.pb.gw.go` | etcd uses `grpc-gateway v1` (not v2); regeneration may require pinning generator version to match. |
| **etcd multi-module workspace** | `etcd/go.mod` + `etcd/api/go.mod` | etcd uses Go workspace `replace` directives across 10 sub-modules; `github.com/golang/protobuf` must be cleaned from each affected `go.mod`. |

### Low Risk

| Risk | Location | Mitigation |
|---|---|---|
| **pretty.go dual-proto logic** | `grpc-go/internal/pretty/pretty.go` | Already handles both v1 and v2; removing v1 path is safe since all messages already implement v2. |
| **Test file breakage** | `grpc-go/channelz/service/*_test.go` | Test-only; failures are caught in CI before release. |
| **`github.com/golang/protobuf` transitive retention** | all three repos | Even after migration, `github.com/golang/protobuf v1.5.x` may remain as an indirect dependency (required by other deps). This is acceptable — the bridge is wire-compatible. Verify `go mod tidy` removes it from `require` blocks after migration. |

---

## 6. Validation Checklist

After each step:
- [ ] `go build ./...` — no compilation errors
- [ ] `go test ./...` — full test suite green
- [ ] `go mod tidy` — `github.com/golang/protobuf` not in `require` block (only indirect or absent)
- [ ] `grep -r "github.com/golang/protobuf" --include="*.go"` returns no hand-written files
- [ ] Integration tests against real gRPC services pass (especially etcd cluster tests)
