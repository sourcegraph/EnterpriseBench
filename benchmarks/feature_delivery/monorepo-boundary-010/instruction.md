# Impact Analysis: Static Initializers in Metadata Instead of MIR

## Context

We're changing how the Rust compiler represents static initializers internally. Currently, static items have their initializers stored as MIR (Mid-level Intermediate Representation) bodies. The change moves them to be stored directly in crate metadata.

The core change is in `rustc_middle` — specifically `compiler/rustc_middle/src/mir/` where the MIR types, interpret queries, and error handling are all updated.

This is an internal representation change (no user-facing API change), but it affects every compiler crate in the pipeline that touches static items: const evaluation, metadata encoding/decoding, code generation, and MIR transformations.

## What I Need

1. **Affected crates**: Which compiler crates handle static initializers and would need updating? Check the full pipeline: rustc_middle → rustc_const_eval → rustc_metadata → rustc_codegen_* → rustc_mir_transform. Also check if miri (the interpreter) is affected.

2. **Impact classification**: Since this is an internal representation change, the classification matters:
   - **minor** for crates that change internal logic but don't break their own public API
   - **major** if any crate's public interface (queries, function signatures) changes

3. **Boundary violations**: Show me the specific files where static initializer handling crosses crate boundaries — especially the query definitions in rustc_middle that other crates call, the metadata encoder/decoder that persists this data, and the const evaluator that interprets it.

## Output

Write findings to `/workspace/agent_output/IMPACT_REPORT.md`.
