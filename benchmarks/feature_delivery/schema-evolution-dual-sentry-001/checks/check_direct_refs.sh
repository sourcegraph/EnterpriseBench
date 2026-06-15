#!/usr/bin/env bash
# Checkpoint 2: Corresponding Relay Rust schema definitions.
# Anchored on the Relay crates/files named in the instruction and curated GT
# (relay-general/src/protocol/event.rs, relay-server/src/envelope.rs).
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4
grep -qE 'relay-general' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'relay-server' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'protocol/event|event\.rs' "$REPORT" && FOUND=$((FOUND + 1)) || true
grep -qE 'envelope\.rs|envelope' "$REPORT" && FOUND=$((FOUND + 1)) || true

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Found %d/%d Relay Rust schema references"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
