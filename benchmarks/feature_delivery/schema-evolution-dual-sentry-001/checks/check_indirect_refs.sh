#!/usr/bin/env bash
# Checkpoint 3: Schema drift points with both-side references — Rust Protocol structs/enums
# and the validation logic the instruction asks the agent to compare across languages.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
grep -qiE 'protocol' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'enum' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'validation|validate' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qiE 'drift' "$REPORT" && FOUND=$((FOUND + 1)) || true

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Found %d/%d schema-drift elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
