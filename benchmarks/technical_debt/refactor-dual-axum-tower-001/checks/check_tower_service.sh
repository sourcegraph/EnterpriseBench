        #!/usr/bin/env bash
        # Verifier for checkpoint: tower_service
        # Task: refactor-dual-axum-tower-001
        set -euo pipefail

        REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

        if [[ ! -f "$REPORT" ]]; then
            printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
            exit 0
        fi

        FOUND=0
        TOTAL=3

        # Repo-specific artifacts only — "Service"/"Layer"/"tower" alone are
        # already in the prompt and must not earn credit on their own.
        grep -qiE 'tower/src/lib\.rs' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'tower-layer/src/lib\.rs|tower::Layer' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'Service<Request<B>>|type Response|type Future' "$REPORT" && FOUND=$((FOUND + 1))

        SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
        PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
        printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
