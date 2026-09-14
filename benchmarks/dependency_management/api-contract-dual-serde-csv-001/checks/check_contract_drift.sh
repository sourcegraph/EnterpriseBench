        #!/usr/bin/env bash
        # Verifier for checkpoint: contract_drift
        # Task: api-contract-dual-serde-csv-001
        set -euo pipefail

        REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

        if [[ ! -f "$REPORT" ]]; then
            printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
            exit 0
        fi

        FOUND=0
        TOTAL=2

        # Repo-specific artifacts only — the concept token is kept but now
        # gated behind evidence that BOTH sides of the contract were cited by
        # file path, not just the generic vocabulary already in the prompt.
        if grep -qiE 'de/mod\.rs|serde_derive/src/de\.rs' "$REPORT" && grep -qiE 'deserializer\.rs|reader\.rs' "$REPORT"; then
            FOUND=$((FOUND + 1))
        fi
grep -qiE 'difference|mismatch|behavior' "$REPORT" && FOUND=$((FOUND + 1))

        SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
        PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
        printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
