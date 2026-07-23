# Impact Analysis: TC39 Decorator 2023-11 Normative Updates

## Context

The TC39 decorators proposal had normative changes in the November 2023 meeting that affect how decorator initialization ordering works. We've landed the implementation update in `@babel/helper-create-class-features-plugin` — specifically in `src/decorators.ts` and `src/index.ts`.

This is going into v7.24.0 as a minor bump. Before we cut the release, I need to understand the full scope of changes across the monorepo.

## What I Need

1. **Affected packages**: Which sibling packages in the babel monorepo consume the decorator helper? Think about the chain: helper → generated helpers → plugins → presets.

2. **Impact classification**: For each affected package:
   - **major** — breaking public API change?
   - **minor** — new behavior or feature?
   - **patch** — internal fix?
   - **none** — not actually affected?

3. **Boundary violations**: Show me the specific files where decorator helper APIs cross package boundaries. I especially want to know about any generated helper files (like `applyDecs*`) that need regenerating, and any plugin source files that call into the helper.

## Output

Write your findings to `/workspace/agent_output/IMPACT_REPORT.md` with sections for each affected package.
