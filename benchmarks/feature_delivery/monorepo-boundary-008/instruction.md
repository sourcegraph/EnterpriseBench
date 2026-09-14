# Impact Analysis: ImageResponse Move to next/og

## Context

For the Next.js 14 release, `ImageResponse` is being moved from `next/server`
to a dedicated `next/og` entry point. The old `next/server` export will be
deprecated.

The main changes are in the `next` package:
- New entry point file(s) for `next/og`
- Updated webpack config and middleware plugin
- A relocated implementation module for the OG image response

Here's what concerns me: this monorepo compiles TypeScript with a compiler
written in a different, lower-level systems language, and that compiler
maintains its own internal table for resolving `next/*` import paths at
build time. If we move the export but don't update that compiler-side
resolution table, module resolution could point at the wrong location (or
fail entirely) for anyone building against the new path.

## What I Need

1. **Affected packages**: What else in the monorepo references
   `ImageResponse` or the `next/server` export path and needs updating?
   Look beyond the `next` package itself — check anywhere that resolves or
   hardcodes `next/*` import paths during compilation, plus any bundled/
   vendored copies of the underlying image-response implementation.

2. **Impact classification**: Classify the semver impact (none/patch/minor/
   major) for each affected package individually, and give an overall
   classification for the change. Justify each classification by explaining
   what breaks (or doesn't) for existing consumers.

3. **Boundary violations**: Show me the exact files where the old
   `next/server` → `ImageResponse` path is hardcoded or resolved, across
   every language involved in the build.

## Output

Write findings to `/workspace/next.js/IMPACT_REPORT.md`.
