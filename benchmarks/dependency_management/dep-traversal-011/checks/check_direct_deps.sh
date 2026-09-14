#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
if grep -qiE 'spring.boot' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'kafka' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Repo-specific: the actual dependent module/artifact names — not stated in the
# prompt, only discoverable by reading the workspace repos' build manifests.
if grep -qiE 'spring-boot-starter-log4j2|pax-logging-log4j2|kafka-clients' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d dependents (including shaded)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
