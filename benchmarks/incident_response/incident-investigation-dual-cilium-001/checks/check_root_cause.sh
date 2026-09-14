#!/usr/bin/env bash
set -euo pipefail
REPORT="${WORKSPACE:-/workspace}/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'; exit 0
fi
FOUND=0; TOTAL=3
# Repo-specific artifacts only — generic incident terms (torn, data race, pod
# churn, endpoint regeneration) are given in the prompt and must not earn credit.
if grep -qiE 'pkg/bpf/collection\.go|LoadCollectionSpec|pkg/bpf/.*collection|bpf/collection' "$REPORT"; then FOUND=$((FOUND+1)); fi
if grep -qiE 'MapSpec\.createMap|createMap|elf_reader\.go|ebpf.*map\.go|ebpf.*collection\.go' "$REPORT"; then FOUND=$((FOUND+1)); fi
# The discriminating hop: must name the actual shared/mutated structure
# (MapSpec / reader cursor) together with a mutation-in-concurrency claim,
# not just echo the prompt's "DATA RACE" / "torn" symptom wording.
if grep -qiE 'MapSpec|cursor' "$REPORT" && grep -qiE 'mutat|shared|concurrent|torn|race' "$REPORT"; then FOUND=$((FOUND+1)); fi
SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d root cause elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
