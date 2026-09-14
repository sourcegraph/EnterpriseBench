        #!/usr/bin/env bash
        # Verifier for checkpoint: refactor_plan
        # Task: refactor-dual-axum-tower-001
        set -euo pipefail

        REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

        if [[ ! -f "$REPORT" ]]; then
            printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
            exit 0
        fi

        FOUND=0
        TOTAL=2

        # Repo-specific artifacts only — "refactor"/"adapt"/"migration"/"middleware"
        # alone are already in the prompt and must not earn credit on their own.
        grep -qiE 'axum::body::Body|BoxBody' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'Layer<S|axum-v0\.7\.0|axum 0\.7\.0|tower-0\.4\.13|tower 0\.4\.13|cargo check' "$REPORT" && FOUND=$((FOUND + 1))

        SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
        PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
        printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
