# Impact Analysis: Static Initializers in Metadata Instead of MIR

## Context

The Rust compiler is changing how static initializers are represented
internally. Today, static items carry their initializers as MIR (Mid-level
Intermediate Representation) bodies. The change stores them directly in
crate metadata instead.

The core change lands in `rustc_middle` (`compiler/rustc_middle/src/mir/`),
touching the MIR types, the interpret-query definitions, and the associated
error handling.

## What I need

Investigate the `rust-lang/rust` compiler tree and write a grounded impact
analysis — cite the specific files and functions you find, not general
compiler theory.

1. **Affected crates**: Starting from `rustc_middle`, trace which other
   compiler crates consume the query/metadata surface for static
   initializers and would therefore need updating. Identify each crate by
   name and cite at least one file in it that would need to change.

2. **Impact classification**: For `rustc_middle` and each affected sibling
   crate, classify the change as none / patch / minor / major, and justify
   each classification against what actually changes in that crate — its
   public interface (queries, function signatures) versus its internal
   implementation.

3. **Boundary violations**: Show me the specific files where static
   initializer handling crosses crate boundaries — the query definitions in
   `rustc_middle` that other crates call, the code that persists this data
   into metadata, and the code that interprets/evaluates it during const
   evaluation.

## Output

Write findings to `/workspace/rust/IMPACT_REPORT.md`.
