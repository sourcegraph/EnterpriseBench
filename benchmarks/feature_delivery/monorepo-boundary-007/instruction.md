# Impact Analysis: SHA256 Hash Migration + Path Escaping

## Context

Two related changes for pnpm v10.0.0:

1. **Pnpmfile checksum** — `@pnpm/crypto.hash` now supports SHA256-based checksum verification for pnpmfiles. This touches the hook execution pipeline where pnpmfiles are loaded and verified before running.

2. **# escaping in paths** — `@pnpm/dependency-path` now escapes `#` characters in directory names. Some package names contain `#` (scoped packages, hash-based versions) and this was causing path resolution failures on certain filesystems.

Both changes affect core data formats — hash output format and path encoding. I need to understand the full blast radius.

## What I Need

1. **Affected packages**: Which packages consume the hash function or dependency path encoding? Check the hooks pipeline, content-addressable store, and core install logic.

2. **Impact classification**: Classify the semver impact (major/minor/patch) for the changed package and for each downstream package, and justify each classification with reasoning grounded in what actually changed in the code — not just "it touches a shared file."

3. **Boundary violations**: Where are hash values stored/compared? Where are dependency paths encoded/decoded? Show me the specific files.

## Output

Write findings to `/workspace/pnpm/IMPACT_REPORT.md`.
