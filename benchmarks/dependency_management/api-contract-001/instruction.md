# Impact Analysis: gRPC-Go Metadata Context Separation

## Context

Hey — the gRPC-Go team just landed a change to how metadata is handled in contexts (grpc-go PR #1157). The gist, from the PR description: the old context-based metadata accessor/constructor API is being replaced with direction-aware variants, one path for metadata coming off an incoming RPC and a separate path for metadata going out on an RPC the service originates. That's all we've been told — no changelog or migration guide has been shared with us.

This is a breaking change for any consumer that uses the old API. We need to assess the impact on our etcd codebase before we can upgrade grpc-go.

## What I Need

1. **Source identification**: Go read `grpc-go/metadata/metadata.go` yourself and tell me exactly what changed — which functions were removed/renamed and what replaced them. Don't guess from the PR description above; confirm it against the actual source.

2. **Consumer file list**: Find every file in the etcd codebase that imports `google.golang.org/grpc/metadata` and uses the old API functions. I need the full list — don't miss any.

3. **Transitive impact**: Some of our code forwards metadata from incoming RPCs to outgoing RPCs. These are the most dangerous because they can stop working unnoticed if metadata isn't explicitly copied between contexts. Flag these specifically, and say what kind of code (not just "a file") does this forwarding.

4. **Breakage classification**: For each affected file, work out what actually happens when it's built and executed against the new API — don't just label it "broken," explain the distinct failure modes you'd observe and which files hit which mode(s).

## Output

Write your findings to `/workspace/analysis/IMPACT_REPORT.md` with:
- The breaking API changes section
- A table of affected etcd files with breakage type
- A "Critical: metadata forwarding" section highlighting interceptors
