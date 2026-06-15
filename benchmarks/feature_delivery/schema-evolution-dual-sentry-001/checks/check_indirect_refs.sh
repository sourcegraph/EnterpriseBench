#!/usr/bin/env bash
# Checkpoint 3: Agent traces indirect schema dependencies across the Sentry/Relay handshake.
# Tokens derived from expected_solution.json :: indirect_refs.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/SCHEMA_IMPACT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "SCHEMA_IMPACT.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=6
# Python-side validation rules that must mirror Rust-side rules
if grep -qE 'event_manager' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Relay normalizer pipeline consuming the protocol module
if grep -qE 'normaliz' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# The cross-repo handshake: project-config payload
if grep -qE 'project[ ._-]?config' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Unsynced enum value (EventType / data category)
if grep -qE 'EventType|enum' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Relay-side protocol schema is the consumed source-of-truth
if grep -qE 'protocol/event\.rs' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Asymmetric error responses / rejection on mismatch
if grep -qE 'reject|asymmetric|4(00|22)' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 4 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d indirect reference locations"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
