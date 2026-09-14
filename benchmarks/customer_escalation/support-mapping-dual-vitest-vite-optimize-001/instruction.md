# Map customer Vitest tests failing with stale module after Vite optimizeDeps refresh to the Vitest pool / Vite optimizeDeps cache boundary

A customer reports that under Vitest watch mode their tests pass on
the first run but fail intermittently on file save with errors like
'TypeError: foo is not a function' for re-exported library symbols.
Restarting Vitest fixes it until the next save.

Your task:
1. Find where Vitest's pool spawns worker threads and how it shares
   Vite's transform cache.
2. Find where Vite's optimizeDeps pre-bundles dependencies and how
   it invalidates the cache on file change.
3. Trace the cross-repo path: Vitest watcher file change -> Vite
   moduleGraph invalidate -> optimizeDeps re-pre-bundle -> worker
   pool module reload. Cite each file and the function responsible.
4. Identify why a stale optimizeDeps bundle can survive a watcher
   tick when the pre-bundled chunk's mtime trails the source change.
