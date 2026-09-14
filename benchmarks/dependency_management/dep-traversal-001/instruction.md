# CVE Blast Radius Analysis: lodash Command Injection

## Context

Our security team flagged CVE-2021-23337, a command injection vulnerability in the `lodash` npm package affecting the `_.template` function. We use lodash across our JavaScript build toolchain — it's a dependency of both webpack and jest.

I need you to determine the full blast radius. Don't just check direct dependencies — trace through the transitive graph to find every package that could be pulling in a vulnerable version.

## What I Need

1. **CVE Identification**: Confirm the CVE ID, affected package, and vulnerable version range.

2. **Direct Dependents**: Which repos in the workspace directly depend on lodash? Show me the manifest files (package.json) where the dependency is declared.

3. **Transitive Paths**: Trace the full dependency chain. For example, if `jest-haste-map` depends on lodash, and `jest` depends on `jest-haste-map`, that's a 2-hop transitive path. Map all such paths.

4. **Version Analysis**: For each consumer (and for lodash itself as checked out in the workspace), determine the exact resolved/pinned lodash version and state whether it is affected. Some may have already upgraded past the fix.

## Output

Write your findings to `/workspace/BLAST_RADIUS.md` with clear sections for each of the above.
