# Detect configuration drift between webpack loader execution order and Babel plugin/preset resolution order

A build team reports that their webpack build produces different output depending
on whether files are processed by babel-loader alone or by a chain of loaders.
Specifically, decorators and class properties are transformed differently when
webpack's module rules apply multiple loaders in sequence vs when Babel applies
its plugins in its own ordering.

The root cause is a drift between webpack's loader execution order (right-to-left /
bottom-to-top in the rules array) and Babel's plugin resolution order (plugins
run before presets, plugins run first-to-last, presets run last-to-first).

Your task:
1. Find where webpack implements module rule matching and loader execution order —
   trace from NormalModule through the loader runner to identify how multiple
   matching rules and their loaders are ordered and executed.
2. Find where Babel resolves and orders its plugins and presets — trace from
   @babel/core's config loading through plugin ordering to identify the
   execution sequence for transforms.
3. Identify the drift points where webpack's loader ordering assumptions conflict
   with Babel's plugin ordering — particularly for decorator and class property
   transforms that are order-sensitive.
4. Document each drift point with source files from both repos.

Document in your answer (use these JSON fields):
{
  "drift_points": [
    {
      "config_key": "<ordering rule or configuration>",
      "webpack_behavior": "<how webpack orders execution>",
      "webpack_source_file": "<path in webpack repo>",
      "babel_behavior": "<how Babel orders plugins>",
      "babel_source_file": "<path in babel repo>",
      "impact": "<how this drift affects build output>"
    }
  ]
}
