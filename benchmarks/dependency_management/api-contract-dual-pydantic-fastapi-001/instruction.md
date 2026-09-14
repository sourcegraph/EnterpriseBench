        # api-contract-dual-pydantic-fastapi-001

        **Suite:** dependency_management | **Type:** api_contract | **Difficulty:** hard
        **Repos:** pydantic + fastapi

        ## Context

        Pydantic v2 rewrote BaseModel internals: `.dict()` → `.model_dump()`,
`.json()` → `.model_dump_json()`, `__fields__` → `model_fields`,
`@validator` → `@field_validator`. FastAPI depends on pydantic for
request/response model handling.

Your task:
1. Find where pydantic v2 defines the new BaseModel API
2. Find where FastAPI uses pydantic BaseModel methods for serialization
3. Identify all pydantic v1 API calls in FastAPI that break with v2
4. Document the migration path for each affected FastAPI module

Write your analysis to /workspace/analysis/IMPACT_REPORT.md

        ## Expected Output

        Write your analysis to `/workspace/analysis/IMPACT_REPORT.md`.

        Your report should include:
        - File paths from each repository that are relevant
        - Specific code symbols, functions, or types involved
        - The nature of each change (removal, rename, signature change, behavioral)
        - Migration recommendations

        ## Hints

        - All repos are cloned under `/workspace/`
        - Focus on import/dependency declarations first, then trace into implementation
        - Check version constraints in dependency manifests (Cargo.toml, go.mod, pom.xml, requirements.txt)
