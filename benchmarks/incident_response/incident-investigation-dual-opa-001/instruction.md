# Incident: Gatekeeper rejects ConstraintTemplates that import rego.v1

## Alert

After upgrading the cluster to OPA Gatekeeper v3.17.1, several
ConstraintTemplates fail to install. Their pod-level status conditions show
`ingest_error` with messages that look like Rego parse errors:

```
rego_parse_error: var cannot be used for rule name
    package my.constraint
                ^
rego_compile_error: rules must not shadow builtin
  metadata.name (use a different name)
```

## Symptoms

- Templates that include `import rego.v1` (modern Rego v1 syntax) fail
  ingestion.
- Templates without that import install cleanly.
- The same Rego files compile and evaluate without error using the standalone
  `opa eval` binary that ships with OPA v0.69.0/v1.0.x.
- New templates work; the failure is independent of constraint resources or
  user input — it happens at template-compile time inside the constraint
  framework.

## Environment

- `/workspace/frameworks/` — `open-policy-agent/frameworks` at the constraint
  module tag pinned by Gatekeeper v3.17.1's `go.mod`.
- `/workspace/opa/` — `open-policy-agent/opa` at v0.69.0.

Both are Go projects. The failure lives at the seam between how the
constraint framework compiles a ConstraintTemplate's Rego and how OPA parses
and evaluates it; you will need to read both repos to locate it.

## What I need

Investigate the two codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
Rego-version theory.

1. **Root cause** — find the file and function in `frameworks` that pins the
   Rego language version used at template-compile time, AND the OPA-side
   file(s) that decide whether v1 syntax is accepted. Cite each by path.
2. **Error chain** — trace the v1-import template from submission into the
   constraint framework through to parse-time rejection, at least three hops,
   each tied to a code path you identified.
3. **Affected components** — identify every component across both repos that
   participates in compiling and evaluating a ConstraintTemplate's Rego.
4. **Remediation** — propose a concrete, code-grounded fix and say which
   repo and file it belongs in.

## Output

Write your incident report to `/workspace/INCIDENT_REPORT.md`.
