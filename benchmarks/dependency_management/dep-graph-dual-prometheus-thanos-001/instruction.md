        # dep-graph-dual-prometheus-thanos-001

        **Suite:** dependency_management | **Type:** dependency_graph | **Difficulty:** hard
        **Repos:** prometheus + thanos

        ## Context

        Prometheus changed its TSDB storage engine API for block compaction
and chunk encoding. Thanos extends Prometheus as a sidecar for
long-term storage and depends on these internal APIs.

Your task:
1. Find where Prometheus defines TSDB storage and compaction APIs
2. Find where Thanos implements its Prometheus sidecar integration
3. Identify TSDB API changes that break Thanos' block uploading
4. Document the dependency chain and affected Thanos components

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
