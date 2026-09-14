        # incident-inv-dual-cortex-thanos-001

        **Suite:** incident_response | **Type:** incident_investigation | **Difficulty:** hard
        **Repos:** cortex + thanos

        ## Context

        A metrics platform using Cortex for write path and Thanos for read path
is experiencing intermittent query failures. The issue appears when
Cortex ingesters change ring membership and Thanos' StoreAPI returns
partial results.

Your task:
1. Find how Cortex manages the ingester hash ring and handles membership changes
2. Find how Thanos StoreAPI discovers and queries Cortex ingesters
3. Identify the race condition between ring membership changes and query routing
4. Document the failure mode and recommended mitigation

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
