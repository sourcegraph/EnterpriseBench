        # api-contract-dual-serde-csv-001

        **Suite:** dependency_management | **Type:** api_contract | **Difficulty:** hard
        **Repos:** serde + csv

        ## Context

        The csv crate depends on serde to convert parsed CSV rows into typed
Rust values. A user reports that after aligning their serde version with
csv 1.3.0's requirements, one category of struct definition that used to
deserialize cleanly now produces different output for the same CSV input,
even though the CSV text itself has not changed.

Your task:
1. Investigate how serde exposes the contract that a data format (such as
   csv) must implement in order to deserialize into arbitrary Rust types.
2. Find where csv implements that contract for its own record types, and
   trace how a single CSV row becomes a deserialized value.
3. Identify which category of struct definition is affected by the version
   change, and pinpoint the exact code on each side of the contract that
   is responsible.
4. Explain why the change alters the output for the affected struct shapes,
   and describe the affected use cases.

Write your analysis to /workspace/analysis/IMPACT_REPORT.md

        ## Expected Output

        Write your analysis to `/workspace/analysis/IMPACT_REPORT.md`.

        Your report should include:
        - File paths from each repository that are relevant
        - Specific code symbols, functions, or types involved
        - The nature of each change (removal, rename, signature change, or
          altered output for the same input)
        - Migration recommendations

        ## Hints

        - All repos are cloned under `/workspace/`
        - Focus on import/dependency declarations first, then trace into implementation
        - Check version constraints in dependency manifests (Cargo.toml, go.mod, pom.xml, requirements.txt)
