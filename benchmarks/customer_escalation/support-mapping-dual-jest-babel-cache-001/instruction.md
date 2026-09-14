# Map customer Jest tests passing once but failing on rerun after Babel plugin upgrade to the babel-jest transform-cache key boundary

A customer reports that after upgrading @babel/plugin-transform-runtime
their Jest test suite passes once on a clean checkout but every rerun
fails with 'ReferenceError: regeneratorRuntime is not defined' in async
tests. Clearing node_modules/.cache makes it pass once again.

Your task:
1. Find where Jest's runtime invokes its transformer to compile source
   files and how it decides whether to use a cached transform output.
2. Find where babel-jest computes the transform cache key (file content,
   babel config, plugin versions) and which inputs it includes.
3. Trace the cross-repo path: jest-runtime ScriptTransformer.transform()
   -> babel-jest getCacheKey -> babel-core loadOptions / partialConfig
   -> plugin signature inputs. Cite each file and the function
   responsible.
4. Identify why a plugin upgrade that does NOT bump the package.json
   version (for example a local lockfile resolution change) can
   produce a stale cache hit, and what makes the second run fail
   when the first one passed.
