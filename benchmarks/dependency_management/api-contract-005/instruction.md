# Impact Analysis: gRPC-Go Protobuf Migration Fallout

## Context

We recently migrated gRPC-Go from `github.com/golang/protobuf` to `google.golang.org/protobuf/proto` (PR #6919). After deploying, on-call engineers started getting reports of intermittent failures in error-handling code that inspects gRPC status details — code that used to work is now failing in ways the build never caught. We need a thorough investigation, grounded in the actual code, not general protobuf-migration theory.

## What I Need

1. **Root cause**: Find the exact code in grpc-go's status handling that changed as part of the migration. Cite the file and identify the function that was replaced — name both the old call and what replaced it — and explain precisely how the value it returns differs from before. This is the crux of why downstream code breaks.

2. **Blast radius within grpc-go**: Find every file that calls `Status.Details()` or `Status.WithDetails()`. Each one could be affected by the change you found in (1).

3. **Fix analysis**: PR #7724 addressed this problem. Read it and explain the approach it took — did it restore compatibility with the old return type, or did callers have to change?

4. **External consumer impact**: Any gRPC-Go user with code generated from `protoc-gen-go` older than v1.4 (released 2020) is potentially affected. Explain the failure mode such a user would see and what we should tell them.

## Output

Write your findings to `/workspace/analysis/IMPACT_REPORT.md`. Include the root cause (with file/function citations), the list of affected files, the fix approach, and consumer guidance.
