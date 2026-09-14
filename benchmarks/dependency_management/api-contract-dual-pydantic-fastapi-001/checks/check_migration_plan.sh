        #!/usr/bin/env bash
        # Verifier for checkpoint: migration_plan
        # Task: api-contract-dual-pydantic-fastapi-001
        set -euo pipefail

        REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

        if [[ ! -f "$REPORT" ]]; then
            printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
            exit 0
        fi

        FOUND=0
        TOTAL=2

        grep -qiE 'migration|v2|model_dump' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'deprecated|replacement' "$REPORT" && FOUND=$((FOUND + 1))

        SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
        PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
        printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
