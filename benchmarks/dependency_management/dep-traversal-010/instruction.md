# CVE Blast Radius Analysis: jackson-databind Deep Nesting DoS

## Alert

Security scanning flagged CVE-2020-36518 (GHSA-57j2-w4cx-62h2) in
`com.fasterxml.jackson.core:jackson-databind`: a denial-of-service where
deeply nested JSON objects cause a stack overflow during deserialization.
jackson-databind is the default JSON library pulled in by both frameworks in
this workspace, so any REST endpoint that deserializes JSON is a candidate
for exposure.

## Environment

- `/workspace/jackson-databind/` — the library named in the CVE
- `/workspace/spring-boot/`
- `/workspace/dropwizard/`

Neither framework's top-level build file names a jackson-databind version
directly. Large Maven/Gradle projects like these commonly centralize
third-party version pins in one place rather than declaring the version
wherever the dependency is used — you'll need to find that place for each
framework.

## What I Need

1. **CVE Identification**: Determine the exact version boundaries — which
   jackson-databind versions have this problem, and which version(s) do not.

2. **Direct Dependents**: For each framework repo, find the specific build
   file that declares a dependency on jackson-databind (even without a
   version attached), and name that file/module.

3. **Dependency Chain**: For each framework, locate the module that actually
   pins the jackson-databind version, and show the full path from
   jackson-databind through that module to the file you identified in (2).

4. **Version Resolution**: For each framework, determine the jackson-databind
   version that ends up on the classpath, and say whether it falls inside
   the boundary from (1) or not.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
