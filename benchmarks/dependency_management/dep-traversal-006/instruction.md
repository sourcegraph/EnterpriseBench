# CVE Blast Radius Analysis: Protobuf JSON Unmarshal Infinite Loop

## Context

CVE-2024-24786 is a denial-of-service in google.golang.org/protobuf. When unmarshaling JSON input that contains `google.protobuf.Any` types, the decoder can enter an infinite loop. Protobuf is the serialization layer used across our service mesh infrastructure, so I need to know exactly which of our services are actually exposed to this — not just which ones happen to import the module somewhere in their dependency tree.

## Environment

- `/workspace/grpc-go/` — `grpc/grpc-go` v1.62.0
- `/workspace/istio/` — `istio/istio` 1.20.0
- `/workspace/go-control-plane/` — `envoyproxy/go-control-plane` v0.12.0

All three are Go projects that live in the same service mesh. Some of them may depend on `google.golang.org/protobuf` directly; others may only pull it in indirectly, through one of the other repos above. You'll need to read each repo's own manifests to know which is which — don't assume the relationship.

## What I Need

1. **CVE Identification**: CVE, module, and the vulnerable version range.

2. **Direct Dependents**: For each repo above, check its own manifest to determine whether it declares a direct dependency on `google.golang.org/protobuf`. Cite the manifest file and the version it resolves to.

3. **Transitive Paths**: Some of these repos may reach `google.golang.org/protobuf` only through one of the others. Map the full dependency DAG, showing which edges are direct and which are transitive, and cite the manifest evidence for each edge.

4. **Code Path Analysis**: The vulnerability only triggers on JSON unmarshal of `Any` types. Search each repo for actual call sites that unmarshal protobuf messages from JSON where an `Any` field could be present. Cite the specific file and call site for anything you find, and use that to distinguish services that merely import protobuf from services that actually exercise the vulnerable code path.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
