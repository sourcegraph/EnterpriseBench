# benchmark_qa_core (first-party, self-contained copy — F2 subset)

`benchmark_qa_core` is a schema-agnostic QA library for benchmark task
definitions, maintained by the EnterpriseBench authors and shared across sibling
benchmark rigs (EB and CSB). It is kept here as a self-contained copy under
`_vendor/` — rather than an external dependency — so `eb_verify` has no
out-of-tree install requirement.

This is first-party code, covered by this repository's Apache 2.0 `LICENSE`.

## Subset note

Only the **aux-file leakage** check (`F*` finding codes) is vendored on main.
The full library — `oracle.py` (A/B/C/D codes), `scoring.py` (E codes),
`_symbols.py` — lives on the `fix/eb-f2-leak-triage` branch and can be ported
into this same package later; the finding-code namespace is reserved for it.

## Layout

| file | purpose |
|------|---------|
| `types.py`   | shared dataclasses / `Finding` shape |
| `leakage.py` | prompt / aux-file leakage checks |

Each rig parses its own task-meta schema and feeds already-extracted inputs into
these pure-functional checks; findings come back as a flat `list[Finding]`.

## EB-local behavior

`leakage.py` — `check_aux_file_leakage` skips the F2 check for bare generic
build-manifest basenames (`go.mod`, `package.json`, `pom.xml`, … see
`GENERIC_MANIFEST_BASENAMES`). These names are non-discriminative — every repo of
a language has one — so naming them in a prompt does not leak which file the
agent should investigate. Tokens with directory components (e.g. `envoy/go.mod`)
stay specific and still raise F2.
