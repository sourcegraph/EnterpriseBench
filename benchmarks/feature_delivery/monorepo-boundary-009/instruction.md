# Impact Analysis: rustc_attr Crate Split

## Context

We're refactoring the `rustc_attr` compiler crate as part of a multi-phase split:

- **Phase 1** (this change): Split into `rustc_attr_parsing` (parsing/validation logic) and `rustc_attr_data_structures` (shared type definitions)
- **Phase 2** (future): Extract `rustc_attr_validation` from the parsing crate

The current `rustc_attr` crate at `compiler/rustc_attr/` exports both data types and parsing functions. After the split, downstream crates will need to depend on one or both of the new crates depending on what they import.

## What I Need

1. **Affected crates**: Which compiler crates have `rustc_attr` in their `Cargo.toml` dependencies? Check all crates under `compiler/` — there are 50+ compiler crates in the workspace.

2. **Impact classification**: For each affected crate, tell me:
   - Does it only use data types (→ depend on `rustc_attr_data_structures`)?
   - Does it call parsing functions (→ depend on `rustc_attr_parsing`)?
   - Does it need both?
   - This determines whether it's a simple Cargo.toml rename or also needs source `use` path changes.

3. **Boundary violations**: Show me the specific `Cargo.toml` dependency lines and the `use rustc_attr::` import statements in source files that reference the crate being split.

## Output

Write findings to `/workspace/agent_output/IMPACT_REPORT.md`.
