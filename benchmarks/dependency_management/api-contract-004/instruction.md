# Impact Analysis: gRPC-Go balancer.ClientConn Embedding Requirement

## Context

We're evaluating the upgrade from gRPC-Go v1.70 to v1.71. The release notes flag a breaking API change: `balancer.ClientConn` implementations must now embed an unexported delegate field to satisfy the interface. An internal method was added to the interface to allow gRPC-Go to add new methods in the future without breaking builds.

Before we ship this internally, we want to understand how gRPC-Go itself absorbed this change: which of its own internal balancer implementations directly implement `balancer.ClientConn`, and how they adapted.

## What I Need

1. **The breaking change**: Show me exactly what was added to `balancer.ClientConn` in grpc-go, name the internal marker type it relies on for the embed, and explain why it breaks direct implementations.

2. **Internal implementors**: Find every type within the grpc-go repository (balancer internals, xds internals) that implements `balancer.ClientConn` — both production code and test code.

3. **Test mocks**: Check if there are test mocks or fakes of `balancer.ClientConn` in grpc-go's own test utilities that would also fail to compile without the embed. These are easy to miss.

4. **Fix assessment**: For each implementor, can it just add `balancer.ClientConn` embedding, or does the custom balancer logic need restructuring? The difference is hours vs weeks.

## Output

Write your findings to `/workspace/analysis/IMPACT_REPORT.md`.
