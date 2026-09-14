        # config-drift-dual-jaeger-otel-001

        **Suite:** platform_engineering | **Type:** config_drift | **Difficulty:** hard
        **Repos:** jaeger + otel-collector

        ## Context

        Jaeger is migrating its backend to use the OpenTelemetry Collector as
its core processing engine. During the migration, configuration formats
between Jaeger's legacy config and OTel Collector config can drift.

Your task:
1. Find Jaeger's legacy collector configuration format and flags
2. Find OpenTelemetry Collector's configuration schema for tracing
3. Identify configuration parameters that exist in one but not the other
4. Document the configuration migration path from Jaeger to OTel format

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
