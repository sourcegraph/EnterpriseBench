# Impact Analysis: ImageResponse Move to next/og

## Context

For the Next.js 14 release, we're moving `ImageResponse` from `next/server` to a dedicated `next/og` entry point. The old `next/server` export will be deprecated.

The main changes are in the `next` package:
- New `og.js` and `og.d.ts` entry points
- Updated webpack config and middleware plugin
- New `src/server/og/image-response.ts` module

Here's what concerns me: the Next.js monorepo has a Rust-based SWC compiler (`@next/swc`) that maintains its own import map for resolving `next/*` paths during compilation. If we move the export but don't update the Rust import map, SWC will still resolve `next/og` to the wrong location (or fail entirely).

## What I Need

1. **Affected packages**: What else in the monorepo references `ImageResponse` or the `next/server` export path? Check the SWC crates, the bundler configs, the compiled vendor packages, and the codemod package.

2. **Impact classification**: The `next` package itself is a major bump. What about `@next/swc`? Is updating an internal import map a major or patch change?

3. **Boundary violations**: Show me the exact files where `next/server` → `ImageResponse` is hardcoded. I particularly need to see the Rust file in the SWC crate.

## Output

Write findings to `/workspace/agent_output/IMPACT_REPORT.md`.
