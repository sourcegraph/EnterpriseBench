        #!/usr/bin/env bash
        # Verifier for checkpoint: csv_serde_impl
        # Task: api-contract-dual-serde-csv-001
        set -euo pipefail

        REPORT="${WORKSPACE:-/workspace}/analysis/IMPACT_REPORT.md"

        if [[ ! -f "$REPORT" ]]; then
            printf '{"score": 0.0, "passed": false, "detail": "Report not found"}\n'
            exit 0
        fi

        FOUND=0
        TOTAL=3

        # Repo-specific artifacts only — generic terms (csv, deserialize) are
        # already given in the prompt and must not earn credit on their own.
        grep -qiE 'csv/src/deserializer\.rs|src/deserializer\.rs' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'deserialize_string_record|DeRecordWrap|DeStringRecord|DeByteRecord' "$REPORT" && FOUND=$((FOUND + 1))
grep -qiE 'StringRecord' "$REPORT" && FOUND=$((FOUND + 1))

        SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
        PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
        printf '{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
