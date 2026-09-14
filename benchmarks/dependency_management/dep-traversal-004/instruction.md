# CVE Blast Radius Analysis: HTTP/2 Rapid Reset in golang.org/x/net

## Context

CVE-2023-39325 is an HTTP/2 Rapid Reset denial-of-service vulnerability in golang.org/x/net, affecting versions prior to 0.17.0 (fixed in 0.17.0). This is the Go-specific advisory for the industry-wide HTTP/2 Rapid Reset attack (CVE-2023-44487) that affected nearly every HTTP/2 implementation.

I need a complete blast radius assessment across our CNCF-based Go infrastructure before we can coordinate patching.

## Environment

- `/workspace/kubernetes/` — `kubernetes/kubernetes` v1.28.0
- `/workspace/etcd/`       — `etcd-io/etcd` v3.5.9
- `/workspace/grpc-go/`    — `grpc/grpc-go` v1.58.0

All three are Go projects. Any of them may depend on `golang.org/x/net` directly, or
pull it in transitively through one of the others — you'll need to read the actual
go.mod / go.sum files in each repo to establish which.

## What I Need

1. **CVE Identification**: Confirm the CVE, module, and version range, and ground
   your confirmation in what you actually find pinned in the repos.

2. **Direct Dependents**: Check go.mod in each workspace repo to determine whether
   `golang.org/x/net` is declared as a direct requirement there, and note the exact
   version each repo pins.

3. **Transitive Paths**: Determine how each repo actually reaches `golang.org/x/net`
   — a direct require, or indirectly through another module. Map the dependency
   graph from what the go.mod files actually show; don't assume the shape of the
   graph in advance.

4. **Version Analysis**: Check go.sum for the actual resolved x/net version in each
   repo and classify each repo as affected or already patched relative to 0.17.0.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
