# Trace the dependency chain from Next.js build system through webpack compilation pipeline and plugin hooks

Next.js uses webpack as its bundler but deeply integrates with webpack internals
beyond the public plugin API. When webpack releases a new version, understanding
which Next.js features depend on which webpack internal APIs is essential for
assessing upgrade risk.

Your task:
1. Trace how Next.js configures webpack — find where Next.js constructs its
   webpack configuration (the webpack-config.ts entry point), and map all the
   custom plugins Next.js registers. For each plugin, identify whether it uses
   webpack's public plugin API or internal/private modules.
2. In the webpack repo, find the internal modules that Next.js depends on —
   trace through webpack's NormalModule, Compilation, and Module classes to
   identify which hooks and properties Next.js plugins access.
3. Map the dependency graph: Next.js build entry -> webpack config construction
   -> custom plugin registration -> webpack compilation hooks -> webpack internal
   modules. Document each edge with the files that define the relationship.
4. Identify fragile dependencies: which Next.js plugins access webpack internals
   that are not part of webpack's public API and could break on webpack upgrades?

Document in your answer (use these JSON fields):
{
  "dependency_chain": [
    {
      "from": "<Next.js module/plugin>",
      "to": "<webpack module/hook>",
      "relationship_type": "<public plugin API | internal module access | hook subscription>",
      "nextjs_file": "<file in Next.js repo>",
      "webpack_file": "<file in webpack repo>",
      "fragility": "<low | medium | high>"
    }
  ]
}
