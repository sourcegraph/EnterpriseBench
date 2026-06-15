# Identify dead code from deprecated React internals still referenced by create-react-app templates and scripts

React v18.2.0 deprecated several internal APIs and module structures as part of the
transition toward React Server Components and the new JSX transform. However,
create-react-app v5.0.1 was built for an earlier React API surface and may still
reference deprecated or removed React internals.

Your task:
1. Identify deprecated or dead exports in React v18.2.0 — look for modules marked
   with deprecation warnings, __SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED
   APIs that are no longer functional, and exports that have no internal callers.
2. Search create-react-app for references to these deprecated React internals —
   check template files, react-scripts webpack configuration, testing utilities,
   and build scripts.
3. For each dead code reference found in create-react-app, determine:
   - The React export/module that is dead or deprecated
   - The create-react-app file(s) that reference it
   - Whether the reference is load-bearing (breaks if removed) or vestigial
4. Identify any create-react-app shims or polyfills that exist solely to bridge
   deprecated React APIs.

Write your findings to /workspace/dead_code_report.json as a JSON array where each
entry uses `file` (the dead/deprecated React module path) and `symbol` (the dead
export name) as the primary keys:
[
  {
    "file": "path/to/react/module.js",
    "symbol": "exportName",
    "cra_references": ["path/to/cra/file.js"],
    "kind": "deprecated_api|dead_export|vestigial_shim",
    "confidence": "high|medium|low",
    "evidence": "explanation of why this is dead code",
    "load_bearing": true|false
  }
]
