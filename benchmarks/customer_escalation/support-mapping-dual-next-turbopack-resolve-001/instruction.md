# Map customer Next.js Turbopack monorepo workspace resolution divergence from Webpack to the next-config / Turbopack resolver boundary

A customer reports that their Next.js 14 monorepo build works under
webpack (next dev) but fails under Turbopack (next dev --turbo) with
'Module not found: Cannot resolve @internal/ui' for a workspace package
they consume via pnpm. Their next.config.js sets transpilePackages and
the webpack alias keeps working, but Turbopack's resolver does not see
the package.

Your task:
1. Find where Next.js's webpack-config.ts adds the workspace package to
   the module resolution path and applies transpilePackages.
2. Find where Turbopack's resolver implementation handles workspace
   roots and how it interprets pnpm's nested node_modules layout.
3. Trace the cross-repo path: next next-server/dev start ->
   loadConfig -> turbopack resolve plugin -> turbo-tasks-fs read ->
   package.json walk. Cite each file and the function or attribute
   responsible.
4. Identify why pnpm's symlinked workspace layout (with .pnpm/ store)
   resolves under webpack's enhanced-resolve but not under
   Turbopack's default resolver, and what config option closes the gap.
