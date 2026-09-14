# Impact Analysis: cel-go Program Interface Breaking Change

## Context

We're bumping the cel-go dependency to v0.10.1 in the grpc-go project.
After that bump, something in the `security/authorization` module of
grpc-go no longer builds.

## What I Need

1. **The interface change**: Inspect the `cel.Program` interface in the
   `google/cel-go` producer repo. What changed going into v0.10.1, and why
   would that change break any type that implements the interface?

2. **Affected mocks**: Find the mock/fake `cel.Program` implementation in
   grpc-go's `security/authorization/` module. Which file has it?

3. **Fix details**: Consumer fix PR grpc/grpc-go#5243 addressed this. Look at
   that PR — precisely which file(s) did it touch and what did it modify?

4. **Broader search**: Are there other places in grpc-go that implement
   `cel.Program`? Check the xDS, RBAC, and authorization packages.

## Output

Write your findings to `/workspace/analysis/IMPACT_REPORT.md`.
